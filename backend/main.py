from datetime import datetime, timezone, timedelta
from multiprocessing.dummy import connection

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from backend.database import get_connection


TAIPEI_TZ = timezone(timedelta(hours=8))

app = FastAPI(
    title="竹南冷凍倉儲系統 API",
    description="提供進貨、庫存、FIFO 出貨與盤點功能",
    version="1.1.0",
)


def to_iso(received_at):
    if not received_at:
        return received_at

    # SQLite 原始格式：2026-09-20 08:00:00
    if "T" not in received_at:
        received_at = received_at.replace(" ", "T")

    if not received_at.endswith("+08:00"):
        received_at += "+08:00"

    return received_at


def batch_to_frontend(row):
    return {
        "id": row["batch_id"],
        "name": row["product_name"],
        "warehouse": str(row["warehouse_id"]),
        "bin": row["location_code"],
        "qty": row["quantity"],
        "receivedAt": to_iso(row["received_at"]),
    }

def record_transaction(
    connection,
    batch_id,
    transaction_type,
    quantity_change,
    operator,
    reason="",
    note="",
):
    created_at = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")

    connection.execute(
        """
        INSERT INTO inventory_transactions
            (
                batch_id,
                transaction_type,
                quantity_change,
                operator,
                reason,
                note,
                created_at
            )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            batch_id,
            transaction_type,
            quantity_change,
            operator,
            reason,
            note,
            created_at,
        ),
    )

class InboundRequest(BaseModel):
    item: str
    warehouse: str
    bin: str
    quantity: int
    operator: str


class OutboundRequest(BaseModel):
    item: str
    quantity: int
    operator: str


class InventoryAdjustmentRequest(BaseModel):
    id: int
    actual_quantity: int
    reason: str = ""
    note: str = ""
    operator: str


@app.get("/")
def home():
    return {"message": "Warehouse System API is running"}


@app.get("/api/inventory")
def get_inventory():
    connection = get_connection()

    try:
        rows = connection.execute(
            """
            SELECT
                ib.batch_id,
                p.product_name,
                l.warehouse_id,
                l.location_code,
                ib.quantity,
                ib.received_at
            FROM inventory_batches ib
            JOIN products p
                ON ib.product_id = p.product_id
            JOIN locations l
                ON ib.location_id = l.location_id
            ORDER BY l.warehouse_id, ib.received_at
            """
        ).fetchall()

        return [batch_to_frontend(row) for row in rows]

    finally:
        connection.close()


@app.post("/api/inbound")
def inbound(request: InboundRequest):
    if request.quantity <= 0:
        raise HTTPException(status_code=400, detail="進貨數量必須大於 0")

    if not request.item.strip():
        raise HTTPException(status_code=400, detail="商品名稱不可為空")

    if not request.bin.strip():
        raise HTTPException(status_code=400, detail="儲位不可為空")

    if not request.operator.strip():
        raise HTTPException(status_code=400, detail="請填寫操作人")

    connection = get_connection()

    try:
        product = connection.execute(
            """
            SELECT product_id
            FROM products
            WHERE product_name = ?
            """,
            (request.item.strip(),),
        ).fetchone()

        if product is None:
            raise HTTPException(status_code=404, detail="找不到此商品")

        location = connection.execute(
            """
            SELECT location_id
            FROM locations
            WHERE warehouse_id = ?
              AND location_code = ?
            """,
            (int(request.warehouse), request.bin.strip().upper()),
        ).fetchone()

        if location is None:
            raise HTTPException(status_code=404, detail="找不到此倉庫儲位")

        next_id = connection.execute(
            """
            SELECT COALESCE(MAX(batch_id), 0) + 1 AS next_id
            FROM inventory_batches
            """
        ).fetchone()["next_id"]

        received_at = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")

        connection.execute(
            """
            INSERT INTO inventory_batches
                (batch_id, product_id, location_id, quantity, received_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                next_id,
                product["product_id"],
                location["location_id"],
                request.quantity,
                received_at,
            ),
        )
        record_transaction(
    connection=connection,
    batch_id=next_id,
    transaction_type="進貨",
    quantity_change=request.quantity,
    operator=request.operator.strip(),
)

        connection.commit()

        return {
            "success": True,
            "message": "進貨完成",
            "batch": {
                "id": next_id,
                "name": request.item.strip(),
                "warehouse": str(request.warehouse),
                "bin": request.bin.strip().upper(),
                "qty": request.quantity,
                "receivedAt": to_iso(received_at),
            },
            "operator": request.operator.strip(),
        }

    except HTTPException:
        connection.rollback()
        raise
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


@app.get("/api/inventory/fifo")
def get_fifo_suggestion(item: str, quantity: int):
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="出貨數量必須大於 0")

    connection = get_connection()

    try:
        rows = connection.execute(
            """
            SELECT
                ib.batch_id,
                p.product_name,
                l.warehouse_id,
                l.location_code,
                ib.quantity,
                ib.received_at
            FROM inventory_batches ib
            JOIN products p
                ON ib.product_id = p.product_id
            JOIN locations l
                ON ib.location_id = l.location_id
            WHERE p.product_name = ?
              AND ib.quantity > 0
            ORDER BY ib.received_at ASC, ib.batch_id ASC
            """,
            (item,),
        ).fetchall()

        remaining = quantity
        outbound = []

        for row in rows:
            if remaining <= 0:
                break

            take = min(row["quantity"], remaining)

            outbound.append(
                {
                    "id": row["batch_id"],
                    "name": row["product_name"],
                    "warehouse": str(row["warehouse_id"]),
                    "bin": row["location_code"],
                    "quantity": take,
                    "receivedAt": to_iso(row["received_at"]),
                }
            )

            remaining -= take

        if remaining > 0:
            return {
                "success": False,
                "message": "庫存不足，無法完成出貨",
                "outbound": outbound,
            }

        return {
            "success": True,
            "requested_quantity": quantity,
            "outbound": outbound,
        }

    finally:
        connection.close()


