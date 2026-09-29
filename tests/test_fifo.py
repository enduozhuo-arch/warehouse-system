import unittest

from backend.fifo import fifo_outbound


class TestFIFO(unittest.TestCase):

    def test_fifo_outbound(self):
        batches = [
            {
                "batch_id": 1,
                "location": "A01",
                "quantity": 50,
                "received_at": "2026-09-20 08:00:00"
            },
            {
                "batch_id": 2,
                "location": "A02",
                "quantity": 30,
                "received_at": "2026-09-25 10:00:00"
            }
        ]

        result = fifo_outbound(batches, 60)

        # FIFO：先從最早進貨的 A01 出 50
        self.assertEqual(result[0]["location"], "A01")
        self.assertEqual(result[0]["quantity"], 50)

        # 剩下 10 再從 A02 出貨
        self.assertEqual(result[1]["location"], "A02")
        self.assertEqual(result[1]["quantity"], 10)

        # 出貨後剩餘庫存
        self.assertEqual(batches[0]["quantity"], 0)
        self.assertEqual(batches[1]["quantity"], 20)


if __name__ == "__main__":
    unittest.main()