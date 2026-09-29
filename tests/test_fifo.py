import unittest

from backend.fifo import fifo_outbound


class TestFIFO(unittest.TestCase):

    def test_fifo_outbound(self):
        batches = [
            {
                "id": 1,
                "bin": "A01",
                "qty": 50,
                "receivedAt": "2026-09-20 08:00:00"
            },
            {
                "id": 2,
                "bin": "A02",
                "qty": 30,
                "receivedAt": "2026-09-25 10:00:00"
            }
        ]

        result = fifo_outbound(batches, 60)

        # FIFO：先從最早進貨的 A01 出 50
        self.assertEqual(result[0]["bin"], "A01")
        self.assertEqual(result[0]["quantity"], 50)

        # 剩下 10 再從 A02 出貨
        self.assertEqual(result[1]["bin"], "A02")
        self.assertEqual(result[1]["quantity"], 10)

        # 出貨後剩餘庫存
        self.assertEqual(batches[0]["qty"], 0)
        self.assertEqual(batches[1]["qty"], 20)


if __name__ == "__main__":
    unittest.main()