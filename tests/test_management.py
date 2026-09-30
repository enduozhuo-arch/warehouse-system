import unittest

from tests.base import DatabaseTestCase, client, day, seed_received_at


class TestManagement(DatabaseTestCase):
    # schema.sql 初始資料（入庫時間、效期以今天為基準）：
    # batch 1 甘藍菜 倉庫 1 A-10 5 箱，5 天前入庫，8 天後到期
    # batch 2 青江菜 倉庫 1 B-02 3 箱，9 天前入庫，2 天後到期
    # batch 3 青江菜 倉庫 2 C-05 10 箱，3 天前入庫，12 天後到期
    # batch 4 高麗菜 倉庫 2 D-01 8 箱，4 天前入庫，15 天後到期
    # batch 5 番茄   倉庫 1 A-03 6 箱，6 天前入庫，6 天後到期

    def alerts(self, today):
        return client.get("/api/alerts", params={"today": today}).json()

    def batch(self, batch_id):
        return next(
            batch for batch in client.get("/api/inventory").json()
            if batch["id"] == batch_id
        )

    def test_seed_matches_frontend_sample_data(self):
        inventory = client.get("/api/inventory").json()

        self.assertEqual(
            sorted(
                (batch["id"], batch["name"], batch["warehouse"], batch["bin"], batch["qty"])
                for batch in inventory
            ),
            [
                (1, "甘藍菜", "1", "A-10", 5),
                (2, "青江菜", "1", "B-02", 3),
                (3, "青江菜", "2", "C-05", 10),
                (4, "高麗菜", "2", "D-01", 8),
                (5, "番茄", "1", "A-03", 6),
            ],
        )

    def test_inventory_has_lot_expiry_and_crates(self):
        batch = self.batch(2)

        self.assertEqual(batch["lotNumber"], "ZN-1-SEED-02")
        self.assertEqual(batch["receivedAt"], seed_received_at(9))
        self.assertEqual(batch["expiryDate"], day(2))
        self.assertEqual(batch["locationCode"], "LOC-1-B02")
        self.assertEqual(
            batch["crateCodes"],
            ["seed-02-BOX-001", "seed-02-BOX-002", "seed-02-BOX-003"],
        )

    def test_direct_inbound_accepts_new_product_and_bin(self):
        response = client.post(
            "/api/inbound",
            json={
                "item": "白蘿蔔",
                "warehouse": "2",
                "bin": "e-07",
                "quantity": 3,
                "operator": "李太太",
                "expiryDate": "2026-10-10",
            },
        )

        self.assertEqual(response.status_code, 200)

        batch = response.json()["batch"]

        self.assertEqual(batch["name"], "白蘿蔔")
        self.assertEqual(batch["bin"], "E-07")
        self.assertEqual(batch["locationCode"], "LOC-2-E07")
        self.assertEqual(batch["expiryDate"], "2026-10-10")
        self.assertEqual(
            batch["crateCodes"],
            ["6-BOX-001", "6-BOX-002", "6-BOX-003"],
        )
        self.assertIn("白蘿蔔", client.get("/api/products").json())

    def test_direct_outbound_removes_crates(self):
        client.post(
            "/api/outbound",
            json={"item": "番茄", "quantity": 4, "operator": "李太太"},
        )

        batch = self.batch(5)

        self.assertEqual(batch["qty"], 2)
        self.assertEqual(
            batch["crateCodes"],
            ["seed-05-BOX-005", "seed-05-BOX-006"],
        )

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
        alerts = self.alerts(day())

        # 今天只有 batch 2（2 天後到期）在 3 天內
        self.assertEqual([item["id"] for item in alerts["expiry"]], [2])
        self.assertEqual(alerts["expiry"][0]["status"], "2 天內到期")

        # 6 天後：batch 2 已過期、batch 5 當天到期、batch 1 剩 2 天
        alerts = self.alerts(day(6))
        statuses = {item["id"]: item["status"] for item in alerts["expiry"]}

        self.assertEqual(
            statuses,
            {
                2: "已過期，請確認",
                5: "今日到期",
                1: "2 天內到期",
            },
        )

    def test_aging_alerts(self):
        # 初始規則：青江菜放 7 天以上提醒；batch 3 只放了 3 天
        alerts = self.alerts(day())

        self.assertEqual(
            [(item["id"], item["days"], item["threshold"]) for item in alerts["aging"]],
            [(2, 9, 7)],
        )

        response = client.put(
            "/api/aging-rules",
            json={"name": "番茄", "days": 5},
        )
        self.assertEqual(response.status_code, 200)

        self.assertEqual(
            [item["id"] for item in self.alerts(day())["aging"]],
            [2, 5],
        )

        self.assertEqual(
            client.put(
                "/api/aging-rules",
                json={"name": "番茄", "days": 0},
            ).status_code,
            400,
        )

    def test_safety_alerts(self):
        # 初始設定：倉庫 1 甘藍菜安全庫存 4，目前 5，不提醒
        self.assertEqual(client.get("/api/alerts").json()["safety"], [])

        client.put(
            "/api/safety-levels",
            json={"warehouse": "1", "name": "甘藍菜", "quantity": 6},
        )
        # 剛好等於安全庫存不提醒
        client.put(
            "/api/safety-levels",
            json={"warehouse": "2", "name": "青江菜", "quantity": 10},
        )

        self.assertEqual(
            client.get("/api/alerts").json()["safety"],
            [{"warehouse": "1", "name": "甘藍菜", "quantity": 5, "level": 6}],
        )
        self.assertEqual(len(client.get("/api/safety-levels").json()), 2)

    def test_waste_summary(self):
        self.assertEqual(client.get("/api/waste-summary").json(), [])

        for batch_id, actual, reason in [
            (5, 4, "腐爛報廢"),
            (3, 9, "破損報廢"),
            (1, 4, "腐爛報廢"),
            (2, 2, "盤點差異"),
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
                {"reason": "腐爛報廢", "quantity": 3},
                {"reason": "破損報廢", "quantity": 1},
            ],
        )


if __name__ == "__main__":
    unittest.main()
