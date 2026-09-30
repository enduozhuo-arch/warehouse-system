import sqlite3
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_PATH = BASE_DIR / "database" / "warehouse.db"
SCHEMA_PATH = BASE_DIR / "database" / "schema.sql"


def get_connection():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database():
    with open(SCHEMA_PATH, "r", encoding="utf-8") as file:
        schema = file.read()

    connection = get_connection()

    try:
        connection.executescript(schema)
        connection.commit()
    finally:
        connection.close()


def reset_database():
    # 刪除現有資料庫並由 schema.sql 重建（會清掉目前的資料）
    if DATABASE_PATH.exists():
        DATABASE_PATH.unlink()

    initialize_database()


if __name__ == "__main__":
    import sys

    if "--reset" in sys.argv:
        reset_database()
        print("Database reset successfully.")
    else:
        initialize_database()
        print("Database initialized successfully.")
