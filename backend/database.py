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


if __name__ == "__main__":
    initialize_database()
    print("Database initialized successfully.")