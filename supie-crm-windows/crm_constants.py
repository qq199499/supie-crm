from collections import OrderedDict


PROJECT_TYPE_LABELS = {
    "integration": "集成类",
    "development": "开发类",
    "implementation": "实施类",
}
PROJECT_STATUS_LABELS = {
    "not_started": "未开始",
    "in_progress": "进行中",
    "blocked": "项目暂停",
    "closed": "已完成",
}
TASK_STATUS_LABELS = {"todo": "待开始", "doing": "进行中", "done": "已完成", "blocked": "阻塞"}
PRIORITY_LABELS = {"high": "高", "medium": "中", "low": "低"}
MILESTONE_STATUS_LABELS = {"open": "未完成", "done": "已完成", "delayed": "延期"}
RISK_LEVEL_LABELS = {"high": "高", "medium": "中", "low": "低"}
RISK_STATUS_LABELS = {"open": "开放", "in_progress": "处理中", "closed": "已关闭"}
HEALTH_LABELS = {"red": "红", "yellow": "黄", "green": "绿"}
PROJECT_STAGE_LABELS = {
    "init": "立项",
    "requirement": "需求分析",
    "design": "方案设计",
    "delivery": "执行交付",
    "acceptance": "验收回款",
    "closed": "结项",
}
# 交付阶段（基于模板的业务阶段，与 project_stage_logs「阶段日志」区分）
PROJECT_PHASE_STATUS_LABELS = {
    "not_started": "未开始",
    "in_progress": "进行中",
    "done": "已完成",
    "skipped": "已跳过",
}
OPPORTUNITY_STATUS_LABELS = {"new": "新建", "tracking": "跟进中", "won": "赢单", "lost": "丢单"}
# 销售漏斗阶段（与《04-商机管理》OP-001 对齐；赢单/输单与 status 终态一致）
OPPORTUNITY_STAGE_LABELS = {
    "lead": "线索",
    "validate": "验证",
    "proposal": "方案",
    "negotiate": "谈判",
    "won": "赢单",
    "lost": "输单",
}
OPPORTUNITY_STAGE_ORDER = ("lead", "validate", "proposal", "negotiate", "won", "lost")
OPEN_OPPORTUNITY_STAGES = frozenset({"lead", "validate", "proposal", "negotiate"})
AMOUNT_CONFIDENCE_LABELS = {"high": "高", "medium": "中", "low": "低"}
# 丢单原因：枚举值 + 展示名（OP-010）
LOST_REASON_LABELS = {
    "price": "价格",
    "product": "产品/方案",
    "competitor": "竞品",
    "relationship": "关系/客户",
    "budget": "预算/流程",
    "cancel": "客户取消",
    "other": "其他",
}
# 商机待办：阶段停滞天数、预计关单临近天数（TD-O01 / TD-O02）
OPPORTUNITY_STALL_DAYS_DEFAULT = 14
OPPORTUNITY_CLOSE_SOON_DAYS_DEFAULT = 7
# 客户经营：分级（CU-020）、状态（CU-021）、跟进方式
CUSTOMER_TIER_LABELS = {"strategic": "战略", "important": "重要", "normal": "普通"}
CUSTOMER_STATUS_LABELS = {
    "potential": "潜在",
    "won": "成交",
    "lost": "流失",
    "frozen": "冻结",
}
CUSTOMER_FOLLOW_METHOD_LABELS = {
    "phone": "电话",
    "visit": "拜访",
    "email": "邮件",
    "other": "其他",
}
# TD-C02：长期未跟进天数（与《02-待办数据来源表》一致，可后续改配置）
CUSTOMER_STALE_FOLLOW_DAYS = 14


def opportunity_status_from_stage(stage: str) -> str:
    """与 opportunities.status 对齐：线索→新建，漏斗中→跟进中，终态保持。"""
    if stage == "lead":
        return "new"
    if stage in ("won", "lost"):
        return stage
    return "tracking"


def opportunity_stage_is_rollback(from_stage: str, to_stage: str) -> bool:
    """开放阶段之间的回退（OP-003：回退需备注）。"""
    try:
        i_f = OPPORTUNITY_STAGE_ORDER.index(from_stage)
        i_t = OPPORTUNITY_STAGE_ORDER.index(to_stage)
    except ValueError:
        return False
    if from_stage not in OPEN_OPPORTUNITY_STAGES or to_stage not in OPEN_OPPORTUNITY_STAGES:
        return False
    return i_t < i_f


