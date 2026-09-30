import unittest

from backend.helpers import today_text
from tests.base import DatabaseTestCase, client


class TestStocktake(DatabaseTestCase):

    def count(self, **body):
        body.setdefault("operator", "倉管人員")
        return client.post("/api/stocktake/counts", json=body)

    def batch(self, batch_id):
        return next(
            batch for batch in client.get("/api/inventory").json()
            if batch["id"] == batch_id
        )

    def test_daily_list_starts_uncounted(self):
        data = client.get("/api/stocktake").json()

        self.assertEqual(data["date"], today_text())
        self.assertEqual(data["total"], 5)
        self.assertEqual(data["counted"], 0)
        self.assertFalse(data["completed"])
        self.assertTrue(all(not item["counted"] for item in data["items"]))

    def test_warehouse_filter_keeps_overall_progress(self):
        data = client.get("/api/stocktake", params={"warehouse": "2"}).json()

        self.assertEqual([item["id"] for item in data["items"]], [4, 3])
        self.assertEqual(data["total"], 5)

    def test_count_with_waste_saves_before_and_after(self):
        # batch 5 番茄原本 6 箱，盤點後實際剩 4 箱
        response = self.count(id=5, actual_quantity=4, reason="腐爛報廢")

        self.assertEqual(response.status_code, 200)

        data = response.json()

        self.assertEqual(data["kind"], "報廢")
        self.assertEqual(data["oldQty"], 6)
        self.assertEqual(data["actual"], 4)
        self.assertEqual(data["qty"], 4)
        self.assertEqual(data["difference"], -2)
        self.assertEqual(data["counted"], 1)
        self.assertEqual(data["total"], 5)

        item = next(
            item for item in client.get("/api/stocktake").json()["items"]
            if item["id"] == 5
        )

        self.assertTrue(item["counted"])
        self.assertEqual(item["oldQty"], 6)
        self.assertEqual(item["actual"], 4)
        self.assertEqual(item["reason"], "腐爛報廢")
        self.assertEqual(item["operator"], "倉管人員")
        self.assertTrue(item["countedAt"])

        transaction = client.get("/api/transactions").json()[0]
        self.assertEqual(transaction["kind"], "報廢")
        self.assertEqual(transaction["quantity"], -2)

        # 報廢後在庫箱號數量與庫存數量一致
        self.assertEqual(len(self.batch(5)["crateCodes"]), 4)

    def test_difference_requires_reason(self):
        response = self.count(id=5, actual_quantity=4)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.qty_by_id()[5], 6)

    def test_count_without_difference_marks_counted(self):
        response = self.count(id=3, actual_quantity=10)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["difference"], 0)
        self.assertEqual(response.json()["reason"], "數量確認")
        # 數量相同且沒填原因，不產生異動紀錄
        self.assertEqual(client.get("/api/transactions").json(), [])
        self.assertEqual(client.get("/api/stocktake").json()["counted"], 1)

    def test_count_increase_adds_crates(self):
        response = self.count(id=2, actual_quantity=5, reason="盤點差異")

        self.assertEqual(response.json()["kind"], "盤點調整")

        batch = self.batch(2)
        self.assertEqual(batch["qty"], 5)
        # 新箱號沿用該批次的箱號格式
        self.assertEqual(
            batch["crateCodes"][-2:],
            ["seed-02-BOX-004", "seed-02-BOX-005"],
        )

    def test_completed_when_all_batches_counted(self):
        for batch_id, quantity in self.qty_by_id().items():
            self.count(id=batch_id, actual_quantity=quantity)

        data = client.get("/api/stocktake").json()

        self.assertEqual(data["counted"], 5)
        self.assertTrue(data["completed"])

    def test_batch_emptied_after_listing_stays_on_list(self):
        client.get("/api/stocktake")
        self.count(id=5, actual_quantity=0, reason="破損報廢")

        data = client.get("/api/stocktake").json()

        self.assertEqual(data["total"], 5)
        self.assertIn(5, [item["id"] for item in data["items"]])

    def test_past_date_is_empty_and_invalid_date_rejected(self):
        past = client.get("/api/stocktake", params={"date": "2026-01-01"})

        self.assertEqual(past.status_code, 200)
        self.assertEqual(past.json()["total"], 0)
        self.assertFalse(past.json()["completed"])

        invalid = client.get("/api/stocktake", params={"date": "yesterday"})
        self.assertEqual(invalid.status_code, 400)

    def test_unknown_batch_returns_404(self):
        self.assertEqual(self.count(id=999, actual_quantity=1).status_code, 404)


if __name__ == "__main__":
    unittest.main()
