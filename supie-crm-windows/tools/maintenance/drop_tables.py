import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import app
conn = app.get_db_connection()
cur = conn.cursor()
if app.uses_postgres():
    cur.execute("DROP TABLE IF EXISTS project_delivery_phase_deliverables CASCADE;")
    cur.execute("DROP TABLE IF EXISTS project_delivery_phases CASCADE;")
    cur.execute("DROP TABLE IF EXISTS phase_template_items CASCADE;")
    cur.execute("DROP TABLE IF EXISTS phase_templates CASCADE;")
else:
    cur.execute("DROP TABLE IF EXISTS project_delivery_phase_deliverables;")
    cur.execute("DROP TABLE IF EXISTS project_delivery_phases;")
    cur.execute("DROP TABLE IF EXISTS phase_template_items;")
    cur.execute("DROP TABLE IF EXISTS phase_templates;")
conn.commit()
conn.close()
print("Drop successful!")
