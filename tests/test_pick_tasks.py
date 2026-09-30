import unittest

from tests.base import DatabaseTestCase, client


class TestPickTasks(DatabaseTestCase):
    # schema.sql 初始資料的高麗菜（依入庫時間）：
    # batch 1 = 50（倉庫 1 A01）→ batch 4 = 60（倉庫 2 B01）→ batch 2 = 30（倉庫 1 A02）

    def create_task(self, quantity, item="高麗菜"):
        response = client.post(
            "/api/pick-tasks",
            json={"item": item, "quantity": quantity, "operator": "李太太"},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def verify(self, task_id, code):
        return client.post(
            f"/api/pick-tasks/{task_id}/verify-location",
            json={"locationCode": code},
        )

    def scan(self, task_id, code):
        return client.post(
            f"/api/pick-tasks/{task_id}/scan",
            json={"crateCode": code},
        )

    def confirm(self, task_id):
        return client.post(f"/api/pick-tasks/{task_id}/confirm")

    def test_create_shows_oldest_batch_first(self):
        task = self.create_task(1)

        self.assertEqual(task["status"], "open")
        self.assertFalse(task["ready"])
        self.assertEqual(len(task["plan"]), 1)
        self.assertEqual(task["next"]["batchId"], 1)
        self.assertEqual(task["next"]["warehouse"], "1")
        self.assertEqual(task["next"]["bin"], "A01")
        self.assertEqual(task["next"]["locationCode"], "LOC-1-A01")
        self.assertEqual(task["next"]["lotNumber"], "ZN-1-20260920-000001")

        # 建立取貨工作不會扣庫存
        self.assertEqual(self.qty_by_id()[1], 50)

    def test_create_rejects_insufficient_stock(self):
        response = client.post(
            "/api/pick-tasks",
            json={"item": "高麗菜", "quantity": 141, "operator": "李太太"},
        )

        self.assertEqual(response.status_code, 400)

    def test_cannot_confirm_before_scanning(self):
        # 對應上台示範：還沒核對就按確認出庫，庫存不能改變
        task = self.create_task(1)

        response = self.confirm(task["id"])

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.qty_by_id()[1], 50)
        self.assertEqual(client.get("/api/transactions").json(), [])

    def test_wrong_location_is_rejected(self):
        task = self.create_task(1)

        # 倉庫 1 A02 是較新的高麗菜，不是 FIFO 指定的儲位
        response = self.verify(task["id"], "LOC-1-A02")

        self.assertEqual(response.status_code, 400)
        self.assertIn("LOC-1-A01", response.json()["detail"])

    def test_scan_requires_location_check(self):
        task = self.create_task(1)

        response = self.scan(task["id"], "1-BOX-001")

        self.assertEqual(response.status_code, 409)

    def test_wrong_batch_crate_is_rejected(self):
        task = self.create_task(1)
        self.assertEqual(self.verify(task["id"], "loc-1-a01").status_code, 200)

        # 較新批次（batch 2）的箱子、不存在的箱號都要拒絕
        self.assertEqual(self.scan(task["id"], "2-BOX-001").status_code, 400)
        self.assertEqual(self.scan(task["id"], "NO-SUCH-BOX").status_code, 400)

    def test_duplicate_scan_is_rejected(self):
        task = self.create_task(2)
        self.verify(task["id"], "LOC-1-A01")

        self.assertEqual(self.scan(task["id"], "1-BOX-001").status_code, 200)
        self.assertEqual(self.scan(task["id"], "1-BOX-001").status_code, 409)

    def test_full_flow_deducts_stock_and_records_transaction(self):
        task = self.create_task(1)
        self.verify(task["id"], "LOC-1-A01")

        scanned = self.scan(task["id"], "1-BOX-007").json()

        self.assertTrue(scanned["ready"])
        self.assertIsNone(scanned["next"])
        self.assertEqual(scanned["scanned"][0]["crateCode"], "1-BOX-007")

        response = self.confirm(task["id"])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["task"]["status"], "confirmed")
        self.assertEqual(self.qty_by_id()[1], 49)

        # 被取出的箱號不再屬於在庫箱號
        batch_1 = next(
            batch for batch in client.get("/api/inventory").json()
            if batch["id"] == 1
        )
        self.assertNotIn("1-BOX-007", batch_1["crateCodes"])
        self.assertEqual(len(batch_1["crateCodes"]), 49)

        transaction = client.get("/api/transactions").json()[0]
        self.assertEqual(transaction["kind"], "出貨")
        self.assertEqual(transaction["quantity"], -1)
        self.assertEqual(transaction["operator"], "李太太")
        self.assertEqual(transaction["operatorRole"], "manager")
        self.assertEqual(transaction["lotNumber"], "ZN-1-20260920-000001")

        # 已結案的工作不能再操作
        self.assertEqual(self.confirm(task["id"]).status_code, 409)

    def test_multi_batch_task_requires_new_location_check(self):
        # 52 箱：batch 1 出 50，再跨倉庫從 batch 4 出 2
        task = self.create_task(52)

        self.assertEqual(
            [(line["batchId"], line["quantity"]) for line in task["plan"]],
            [(1, 50), (4, 2)],
        )

        self.verify(task["id"], "LOC-1-A01")

        for seq in range(1, 51):
            response = self.scan(task["id"], f"1-BOX-{seq:03d}")
            self.assertEqual(response.status_code, 200)

        state = response.json()
        self.assertEqual(state["next"]["batchId"], 4)
        self.assertIsNone(state["verifiedBatchId"])

        # 換批次後沒重新核對儲位就不能掃
        self.assertEqual(self.scan(task["id"], "4-BOX-001").status_code, 409)

        self.verify(task["id"], "LOC-2-B01")
        self.scan(task["id"], "4-BOX-001")
        self.scan(task["id"], "4-BOX-002")

        self.assertEqual(self.confirm(task["id"]).status_code, 200)

        quantities = self.qty_by_id()
        self.assertEqual(quantities[1], 0)
        self.assertEqual(quantities[4], 58)
        self.assertEqual(quantities[2], 30)

    def test_confirm_fails_when_stock_changed(self):
        task = self.create_task(1)
        self.verify(task["id"], "LOC-1-A01")
        self.scan(task["id"], "1-BOX-050")

        # 掃描後、確認前，這一箱被盤點報廢掉了
        client.patch(
            "/api/inventory",
            json={
                "id": 1,
                "actual_quantity": 49,
                "reason": "rotten",
                "operator": "倉管人員",
            },
        )

        response = self.confirm(task["id"])

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.qty_by_id()[1], 49)

    def test_cancel_keeps_stock(self):
        task = self.create_task(1)
        self.verify(task["id"], "LOC-1-A01")
        self.scan(task["id"], "1-BOX-001")

        response = client.post(f"/api/pick-tasks/{task['id']}/cancel")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["task"]["status"], "cancelled")
        self.assertEqual(self.qty_by_id()[1], 50)
        self.assertEqual(client.get("/api/pick-tasks").json(), [])

    def test_unknown_task_returns_404(self):
        self.assertEqual(client.get("/api/pick-tasks/999").status_code, 404)


if __name__ == "__main__":
    unittest.main()
