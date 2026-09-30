import unittest

from tests.base import DatabaseTestCase, client


class TestManagement(DatabaseTestCase):

    def alerts(self, today):
        return client.get("/api/alerts", params={"today": today}).json()

    def test_inventory_has_lot_expiry_and_crates(self):
        batch = client.get("/api/inventory").json()[0]

        self.assertEqual(batch["id"], 1)
        self.assertEqual(batch["lotNumber"], "ZN-1-20260920-000001")
        self.assertEqual(batch["expiryDate"], "2026-10-02")
        self.assertEqual(batch["locationCode"], "LOC-1-A01")
        self.assertEqual(len(batch["crateCodes"]), 50)
        self.assertEqual(batch["crateCodes"][0], "1-BOX-001")

    def test_direct_inbound_accepts_new_product_and_bin(self):
        response = client.post(
            "/api/inbound",
            json={
                "item": "甘藍菜",
                "warehouse": "2",
                "bin": "c-05",
                "quantity": 3,
                "operator": "李太太",
                "expiryDate": "2026-10-10",
            },
        )

        self.assertEqual(response.status_code, 200)

        batch = response.json()["batch"]

        self.assertEqual(batch["name"], "甘藍菜")
        self.assertEqual(batch["bin"], "C-05")
        self.assertEqual(batch["locationCode"], "LOC-2-C05")
        self.assertEqual(batch["expiryDate"], "2026-10-10")
        self.assertEqual(len(batch["crateCodes"]), 3)
        self.assertIn("甘藍菜", client.get("/api/products").json())

    def test_direct_outbound_removes_crates(self):
        client.post(
            "/api/outbound",
            json={"item": "番茄", "quantity": 4, "operator": "李太太"},
        )

        batch = next(
            batch for batch in client.get("/api/inventory").json()
            if batch["id"] == 3
        )

        self.assertEqual(batch["qty"], 36)
        self.assertEqual(len(batch["crateCodes"]), 36)
        self.assertEqual(batch["crateCodes"][0], "3-BOX-005")

    def test_users(self):
        self.assertEqual(
            client.get("/api/users").json(),
            [
                {"name": "李太太", "role": "manager"},
                {"name": "倉管人員", "role": "warehouse"},
            ],
        )

        response = client.post(
            "/api/users",
            json={"name": "小陳", "role": "warehouse"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(client.get("/api/users").json()), 3)

        # 重複姓名、不存在的角色
        self.assertEqual(
            client.post("/api/users", json={"name": "小陳"}).status_code,
            409,
        )
        self.assertEqual(
            client.post(
                "/api/users",
                json={"name": "小王", "role": "admin"},
            ).status_code,
            400,
        )

    def test_expiry_alerts(self):
        alerts = self.alerts("2026-09-30")

        # 只有 batch 1（2026-10-02 到期）在 3 天內
        self.assertEqual([item["id"] for item in alerts["expiry"]], [1])
        self.assertEqual(alerts["expiry"][0]["status"], "2 天內到期")

        alerts = self.alerts("2026-10-06")
        statuses = {item["id"]: item["status"] for item in alerts["expiry"]}

        self.assertEqual(statuses[1], "已過期，請確認")
        self.assertEqual(statuses[3], "今日到期")
        self.assertEqual(statuses[4], "1 天內到期")
        self.assertEqual(statuses[2], "3 天內到期")
        self.assertNotIn(5, statuses)

    def test_aging_alerts(self):
        # 初始規則：高麗菜放 7 天以上提醒
        alerts = self.alerts("2026-09-30")

        self.assertEqual(
            [(item["id"], item["days"]) for item in alerts["aging"]],
            [(1, 10), (4, 7)],
        )

        response = client.put(
            "/api/aging-rules",
            json={"name": "番茄", "days": 5},
        )
        self.assertEqual(response.status_code, 200)

        self.assertIn(
            3,
            [item["id"] for item in self.alerts("2026-09-30")["aging"]],
        )

        self.assertEqual(
            client.put(
                "/api/aging-rules",
                json={"name": "番茄", "days": 0},
            ).status_code,
            400,
        )

    def test_safety_alerts(self):
        # 初始設定：倉庫 1 番茄安全庫存 50，目前 40
        self.assertEqual(
            client.get("/api/alerts").json()["safety"],
            [{"warehouse": "1", "name": "番茄", "quantity": 40, "level": 50}],
        )

        client.put(
            "/api/safety-levels",
            json={"warehouse": "1", "name": "番茄", "quantity": 40},
        )
        client.put(
            "/api/safety-levels",
            json={"warehouse": "2", "name": "白蘿蔔", "quantity": 36},
        )

        self.assertEqual(
            client.get("/api/alerts").json()["safety"],
            [{"warehouse": "2", "name": "白蘿蔔", "quantity": 35, "level": 36}],
        )
        self.assertEqual(len(client.get("/api/safety-levels").json()), 2)

    def test_waste_summary(self):
        self.assertEqual(client.get("/api/waste-summary").json(), [])

        for batch_id, actual, reason in [
            (5, 30, "腐爛報廢"),
            (3, 38, "破損報廢"),
            (1, 47, "腐爛報廢"),
            (2, 29, "盤點差異"),
        ]:
            client.post(
                "/api/stocktake/counts",
                json={
                    "id": batch_id,
                    "actual_quantity": actual,
                    "reason": reason,
                    "operator": "倉管人員",
                },
            )

        # 盤點差異不算報廢
        self.assertEqual(
            client.get("/api/waste-summary").json(),
            [
                {"reason": "腐爛報廢", "quantity": 8},
                {"reason": "破損報廢", "quantity": 2},
            ],
        )


if __name__ == "__main__":
    unittest.main()
