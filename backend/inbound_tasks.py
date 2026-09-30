"""
收貨工作（入庫掃碼確認）

流程：建立收貨單 → 核對儲位 → 逐箱掃描 → 確認入庫。
庫存只在「確認入庫」時入帳，入庫時間由伺服器記錄，作為 FIFO 依據。
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.helpers import (
    batch_to_frontend,
    crate_code,
    create_batch,
    fetch_batch,
    find_location,
    get_or_create_location,
    get_or_create_product,
    in_stock_crates,
    location_code,
    make_lot_number,
    next_batch_id,
    now_text,
    parse_date,
    parse_warehouse,
    read_connection,
    record_transaction,
    require_operator,
    to_iso,
    today_text,
    write_connection,
)


router = APIRouter(prefix="/api/inbound-tasks", tags=["收貨工作"])


class InboundTaskRequest(BaseModel):
    item: str
    warehouse: str
    bin: str
    quantity: int
    operator: str
    expiryDate: str = ""


class LocationScanRequest(BaseModel):
    locationCode: str


class CrateScanRequest(BaseModel):
    crateCode: str


def load_task(connection, task_id):
    task = connection.execute(
        "SELECT * FROM inbound_tasks WHERE task_id = ?",
        (task_id,),
    ).fetchone()

    if task is None:
        raise HTTPException(status_code=404, detail="找不到此收貨工作")

    return task


def load_open_task(connection, task_id):
    task = load_task(connection, task_id)

    if task["status"] != "open":
        raise HTTPException(status_code=409, detail="此收貨工作已結束")

    return task


def task_crate_codes(task):
    return [
        crate_code(task["batch_id"], seq)
        for seq in range(1, task["quantity"] + 1)
    ]


def task_payload(connection, task_id):
    task = load_task(connection, task_id)

    scanned = [
        row["crate_code"]
        for row in connection.execute(
            """
            SELECT crate_code
            FROM inbound_task_scans
            WHERE task_id = ?
            ORDER BY rowid
            """,
            (task_id,),
        ).fetchall()
    ]

    if task["status"] == "confirmed":
        batch = batch_to_frontend(
            fetch_batch(connection, task["batch_id"]),
            in_stock_crates(connection, task["batch_id"]),
        )
    else:
        # 尚未入庫：receivedAt 留空，確認入庫時才由伺服器記錄
        batch = {
            "id": task["batch_id"],
            "name": task["product_name"],
            "warehouse": str(task["warehouse_id"]),
            "bin": task["location_code"],
            "qty": task["quantity"],
            "receivedAt": "",
            "lotNumber": task["lot_number"],
            "expiryDate": task["expiry_date"] or "",
            "locationCode": location_code(
                task["warehouse_id"],
                task["location_code"],
            ),
            "crateCodes": task_crate_codes(task),
        }

    return {
        "id": task["task_id"],
        "operator": task["operator"],
        "status": task["status"],
        "createdAt": to_iso(task["created_at"]),
        "closedAt": to_iso(task["closed_at"]),
        "batch": batch,
        "locationCode": batch["locationCode"],
        "locationVerified": bool(task["location_verified"]),
        "scannedCodes": scanned,
        "ready": (
            task["status"] == "open"
            and bool(task["location_verified"])
            and len(scanned) == task["quantity"]
        ),
    }


@router.post("")
def create_inbound_task(request: InboundTaskRequest):
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

        # 已存在的儲位沿用原本的寫法（A-01 與 A01 視為同一個儲位）
        existing = find_location(connection, warehouse_id, bin_code)

        if existing is not None:
            bin_code = existing["location_code"]

        batch_id = next_batch_id(connection)

        task_id = connection.execute(
            """
            INSERT INTO inbound_tasks
                (
                    batch_id,
                    product_name,
                    warehouse_id,
                    location_code,
                    quantity,
                    lot_number,
                    expiry_date,
                    operator,
                    status,
                    created_at
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
            """,
            (
                batch_id,
                item,
                warehouse_id,
                bin_code,
                request.quantity,
                make_lot_number(warehouse_id, batch_id, today_text()),
                expiry_date or None,
                operator,
                now_text(),
            ),
        ).lastrowid

        return task_payload(connection, task_id)


@router.get("")
def list_inbound_tasks(status: str = "open"):
    with read_connection() as connection:
        if status == "all":
            rows = connection.execute(
                "SELECT task_id FROM inbound_tasks ORDER BY task_id DESC"
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT task_id
                FROM inbound_tasks
                WHERE status = ?
                ORDER BY task_id DESC
                """,
                (status,),
            ).fetchall()

        return [task_payload(connection, row["task_id"]) for row in rows]


