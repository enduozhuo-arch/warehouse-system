from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from backend.database import get_connection
class OutboundRequest(BaseModel):
    warehouse_id: int
    product_id: int
    quantity: int

app = FastAPI(
    title="竹南倉儲管理系統 API",
    description="提供庫存、FIFO 出貨與盤點功能",
    version="1.0.0"
)

class SpoilageRequest(BaseModel):
    batch_id: int
    spoiled_quantity: int

@app.get("/")
def home():
    return {
        "message": "Warehouse System API is running"
    }


@app.get("/api/inventory")
def get_inventory():
    connection = get_connection()

    try:
        rows = connection.execute("""
            SELECT
                ib.batch_id,
                w.warehouse_id,
                w.warehouse_name,
                p.product_id,
                p.product_name,
                l.location_code,
                ib.quantity,
                ib.received_at
            FROM inventory_batches ib
            JOIN products p
                ON ib.product_id = p.product_id
            JOIN locations l
                ON ib.location_id = l.location_id
            JOIN warehouses w
                ON l.warehouse_id = w.warehouse_id
            ORDER BY w.warehouse_id, ib.received_at
        """).fetchall()

        return [dict(row) for row in rows]

    finally:
        connection.close()
@app.get("/api/inventory/fifo")
def get_fifo_suggestion(
    warehouse_id: int,
    product_id: int,
    quantity: int
):
    connection = get_connection()

    try:
        rows = connection.execute("""
            SELECT
                ib.batch_id,
                l.location_code,
                ib.quantity,
                ib.received_at
            FROM inventory_batches ib
            JOIN locations l
                ON ib.location_id = l.location_id
            WHERE l.warehouse_id = ?
              AND ib.product_id = ?
              AND ib.quantity > 0
            ORDER BY ib.received_at ASC
        """, (warehouse_id, product_id)).fetchall()

        remaining = quantity
        outbound = []

        for row in rows:
            if remaining <= 0:
                break

            take_quantity = min(row["quantity"], remaining)

            outbound.append({
                "batch_id": row["batch_id"],
                "location": row["location_code"],
                "quantity": take_quantity,
                "received_at": row["received_at"]
            })

            remaining -= take_quantity

        if remaining > 0:
            return {
                "success": False,
                "message": "庫存不足，無法完成出貨"
            }

        return {
            "success": True,
            "requested_quantity": quantity,
            "outbound": outbound
        }

    finally:
        connection.close()
@app.post("/api/outbound")
def outbound(request: OutboundRequest):
    if request.quantity <= 0:
        raise HTTPException(
            status_code=400,
            detail="出貨數量必須大於 0"
        )

    connection = get_connection()

    try:
        rows = connection.execute("""
            SELECT
                ib.batch_id,
                l.location_code,
                ib.quantity,
                ib.received_at
            FROM inventory_batches ib
            JOIN locations l
                ON ib.location_id = l.location_id
            WHERE l.warehouse_id = ?
              AND ib.product_id = ?
              AND ib.quantity > 0
            ORDER BY ib.received_at ASC
        """, (
            request.warehouse_id,
            request.product_id
        )).fetchall()

        total_stock = sum(row["quantity"] for row in rows)

        if total_stock < request.quantity:
            raise HTTPException(
                status_code=400,
                detail="庫存不足，無法完成出貨"
            )

        remaining = request.quantity
        outbound_records = []

        for row in rows:
            if remaining <= 0:
                break

            take_quantity = min(row["quantity"], remaining)
            new_quantity = row["quantity"] - take_quantity

            connection.execute("""
                UPDATE inventory_batches
                SET quantity = ?
                WHERE batch_id = ?
            """, (new_quantity, row["batch_id"]))

            outbound_records.append({
                "batch_id": row["batch_id"],
                "location": row["location_code"],
                "quantity": take_quantity
            })

            remaining -= take_quantity

        connection.commit()

        return {
            "success": True,
            "message": "出貨完成",
            "outbound": outbound_records
        }

    except HTTPException:
        connection.rollback()
        raise

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()
@app.patch("/api/inventory/spoilage")
def update_spoilage_api(request: SpoilageRequest):
    if request.spoiled_quantity <= 0:
        raise HTTPException(
            status_code=400,
            detail="腐爛數量必須大於 0"
        )

    connection = get_connection()

    try:
        batch = connection.execute("""
            SELECT
                ib.batch_id,
                ib.quantity,
                l.location_code,
                w.warehouse_id,
                p.product_name
            FROM inventory_batches ib
            JOIN locations l
                ON ib.location_id = l.location_id
            JOIN warehouses w
                ON l.warehouse_id = w.warehouse_id
            JOIN products p
                ON ib.product_id = p.product_id
            WHERE ib.batch_id = ?
        """, (request.batch_id,)).fetchone()

        if batch is None:
            raise HTTPException(
                status_code=404,
                detail="找不到指定批次"
            )

        if request.spoiled_quantity > batch["quantity"]:
            raise HTTPException(
                status_code=400,
                detail="腐爛數量不能大於目前庫存"
            )

        new_quantity = (
            batch["quantity"] - request.spoiled_quantity
        )

        connection.execute("""
            UPDATE inventory_batches
            SET quantity = ?
            WHERE batch_id = ?
        """, (new_quantity, request.batch_id))

        connection.commit()

        return {
            "success": True,
            "message": "盤點更新完成",
            "batch_id": batch["batch_id"],
            "warehouse_id": batch["warehouse_id"],
            "product_name": batch["product_name"],
            "location": batch["location_code"],
            "spoiled_quantity": request.spoiled_quantity,
            "remaining_quantity": new_quantity
        }

    except HTTPException:
        connection.rollback()
        raise

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()