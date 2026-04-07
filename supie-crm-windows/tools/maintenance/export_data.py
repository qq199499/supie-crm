import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app import create_connection, uses_postgres

OUTPUT_PATH = ROOT_DIR / "data" / "backups" / "crm_data_backup.json"

def json_serial(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Type {type(obj)} not serializable")

def export_all():
    conn = create_connection()
    cur = conn.cursor()
    
    if uses_postgres():
        cur.execute("SELECT tablename as name FROM pg_catalog.pg_tables WHERE schemaname = 'public';")
    else:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        
    tables = [row['name'] if isinstance(row, dict) else row[0] for row in cur.fetchall()]
    
    data = {}
    for table in tables:
        cur.execute(f"SELECT * FROM {table};")
        rows = cur.fetchall()
        data[table] = [dict(row) for row in rows]
        
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=json_serial)
        
    conn.close()

if __name__ == "__main__":
    export_all()
    print(f"Data exported successfully to {OUTPUT_PATH}")
