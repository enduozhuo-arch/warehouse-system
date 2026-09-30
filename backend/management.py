"""
管理功能：商品清單、人員名單、安全庫存、久放提醒、效期提醒、報廢統計

人員名單只用來標示異動紀錄的操作人與角色，不是登入或權限控管。
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.helpers import (
    BATCH_SELECT,
    batch_to_frontend,
    parse_date,
    parse_warehouse,
    read_connection,
    today_text,
    write_connection,
)


router = APIRouter(prefix="/api", tags=["管理"])

ROLES = {"manager", "warehouse"}


class UserRequest(BaseModel):
    name: str
    role: str = "warehouse"


class SafetyLevelRequest(BaseModel):
    warehouse: str
    name: str
    quantity: int


class AgingRuleRequest(BaseModel):
    name: str
    days: int


def expiry_status(days_left):
    # 與前端 expiryStatus() 的文字相同
    if days_left < 0:
        return "已過期，請確認"

    if days_left == 0:
        return "今日到期"

    if days_left <= 3:
        return f"{days_left} 天內到期"

    return "正常"


@router.get("/products")
def get_products():
    with read_connection() as connection:
        rows = connection.execute(
            "SELECT product_name FROM products ORDER BY product_name"
        ).fetchall()

        return [row["product_name"] for row in rows]


@router.get("/users")
def get_users():
    with read_connection() as connection:
        rows = connection.execute(
            "SELECT name, role FROM users ORDER BY rowid"
        ).fetchall()

        return [{"name": row["name"], "role": row["role"]} for row in rows]


@router.post("/users")
def add_user(request: UserRequest):
    name = request.name.strip()

    if not name:
        raise HTTPException(status_code=400, detail="請輸入人員姓名")

    if request.role not in ROLES:
        raise HTTPException(
            status_code=400,
            detail="角色必須是 manager 或 warehouse",
        )

    with write_connection() as connection:
        existing = connection.execute(
            "SELECT 1 FROM users WHERE LOWER(name) = LOWER(?)",
            (name,),
        ).fetchone()

        if existing:
            raise HTTPException(status_code=409, detail="此人員姓名已存在")

        connection.execute(
            "INSERT INTO users (name, role) VALUES (?, ?)",
            (name, request.role),
        )

        return {"success": True, "name": name, "role": request.role}


@router.get("/safety-levels")
def get_safety_levels():
    with read_connection() as connection:
        rows = connection.execute(
            """
            SELECT warehouse_id, product_name, quantity
            FROM safety_levels
            ORDER BY warehouse_id, product_name
            """
        ).fetchall()

        return [
            {
                "warehouse": str(row["warehouse_id"]),
                "name": row["product_name"],
                "quantity": row["quantity"],
            }
            for row in rows
        ]


@router.put("/safety-levels")
def set_safety_level(request: SafetyLevelRequest):
    name = request.name.strip()

    if not name or request.quantity < 0:
        raise HTTPException(
            status_code=400,
            detail="請輸入商品及有效安全庫存量",
        )

    with write_connection() as connection:
        warehouse_id = parse_warehouse(connection, request.warehouse)

        connection.execute(
            """
            INSERT INTO safety_levels (warehouse_id, product_name, quantity)
            VALUES (?, ?, ?)
            ON CONFLICT (warehouse_id, product_name) DO UPDATE SET
                quantity = excluded.quantity
            """,
            (warehouse_id, name, request.quantity),
        )

        return {
            "success": True,
            "warehouse": str(warehouse_id),
            "name": name,
            "quantity": request.quantity,
        }


@router.get("/aging-rules")
def get_aging_rules():
    with read_connection() as connection:
        rows = connection.execute(
            "SELECT product_name, days FROM aging_rules ORDER BY product_name"
        ).fetchall()

        return [
            {"name": row["product_name"], "days": row["days"]}
            for row in rows
        ]


@router.put("/aging-rules")
def set_aging_rule(request: AgingRuleRequest):
    name = request.name.strip()

    if not name or request.days < 1:
        raise HTTPException(
            status_code=400,
            detail="請輸入商品和大於 0 的提醒天數",
        )

    with write_connection() as connection:
        connection.execute(
            """
            INSERT INTO aging_rules (product_name, days)
            VALUES (?, ?)
            ON CONFLICT (product_name) DO UPDATE SET
                days = excluded.days
            """,
            (name, request.days),
        )

        return {"success": True, "name": name, "days": request.days}


@router.get("/alerts")
def get_alerts(today: str = ""):
    """效期、久放、安全庫存提醒。today 可指定基準日（預設為今天）。"""
    base_date = parse_date(today or today_text(), "基準日")

    with read_connection() as connection:
        batches = connection.execute(
            BATCH_SELECT
            + """
            WHERE ib.quantity > 0
            ORDER BY ib.received_at, ib.batch_id
            """
        ).fetchall()

        aging_rules = {
            row["product_name"]: row["days"]
            for row in connection.execute(
                "SELECT product_name, days FROM aging_rules"
            ).fetchall()
        }

        safety_levels = connection.execute(
            """
            SELECT warehouse_id, product_name, quantity
            FROM safety_levels
            ORDER BY warehouse_id, product_name
            """
        ).fetchall()

    expiry = []
    aging = []

    for row in batches:
        batch = batch_to_frontend(row)

        if row["expiry_date"]:
            days_left = (parse_date(row["expiry_date"], "效期") - base_date).days
            status = expiry_status(days_left)

            if status != "正常":
                expiry.append({**batch, "daysLeft": days_left, "status": status})

        threshold = aging_rules.get(row["product_name"])

        if threshold:
            elapsed = max(
                0,
                (base_date - parse_date(row["received_at"][:10], "入庫時間")).days,
            )

            if elapsed >= threshold:
                aging.append({**batch, "days": elapsed, "threshold": threshold})

    expiry.sort(key=lambda item: item["expiryDate"])

    safety = []

    for level in safety_levels:
        quantity = sum(
            row["quantity"]
            for row in batches
            if row["warehouse_id"] == level["warehouse_id"]
            and row["product_name"] == level["product_name"]
        )

        if quantity < level["quantity"]:
            safety.append(
                {
                    "warehouse": str(level["warehouse_id"]),
                    "name": level["product_name"],
                    "quantity": quantity,
                    "level": level["quantity"],
                }
            )

    return {
        "today": base_date.isoformat(),
        "expiry": expiry,
        "aging": aging,
        "safety": safety,
    }


@router.get("/waste-summary")
def get_waste_summary():
    with read_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                COALESCE(NULLIF(reason, ''), '未填原因') AS reason,
                SUM(ABS(quantity_change)) AS quantity
            FROM inventory_transactions
            WHERE transaction_type = '報廢'
            GROUP BY 1
            ORDER BY quantity DESC, reason
            """
        ).fetchall()

        return [
            {"reason": row["reason"], "quantity": row["quantity"]}
            for row in rows
        ]
