import unittest

from tests.base import DatabaseTestCase, client


class TestInboundTasks(DatabaseTestCase):

    def create_task(self, **overrides):
        body = {
            "item": "白蘿蔔",
            "warehouse": "1",
            "bin": "b-02",
            "quantity": 2,
            "operator": "倉管人員",
            "expiryDate": "2026-10-15",
        }
        body.update(overrides)

        return client.post("/api/inbound-tasks", json=body)

    def verify(self, task_id, code):
        return client.post(
            f"/api/inbound-tasks/{task_id}/verify-location",
            json={"locationCode": code},
        )

    def scan(self, task_id, code):
        return client.post(
            f"/api/inbound-tasks/{task_id}/scan",
            json={"crateCode": code},
        )

    def confirm(self, task_id):
        return client.post(f"/api/inbound-tasks/{task_id}/confirm")

    def test_create_does_not_add_stock(self):
        before = self.qty_by_id()

        response = self.create_task()

        self.assertEqual(response.status_code, 200)

        task = response.json()

        self.assertEqual(task["status"], "open")
        self.assertFalse(task["locationVerified"])
        self.assertFalse(task["ready"])
        self.assertEqual(task["locationCode"], "LOC-1-B02")
        self.assertEqual(task["batch"]["id"], 6)
        self.assertEqual(task["batch"]["bin"], "B-02")
        self.assertEqual(task["batch"]["receivedAt"], "")
        self.assertEqual(task["batch"]["expiryDate"], "2026-10-15")
        self.assertEqual(task["batch"]["crateCodes"], ["6-BOX-001", "6-BOX-002"])
        self.assertTrue(task["batch"]["lotNumber"].startswith("ZN-1-"))

        # 收貨單建立後、確認入庫前，庫存不變
        self.assertEqual(self.qty_by_id(), before)

    def test_full_flow_creates_batch_and_new_product(self):
        task_id = self.create_task().json()["id"]

        # 還沒核對儲位就不能掃箱
        self.assertEqual(self.scan(task_id, "6-BOX-001").status_code, 409)

        # 掃錯儲位
        self.assertEqual(self.verify(task_id, "LOC-1-A10").status_code, 400)
        self.assertEqual(self.verify(task_id, "LOC-1-B02").status_code, 200)

        # 箱數還沒掃滿不能確認入庫
        self.assertEqual(self.scan(task_id, "6-BOX-001").status_code, 200)
        self.assertEqual(self.confirm(task_id).status_code, 400)

        # 不屬於這張收貨單的箱號、重複掃描
        self.assertEqual(self.scan(task_id, "seed-01-BOX-001").status_code, 400)
        self.assertEqual(self.scan(task_id, "6-BOX-001").status_code, 409)

        self.assertTrue(self.scan(task_id, "6-BOX-002").json()["ready"])

        response = self.confirm(task_id)

        self.assertEqual(response.status_code, 200)

        batch = response.json()["task"]["batch"]

        self.assertEqual(batch["id"], 6)
        self.assertEqual(batch["name"], "白蘿蔔")
        self.assertEqual(batch["warehouse"], "1")
        self.assertEqual(batch["qty"], 2)
        # 入庫時間由伺服器在確認入庫時記錄
        self.assertTrue(batch["receivedAt"].endswith("+08:00"))

        self.assertEqual(self.qty_by_id()[6], 2)
        # 原本沒有的商品會自動建立
        self.assertIn("白蘿蔔", client.get("/api/products").json())

        transaction = client.get("/api/transactions").json()[0]
        self.assertEqual(transaction["kind"], "進貨")
        self.assertEqual(transaction["quantity"], 2)
        self.assertEqual(transaction["operatorRole"], "warehouse")

        # 新入庫的批次可以用取貨工作掃碼出貨
        pick = client.post(
            "/api/pick-tasks",
            json={"item": "白蘿蔔", "quantity": 1, "operator": "李太太"},
        ).json()
        self.assertEqual(pick["next"]["locationCode"], "LOC-1-B02")

    def test_existing_bin_is_matched_without_dash(self):
        # A10 與既有儲位 A-10 是同一個位置
        task = self.create_task(item="甘藍菜", bin="a10").json()

        self.assertEqual(task["batch"]["bin"], "A-10")
        self.assertEqual(task["locationCode"], "LOC-1-A10")

    def test_cancel_and_batch_id_is_not_reused(self):
        first = self.create_task().json()

        response = client.post(f"/api/inbound-tasks/{first['id']}/cancel")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(6, self.qty_by_id())

        # 已預留的批次編號不重複使用，避免箱號相同
        second = self.create_task().json()
        self.assertEqual(second["batch"]["id"], 7)

        direct = client.post(
            "/api/inbound",
            json={
                "item": "番茄",
                "warehouse": "1",
                "bin": "A03",
                "quantity": 1,
                "operator": "測試人員",
            },
        ).json()
        self.assertEqual(direct["batch"]["id"], 8)

    def test_validation(self):
        self.assertEqual(self.create_task(quantity=0).status_code, 400)
        self.assertEqual(self.create_task(warehouse="9").status_code, 404)
        self.assertEqual(self.create_task(expiryDate="10/15").status_code, 400)
        self.assertEqual(self.create_task(operator=" ").status_code, 400)


if __name__ == "__main__":
    unittest.main()
