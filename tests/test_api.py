import shutil
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.main import app
from backend.database import get_connection, initialize_database


client = TestClient(app)

BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_PATH = BASE_DIR / "database" / "warehouse.db"
BACKUP_PATH = BASE_DIR / "database" / "warehouse_test_backup.db"


class TestAPI(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # 整組測試開始前，備份目前 Demo 資料庫（若存在）
        if DATABASE_PATH.exists():
            shutil.copy(DATABASE_PATH, BACKUP_PATH)

    def setUp(self):
        # 每個測試開始前都由 schema.sql 重建資料庫，
        # 測試結果不受本機 Demo 資料庫目前內容影響
        if DATABASE_PATH.exists():
            DATABASE_PATH.unlink()

        initialize_database()

    @classmethod
    def tearDownClass(cls):
        # 全部測試完成後恢復原本 Demo 資料庫；
        # 原本沒有資料庫時，留下 schema.sql 的初始資料
        if BACKUP_PATH.exists():
            shutil.copy(BACKUP_PATH, DATABASE_PATH)
            BACKUP_PATH.unlink()
        else:
            DATABASE_PATH.unlink()
            initialize_database()

    def test_01_get_inventory(self):
        response = client.get("/api/inventory")

        self.assertEqual(response.status_code, 200)

        data = response.json()

        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)

        first = data[0]

        for key in (
            "id",
            "name",
            "warehouse",
            "bin",
            "qty",
            "receivedAt",
        ):
            self.assertIn(key, first)

        self.assertIsInstance(first["warehouse"], str)

    def test_02_fifo_suggestion(self):
        # schema.sql 初始資料：
        # batch 1 高麗菜 = 50（2026-09-20，倉庫 1 A01）
        # batch 4 高麗菜 = 60（2026-09-23，倉庫 2 B01）
        # batch 2 高麗菜 = 30（2026-09-25，倉庫 1 A02）
        response = client.get(
            "/api/inventory/fifo",
            params={
                "item": "高麗菜",
                "quantity": 70,
            },
        )

        self.assertEqual(response.status_code, 200)

        data = response.json()

        self.assertTrue(data["success"])
        self.assertEqual(data["requested_quantity"], 70)

        # 先從最舊的 batch 1 出 50
        self.assertEqual(data["outbound"][0]["id"], 1)
        self.assertEqual(data["outbound"][0]["warehouse"], "1")
        self.assertEqual(data["outbound"][0]["bin"], "A01")
        self.assertEqual(data["outbound"][0]["quantity"], 50)

        # 再跨倉庫從 batch 4 出 20
        self.assertEqual(data["outbound"][1]["id"], 4)
        self.assertEqual(data["outbound"][1]["warehouse"], "2")
        self.assertEqual(data["outbound"][1]["bin"], "B01")
        self.assertEqual(data["outbound"][1]["quantity"], 20)
        self.assertEqual(len(data["outbound"]), 2)

    def test_03_outbound(self):
        response = client.post(
            "/api/outbound",
            json={
                "item": "高麗菜",
                "quantity": 70,
                "operator": "測試人員",
            },
        )

        self.assertEqual(response.status_code, 200)

        data = response.json()

        self.assertTrue(data["success"])
        self.assertEqual(data["operator"], "測試人員")

        self.assertEqual(data["outbound"][0]["id"], 1)
        self.assertEqual(data["outbound"][0]["quantity"], 50)

        self.assertEqual(data["outbound"][1]["id"], 4)
        self.assertEqual(data["outbound"][1]["quantity"], 20)

        # 確認庫存真的被扣除
        inventory = client.get("/api/inventory").json()

        qty_by_id = {batch["id"]: batch["qty"] for batch in inventory}

        self.assertEqual(qty_by_id[1], 0)
        self.assertEqual(qty_by_id[4], 40)
        # 較新的 batch 2 不應被動到
        self.assertEqual(qty_by_id[2], 30)

    def test_04_inventory_adjustment(self):
        # batch 5 白蘿蔔原本 35，盤點後實際剩 30
        response = client.patch(
            "/api/inventory",
            json={
                "id": 5,
                "actual_quantity": 30,
                "reason": "rotten",
                "note": "盤點發現腐敗",
                "operator": "測試人員",
            },
        )

        self.assertEqual(response.status_code, 200)

        data = response.json()

        self.assertTrue(data["success"])
        self.assertEqual(data["oldQty"], 35)
        self.assertEqual(data["qty"], 30)
        self.assertEqual(data["difference"], -5)
        self.assertEqual(data["id"], 5)
        self.assertEqual(data["warehouse"], "2")
        self.assertEqual(data["name"], "白蘿蔔")
        self.assertEqual(data["bin"], "B02")
        self.assertEqual(data["reason"], "rotten")
        self.assertEqual(data["operator"], "測試人員")

    def test_05_inbound(self):
        response = client.post(
            "/api/inbound",
            json={
                "item": "番茄",
                "warehouse": "1",
                "bin": "A03",
                "quantity": 12,
                "operator": "測試人員",
            },
        )

        self.assertEqual(response.status_code, 200)

        data = response.json()

        self.assertTrue(data["success"])

        self.assertEqual(
            data["batch"]["name"],
            "番茄",
        )

        self.assertEqual(
            data["batch"]["warehouse"],
            "1",
        )

        self.assertEqual(
            data["batch"]["bin"],
            "A03",
        )

        self.assertEqual(
            data["batch"]["qty"],
            12,
        )

        self.assertEqual(
            data["operator"],
            "測試人員",
        )

    def test_06_transaction_record(self):
        # 先製造一筆進貨紀錄
        response = client.post(
            "/api/inbound",
            json={
                "item": "番茄",
                "warehouse": "1",
                "bin": "A03",
                "quantity": 5,
                "operator": "測試人員",
            },
        )

        self.assertEqual(response.status_code, 200)

        connection = get_connection()

        try:
            row = connection.execute(
                """
                SELECT
                    transaction_type,
                    quantity_change,
                    operator
                FROM inventory_transactions
                ORDER BY transaction_id DESC
                LIMIT 1
                """
            ).fetchone()

            self.assertIsNotNone(row)

            self.assertEqual(
                row["transaction_type"],
                "進貨",
            )

            self.assertEqual(
                row["quantity_change"],
                5,
            )

            self.assertEqual(
                row["operator"],
                "測試人員",
            )

        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()