import os
import re
import secrets
from datetime import date, datetime
from pathlib import Path
from typing import Any

from crm_constants import PROJECT_STAGE_LABELS


def max_upload_bytes() -> int:
    """单次请求体上限（含 multipart 上传），默认 200MB；可通过环境变量 MAX_UPLOAD_MB 调整。"""
    raw = os.getenv("MAX_UPLOAD_MB", "200").strip()
    try:
        mb = max(1, min(int(raw), 2048))
    except ValueError:
        mb = 200
    return mb * 1024 * 1024


def load_env_file(path: Path) -> bool:
    """从简单的 KEY=VALUE 文件加载环境变量；已有环境变量保持不变。"""
    if not path.exists():
        return False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value
    return True


def env_value(*keys: str, default: str = "") -> str:
    """按顺序读取环境变量，返回第一个非空值。"""
    for key in keys:
        value = os.getenv(key)
        if value is not None and value.strip():
            return value.strip()
    return default


def resolve_db_backend(explicit_backend: str | None = None, db_path: Path | None = None) -> str:
    """项目固定使用 PostgreSQL，保留该函数仅兼容旧调用点。"""
    return "postgres"


def resolve_app_port(raw_port: str | None = None) -> int:
    raw = (raw_port if raw_port is not None else os.getenv("APP_PORT", "3000")).strip()
    try:
        port = int(raw)
    except ValueError:
        return 3000
    if 1 <= port <= 65535:
        return port
    return 3000


def slug_role_code(name: str) -> str:
    base = re.sub(r"[^a-z0-9_]+", "_", (name or "").strip().lower())
    base = re.sub(r"_+", "_", base).strip("_")
    if not base or base[0].isdigit():
        return f"role_{secrets.token_hex(4)}"
    return base[:36]


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def format_file_size(n: Any) -> str:
    if n is None:
        return "—"
    try:
        size = int(n)
    except (TypeError, ValueError):
        return "—"
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def normalize_stage(stage: str) -> str:
    if stage in PROJECT_STAGE_LABELS:
        return stage
    return "init"


def parse_date_text(value: Any) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def like_kw(keyword: str) -> str:
    return f"%{keyword.strip()}%"


def in_clause_params(ids: list[int]) -> tuple[str, tuple[int, ...]]:
    """生成 IN 子句占位符，保持 SQLite/PostgreSQL 双后端兼容。"""
    if not ids:
        return "(NULL)", ()
    return f"({', '.join(['?'] * len(ids))})", tuple(ids)