CONTRACT_STATUS_LABELS = {"draft": "草拟", "signed": "已签约", "executing": "执行中", "closed": "已归档"}
# 开票状态（TD-K02：待开具）
INVOICE_STATUS_LABELS = {
    "pending": "待开具",
    "issued": "已开具",
    "invalid": "已作废",
    "red_flush": "已红冲",
}
# TD-K03：截至今日应收计划与实际回款偏差率阈值（与《02-待办数据来源表》草案一致，可配置化）
CONTRACT_DEVIATION_THRESHOLD_PCT = 0.10
APPROVAL_STATUS_LABELS = {"pending": "待审批", "approved": "已通过", "rejected": "已驳回"}
RECEIVABLE_STATUS_LABELS = {"planned": "计划中", "partial": "部分回款", "received": "已回款", "overdue": "逾期"}
ROLE_LABELS = {
    "management": "管理层",
    "sales_director": "销售总监",
    "project_director": "项目总监",
    "sales": "销售",
    "pm": "项目经理",
    "implementer": "实施工程师",
    "finance": "财务",
    "admin": "管理员",
    "normal": "普通用户",
}

TASK_STATUS_IMPORT_MAP = {
    "待开始": "todo",
    "进行中": "doing",
    "已完成": "done",
    "阻塞": "blocked",
    "todo": "todo",
    "doing": "doing",
    "done": "done",
    "blocked": "blocked",
}
PRIORITY_IMPORT_MAP = {
    "高": "high",
    "中": "medium",
    "低": "low",
    "high": "high",
    "medium": "medium",
    "low": "low",
}
MILESTONE_STATUS_IMPORT_MAP = {
    "未完成": "open",
    "已完成": "done",
    "延期": "delayed",
    "open": "open",
    "done": "done",
    "delayed": "delayed",
}

# 项目经理角色在 roles 表中的 code（与 ROLE_LABELS「项目经理」对应）
PM_ROLE_CODE = "pm"
# 项目软删除后在回收站保留天数，逾期自动物理删除
PROJECT_RECYCLE_DAYS = 30

ROLE_PERMISSIONS = {
    "admin": {"*"},
    "management": {"all:view", "approval:view", "dashboard:view", "workbench:view"},
    "sales_director": {
        "workbench:view",
        "dashboard:view",
        "approval:view",
        "approval:handle",
        "customer:view",
        "customer:manage",
        "opportunity:view",
        "opportunity:manage",
        "contract:view",
        "contract:manage",
        "receivable:view",
        "receivable:manage",
        "invoice:view",
        "project:view",
    },
    "project_director": {
        "workbench:view",
        "dashboard:view",
        "approval:view",
        "approval:handle",
        "customer:view",
        "opportunity:view",
        "contract:view",
        "receivable:view",
        "invoice:view",
        "project:manage",
    },
    "sales": {
        "workbench:view",
        "dashboard:view",
        "customer:manage",
        "opportunity:manage",
        "contract:manage",
        "receivable:manage",
        "project:view",
    },
    "pm": {
        "workbench:view",
        "dashboard:view",
        "project:manage",
        "implementation:manage",
        "acceptance:manage",
        "customer:view",
        "opportunity:view",
        "contract:view",
        "receivable:view",
    },
    "implementer": {
        "workbench:view",
        "dashboard:view",
        "implementation:manage",
        "issue:manage",
        "project:view",
        "acceptance:view",
        "customer:view",
        "opportunity:view",
        "contract:view",
        "receivable:view",
    },
    "finance": {"all:view", "workbench:view", "dashboard:view", "invoice:manage"},
    "normal": set(),
}

# 角色授权时可勾选的权限项（与 has_module_permission(module, action) 的 module:action 一致）
PERMISSION_CATALOG: list[tuple[str, str, str]] = [
    ("workbench:view", "工作台", "首页"),
    ("dashboard:view", "项目总览", "首页"),
    ("all:view", "全局搜索（跨模块）", "首页"),
    ("project:view", "项目 — 查看", "业务"),
    ("project:manage", "项目 — 管理", "业务"),
    ("customer:view", "客户 — 查看", "业务"),
    ("customer:manage", "客户 — 管理", "业务"),
    ("opportunity:view", "商机 — 查看", "业务"),
    ("opportunity:manage", "商机 — 管理", "业务"),
    ("contract:view", "合同 — 查看", "业务"),
    ("contract:manage", "合同 — 管理", "业务"),
    ("implementation:manage", "实施交付 — 管理", "业务"),
    ("acceptance:view", "验收 — 查看", "业务"),
    ("acceptance:manage", "验收 — 管理", "业务"),
    ("issue:manage", "风险/问题 — 管理", "业务"),
    ("receivable:view", "回款 — 查看", "财务与流程"),
    ("receivable:manage", "回款 — 管理", "财务与流程"),
    ("invoice:view", "开票 — 查看", "财务与流程"),
    ("invoice:manage", "开票 — 管理", "财务与流程"),
    ("approval:view", "审批 — 查看", "财务与流程"),
    ("approval:handle", "审批 — 处理", "财务与流程"),
    ("*", "全部权限（所有模块与操作）", "其他"),
]

VALID_PERMISSION_KEYS = {key for key, _, _ in PERMISSION_CATALOG} | {"*"}


def permission_catalog_by_group() -> OrderedDict[str, list[tuple[str, str]]]:
    groups: OrderedDict[str, list[tuple[str, str]]] = OrderedDict()
    for key, label, group in PERMISSION_CATALOG:
        groups.setdefault(group, []).append((key, label))
    return groups
