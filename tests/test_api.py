import shutil
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.main import app


client = TestClient(app)

BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_PATH = BASE_DIR / "database" / "warehouse.db"
BACKUP_PATH = BASE_DIR / "database" / "warehouse_test_backup.db"


class TestAPI(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # 測試前備份目前 Demo 資料庫
        shutil.copy(DATABASE_PATH, BACKUP_PATH)

    @classmethod
    def tearDownClass(cls):
        # 測試完成後恢復 Demo 資料庫
        shutil.copy(BACKUP_PATH, DATABASE_PATH)
        BACKUP_PATH.unlink()

    def test_01_get_inventory(self):
        response = client.get("/api/inventory")

        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.json(), list)

    def test_02_fifo_suggestion(self):
        # 倉庫 2 的高麗菜目前有 60，避免使用已出貨的倉庫 1
        response = client.get(
            "/api/inventory/fifo",
            params={
                "warehouse_id": 2,
                "product_id": 1,
                "quantity": 20
            }
        )

        self.assertEqual(response.status_code, 200)

        data = response.json()

        self.assertTrue(data["success"])
        self.assertEqual(data["requested_quantity"], 20)
        self.assertEqual(data["outbound"][0]["location"], "B01")
        self.assertEqual(data["outbound"][0]["quantity"], 20)

    def test_03_outbound(self):
        response = client.post(
            "/api/outbound",
            json={
                "warehouse_id": 2,
                "product_id": 1,
                "quantity": 10
            }
        )

        self.assertEqual(response.status_code, 200)

        data = response.json()

        self.assertTrue(data["success"])
        self.assertEqual(data["outbound"][0]["location"], "B01")
        self.assertEqual(data["outbound"][0]["quantity"], 10)

    def test_04_spoilage(self):
        # batch 5 = 倉庫 2、白蘿蔔，原始測試資料為 35
        response = client.patch(
            "/api/inventory/spoilage",
            json={
                "batch_id": 5,
                "spoiled_quantity": 5
            }
        )

        self.assertEqual(response.status_code, 200)

        data = response.json()

        self.assertTrue(data["success"])
        self.assertEqual(data["spoiled_quantity"], 5)
        self.assertEqual(data["remaining_quantity"], 30)


if __name__ == "__main__":
    unittest.main()