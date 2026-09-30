"""
FIFO 取貨工作（出貨掃碼確認）

流程：建立取貨工作 → 核對儲位 → 逐箱掃描 → 確認出庫。
庫存只在「確認出庫」時扣除；掃到錯的儲位、批次或箱號一律拒絕。
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.helpers import (
    batch_to_frontend,
    build_fifo_plan,
    deduct_batch,
    fetch_batch,
    fetch_fifo_rows,
    now_text,
    read_connection,
    record_transaction,
    require_operator,
    to_iso,
    write_connection,
)


router = APIRouter(prefix="/api/pick-tasks", tags=["取貨工作"])


class PickTaskRequest(BaseModel):
    item: str
    quantity: int
    operator: str


class LocationScanRequest(BaseModel):
    locationCode: str


class CrateScanRequest(BaseModel):
    crateCode: str


def load_task(connection, task_id):
    task = connection.execute(
        "SELECT * FROM pick_tasks WHERE task_id = ?",
        (task_id,),
    ).fetchone()

    if task is None:
        raise HTTPException(status_code=404, detail="找不到此取貨工作")

    return task


def load_open_task(connection, task_id):
    task = load_task(connection, task_id)

    if task["status"] != "open":
        raise HTTPException(status_code=409, detail="此取貨工作已結束")

    return task


def load_lines(connection, task_id):
    return connection.execute(
        """
        SELECT
            line.line_no,
            line.batch_id,
            line.quantity,
            (
                SELECT COUNT(*)
                FROM pick_task_scans scan
                WHERE scan.task_id = line.task_id
                  AND scan.batch_id = line.batch_id
            ) AS scanned
        FROM pick_task_lines line
        WHERE line.task_id = ?
        ORDER BY line.line_no
        """,
        (task_id,),
    ).fetchall()


def next_line(lines):
    # 目前應取貨的批次：依 FIFO 順序第一個尚未掃滿的批次
    for line in lines:
        if line["scanned"] < line["quantity"]:
            return line

    return None


def plan_entry(connection, line):
    entry = batch_to_frontend(fetch_batch(connection, line["batch_id"]))

    return {
        "batchId": entry["id"],
        "warehouse": entry["warehouse"],
        "bin": entry["bin"],
        "lotNumber": entry["lotNumber"],
        "receivedAt": entry["receivedAt"],
        "locationCode": entry["locationCode"],
        "quantity": line["quantity"],
        "scanned": line["scanned"],
    }


def task_payload(connection, task_id):
    task = load_task(connection, task_id)
    lines = load_lines(connection, task_id)
    current = next_line(lines)

    scans = connection.execute(
        """
        SELECT scan.crate_code, scan.batch_id, ib.lot_number
        FROM pick_task_scans scan
        JOIN inventory_batches ib
            ON scan.batch_id = ib.batch_id
        WHERE scan.task_id = ?
        ORDER BY scan.scanned_at, scan.rowid
        """,
        (task_id,),
    ).fetchall()

    return {
        "id": task["task_id"],
        "name": task["product_name"],
        "quantity": task["quantity"],
        "operator": task["operator"],
        "status": task["status"],
        "createdAt": to_iso(task["created_at"]),
        "closedAt": to_iso(task["closed_at"]),
        "plan": [plan_entry(connection, line) for line in lines],
        "scanned": [
            {
                "crateCode": scan["crate_code"],
                "batchId": scan["batch_id"],
                "lotNumber": scan["lot_number"] or "",
            }
            for scan in scans
        ],
        "verifiedBatchId": task["verified_batch_id"],
        "next": plan_entry(connection, current) if current else None,
        "ready": task["status"] == "open" and len(scans) == task["quantity"],
    }


@router.post("")
def create_pick_task(request: PickTaskRequest):
    if request.quantity <= 0:
        raise HTTPException(status_code=400, detail="出貨數量必須大於 0")

    operator = require_operator(request.operator)
    item = request.item.strip()

    with write_connection() as connection:
        plan, remaining = build_fifo_plan(
            fetch_fifo_rows(connection, item),
            request.quantity,
        )

        if remaining > 0:
            raise HTTPException(
                status_code=400,
                detail="庫存不足，無法建立取貨工作",
            )

        task_id = connection.execute(
            """
            INSERT INTO pick_tasks
                (product_name, quantity, operator, status, created_at)
            VALUES (?, ?, ?, 'open', ?)
            """,
            (item, request.quantity, operator, now_text()),
        ).lastrowid

        connection.executemany(
            """
            INSERT INTO pick_task_lines (task_id, line_no, batch_id, quantity)
            VALUES (?, ?, ?, ?)
            """,
            [
                (task_id, index + 1, row["batch_id"], take)
                for index, (row, take) in enumerate(plan)
            ],
        )

        return task_payload(connection, task_id)


@router.get("")
def list_pick_tasks(status: str = "open"):
    with read_connection() as connection:
        if status == "all":
            rows = connection.execute(
                "SELECT task_id FROM pick_tasks ORDER BY task_id DESC"
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT task_id
                FROM pick_tasks
                WHERE status = ?
                ORDER BY task_id DESC
                """,
                (status,),
            ).fetchall()

        return [task_payload(connection, row["task_id"]) for row in rows]


