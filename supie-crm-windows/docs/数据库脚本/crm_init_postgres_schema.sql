-- 项目过程管理系统 PostgreSQL 初始化脚本
-- 来源：当前应用的建表与索引逻辑整理
-- 说明：仅包含最原始的结构定义与索引，并补充最小可登录的初始数据

BEGIN;

CREATE TABLE IF NOT EXISTS roles (
    id SERIAL PRIMARY KEY,
    code VARCHAR(40) NOT NULL UNIQUE,
    name VARCHAR(120) NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    is_system BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(80) NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name VARCHAR(120) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'normal',
    role_id INTEGER REFERENCES roles(id),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS role_permissions (
    role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    perm_key VARCHAR(80) NOT NULL,
    PRIMARY KEY (role_id, perm_key)
);

CREATE TABLE IF NOT EXISTS user_roles (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, role_id)
);

CREATE TABLE IF NOT EXISTS customers (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL UNIQUE,
    owner VARCHAR(100) NOT NULL,
    phone VARCHAR(50),
    email VARCHAR(120),
    industry VARCHAR(120),
    level VARCHAR(20) NOT NULL DEFAULT 'A',
    status VARCHAR(30) NOT NULL DEFAULT 'potential',
    tier VARCHAR(30) NOT NULL DEFAULT 'normal',
    tags VARCHAR(500),
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS opportunities (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    title VARCHAR(200) NOT NULL,
    amount NUMERIC(14, 2) NOT NULL DEFAULT 0,
    owner VARCHAR(100) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'new',
    stage VARCHAR(50),
    stage_started_at TIMESTAMP,
    amount_confidence VARCHAR(20),
    lost_reason VARCHAR(80),
    lost_reason_note TEXT,
    competitor TEXT,
    expected_sign_date DATE,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS customer_contacts (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    name VARCHAR(120) NOT NULL,
    title VARCHAR(120),
    phone VARCHAR(50),
    email VARCHAR(120),
    is_primary SMALLINT NOT NULL DEFAULT 0,
    note TEXT,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS customer_follow_ups (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    followed_by VARCHAR(120) NOT NULL,
    followed_at TIMESTAMP NOT NULL,
    method VARCHAR(20) NOT NULL DEFAULT 'phone',
    content TEXT NOT NULL,
    next_followup_at DATE,
    opportunity_id INTEGER REFERENCES opportunities(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS opportunity_stage_logs (
    id SERIAL PRIMARY KEY,
    opportunity_id INTEGER NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
    from_stage VARCHAR(50),
    to_stage VARCHAR(50) NOT NULL,
    note TEXT,
    changed_by VARCHAR(120) NOT NULL,
    changed_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    customer_id INTEGER,
    project_type VARCHAR(50) NOT NULL,
    manager VARCHAR(100) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'in_progress',
    current_stage VARCHAR(50) NOT NULL DEFAULT 'init',
    start_date DATE,
    end_date DATE,
    description TEXT,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    deleted_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tasks (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    assignee VARCHAR(100) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'todo',
    progress INTEGER NOT NULL DEFAULT 0,
    priority VARCHAR(20) NOT NULL DEFAULT 'medium',
    planned_end DATE,
    actual_end DATE,
    blocked_reason TEXT,
    updated_at TIMESTAMP NOT NULL,
    depends_on_task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS milestones (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    owner VARCHAR(100) NOT NULL,
    due_date DATE NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'open',
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS risks (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    level VARCHAR(20) NOT NULL DEFAULT 'medium',
    status VARCHAR(50) NOT NULL DEFAULT 'open',
    owner VARCHAR(100) NOT NULL,
    due_date DATE,
    mitigation TEXT,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS project_stage_logs (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    stage VARCHAR(50) NOT NULL,
    note TEXT,
    changed_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS project_activity_logs (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    actor VARCHAR(120) NOT NULL,
    action VARCHAR(100) NOT NULL,
    entity_type VARCHAR(50) NOT NULL,
    entity_label TEXT,
    detail TEXT,
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS project_members (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMP NOT NULL,
    UNIQUE(project_id, user_id)
);

CREATE TABLE IF NOT EXISTS project_progress_entries (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id),
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS project_delivery_phases (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name VARCHAR(120) NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    description TEXT
);

CREATE TABLE IF NOT EXISTS phase_template_items (
    id SERIAL PRIMARY KEY,
    template_id INTEGER NOT NULL,
    name VARCHAR(120) NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    default_duration_days INTEGER NOT NULL DEFAULT 0,
    description TEXT
);

CREATE TABLE IF NOT EXISTS project_phase_change_logs (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    summary TEXT NOT NULL,
    actor VARCHAR(120) NOT NULL,
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS contracts (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    contract_no VARCHAR(120) NOT NULL,
    amount NUMERIC(14, 2) NOT NULL DEFAULT 0,
    sign_date DATE,
    status VARCHAR(50) NOT NULL DEFAULT 'draft',
    updated_at TIMESTAMP NOT NULL,
    opportunity_id INTEGER REFERENCES opportunities(id) ON DELETE SET NULL,
    owner VARCHAR(120),
    currency VARCHAR(10) NOT NULL DEFAULT 'CNY',
    end_date DATE
);

CREATE TABLE IF NOT EXISTS receivables (
    id SERIAL PRIMARY KEY,
    contract_id INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    plan_date DATE NOT NULL,
    plan_amount NUMERIC(14, 2) NOT NULL DEFAULT 0,
    actual_date DATE,
    actual_amount NUMERIC(14, 2) NOT NULL DEFAULT 0,
    status VARCHAR(30) NOT NULL DEFAULT 'planned',
    note TEXT,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS invoices (
    id SERIAL PRIMARY KEY,
    invoice_no VARCHAR(120) NOT NULL UNIQUE,
    contract_id INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    receivable_id INTEGER REFERENCES receivables(id) ON DELETE SET NULL,
    amount NUMERIC(14, 2) NOT NULL DEFAULT 0,
    invoice_date DATE NOT NULL,
    invoice_type VARCHAR(50) NOT NULL,
    invoice_code VARCHAR(120) NOT NULL UNIQUE,
    status VARCHAR(50) NOT NULL DEFAULT 'issued',
    created_by VARCHAR(120) NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    id SERIAL PRIMARY KEY,
    module_type VARCHAR(50) NOT NULL,
    module_id INTEGER NOT NULL,
    title VARCHAR(220) NOT NULL,
    requested_value VARCHAR(80) NOT NULL,
    applicant VARCHAR(100) NOT NULL,
    approver VARCHAR(100) NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'pending',
    apply_note TEXT,
    comment TEXT,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS attachments (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
    milestone_id INTEGER REFERENCES milestones(id) ON DELETE CASCADE,
    category VARCHAR(50) NOT NULL DEFAULT '合同',
    file_name TEXT NOT NULL,
    stored_name TEXT NOT NULL UNIQUE,
    uploaded_at TIMESTAMP NOT NULL,
    uploaded_by VARCHAR(200),
    file_size BIGINT
);

CREATE TABLE IF NOT EXISTS ai_generation_logs (
    id SERIAL PRIMARY KEY,
    scene_code VARCHAR(80) NOT NULL,
    target_type VARCHAR(50) NOT NULL,
    target_id INTEGER,
    triggered_by VARCHAR(120) NOT NULL,
    prompt_version VARCHAR(80) NOT NULL,
    provider VARCHAR(80) NOT NULL,
    source_snapshot TEXT,
    generated_content TEXT NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'generated',
    accepted BOOLEAN,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_feedback (
    id SERIAL PRIMARY KEY,
    generation_id INTEGER NOT NULL REFERENCES ai_generation_logs(id) ON DELETE CASCADE,
    feedback_type VARCHAR(40) NOT NULL,
    feedback_note TEXT,
    created_by VARCHAR(120) NOT NULL,
    created_at TIMESTAMP NOT NULL
);

-- 默认管理员账号：admin / admin123
-- 初始角色、管理员账号与权限种子数据
INSERT INTO roles (code, name, sort_order, is_system, is_active, updated_at)
VALUES
('admin', '管理员', 0, TRUE, TRUE, CURRENT_TIMESTAMP),
('management', '管理层', 1, TRUE, TRUE, CURRENT_TIMESTAMP),
('sales_director', '销售总监', 2, TRUE, TRUE, CURRENT_TIMESTAMP),
('project_director', '项目总监', 3, TRUE, TRUE, CURRENT_TIMESTAMP),
('sales', '销售', 4, TRUE, TRUE, CURRENT_TIMESTAMP),
('pm', '项目经理', 5, TRUE, TRUE, CURRENT_TIMESTAMP),
('implementer', '实施工程师', 6, TRUE, TRUE, CURRENT_TIMESTAMP),
('finance', '财务', 7, TRUE, TRUE, CURRENT_TIMESTAMP),
('normal', '普通用户', 8, TRUE, TRUE, CURRENT_TIMESTAMP)
ON CONFLICT (code) DO UPDATE SET
    name = EXCLUDED.name,
    sort_order = EXCLUDED.sort_order,
    is_system = EXCLUDED.is_system,
    is_active = EXCLUDED.is_active,
    updated_at = EXCLUDED.updated_at;


INSERT INTO users (username, password_hash, display_name, role, role_id, is_active, updated_at)
VALUES (
    'admin',
    'scrypt:32768:8:1$ycoXQVa3ZfAppZn5$09b2b449b45bd7b08655092c8b30dffbaa2ed3f93290c06ad280d97fbef1a32a6fa8b58aa43b4fe8fdcd94a87a55c588287b258daaf5fe62ea040d9125027b58',
    '系统管理员',
    'admin',
    (SELECT id FROM roles WHERE code = 'admin' LIMIT 1),
    TRUE,
    CURRENT_TIMESTAMP
)
ON CONFLICT (username) DO UPDATE SET
    password_hash = EXCLUDED.password_hash,
    display_name = EXCLUDED.display_name,
    role = EXCLUDED.role,
    role_id = EXCLUDED.role_id,
    is_active = EXCLUDED.is_active,
    updated_at = EXCLUDED.updated_at;

INSERT INTO user_roles (user_id, role_id)
SELECT u.id, r.id
FROM users u
JOIN roles r ON r.code = 'admin'
WHERE u.username = 'admin'
ON CONFLICT DO NOTHING;

INSERT INTO role_permissions (role_id, perm_key)
SELECT r.id, v.perm_key
FROM roles r
JOIN (
    VALUES
('admin', '*'),
('management', 'all:view'),
('management', 'approval:view'),
('management', 'dashboard:view'),
('management', 'workbench:view'),
('sales_director', 'approval:handle'),
('sales_director', 'approval:view'),
('sales_director', 'contract:manage'),
('sales_director', 'contract:view'),
('sales_director', 'customer:manage'),
('sales_director', 'customer:view'),
('sales_director', 'dashboard:view'),
('sales_director', 'invoice:view'),
('sales_director', 'opportunity:manage'),
('sales_director', 'opportunity:view'),
('sales_director', 'project:view'),
('sales_director', 'receivable:manage'),
('sales_director', 'receivable:view'),
('sales_director', 'workbench:view'),
('project_director', 'approval:handle'),
('project_director', 'approval:view'),
('project_director', 'contract:view'),
('project_director', 'customer:view'),
('project_director', 'dashboard:view'),
('project_director', 'invoice:view'),
('project_director', 'opportunity:view'),
('project_director', 'project:manage'),
('project_director', 'receivable:view'),
('project_director', 'workbench:view'),
('sales', 'contract:manage'),
('sales', 'customer:manage'),
('sales', 'dashboard:view'),
('sales', 'opportunity:manage'),
('sales', 'project:view'),
('sales', 'receivable:manage'),
('sales', 'workbench:view'),
('pm', 'acceptance:manage'),
('pm', 'contract:view'),
('pm', 'customer:view'),
('pm', 'dashboard:view'),
('pm', 'implementation:manage'),
('pm', 'opportunity:view'),
('pm', 'project:manage'),
('pm', 'receivable:view'),
('pm', 'workbench:view'),
('implementer', 'acceptance:view'),
('implementer', 'contract:view'),
('implementer', 'customer:view'),
('implementer', 'dashboard:view'),
('implementer', 'implementation:manage'),
('implementer', 'issue:manage'),
('implementer', 'opportunity:view'),
('implementer', 'project:view'),
('implementer', 'receivable:view'),
('implementer', 'workbench:view'),
('finance', 'all:view'),
('finance', 'dashboard:view'),
('finance', 'invoice:manage'),
('finance', 'workbench:view')
) AS v(role_code, perm_key)
ON v.role_code = r.code
ON CONFLICT DO NOTHING;

CREATE INDEX IF NOT EXISTS idx_customer_contacts_customer ON customer_contacts(customer_id);
CREATE INDEX IF NOT EXISTS idx_customer_follow_ups_customer_followed ON customer_follow_ups(customer_id, followed_at);
CREATE INDEX IF NOT EXISTS idx_customer_follow_ups_next ON customer_follow_ups(customer_id, next_followup_at);
CREATE INDEX IF NOT EXISTS idx_customers_owner_tier ON customers(owner, tier);
CREATE INDEX IF NOT EXISTS idx_opportunities_stage_started ON opportunities(stage, stage_started_at);
CREATE INDEX IF NOT EXISTS idx_opportunities_owner_stage ON opportunities(owner, stage);
CREATE INDEX IF NOT EXISTS idx_contracts_opportunity ON contracts(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_projects_active_updated ON projects(deleted_at, updated_at);
CREATE INDEX IF NOT EXISTS idx_projects_active_status ON projects(deleted_at, status);
CREATE INDEX IF NOT EXISTS idx_projects_active_stage ON projects(deleted_at, current_stage);
CREATE INDEX IF NOT EXISTS idx_tasks_project_updated ON tasks(project_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_tasks_project_status_end ON tasks(project_id, status, planned_end);
CREATE INDEX IF NOT EXISTS idx_tasks_depends_on ON tasks(depends_on_task_id);
CREATE INDEX IF NOT EXISTS idx_milestones_project_due_status ON milestones(project_id, due_date, status);
CREATE INDEX IF NOT EXISTS idx_risks_project_level_status ON risks(project_id, level, status);
CREATE INDEX IF NOT EXISTS idx_risks_project_due_status ON risks(project_id, due_date, status);
CREATE INDEX IF NOT EXISTS idx_attachments_project_scope ON attachments(project_id, task_id, milestone_id);
CREATE INDEX IF NOT EXISTS idx_contracts_project_updated ON contracts(project_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_contracts_customer_updated ON contracts(customer_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_opportunities_customer_updated ON opportunities(customer_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_opportunities_status_updated ON opportunities(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_approvals_status_updated ON approvals(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_receivables_status_plan_date ON receivables(status, plan_date);
CREATE INDEX IF NOT EXISTS idx_invoices_contract_updated ON invoices(contract_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_project_members_project_user ON project_members(project_id, user_id);
CREATE INDEX IF NOT EXISTS idx_project_progress_project_created ON project_progress_entries(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_project_activity_project_created ON project_activity_logs(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_project_stage_logs_project_changed ON project_stage_logs(project_id, changed_at);
CREATE INDEX IF NOT EXISTS idx_project_delivery_phases_project_sort ON project_delivery_phases(project_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_phase_template_items_template_sort ON phase_template_items(template_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_project_phase_change_logs_project ON project_phase_change_logs(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ai_generation_scene_target ON ai_generation_logs(scene_code, target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_ai_generation_created ON ai_generation_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_ai_feedback_generation ON ai_feedback(generation_id, created_at);

COMMIT;
