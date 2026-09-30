import shutil
import unittest
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from backend.database import reset_database
from backend.helpers import today_text
from backend.main import app


client = TestClient(app)

BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_PATH = BASE_DIR / "database" / "warehouse.db"
BACKUP_PATH = BASE_DIR / "database" / "warehouse_test_backup.db"


def day(offset=0):
    # schema.sql 的初始資料以重建資料庫當天為基準，測試用同樣方式推算日期
    return (date.fromisoformat(today_text()) + timedelta(days=offset)).isoformat()


def seed_received_at(days_old):
    return f"{day(-days_old)}T08:00:00+08:00"


class DatabaseTestCase(unittest.TestCase):
    """每個測試都由 schema.sql 重建資料庫，結束後恢復原本的 Demo 資料庫。"""

    @classmethod
    def setUpClass(cls):
        # 整組測試開始前，備份目前 Demo 資料庫（若存在）
        if DATABASE_PATH.exists():
            shutil.copy(DATABASE_PATH, BACKUP_PATH)

    def setUp(self):
        # 測試結果不受本機 Demo 資料庫目前內容影響
        reset_database()

    @classmethod
    def tearDownClass(cls):
        # 原本沒有資料庫時，留下 schema.sql 的初始資料
        if BACKUP_PATH.exists():
            shutil.copy(BACKUP_PATH, DATABASE_PATH)
            BACKUP_PATH.unlink()
        else:
            reset_database()

    def qty_by_id(self):
        inventory = client.get("/api/inventory").json()
        return {batch["id"]: batch["qty"] for batch in inventory}