@app.post("/api/outbound")
def outbound(request: OutboundRequest):
    if request.quantity <= 0:
        raise HTTPException(status_code=400, detail="出貨數量必須大於 0")

    if not request.operator.strip():
        raise HTTPException(status_code=400, detail="請填寫操作人")

    connection = get_connection()

    try:
        rows = connection.execute(
            """
            SELECT
                ib.batch_id,
                p.product_name,
                l.warehouse_id,
                l.location_code,
                ib.quantity,
                ib.received_at
            FROM inventory_batches ib
            JOIN products p
                ON ib.product_id = p.product_id
            JOIN locations l
                ON ib.location_id = l.location_id
            WHERE p.product_name = ?
              AND ib.quantity > 0
            ORDER BY ib.received_at ASC, ib.batch_id ASC
            """,
            (request.item,),
        ).fetchall()

        total_stock = sum(row["quantity"] for row in rows)

        if total_stock < request.quantity:
            raise HTTPException(
                status_code=400,
                detail="庫存不足，無法完成出貨",
            )

        remaining = request.quantity
        outbound_records = []

        for row in rows:
            if remaining <= 0:
                break

            take = min(row["quantity"], remaining)
            new_quantity = row["quantity"] - take

            connection.execute(
                """
                UPDATE inventory_batches
                SET quantity = ?
                WHERE batch_id = ?
                """,
                (new_quantity, row["batch_id"]),
            )
            record_transaction(
             connection=connection,
            batch_id=row["batch_id"],
            transaction_type="出貨",
             quantity_change=-take,
            operator=request.operator.strip(),
)
            outbound_records.append(
                {
                    "id": row["batch_id"],
                    "name": row["product_name"],
                    "warehouse": str(row["warehouse_id"]),
                    "bin": row["location_code"],
                    "quantity": take,
                    "receivedAt": to_iso(row["received_at"]),
                }
            )

            remaining -= take

        connection.commit()

        return {
            "success": True,
            "message": "出貨完成",
            "operator": request.operator.strip(),
            "outbound": outbound_records,
        }

    except HTTPException:
        connection.rollback()
        raise
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


@app.patch("/api/inventory")
def update_inventory(request: InventoryAdjustmentRequest):
    if request.actual_quantity < 0:
        raise HTTPException(status_code=400, detail="實際數量不可小於 0")

    if not request.operator.strip():
        raise HTTPException(status_code=400, detail="請填寫操作人")

    connection = get_connection()

    try:
        batch = connection.execute(
            """
            SELECT
                ib.batch_id,
                ib.quantity,
                ib.received_at,
                p.product_name,
                l.warehouse_id,
                l.location_code
            FROM inventory_batches ib
            JOIN products p
                ON ib.product_id = p.product_id
            JOIN locations l
                ON ib.location_id = l.location_id
            WHERE ib.batch_id = ?
            """,
            (request.id,),
        ).fetchone()

        if batch is None:
            raise HTTPException(
                status_code=404,
                detail="找不到此庫存批次"
            )

        old_quantity = batch["quantity"]
        difference = request.actual_quantity - old_quantity

        if difference != 0 and not request.reason.strip():
            raise HTTPException(
                status_code=400,
                detail="庫存數量有差異時必須填寫原因",
            )

        connection.execute(
            """
            UPDATE inventory_batches
            SET quantity = ?
            WHERE batch_id = ?
            """,
            (request.actual_quantity, request.id),
        )

        if difference != 0:
            transaction_type = (
                "報廢"
                if request.reason.strip() in ["rotten", "damaged"]
                else "盤點調整"
            )

            record_transaction(
                connection=connection,
                batch_id=request.id,
                transaction_type=transaction_type,
                quantity_change=difference,
                operator=request.operator.strip(),
                reason=request.reason.strip(),
                note=request.note.strip(),
            )

        connection.commit()

        return {
            "success": True,
            "message": "盤點更新完成",
            "id": batch["batch_id"],
            "name": batch["product_name"],
            "warehouse": str(batch["warehouse_id"]),
            "bin": batch["location_code"],
            "oldQty": old_quantity,
            "qty": request.actual_quantity,
            "difference": difference,
            "reason": request.reason,
            "note": request.note,
            "operator": request.operator.strip(),
            "receivedAt": to_iso(batch["received_at"]),
        }

    except HTTPException:
        connection.rollback()
        raise

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()
@app.get("/api/transactions")
def get_transactions():
    connection = get_connection()

    try:
        rows = connection.execute(
            """
            SELECT
                t.transaction_id,
                t.transaction_type,
                t.quantity_change,
                t.operator,
                t.reason,
                t.note,
                t.created_at,
                p.product_name,
                l.warehouse_id,
                l.location_code
            FROM inventory_transactions t
            JOIN inventory_batches ib
                ON t.batch_id = ib.batch_id
            JOIN products p
                ON ib.product_id = p.product_id
            JOIN locations l
                ON ib.location_id = l.location_id
            ORDER BY t.created_at DESC, t.transaction_id DESC
            """
        ).fetchall()

        return [
            {
                "id": row["transaction_id"],
                "at": to_iso(row["created_at"]),
                "kind": row["transaction_type"],
                "name": row["product_name"],
                "warehouse": str(row["warehouse_id"]),
                "bin": row["location_code"],
                "quantity": row["quantity_change"],
                "operator": row["operator"],
                "reason": row["reason"] or "",
                "note": row["note"] or "",
            }
            for row in rows
        ]

    finally:
        connection.close()