@router.get("/{task_id}")
def get_inbound_task(task_id: int):
    with read_connection() as connection:
        return task_payload(connection, task_id)


@router.post("/{task_id}/verify-location")
def verify_location(task_id: int, request: LocationScanRequest):
    with write_connection() as connection:
        task = load_open_task(connection, task_id)
        expected = location_code(task["warehouse_id"], task["location_code"])

        if request.locationCode.strip().upper() != expected:
            raise HTTPException(
                status_code=400,
                detail=f"儲位不符，請掃描 {expected}",
            )

        connection.execute(
            "UPDATE inbound_tasks SET location_verified = 1 WHERE task_id = ?",
            (task_id,),
        )

        return task_payload(connection, task_id)


@router.post("/{task_id}/scan")
def scan_crate(task_id: int, request: CrateScanRequest):
    code = request.crateCode.strip()

    with write_connection() as connection:
        task = load_open_task(connection, task_id)

        if not task["location_verified"]:
            raise HTTPException(status_code=409, detail="請先核對儲位")

        if code not in task_crate_codes(task):
            raise HTTPException(
                status_code=400,
                detail="箱標籤不屬於這張收貨單，請核對商品及箱號",
            )

        already_scanned = connection.execute(
            """
            SELECT 1
            FROM inbound_task_scans
            WHERE task_id = ?
              AND crate_code = ?
            """,
            (task_id, code),
        ).fetchone()

        if already_scanned:
            raise HTTPException(
                status_code=409,
                detail="這箱已經登記過，請勿重複掃描",
            )

        connection.execute(
            "INSERT INTO inbound_task_scans (task_id, crate_code) VALUES (?, ?)",
            (task_id, code),
        )

        return task_payload(connection, task_id)


@router.post("/{task_id}/confirm")
def confirm_inbound_task(task_id: int):
    with write_connection() as connection:
        task = load_open_task(connection, task_id)

        scanned = connection.execute(
            "SELECT COUNT(*) AS total FROM inbound_task_scans WHERE task_id = ?",
            (task_id,),
        ).fetchone()["total"]

        if not task["location_verified"] or scanned != task["quantity"]:
            raise HTTPException(
                status_code=400,
                detail="請先完成儲位核對及逐箱掃描",
            )

        product_id = get_or_create_product(connection, task["product_name"])
        location_id, _ = get_or_create_location(
            connection,
            task["warehouse_id"],
            task["location_code"],
        )

        create_batch(
            connection=connection,
            batch_id=task["batch_id"],
            product_id=product_id,
            location_id=location_id,
            quantity=task["quantity"],
            received_at=now_text(),
            lot_number=task["lot_number"],
            expiry_date=task["expiry_date"],
        )

        record_transaction(
            connection=connection,
            batch_id=task["batch_id"],
            transaction_type="進貨",
            quantity_change=task["quantity"],
            operator=task["operator"],
            reason="收貨掃描完成",
        )

        connection.execute(
            """
            UPDATE inbound_tasks
            SET status = 'confirmed',
                closed_at = ?
            WHERE task_id = ?
            """,
            (now_text(), task_id),
        )

        return {
            "success": True,
            "message": f"{task['product_name']} {task['quantity']} 箱已完成入庫",
            "task": task_payload(connection, task_id),
        }


@router.post("/{task_id}/cancel")
def cancel_inbound_task(task_id: int):
    with write_connection() as connection:
        load_open_task(connection, task_id)

        connection.execute(
            """
            UPDATE inbound_tasks
            SET status = 'cancelled',
                closed_at = ?
            WHERE task_id = ?
            """,
            (now_text(), task_id),
        )

        return {
            "success": True,
            "message": "已取消收貨工作，庫存未變更",
            "task": task_payload(connection, task_id),
        }