@router.get("/{task_id}")
def get_pick_task(task_id: int):
    with read_connection() as connection:
        return task_payload(connection, task_id)


@router.post("/{task_id}/verify-location")
def verify_location(task_id: int, request: LocationScanRequest):
    with write_connection() as connection:
        load_open_task(connection, task_id)
        current = next_line(load_lines(connection, task_id))

        if current is None:
            raise HTTPException(
                status_code=409,
                detail="需求數量已全部掃描，請確認出庫",
            )

        expected = plan_entry(connection, current)["locationCode"]

        if request.locationCode.strip().upper() != expected:
            raise HTTPException(
                status_code=400,
                detail=f"儲位不符，請依 FIFO 指示掃描 {expected}",
            )

        connection.execute(
            "UPDATE pick_tasks SET verified_batch_id = ? WHERE task_id = ?",
            (current["batch_id"], task_id),
        )

        return task_payload(connection, task_id)


@router.post("/{task_id}/scan")
def scan_crate(task_id: int, request: CrateScanRequest):
    code = request.crateCode.strip()

    with write_connection() as connection:
        task = load_open_task(connection, task_id)
        current = next_line(load_lines(connection, task_id))

        if current is None:
            raise HTTPException(
                status_code=409,
                detail="需求數量已全部掃描，請確認出庫",
            )

        if task["verified_batch_id"] != current["batch_id"]:
            raise HTTPException(
                status_code=409,
                detail="請先核對目前 FIFO 指定的儲位",
            )

        already_scanned = connection.execute(
            """
            SELECT 1
            FROM pick_task_scans
            WHERE task_id = ?
              AND crate_code = ?
            """,
            (task_id, code),
        ).fetchone()

        if already_scanned:
            raise HTTPException(
                status_code=409,
                detail="這箱已掃描過，不能重複計數",
            )

        crate = connection.execute(
            """
            SELECT 1
            FROM crates
            WHERE crate_code = ?
              AND batch_id = ?
              AND status = 'in_stock'
            """,
            (code, current["batch_id"]),
        ).fetchone()

        if crate is None:
            raise HTTPException(
                status_code=400,
                detail="這個箱標籤不屬於目前 FIFO 指定批次，請重新確認",
            )

        connection.execute(
            """
            INSERT INTO pick_task_scans
                (task_id, crate_code, batch_id, scanned_at)
            VALUES (?, ?, ?, ?)
            """,
            (task_id, code, current["batch_id"], now_text()),
        )

        # 這個批次掃滿後，換下一個批次必須重新核對儲位
        if current["scanned"] + 1 >= current["quantity"]:
            connection.execute(
                "UPDATE pick_tasks SET verified_batch_id = NULL WHERE task_id = ?",
                (task_id,),
            )

        return task_payload(connection, task_id)


@router.post("/{task_id}/confirm")
def confirm_pick_task(task_id: int):
    with write_connection() as connection:
        task = load_open_task(connection, task_id)
        lines = load_lines(connection, task_id)

        if sum(line["scanned"] for line in lines) != task["quantity"]:
            raise HTTPException(
                status_code=400,
                detail="實際掃描箱數與出貨需求不符，不能確認出庫",
            )

        for line in lines:
            batch = fetch_batch(connection, line["batch_id"])

            codes = [
                row["crate_code"]
                for row in connection.execute(
                    """
                    SELECT scan.crate_code
                    FROM pick_task_scans scan
                    JOIN crates
                        ON crates.crate_code = scan.crate_code
                    WHERE scan.task_id = ?
                      AND scan.batch_id = ?
                      AND crates.status = 'in_stock'
                    """,
                    (task_id, line["batch_id"]),
                ).fetchall()
            ]

            # 建立工作後庫存可能已被其他人異動，這裡重新檢查
            if batch["quantity"] < line["quantity"] or len(codes) != line["quantity"]:
                raise HTTPException(
                    status_code=409,
                    detail="庫存已變動，請取消此工作並重新建立 FIFO 取貨單",
                )

            deduct_batch(connection, line["batch_id"], codes)

            record_transaction(
                connection=connection,
                batch_id=line["batch_id"],
                transaction_type="出貨",
                quantity_change=-line["quantity"],
                operator=task["operator"],
                reason="PDA／掃碼確認",
            )

        connection.execute(
            """
            UPDATE pick_tasks
            SET status = 'confirmed',
                verified_batch_id = NULL,
                closed_at = ?
            WHERE task_id = ?
            """,
            (now_text(), task_id),
        )

        return {
            "success": True,
            "message": f"已確認出庫 {task['product_name']} {task['quantity']} 箱",
            "task": task_payload(connection, task_id),
        }


@router.post("/{task_id}/cancel")
def cancel_pick_task(task_id: int):
    with write_connection() as connection:
        load_open_task(connection, task_id)

        connection.execute(
            """
            UPDATE pick_tasks
            SET status = 'cancelled',
                verified_batch_id = NULL,
                closed_at = ?
            WHERE task_id = ?
            """,
            (now_text(), task_id),
        )

        return {
            "success": True,
            "message": "已取消取貨工作，庫存未變更",
            "task": task_payload(connection, task_id),
        }
