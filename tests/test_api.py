from backend.database import get_connection
from tests.base import DatabaseTestCase, client, seed_received_at


class TestAPI(DatabaseTestCase):

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
        # schema.sql 初始資料的青江菜：
        # batch 2 = 3（9 天前入庫，倉庫 1 B-02）
        # batch 3 = 10（3 天前入庫，倉庫 2 C-05）
        response = client.get(
            "/api/inventory/fifo",
            params={
                "item": "青江菜",
                "quantity": 5,
            },
        )

        self.assertEqual(response.status_code, 200)

        data = response.json()

        self.assertTrue(data["success"])
        self.assertEqual(data["requested_quantity"], 5)

        # 先從最舊的 batch 2 出 3
        self.assertEqual(data["outbound"][0]["id"], 2)
        self.assertEqual(data["outbound"][0]["warehouse"], "1")
        self.assertEqual(data["outbound"][0]["bin"], "B-02")
        self.assertEqual(data["outbound"][0]["quantity"], 3)
        self.assertEqual(data["outbound"][0]["receivedAt"], seed_received_at(9))

        # 再跨倉庫從 batch 3 出 2
        self.assertEqual(data["outbound"][1]["id"], 3)
        self.assertEqual(data["outbound"][1]["warehouse"], "2")
        self.assertEqual(data["outbound"][1]["bin"], "C-05")
        self.assertEqual(data["outbound"][1]["quantity"], 2)
        self.assertEqual(len(data["outbound"]), 2)

    def test_03_outbound(self):
        response = client.post(
            "/api/outbound",
            json={
                "item": "青江菜",
                "quantity": 5,
                "operator": "測試人員",
            },
        )

        self.assertEqual(response.status_code, 200)

        data = response.json()

        self.assertTrue(data["success"])
        self.assertEqual(data["operator"], "測試人員")

        self.assertEqual(data["outbound"][0]["id"], 2)
        self.assertEqual(data["outbound"][0]["quantity"], 3)

        self.assertEqual(data["outbound"][1]["id"], 3)
        self.assertEqual(data["outbound"][1]["quantity"], 2)

        # 確認庫存真的被扣除
        inventory = client.get("/api/inventory").json()

        qty_by_id = {batch["id"]: batch["qty"] for batch in inventory}

        self.assertEqual(qty_by_id[2], 0)
        self.assertEqual(qty_by_id[3], 8)
        # 其他商品不應被動到
        self.assertEqual(qty_by_id[1], 5)

    def test_04_inventory_adjustment(self):
        # batch 5 番茄原本 6，盤點後實際剩 4
        response = client.patch(
            "/api/inventory",
            json={
                "id": 5,
                "actual_quantity": 4,
                "reason": "rotten",
                "note": "盤點發現腐敗",
                "operator": "測試人員",
            },
        )

        self.assertEqual(response.status_code, 200)

        data = response.json()

        self.assertTrue(data["success"])
        self.assertEqual(data["oldQty"], 6)
        self.assertEqual(data["qty"], 4)
        self.assertEqual(data["difference"], -2)
        self.assertEqual(data["id"], 5)
        self.assertEqual(data["warehouse"], "1")
        self.assertEqual(data["name"], "番茄")
        self.assertEqual(data["bin"], "A-03")
        self.assertEqual(data["reason"], "rotten")
        self.assertEqual(data["operator"], "測試人員")

    def test_05_inbound(self):
        response = client.post(
            "/api/inbound",
            json={
                "item": "番茄",
                "warehouse": "1",
                "bin": "A-03",
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
            "A-03",
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