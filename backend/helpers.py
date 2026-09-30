import re
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException

from backend.database import get_connection


TAIPEI_TZ = timezone(timedelta(hours=8))

# 前端第三版使用中文原因，舊版 API 使用英文代碼，兩種都視為報廢
WASTE_REASONS = {"rotten", "damaged", "腐爛報廢", "破損報廢"}

BATCH_SELECT = """
    SELECT
        ib.batch_id,
        ib.product_id,
        p.product_name,
        l.warehouse_id,
        l.location_code,
        ib.quantity,
        ib.received_at,
        ib.lot_number,
        ib.expiry_date
    FROM inventory_batches ib
    JOIN products p
        ON ib.product_id = p.product_id
    JOIN locations l
        ON ib.location_id = l.location_id
"""


def now_text():
    return datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")


def today_text():
    return datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")


def to_iso(received_at):
    if not received_at:
        return received_at

    # SQLite 原始格式：2026-09-20 08:00:00
    if "T" not in received_at:
        received_at = received_at.replace(" ", "T")

    if not received_at.endswith("+08:00"):
        received_at += "+08:00"

    return received_at


def parse_date(value, field_name):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail=f"{field_name}格式必須為 YYYY-MM-DD",
        )


def location_code(warehouse, bin_code):
    # 儲位 QR Code 內容，與前端 locationCode() 相同：LOC-1-B02
    cleaned = re.sub(r"[^A-Z0-9]", "", str(bin_code).upper())
    return f"LOC-{warehouse}-{cleaned}"


def crate_code(batch_id, seq):
    # 箱標籤內容，與前端 crateCodesFor() 相同：2-BOX-001
    return f"{batch_id}-BOX-{seq:03d}"


def make_lot_number(warehouse, batch_id, received_date):
    return f"ZN-{warehouse}-{received_date.replace('-', '')}-{batch_id:06d}"


@contextmanager
def read_connection():
    connection = get_connection()

    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def write_connection():
    # BEGIN IMMEDIATE：先取得寫入鎖，檢查庫存與扣除庫存在同一筆交易內完成
    connection = get_connection()

    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def in_stock_crates(connection, batch_id):
    rows = connection.execute(
        """
        SELECT crate_code
        FROM crates
        WHERE batch_id = ?
          AND status = 'in_stock'
        ORDER BY seq
        """,
        (batch_id,),
    ).fetchall()

    return [row["crate_code"] for row in rows]


def batch_to_frontend(row, crate_codes=None):
    batch = {
        "id": row["batch_id"],
        "name": row["product_name"],
        "warehouse": str(row["warehouse_id"]),
        "bin": row["location_code"],
        "qty": row["quantity"],
        "receivedAt": to_iso(row["received_at"]),
        "lotNumber": row["lot_number"] or "",
        "expiryDate": row["expiry_date"] or "",
        "locationCode": location_code(row["warehouse_id"], row["location_code"]),
    }

    if crate_codes is not None:
        batch["crateCodes"] = crate_codes

    return batch


def fetch_batch(connection, batch_id):
    return connection.execute(
        BATCH_SELECT + " WHERE ib.batch_id = ?",
        (batch_id,),
    ).fetchone()


def fetch_fifo_rows(connection, item):
    return connection.execute(
        BATCH_SELECT
        + """
        WHERE p.product_name = ?
          AND ib.quantity > 0
        ORDER BY ib.received_at ASC, ib.batch_id ASC
        """,
        (item,),
    ).fetchall()


def build_fifo_plan(rows, quantity):
    remaining = quantity
    plan = []

    for row in rows:
        if remaining <= 0:
            break

        take = min(row["quantity"], remaining)
        plan.append((row, take))
        remaining -= take

    return plan, remaining


def parse_warehouse(connection, warehouse):
    try:
        warehouse_id = int(warehouse)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="倉庫編號格式錯誤")

    row = connection.execute(
        "SELECT warehouse_id FROM warehouses WHERE warehouse_id = ?",
        (warehouse_id,),
    ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="找不到此倉庫")

    return warehouse_id


def get_or_create_product(connection, name):
    # 支援前端「自行輸入商品」：商品不存在時自動建立
    row = connection.execute(
        "SELECT product_id FROM products WHERE product_name = ?",
        (name,),
    ).fetchone()

    if row is not None:
        return row["product_id"]

    next_id = connection.execute(
        "SELECT COALESCE(MAX(product_id), 0) + 1 AS next_id FROM products"
    ).fetchone()["next_id"]

    connection.execute(
        "INSERT INTO products (product_id, product_name) VALUES (?, ?)",
        (next_id, name),
    )

    return next_id


def find_location(connection, warehouse_id, bin_code):
    # A-01 與 A01 視為同一個儲位（儲位 QR Code 相同）
    target = location_code(warehouse_id, bin_code)

    rows = connection.execute(
        """
        SELECT location_id, location_code
        FROM locations
        WHERE warehouse_id = ?
        """,
        (warehouse_id,),
    ).fetchall()

    for row in rows:
        if location_code(warehouse_id, row["location_code"]) == target:
            return row

    return None


