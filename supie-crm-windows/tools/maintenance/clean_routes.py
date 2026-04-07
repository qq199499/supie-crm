import re
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
ROUTES_PROJECTS_PATH = ROOT_DIR / "routes_projects.py"
APP_PATH = ROOT_DIR / "app.py"

with ROUTES_PROJECTS_PATH.open('r', encoding='utf-8') as f:
    content = f.read()

# 1. Remove _load_delivery_phases
content = re.sub(r'def _load_delivery_phases\(.*?\).*?return phases, logs\n\n\n', '', content, flags=re.DOTALL)

# 2. Remove context usages in _project_detail_context
content = re.sub(r'    delivery_phases, phase_change_logs = _load_delivery_phases\(project_id\)\n', '', content)
content = re.sub(r'        "delivery_phases": delivery_phases,\n', '', content)
content = re.sub(r'        "phase_change_logs": phase_change_logs,\n', '', content)
content = re.sub(r'        "has_delivery_phases": bool\(delivery_phases\),\n', '', content)
content = re.sub(r'    en_sql = "SELECT id, name FROM phase_templates WHERE enabled IS TRUE ORDER BY id" if uses_postgres\(\) else "SELECT id, name FROM phase_templates WHERE enabled = 1 ORDER BY id"\n    phase_templates_enabled = fetchall\(en_sql\)\n', '', content)
content = re.sub(r'        "phase_templates_enabled": phase_templates_enabled,\n', '', content)

# 3. Remove from project_manage context
content = re.sub(r'    en_sql = "SELECT id, name FROM phase_templates WHERE enabled IS TRUE ORDER BY id" if uses_postgres\(\) else "SELECT id, name FROM phase_templates WHERE enabled = 1 ORDER BY id"\n    phase_templates = fetchall\(en_sql\)\n', '', content)
content = re.sub(r'        phase_templates=phase_templates,\n', '', content)

# 4. Remove from create_project / edit_project parsing
content = re.sub(r'        ptid = parse_int_form_value\(request\.form\.get\("phase_template_id"\), 0\) or 0\n', '', content)
# It's also used in app.py's create_project? No, creating project is in routes_projects.py. Let's just remove the block handling ptid if it exists.
content = re.sub(r'        if ptid > 0:.*?app\.py", line.*?# we will skip the exact replacement and do it via python string methods if re is hard.', '', content, flags=re.DOTALL)

# Let's remove the routes
routes_to_remove = [
    'project_apply_delivery_phase_template',
    'project_delivery_phase_start',
    'project_delivery_phase_complete',
    'project_delivery_phase_skip',
    'project_delivery_phase_deliverable_toggle'
]

for route in routes_to_remove:
    content = re.sub(r'@app\.route\("/projects/<int:project_id>/delivery-phases/.*?\ndef ' + route + r'\(.*?\).*?(?=\n\n@|\Z)', '', content, flags=re.DOTALL)

with ROUTES_PROJECTS_PATH.open('w', encoding='utf-8') as f:
    f.write(content)

with APP_PATH.open('r', encoding='utf-8') as f:
    app_content = f.read()
app_content = app_content.replace('    _ensure_delivery_phases_schema(conn)\n', '')
app_content = app_content.replace('    _seed_default_phase_templates(conn)\n', '')
app_content = app_content.replace('        _ensure_delivery_phases_schema(g.db)\n', '')

# Remove schema functions
app_content = re.sub(r'def _seed_default_phase_templates\(conn: Any\) -> None:.*?(?=\n\ndef )', '', app_content, flags=re.DOTALL)
app_content = re.sub(r'def _ensure_delivery_phases_schema\(conn: Any\) -> None:.*?(?=\n\ndef )', '', app_content, flags=re.DOTALL)

with APP_PATH.open('w', encoding='utf-8') as f:
    f.write(app_content)

print("Cleanup script completed.")
