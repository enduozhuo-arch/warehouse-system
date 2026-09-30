"""
每日盤點

每天一張盤點清單：當日有庫存的批次都會列入，
並保存盤點前後數量、原因、操作人，讓管理者看得到哪些批次還沒盤。
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.helpers import (
    BATCH_SELECT,
    apply_count,
    batch_to_frontend,
    fetch_batch,
    now_text,
    parse_date,
    read_connection,
    require_operator,
    to_iso,
    today_text,
    write_connection,
)


router = APIRouter(prefix="/api/stocktake", tags=["每日盤點"])


class StocktakeCountRequest(BaseModel):
    id: int
    actual_quantity: int
    reason: str = ""
    note: str = ""
    operator: str


def add_active_batches(connection, count_date):
    # 把目前有庫存、尚未列入的批次加進當日盤點清單
    connection.execute(
        """
        INSERT OR IGNORE INTO stocktake_items (count_date, batch_id)
        SELECT ?, batch_id
        FROM inventory_batches
        WHERE quantity > 0
        """,
        (count_date,),
    )


def stocktake_payload(connection, count_date, warehouse="all"):
    rows = connection.execute(
        """
        SELECT
            item.system_quantity,
            item.actual_quantity,
            item.reason,
            item.note,
            item.operator,
            item.counted_at,
            batch.*
        FROM stocktake_items item
        JOIN (
        """
        + BATCH_SELECT
        + """
        ) batch
            ON batch.batch_id = item.batch_id
        WHERE item.count_date = ?
        ORDER BY batch.warehouse_id, batch.received_at, batch.batch_id
        """,
        (count_date,),
    ).fetchall()

    counted = sum(1 for row in rows if row["counted_at"])

    items = []

    for row in rows:
        if warehouse != "all" and str(row["warehouse_id"]) != warehouse:
            continue

        item = batch_to_frontend(row)
        item.update(
            {
                "counted": bool(row["counted_at"]),
                "oldQty": row["system_quantity"],
                "actual": row["actual_quantity"],
                "reason": row["reason"] or "",
                "note": row["note"] or "",
                "operator": row["operator"] or "",
                "countedAt": to_iso(row["counted_at"]),
            }
        )
        items.append(item)

    return {
        "date": count_date,
        "total": len(rows),
        "counted": counted,
        "completed": len(rows) > 0 and counted == len(rows),
        "items": items,
    }


@router.get("")
def get_stocktake(date: str = "", warehouse: str = "all"):
    count_date = date or today_text()
    parse_date(count_date, "盤點日期")

    # 只有當日清單會補入新批次；過去的清單維持當時的內容
    if count_date == today_text():
        with write_connection() as connection:
            add_active_batches(connection, count_date)
            return stocktake_payload(connection, count_date, warehouse)

    with read_connection() as connection:
        return stocktake_payload(connection, count_date, warehouse)


@router.post("/counts")
def save_count(request: StocktakeCountRequest):
    operator = require_operator(request.operator)
    reason = request.reason.strip()
    note = request.note.strip()
    count_date = today_text()

    with write_connection() as connection:
        batch = fetch_batch(connection, request.id)

        if batch is None:
            raise HTTPException(status_code=404, detail="找不到此庫存批次")

        add_active_batches(connection, count_date)

        old_quantity, difference, kind = apply_count(
            connection=connection,
            batch=batch,
            actual_quantity=request.actual_quantity,
            reason=reason,
            note=note,
            operator=operator,
            record_confirmation=True,
        )

        connection.execute(
            """
            INSERT INTO stocktake_items
                (
                    count_date,
                    batch_id,
                    system_quantity,
                    actual_quantity,
                    reason,
                    note,
                    operator,
                    counted_at
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (count_date, batch_id) DO UPDATE SET
                system_quantity = excluded.system_quantity,
                actual_quantity = excluded.actual_quantity,
                reason = excluded.reason,
                note = excluded.note,
                operator = excluded.operator,
                counted_at = excluded.counted_at
            """,
            (
                count_date,
                request.id,
                old_quantity,
                request.actual_quantity,
                reason or "數量確認",
                note,
                operator,
                now_text(),
            ),
        )

        progress = stocktake_payload(connection, count_date)

        result = batch_to_frontend(fetch_batch(connection, request.id))
        result.update(
            {
                "success": True,
                "message": (
                    f"已盤點並登記報廢 {abs(difference)} 箱"
                    if kind == "報廢"
                    else "盤點完成"
                ),
                "kind": kind,
                "oldQty": old_quantity,
                "actual": request.actual_quantity,
                "difference": difference,
                "reason": reason or "數量確認",
                "note": note,
                "operator": operator,
                "date": count_date,
                "counted": progress["counted"],
                "total": progress["total"],
                "completed": progress["completed"],
            }
        )

        return result
