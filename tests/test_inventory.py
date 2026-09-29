import unittest

from backend.inventory import update_spoilage


class TestInventory(unittest.TestCase):

    def test_update_spoilage(self):
        batch = {
            "batch_id": 1,
            "warehouse_id": 1,
            "location": "A01",
            "product": "高麗菜",
            "quantity": 50
        }

        result = update_spoilage(batch, 8)

        # 腐爛 8 顆後，庫存應由 50 變成 42
        self.assertEqual(batch["quantity"], 42)

        # 確認盤點結果
        self.assertEqual(result["spoiled_quantity"], 8)
        self.assertEqual(result["remaining_quantity"], 42)


if __name__ == "__main__":
    unittest.main()