def get_or_create_location(connection, warehouse_id, bin_code):
    row = find_location(connection, warehouse_id, bin_code)

    if row is not None:
        return row["location_id"], row["location_code"]

    next_id = connection.execute(
        "SELECT COALESCE(MAX(location_id), 0) + 1 AS next_id FROM locations"
    ).fetchone()["next_id"]

    connection.execute(
        """
        INSERT INTO locations (location_id, warehouse_id, location_code)
        VALUES (?, ?, ?)
        """,
        (next_id, warehouse_id, bin_code),
    )

    return next_id, bin_code


def next_batch_id(connection):
    # 進行中的收貨工作已預留批次編號，需一併避開
    return connection.execute(
        """
        SELECT MAX(
            (SELECT COALESCE(MAX(batch_id), 0) FROM inventory_batches),
            (SELECT COALESCE(MAX(batch_id), 0) FROM inbound_tasks)
        ) + 1 AS next_id
        """
    ).fetchone()["next_id"]


def create_batch(
    connection,
    batch_id,
    product_id,
    location_id,
    quantity,
    received_at,
    lot_number,
    expiry_date,
):
    connection.execute(
        """
        INSERT INTO inventory_batches
            (
                batch_id,
                product_id,
                location_id,
                quantity,
                received_at,
                lot_number,
                expiry_date
            )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            batch_id,
            product_id,
            location_id,
            quantity,
            received_at,
            lot_number,
            expiry_date or None,
        ),
    )

    connection.executemany(
        """
        INSERT INTO crates (crate_code, batch_id, seq, status)
        VALUES (?, ?, ?, 'in_stock')
        """,
        [
            (crate_code(batch_id, seq), batch_id, seq)
            for seq in range(1, quantity + 1)
        ],
    )


def take_crates(connection, batch_id, codes, status):
    connection.executemany(
        "UPDATE crates SET status = ? WHERE crate_code = ? AND batch_id = ?",
        [(status, code, batch_id) for code in codes],
    )


def set_batch_quantity(connection, batch_id, new_quantity):
    """盤點調整數量，並讓在庫箱號數量與庫存數量保持一致。"""
    codes = in_stock_crates(connection, batch_id)

    if new_quantity < len(codes):
        # 與前端相同：保留前面的箱號，扣除後面的
        take_crates(connection, batch_id, codes[new_quantity:], "removed")

    elif new_quantity > len(codes):
        last = connection.execute(
            """
            SELECT crate_code, seq
            FROM crates
            WHERE batch_id = ?
            ORDER BY seq DESC
            LIMIT 1
            """,
            (batch_id,),
        ).fetchone()

        # 新箱號沿用這個批次既有箱號的前綴（初始資料為 seed-02-BOX-）
        prefix = (
            last["crate_code"].rsplit("-", 1)[0]
            if last
            else crate_code(batch_id, 0).rsplit("-", 1)[0]
        )
        last_seq = last["seq"] if last else 0

        connection.executemany(
            """
            INSERT INTO crates (crate_code, batch_id, seq, status)
            VALUES (?, ?, ?, 'in_stock')
            """,
            [
                (f"{prefix}-{seq:03d}", batch_id, seq)
                for seq in range(last_seq + 1, last_seq + 1 + new_quantity - len(codes))
            ],
        )

    connection.execute(
        "UPDATE inventory_batches SET quantity = ? WHERE batch_id = ?",
        (new_quantity, batch_id),
    )


def deduct_batch(connection, batch_id, codes):
    """出貨：扣除指定箱號並減少庫存數量。"""
    take_crates(connection, batch_id, codes, "out")

    connection.execute(
        "UPDATE inventory_batches SET quantity = quantity - ? WHERE batch_id = ?",
        (len(codes), batch_id),
    )


def record_transaction(
    connection,
    batch_id,
    transaction_type,
    quantity_change,
    operator,
    reason="",
    note="",
):
    user = connection.execute(
        "SELECT role FROM users WHERE name = ?",
        (operator,),
    ).fetchone()

    connection.execute(
        """
        INSERT INTO inventory_transactions
            (
                batch_id,
                transaction_type,
                quantity_change,
                operator,
                operator_role,
                reason,
                note,
                created_at
            )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            batch_id,
            transaction_type,
            quantity_change,
            operator,
            user["role"] if user else "warehouse",
            reason,
            note,
            now_text(),
        ),
    )


def require_operator(operator):
    operator = operator.strip()

    if not operator:
        raise HTTPException(status_code=400, detail="請填寫操作人")

    return operator


def apply_count(connection, batch, actual_quantity, reason, note, operator,
                record_confirmation=False):
    """
    盤點：把批次數量改為實際數量並寫入異動紀錄。

    record_confirmation：數量沒有差異但有填原因時，是否也留下紀錄
    （前端第三版每日盤點的行為）。
    """
    if actual_quantity < 0:
        raise HTTPException(status_code=400, detail="實際數量不可小於 0")

    old_quantity = batch["quantity"]
    difference = actual_quantity - old_quantity

    if difference != 0 and not reason:
        raise HTTPException(
            status_code=400,
            detail="庫存數量有差異時必須填寫原因",
        )

    kind = "報廢" if difference < 0 and reason in WASTE_REASONS else "盤點調整"

    if difference != 0:
        set_batch_quantity(connection, batch["batch_id"], actual_quantity)

    if difference != 0 or (record_confirmation and reason):
        record_transaction(
            connection=connection,
            batch_id=batch["batch_id"],
            transaction_type=kind,
            quantity_change=difference,
            operator=operator,
            reason=reason,
            note=note,
        )

    return old_quantity, difference, kind
