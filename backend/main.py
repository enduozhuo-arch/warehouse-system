from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend import inbound_tasks, management, pick_tasks, stocktake
from backend.helpers import (
    BATCH_SELECT,
    apply_count,
    batch_to_frontend,
    build_fifo_plan,
    create_batch,
    deduct_batch,
    fetch_batch,
    fetch_fifo_rows,
    get_or_create_location,
    get_or_create_product,
    in_stock_crates,
    make_lot_number,
    next_batch_id,
    now_text,
    parse_date,
    parse_warehouse,
    read_connection,
    record_transaction,
    require_operator,
    to_iso,
    write_connection,
)


app = FastAPI(
    title="竹南冷凍倉儲系統 API",
    description="提供進貨、庫存、FIFO 出貨、掃碼確認與盤點功能",
    version="1.2.0",
)

# 前端（fronted/index.html）與 API 不同來源，需開放跨來源請求
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(pick_tasks.router)
app.include_router(inbound_tasks.router)
app.include_router(stocktake.router)
app.include_router(management.router)


class InboundRequest(BaseModel):
    item: str
    warehouse: str
    bin: str
    quantity: int
    operator: str
    expiryDate: str = ""


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


def fifo_line(row, take):
    line = batch_to_frontend(row)
    del line["qty"]
    line["quantity"] = take

    return line


@app.get("/")
def home():
    return {"message": "Warehouse System API is running"}


@app.get("/api/inventory")
def get_inventory():
    with read_connection() as connection:
        rows = connection.execute(
            BATCH_SELECT + " ORDER BY l.warehouse_id, ib.received_at, ib.batch_id"
        ).fetchall()

        return [
            batch_to_frontend(row, in_stock_crates(connection, row["batch_id"]))
            for row in rows
        ]


@app.post("/api/inbound")
def inbound(request: InboundRequest):
    if request.quantity <= 0:
        raise HTTPException(status_code=400, detail="進貨數量必須大於 0")

    item = request.item.strip()
    bin_code = request.bin.strip().upper()

    if not item:
        raise HTTPException(status_code=400, detail="商品名稱不可為空")

    if not bin_code:
        raise HTTPException(status_code=400, detail="儲位不可為空")

    operator = require_operator(request.operator)
    expiry_date = request.expiryDate.strip()

    if expiry_date:
        parse_date(expiry_date, "效期")

    with write_connection() as connection:
        warehouse_id = parse_warehouse(connection, request.warehouse)
        product_id = get_or_create_product(connection, item)
        location_id, _ = get_or_create_location(connection, warehouse_id, bin_code)

        batch_id = next_batch_id(connection)
        received_at = now_text()

        create_batch(
            connection=connection,
            batch_id=batch_id,
            product_id=product_id,
            location_id=location_id,
            quantity=request.quantity,
            received_at=received_at,
            lot_number=make_lot_number(warehouse_id, batch_id, received_at[:10]),
            expiry_date=expiry_date,
        )

        record_transaction(
            connection=connection,
            batch_id=batch_id,
            transaction_type="進貨",
            quantity_change=request.quantity,
            operator=operator,
        )

        batch = fetch_batch(connection, batch_id)

        return {
            "success": True,
            "message": "進貨完成",
            "batch": batch_to_frontend(
                batch,
                in_stock_crates(connection, batch_id),
            ),
            "operator": operator,
        }


@app.get("/api/inventory/fifo")
def get_fifo_suggestion(item: str, quantity: int):
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="出貨數量必須大於 0")

    with read_connection() as connection:
        plan, remaining = build_fifo_plan(
            fetch_fifo_rows(connection, item),
            quantity,
        )

        outbound = [fifo_line(row, take) for row, take in plan]

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


@app.post("/api/outbound")
def outbound(request: OutboundRequest):
    """
    直接出貨（不經掃碼）。需要逐箱掃碼確認時，請改用 /api/pick-tasks。
    """
    if request.quantity <= 0:
        raise HTTPException(status_code=400, detail="出貨數量必須大於 0")

    operator = require_operator(request.operator)

    with write_connection() as connection:
        plan, remaining = build_fifo_plan(
            fetch_fifo_rows(connection, request.item),
            request.quantity,
        )

        if remaining > 0:
            raise HTTPException(
                status_code=400,
                detail="庫存不足，無法完成出貨",
            )

        outbound_records = []

        for row, take in plan:
            codes = in_stock_crates(connection, row["batch_id"])[:take]
            deduct_batch(connection, row["batch_id"], codes)

            record_transaction(
                connection=connection,
                batch_id=row["batch_id"],
                transaction_type="出貨",
                quantity_change=-take,
                operator=operator,
            )

            outbound_records.append(fifo_line(row, take))

        return {
            "success": True,
            "message": "出貨完成",
            "operator": operator,
            "outbound": outbound_records,
        }


@app.patch("/api/inventory")
def update_inventory(request: InventoryAdjustmentRequest):
    operator = require_operator(request.operator)

    with write_connection() as connection:
        batch = fetch_batch(connection, request.id)

        if batch is None:
            raise HTTPException(
                status_code=404,
                detail="找不到此庫存批次",
            )

        old_quantity, difference, _ = apply_count(
            connection=connection,
            batch=batch,
            actual_quantity=request.actual_quantity,
            reason=request.reason.strip(),
            note=request.note.strip(),
            operator=operator,
        )

        result = batch_to_frontend(batch)
        result.update(
            {
                "success": True,
                "message": "盤點更新完成",
                "oldQty": old_quantity,
                "qty": request.actual_quantity,
                "difference": difference,
                "reason": request.reason,
                "note": request.note,
                "operator": operator,
            }
        )

        return result


@app.get("/api/transactions")
def get_transactions():
    with read_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                t.transaction_id,
                t.transaction_type,
                t.quantity_change,
                t.operator,
                t.operator_role,
                t.reason,
                t.note,
                t.created_at,
                p.product_name,
                l.warehouse_id,
                l.location_code,
                ib.lot_number
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
                "lotNumber": row["lot_number"] or "",
                "quantity": row["quantity_change"],
                "operator": row["operator"],
                "operatorRole": row["operator_role"] or "warehouse",
                "reason": row["reason"] or "",
                "note": row["note"] or "",
            }
            for row in rows
        ]
