import unittest

from backend.inventory import update_spoilage


class TestInventory(unittest.TestCase):

    def test_update_spoilage(self):
        batch = {
            "id": 1,
            "warehouse": "1",
            "bin": "A01",
            "name": "高麗菜",
            "qty": 50
        }

        result = update_spoilage(batch, 8)

        # 腐爛 8 顆後，庫存應由 50 變成 42
        self.assertEqual(batch["qty"], 42)

        # 確認盤點結果
        self.assertEqual(result["spoiled_quantity"], 8)
        self.assertEqual(result["qty"], 42)


if __name__ == "__main__":
    unittest.main()