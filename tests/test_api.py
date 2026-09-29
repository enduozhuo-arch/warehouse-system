import shutil
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.main import app
from backend.database import get_connection


client = TestClient(app)

BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_PATH = BASE_DIR / "database" / "warehouse.db"
BACKUP_PATH = BASE_DIR / "database" / "warehouse_test_backup.db"


class TestAPI(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # 整組測試開始前，備份目前 Demo 資料庫
        shutil.copy(DATABASE_PATH, BACKUP_PATH)

    def setUp(self):
        # 每個測試開始前恢復相同的資料庫狀態
        shutil.copy(BACKUP_PATH, DATABASE_PATH)

    @classmethod
    def tearDownClass(cls):
        # 全部測試完成後恢復原本 Demo 資料庫
        shutil.copy(BACKUP_PATH, DATABASE_PATH)

        if BACKUP_PATH.exists():
            BACKUP_PATH.unlink()

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
        # 目前 Demo DB：
        # batch 1 高麗菜 = 0
        # batch 4 高麗菜 = 60（2026-09-23）
        # batch 2 高麗菜 = 20（2026-09-25）
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

        # 先從目前最舊且有庫存的 batch 4 出 60
        self.assertEqual(data["outbound"][0]["id"], 4)
        self.assertEqual(data["outbound"][0]["warehouse"], "2")
        self.assertEqual(data["outbound"][0]["bin"], "B01")
        self.assertEqual(data["outbound"][0]["quantity"], 60)

        # 再從 batch 2 出 10
        self.assertEqual(data["outbound"][1]["id"], 2)
        self.assertEqual(data["outbound"][1]["warehouse"], "1")
        self.assertEqual(data["outbound"][1]["bin"], "A02")
        self.assertEqual(data["outbound"][1]["quantity"], 10)

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

        self.assertEqual(data["outbound"][0]["id"], 4)
        self.assertEqual(data["outbound"][0]["quantity"], 60)

        self.assertEqual(data["outbound"][1]["id"], 2)
        self.assertEqual(data["outbound"][1]["quantity"], 10)

        # 確認庫存真的被扣除
        inventory = client.get("/api/inventory").json()

        batch_4 = next(
            batch for batch in inventory
            if batch["id"] == 4
        )

        batch_2 = next(
            batch for batch in inventory
            if batch["id"] == 2
        )

        self.assertEqual(batch_4["qty"], 0)
        self.assertEqual(batch_2["qty"], 10)

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