from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None


SESSION_FILE_PATTERN = "rollout-*.jsonl"
SESSION_ID_RE = re.compile(r"([0-9a-f-]{36})\.jsonl$")
FILE_DIRECTIVE_RE = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+)$")
MOVE_DIRECTIVE_RE = re.compile(r"^\*\*\* Move to: (.+)$")
NONZERO_EXIT_RE = re.compile(r"Process exited with code (\d+)")
COMMAND_NOT_FOUND_RE = re.compile(r"(command not found|is not recognized)", re.IGNORECASE)
MISSING_FILE_RE = re.compile(r"(No such file|not found)", re.IGNORECASE)
IMPORT_ERROR_RE = re.compile(r"(ModuleNotFoundError|ImportError)", re.IGNORECASE)
SYNTAX_ERROR_RE = re.compile(r"SyntaxError", re.IGNORECASE)
TRACEBACK_RE = re.compile(r"Traceback \(most recent call last\):")
JSON_ONLY_PROMPT = "RESPOND WITH ONLY A VALID JSON OBJECT"
DEFAULT_TZ_NAME = "UTC"

POSITIVE_MARKERS = (
    "很好",
    "很符合",
    "方向是正确的",
    "我相信你",
    "继续了",
    "继续改",
    "好的",
    "ok",
    "okay",
)
NEGATIVE_MARKERS = (
    "不满意",
    "不对",
    "问题",
    "差距很大",
    "去掉",
    "删除",
    "为什么",
    "不完善",
)
REPEATED_INSTRUCTION_RULES = {
    "保留首页既有风格": lambda message: "不改变主页面的风格" in message or "主页面风格已经不变" in message,
    "统一详情页/列表页/工作台视觉语言": lambda message: "视觉语言" in message and "统一" in message,
    "统一按钮/表单/卡片/空状态/提示样式": lambda message: "统一按钮" in message and "空状态" in message,
    "按验收反馈逐项修正": lambda message: "一项一项验收" in message or "继续改" in message,
}


@dataclass
class SessionRecord:
    session_id: str
    path: Path
    start_time: dt.datetime
    end_time: dt.datetime
    user_messages: list[str]
    assistant_messages: list[str]
    tool_counts: Counter = field(default_factory=Counter)
    tool_errors: Counter = field(default_factory=Counter)
    files_modified: set[str] = field(default_factory=set)
    languages: Counter = field(default_factory=Counter)
    lines_added: int = 0
    lines_removed: int = 0
    git_commits: int = 0
    git_pushes: int = 0
    used_subagents: bool = False

    @property
    def duration_minutes(self) -> float:
        return max((self.end_time - self.start_time).total_seconds() / 60.0, 0.0)

    @property
    def first_prompt(self) -> str:
        return self.user_messages[0] if self.user_messages else ""

    @property
    def summary(self) -> str:
        goal = shorten(self.first_prompt, 54)
        outcome = "已落地" if self.files_modified else "已响应"
        if self.tool_errors:
            outcome = "经历修正"
        if detect_negative_signal(self.user_messages):
            outcome = "反复迭代"
        return f"{goal}，{outcome}"


def shorten(text: str, max_length: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_length:
        return compact
    return compact[: max_length - 1].rstrip() + "…"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate an HTML insights report from Codex session logs.")
    parser.add_argument(
        "--sessions-root",
        default="/home/agent/.codex/sessions",
        help="Root directory containing session JSONL files.",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Local date to analyze in YYYY-MM-DD format. Defaults to today in the selected timezone.",
    )
    parser.add_argument(
        "--timezone",
        default=DEFAULT_TZ_NAME,
        help="IANA timezone name used for date filtering.",
    )
    parser.add_argument(
        "--output",
        default="insights-report.html",
        help="Path to the generated HTML report.",
    )
    return parser.parse_args()


def resolve_timezone(name: str) -> dt.tzinfo:
    if ZoneInfo is None:
        return dt.timezone.utc
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(DEFAULT_TZ_NAME)


def parse_target_date(date_text: str | None, tz: dt.tzinfo) -> dt.date:
    if date_text:
        return dt.date.fromisoformat(date_text)
    return dt.datetime.now(tz=tz).date()


def collect_session_paths(sessions_root: Path, target_date: dt.date) -> list[Path]:
    day_dir = sessions_root / f"{target_date.year:04d}" / f"{target_date.month:02d}" / f"{target_date.day:02d}"
    if not day_dir.exists():
        return []
    return sorted(day_dir.glob(SESSION_FILE_PATTERN))


def load_sessions(session_paths: Iterable[Path], tz: dt.tzinfo, target_date: dt.date) -> list[SessionRecord]:
    sessions: list[SessionRecord] = []
    for path in session_paths:
        session = parse_session(path, tz)
        if session is None:
            continue
        if session.start_time.astimezone(tz).date() != target_date:
            continue
        if session.duration_minutes < 1:
            continue
        if len(session.user_messages) < 2:
            continue
        if any(JSON_ONLY_PROMPT in message for message in session.user_messages):
            continue
        sessions.append(session)
    return dedupe_sessions(sessions)


def dedupe_sessions(sessions: list[SessionRecord]) -> list[SessionRecord]:
    best_by_id: dict[str, SessionRecord] = {}
    for session in sessions:
        current = best_by_id.get(session.session_id)
        if current is None:
            best_by_id[session.session_id] = session
            continue
        current_key = (len(current.user_messages), current.duration_minutes)
        session_key = (len(session.user_messages), session.duration_minutes)
        if session_key > current_key:
            best_by_id[session.session_id] = session
    return sorted(best_by_id.values(), key=lambda item: item.start_time)


def parse_session(path: Path, tz: dt.tzinfo) -> SessionRecord | None:
    session_id = extract_session_id(path)
    start_time: dt.datetime | None = None
    end_time: dt.datetime | None = None
    user_messages: list[str] = []
    assistant_messages: list[str] = []
    tool_counts: Counter = Counter()
    tool_errors: Counter = Counter()
    files_modified: set[str] = set()
    languages: Counter = Counter()
    lines_added = 0
    lines_removed = 0
    git_commits = 0
    git_pushes = 0
    used_subagents = False
    is_subagent = False

    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            event = json.loads(raw_line)
            timestamp = parse_timestamp(event.get("timestamp"))
            if timestamp is not None:
                timestamp = timestamp.astimezone(tz)
                if start_time is None or timestamp < start_time:
                    start_time = timestamp
                if end_time is None or timestamp > end_time:
                    end_time = timestamp

            event_type = event.get("type")
            payload = event.get("payload") or {}
            payload_type = payload.get("type")

            if event_type == "session_meta":
                meta = payload
                if meta.get("id") == session_id:
                    is_subagent = bool(meta.get("forked_from_id")) or bool(meta.get("agent_role"))
                    source = meta.get("source")
                    if isinstance(source, dict) and "subagent" in source:
                        is_subagent = True
                continue

            if event_type == "event_msg" and payload_type == "user_message":
                message = (payload.get("message") or "").strip()
                if message:
                    user_messages.append(message)
                continue

            if event_type == "event_msg" and payload_type == "task_complete":
                message = (payload.get("last_agent_message") or "").strip()
                if message:
                    assistant_messages.append(message)
                continue

            if event_type != "response_item":
                continue

            if payload_type == "agent_message":
                message = (payload.get("message") or "").strip()
                if message:
                    assistant_messages.append(message)
                continue

            if payload_type == "function_call":
                tool_name = payload.get("name") or "unknown"
                tool_counts[tool_name] += 1
                arguments = parse_tool_arguments(payload.get("arguments"))
                command = arguments.get("cmd", "") if isinstance(arguments, dict) else ""
                git_commits += int(is_git_commit(command))
                git_pushes += int(is_git_push(command))
                if tool_name == "spawn_agent":
                    used_subagents = True
                continue

            if payload_type == "custom_tool_call":
                tool_name = payload.get("name") or "custom_tool"
                tool_counts[tool_name] += 1
                if tool_name == "apply_patch":
                    patch_stats = parse_apply_patch_stats(payload.get("input", ""))
                    files_modified.update(patch_stats["files"])
                    lines_added += patch_stats["added"]
                    lines_removed += patch_stats["removed"]
                    for ext in infer_languages_from_files(patch_stats["files"]).elements():
                        languages[ext] += 1
                continue

            if payload_type == "function_call_output":
                error_category = classify_exec_output(payload.get("output", ""))
                if error_category:
                    tool_errors[error_category] += 1
                continue

            if payload_type == "custom_tool_call_output":
                error_category = classify_custom_tool_output(payload.get("output", ""))
                if error_category:
                    tool_errors[error_category] += 1
                continue

    if is_subagent or start_time is None or end_time is None:
        return None

    return SessionRecord(
        session_id=session_id,
        path=path,
        start_time=start_time,
        end_time=end_time,
        user_messages=user_messages,
        assistant_messages=assistant_messages,
        tool_counts=tool_counts,
        tool_errors=tool_errors,
        files_modified=files_modified,
        languages=languages,
        lines_added=lines_added,
        lines_removed=lines_removed,
        git_commits=git_commits,
        git_pushes=git_pushes,
        used_subagents=used_subagents,
    )


def extract_session_id(path: Path) -> str:
    match = SESSION_ID_RE.search(path.name)
    if match is None:
        raise ValueError(f"Could not parse session id from {path}")
    return match.group(1)


def parse_timestamp(timestamp_text: str | None) -> dt.datetime | None:
    if not timestamp_text:
        return None
    return dt.datetime.fromisoformat(timestamp_text.replace("Z", "+00:00"))


def parse_tool_arguments(arguments: str | dict | None) -> dict:
    if isinstance(arguments, dict):
        return arguments
    if not arguments:
        return {}
    try:
        return json.loads(arguments)
    except json.JSONDecodeError:
        return {}


def is_git_commit(command: str) -> bool:
    compact = command.strip()
    return compact.startswith("git commit") or " git commit" in compact


def is_git_push(command: str) -> bool:
    compact = command.strip()
    return compact.startswith("git push") or " git push" in compact


def parse_apply_patch_stats(patch_text: str) -> dict[str, object]:
    files: set[str] = set()
    added = 0
    removed = 0
    for line in patch_text.splitlines():
        file_match = FILE_DIRECTIVE_RE.match(line)
        if file_match:
            files.add(file_match.group(2).strip())
            continue
        move_match = MOVE_DIRECTIVE_RE.match(line)
        if move_match:
            files.add(move_match.group(1).strip())
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
            continue
        if line.startswith("-"):
            removed += 1
    return {"files": files, "added": added, "removed": removed}


def infer_languages_from_files(files: Iterable[str]) -> Counter:
    mapping = {
        ".py": "Python",
        ".ts": "TypeScript",
        ".tsx": "TypeScript",
        ".js": "JavaScript",
        ".jsx": "JavaScript",
        ".md": "Markdown",
        ".json": "JSON",
        ".yml": "YAML",
        ".yaml": "YAML",
        ".sh": "Shell",
        ".css": "CSS",
        ".html": "HTML",
    }
    languages: Counter = Counter()
    for file_path in files:
        suffix = Path(file_path).suffix.lower()
        language = mapping.get(suffix)
        if language:
            languages[language] += 1
    return languages


def normalize_output_text(output: object) -> str:
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            if isinstance(item, dict):
                item_type = item.get("type")
                if item_type == "output_text":
                    parts.append(str(item.get("text", "")))
                elif item_type == "input_text":
                    parts.append(str(item.get("text", "")))
                elif item_type == "input_image":
                    parts.append("[image]")
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    if isinstance(output, dict):
        return json.dumps(output, ensure_ascii=False)
    if output is None:
        return ""
    return str(output)


def classify_exec_output(output: object) -> str | None:
    output_text = normalize_output_text(output)
    match = NONZERO_EXIT_RE.search(output_text)
    if not match:
        return None
    if match.group(1) == "0":
        return None
    if COMMAND_NOT_FOUND_RE.search(output_text):
        return "command_not_found"
    if IMPORT_ERROR_RE.search(output_text):
        return "import_error"
    if SYNTAX_ERROR_RE.search(output_text):
        return "syntax_error"
    if TRACEBACK_RE.search(output_text):
        return "python_exception"
    if MISSING_FILE_RE.search(output_text):
        return "missing_file"
    return "shell_nonzero"


def classify_custom_tool_output(output: object) -> str | None:
    output_text = normalize_output_text(output)
    try:
        payload = json.loads(output_text)
    except json.JSONDecodeError:
        return "tool_output_parse_error"
    metadata = payload.get("metadata") or {}
    exit_code = metadata.get("exit_code")
    if exit_code in (None, 0):
        return None
    return "tool_nonzero"


def build_report_data(sessions: list[SessionRecord], target_date: dt.date, tz_name: str) -> dict:
    all_user_messages = [message for session in sessions for message in session.user_messages]
    total_duration_hours = round(sum(session.duration_minutes for session in sessions) / 60.0, 1)
    tool_counts = Counter()
    languages = Counter()
    error_counts = Counter()
    area_counts = Counter()
    satisfaction_counts = Counter()
    repeated_instructions = Counter()

    for session in sessions:
        tool_counts.update(session.tool_counts)
        languages.update(session.languages)
        error_counts.update(session.tool_errors)
        area_counts.update(classify_project_areas(session))
        satisfaction_counts[classify_satisfaction(session.user_messages)] += 1
        repeated_instructions.update(extract_repeated_instructions(session.user_messages))

    overlap = detect_parallel_overlap(sessions)
    top_areas = area_counts.most_common(5)
    top_tools = tool_counts.most_common(8)
    top_languages = languages.most_common(6)
    total_messages = sum(len(session.user_messages) for session in sessions)
    total_files_modified = sum(len(session.files_modified) for session in sessions)
    total_lines_added = sum(session.lines_added for session in sessions)
    total_lines_removed = sum(session.lines_removed for session in sessions)
    sessions_using_subagents = sum(1 for session in sessions if session.used_subagents)

    return {
        "target_date": target_date.isoformat(),
        "timezone": tz_name,
        "session_count": len(sessions),
        "total_messages": total_messages,
        "total_duration_hours": total_duration_hours,
        "total_files_modified": total_files_modified,
        "total_lines_added": total_lines_added,
        "total_lines_removed": total_lines_removed,
        "git_commits": sum(session.git_commits for session in sessions),
        "git_pushes": sum(session.git_pushes for session in sessions),
        "sessions_using_subagents": sessions_using_subagents,
        "tool_counts": top_tools,
        "language_counts": top_languages,
        "error_counts": error_counts.most_common(6),
        "project_areas": build_project_area_cards(top_areas),
        "interaction_style": build_interaction_style(sessions, sessions_using_subagents),
        "what_works": build_workflows(top_areas, sessions),
        "friction_analysis": build_friction_analysis(error_counts, sessions),
        "suggestions": build_suggestions(repeated_instructions, top_areas),
        "opportunities": build_opportunities(top_areas),
        "fun_ending": build_fun_ending(sessions),
        "at_a_glance": build_at_a_glance(top_areas, error_counts, sessions_using_subagents, repeated_instructions),
        "satisfaction_counts": list(satisfaction_counts.items()),
        "overlap": overlap,
        "session_summaries": [
            {
                "start": session.start_time.strftime("%H:%M"),
                "duration": f"{session.duration_minutes:.0f} 分钟",
                "summary": session.summary,
                "files": len(session.files_modified),
                "errors": sum(session.tool_errors.values()),
            }
            for session in sessions
        ],
        "user_message_examples": [shorten(message, 88) for message in all_user_messages[:8]],
    }


def classify_project_areas(session: SessionRecord) -> Counter:
    text = " ".join(session.user_messages)
    counts = Counter()
    rules = {
        "界面与体验优化": ("ui", "视觉", "详情页", "列表页", "工作台", "页面", "弹窗", "demo"),
        "产品文档与方案": ("prd", "文档", "方案", "说明", "报告", "测试用例"),
        "部署与运行维护": ("启动", "端口", "服务", "systemd", "脚本", "重启", "打包"),
        "数据与配置调整": ("数据库", "PG_HOST", "PG_PORT", "连接配置"),
        "AI 功能规划取舍": ("ai", "智能", "检索中心", "智能化"),
        "缺陷修复与验收": ("问题", "不对", "继续改", "验收", "滚动条", "刷新"),
    }
    lowered = text.lower()
    for area, needles in rules.items():
        if any(needle.lower() in lowered for needle in needles):
            counts[area] += 1
    if not counts:
        counts["轻量交互与确认"] += 1
    return counts


def classify_satisfaction(messages: list[str]) -> str:
    if detect_negative_signal(messages):
        return "存在不满"
    lowered = " ".join(messages).lower()
    if any(marker in lowered for marker in POSITIVE_MARKERS):
        return "明确正向"
    return "中性推进"


def detect_negative_signal(messages: list[str]) -> bool:
    lowered = " ".join(messages).lower()
    return any(marker in lowered for marker in NEGATIVE_MARKERS)


def extract_repeated_instructions(messages: list[str]) -> Counter:
    counts = Counter()
    for message in messages:
        for label, rule in REPEATED_INSTRUCTION_RULES.items():
            if rule(message):
                counts[label] += 1
    return counts


def build_project_area_cards(area_counts: list[tuple[str, int]]) -> list[dict[str, str | int]]:
    descriptions = {
        "界面与体验优化": "今天最重的工作负载集中在页面视觉统一、详情页布局修整和工作台体验打磨，AI Agent 更像执行型设计研发搭档。",
        "产品文档与方案": "你会让 Agent 先把现状梳理成 PRD、说明文档或方案，再继续推进实现，这让后续协作有了统一基线。",
        "部署与运行维护": "除了改代码，你也频繁要求 Agent 兜底启动、打包、重启和自启动，把交付链路拉到了可运行层面。",
        "数据与配置调整": "涉及数据库连接和环境参数时，你倾向于一次性要求改全，避免残留旧配置。",
        "AI 功能规划取舍": "你会先让 Agent 发散思考智能化方向，再基于成熟度迅速收缩范围，保持产品节奏可控。",
        "缺陷修复与验收": "你采用明确的问题单式验收，发现偏差后要求逐项修正，直到界面和行为符合预期。",
        "轻量交互与确认": "少量会话是快速确认或服务操作，目标清晰，执行节奏很短。",
    }
    return [
        {"name": name, "session_count": count, "description": descriptions.get(name, "围绕该主题持续推进工作。")}
        for name, count in area_counts
    ]


def build_interaction_style(sessions: list[SessionRecord], sessions_using_subagents: int) -> dict[str, str]:
    iterative_sessions = sum(1 for session in sessions if len(session.user_messages) >= 4)
    dissatisfied_sessions = sum(1 for session in sessions if detect_negative_signal(session.user_messages))
    narrative = (
        f"你今天最典型的模式是 **先给方向，再用连续验收把结果拉回你的标准**。"
        f" {iterative_sessions} 个会话出现了 4 条以上追问，说明你更习惯在真实页面、真实服务和真实文档上迭代，而不是一次性把规格写死。\n\n"
        f"当结果偏离预期时，你会非常直接地点出问题，例如视觉不够醒目、详情页布局有空白、某个模块成熟度不够就先移除。"
        f" 同时，只要方向正确，你也愿意把较大执行权限交给 Agent；今天有 {sessions_using_subagents} 个顶层会话显式启用了子 Agent 或并行分工。"
    )
    if dissatisfied_sessions:
        key_pattern = "高频验收驱动型协作：先放权推进，再用明确反馈收紧结果。"
    else:
        key_pattern = "目标驱动型协作：给定方向后允许 Agent 自主推进。"
    return {"narrative": narrative, "key_pattern": key_pattern}


def build_workflows(top_areas: list[tuple[str, int]], sessions: list[SessionRecord]) -> dict[str, object]:
    titles = [
        ("Demo 先行", "你会先让 Agent 产出方案或 demo，再决定是否全量铺开。这样能在视觉和方向问题上更早止损。"),
        ("逐项验收", "你把问题拆成一个个可验证的小缺陷，Agent 每次只修正一批，反馈链路短，回归也更清楚。"),
        ("运行闭环", "你不只要代码修改，还要求启动服务、打包文件、处理环境差异，确保改动能被真正验证。"),
    ]
    if top_areas and top_areas[0][0] == "AI 功能规划取舍":
        titles[0] = ("先发散后收缩", "你先让 Agent 把 AI 化可能性铺开，再快速剔除不成熟模块，避免为概念买单。")
    return {
        "intro": "今天最有效的并不是单次回答，而是几种重复出现的工作流。",
        "impressive_workflows": [{"title": title, "description": description} for title, description in titles],
        "iterative_sessions": sum(1 for session in sessions if len(session.user_messages) >= 4),
    }


def build_friction_analysis(error_counts: Counter, sessions: list[SessionRecord]) -> dict[str, object]:
    examples = [shorten(message, 44) for session in sessions for message in session.user_messages if detect_negative_signal([message])]
    categories = [
        {
            "category": "视觉预期偏差",
            "description": "你对页面层级、按钮显著性和布局完整性要求很高，但这些审美约束如果不提前固化，Agent 容易按自己的默认风格偏航。",
            "examples": examples[:2] or ["界面细节反馈主要集中在详情页观感和视觉强调不足。"] * 2,
        },
        {
            "category": "环境与运行链路",
            "description": "今天多次穿插启动端口、重启服务、打包和数据库配置修改，说明开发任务经常被环境稳定性打断。",
            "examples": [
                "你多次要求直接启动 3000 端口服务，说明运行入口还不够顺手。",
                "数据库连接配置和自启动脚本都需要 Agent 介入，环境知识还没有完全沉淀。",
            ],
        },
        {
            "category": "范围变更偏快",
            "description": "你会在推进中快速做范围裁剪，这很务实，但如果缺少阶段性冻结点，Agent 容易先做后删，吞掉执行时间。",
            "examples": [
                "智能化能力刚进入规划和 P0/P1 分工，又被要求撤掉不成熟模块。",
                "UI 优化先要整套完成，随后又回到逐页逐问题验收模式。",
            ],
        },
    ]
    if error_counts:
        categories[1]["examples"][1] = f"日志里出现了 {sum(error_counts.values())} 次工具失败，环境问题会放大交付摩擦。"
    return {"intro": "摩擦点主要不是目标不清，而是标准、环境和范围切换在同一天里同时发生。", "categories": categories}


def build_suggestions(repeated_instructions: Counter, top_areas: list[tuple[str, int]]) -> dict[str, object]:
    repeated_items = repeated_instructions.most_common(3)
    system_prompt_additions = []
    for label, count in repeated_items:
        system_prompt_additions.append(
            {
                "addition": f"{label}；若任务涉及 UI，先对照现有主页面与已确认 demo，再提交改动。",
                "why": f"这条要求今天至少出现了 {count} 次，已经是你的稳定协作偏好。",
                "prompt_scaffold": "加入 `## 界面交付约束` 章节。",
            }
        )
    if not system_prompt_additions:
        system_prompt_additions.append(
            {
                "addition": "遇到 UI 或交互修改时，先列出不变约束、对齐样例和验收清单，再开始实现。",
                "why": "这样能减少默认风格带来的返工。",
                "prompt_scaffold": "加入 `## 实现前确认` 章节。",
            }
        )

    feature_choices = [
        {
            "feature": "Custom Skills",
            "one_liner": "把反复出现的工作流做成一条命令可复用的技能。",
            "why_for_you": "你今天反复切换产品经理、UI 经理、测试经理和项目经理角色，适合把这些流程固化下来。",
            "example_code": "codex skills add ui_acceptance ./skills/ui_acceptance/SKILL.md",
        },
        {
            "feature": "Hooks",
            "one_liner": "在关键阶段自动执行命令，例如测试、截图或打包。",
            "why_for_you": "你经常要求“该做的事情不要落下”，Hook 能把这些兜底动作自动化。",
            "example_code": "[hooks.post_apply_patch]\ncommand = \"pytest -q\"",
        },
        {
            "feature": "Headless Mode",
            "one_liner": "把启动服务、回归测试、生成报告这些动作放进脚本化流水线。",
            "why_for_you": "你今天有多次启动/重启/打包需求，适合收敛成稳定的无交互入口。",
            "example_code": "codex run --headless \"启动服务并执行回归检查\"",
        },
    ]
    if top_areas and top_areas[0][0] == "AI 功能规划取舍":
        feature_choices[2]["why_for_you"] = "规划类和实现类任务切换很多，用脚本入口更容易做阶段冻结和回滚。"

    usage_patterns = [
        {
            "title": "先锁不变项",
            "suggestion": "每次 UI 任务都先写出禁止修改的页面和必须对齐的样例。",
            "detail": "你已经多次强调首页风格不能动、详情页与 demo 要一致。把这些约束放在最前面，Agent 会少走很多默认设计路径。",
            "copyable_prompt": "先不要改代码。先列出本次任务的 1) 不允许改变的页面/模块 2) 必须对齐的参考页面 3) 验收清单。确认后再开始实现。",
        },
        {
            "title": "把验收拆小",
            "suggestion": "继续保持逐项验收，但要求每轮只改一个页面簇。",
            "detail": "你的反馈很具体，这是优势。再进一步，把一次改动限制在单个页面簇或单类交互上，回归范围会更稳。",
            "copyable_prompt": "这轮只处理一个页面簇。先说明会改哪些文件、验证哪些行为、不会波及哪些页面；完成后给我一个最小回归清单。",
        },
        {
            "title": "环境动作模板化",
            "suggestion": "把启动、重启、打包和数据库检查收敛成固定脚本。",
            "detail": "今天运行类请求多次打断主线开发。把这些动作抽成脚本后，Agent 以后只需要调用固定入口，不用每次重新探索。",
            "copyable_prompt": "把本项目所有常用运行命令整理成 1 个入口脚本，包含启动、停止、重启、健康检查和打包，并补 1 个测试验证脚本存在性。",
        },
    ]
    return {
        "system_prompt_additions": system_prompt_additions,
        "features_to_try": feature_choices,
        "usage_patterns": usage_patterns,
    }


def build_opportunities(top_areas: list[tuple[str, int]]) -> dict[str, object]:
    first_area = top_areas[0][0] if top_areas else "界面与体验优化"
    opportunities = [
        {
            "title": "持续 UI 回归",
            "whats_possible": "把关键页面截图、布局校验和浏览器对比做成固定回归流。以后每次 UI 改动后，Agent 可以自动产出差异说明而不是等你人工发现。",
            "how_to_try": "结合 Hooks、浏览器自动化和一套固定截图基线。",
            "copyable_prompt": "以后凡是改动详情页、列表页或工作台，都自动打开页面截图，对照已确认 demo 输出差异清单，并在修复后重新截图确认。",
        },
        {
            "title": "角色化验收流水线",
            "whats_possible": "你已经自然地在用产品经理、UI 经理、测试经理这些角色。下一步可以把每个角色的产出物、输入和退出条件写死，形成稳定的多阶段工作流。",
            "how_to_try": "把角色职责做成 Skills，再让主 Agent 串联执行。",
            "copyable_prompt": "把“产品方案确认 -> UI 方案确认 -> 实现 -> 浏览器回归 -> 文档更新”做成固定流水线，每一步都输出一段可验收摘要。",
        },
        {
            "title": "产品成熟度闸门",
            "whats_possible": "像智能检索中心这类模块，可以先经过成熟度闸门再决定是否进主分支。Agent 能先做可行性、依赖和回滚评估，再进入正式开发。",
            "how_to_try": "在需求开始前先运行一个轻量立项模板。",
            "copyable_prompt": "新功能先不要开发。先输出成熟度评估：目标、依赖、最小可行范围、回滚成本、哪些条件不满足就不立项。",
        },
    ]
    if first_area == "部署与运行维护":
        opportunities[0]["title"] = "持续运行检查"
        opportunities[0]["whats_possible"] = "把服务拉起、端口探测和健康检查变成自动守护流程。以后 Agent 不只会启动服务，还能判断是不是稳定可用。"
    return {"intro": "你已经在把 Agent 当成执行搭档，下一阶段值得投入的是把这些搭档动作产品化。", "opportunities": opportunities}


def build_fun_ending(sessions: list[SessionRecord]) -> dict[str, str]:
    for session in sessions:
        joined = " ".join(session.user_messages)
        if "智能检索中心" in joined and "去掉" in joined:
            return {
                "headline": "最有质感的一幕，是你在推进 AI 功能时又果断把“智能检索中心”砍掉。",
                "detail": "这不是反复横跳，而是很典型的产品判断：先允许探索，再在成熟度不足时立刻止损。",
            }
    return {
        "headline": "今天的会话很像一场真实项目日常，而不是一串零散指令。",
        "detail": "从文档、视觉、启动到修 bug，你一直在把 Agent 往“能交付”的方向拉。",
    }


def build_at_a_glance(
    top_areas: list[tuple[str, int]],
    error_counts: Counter,
    sessions_using_subagents: int,
    repeated_instructions: Counter,
) -> dict[str, str]:
    leading_area = top_areas[0][0] if top_areas else "界面与体验优化"
    repeated_hint = repeated_instructions.most_common(1)[0][0] if repeated_instructions else "先锁定不变约束"
    return {
        "whats_working": f"你最强的地方是把 Agent 放进真实交付链路里。今天围绕“{leading_area}”反复推进，并且在方向明确时愿意放权给 Agent 或子 Agent，执行速度很快。",
        "whats_hindering": f"真正拖慢你的不是目标，而是标准和环境同时波动。视觉预期、运行入口、模块成熟度都在同一天变化；日志里记录了 {sum(error_counts.values())} 次工具失败，也会放大这种摩擦。",
        "quick_wins": f"先把“{repeated_hint}”写进系统提示词，再用 Hook 接住测试、截图和打包这些兜底动作。这样能把今天重复说过的话变成默认行为。",
        "ambitious_workflows": f"下一步可以把角色化协作和持续回归串起来。今天已有 {sessions_using_subagents} 个会话使用并行分工，这正好是以后多阶段流水线的雏形。",
    }


def detect_parallel_overlap(sessions: list[SessionRecord]) -> dict[str, int]:
    events: list[tuple[dt.datetime, str]] = []
    for session in sessions:
        current_time = session.start_time
        for _ in session.user_messages:
            events.append((current_time, session.session_id))
            current_time += dt.timedelta(seconds=1)
    events.sort(key=lambda item: item[0])

    window: deque[tuple[dt.datetime, str]] = deque()
    overlap_events = 0
    sessions_involved: set[str] = set()
    user_messages_during = 0
    for timestamp, session_id in events:
        while window and (timestamp - window[0][0]) > dt.timedelta(minutes=30):
            window.popleft()
        active_sessions = {item[1] for item in window}
        if active_sessions and (active_sessions - {session_id}):
            overlap_events += 1
            sessions_involved.update(active_sessions)
            sessions_involved.add(session_id)
            user_messages_during += 1
        window.append((timestamp, session_id))
    return {
        "overlap_events": overlap_events,
        "sessions_involved": len(sessions_involved),
        "user_messages_during": user_messages_during,
    }


def render_html(report: dict) -> str:
    overview_cards = [
        ("分析日期", report["target_date"]),
        ("有效会话", str(report["session_count"])),
        ("用户消息", str(report["total_messages"])),
        ("累计时长", f'{report["total_duration_hours"]} 小时'),
        ("修改文件", str(report["total_files_modified"])),
        ("工具失败", str(sum(count for _, count in report["error_counts"]))),
    ]
    project_area_rows = "".join(render_project_area_card(area) for area in report["project_areas"])
    workflow_rows = "".join(
        f"<article class='panel'><h4>{escape_html(item['title'])}</h4><p>{escape_html(item['description'])}</p></article>"
        for item in report["what_works"]["impressive_workflows"]
    )
    friction_rows = "".join(render_friction_card(item) for item in report["friction_analysis"]["categories"])
    prompt_rows = "".join(render_prompt_card(item) for item in report["suggestions"]["system_prompt_additions"])
    feature_rows = "".join(render_feature_card(item) for item in report["suggestions"]["features_to_try"])
    usage_rows = "".join(render_usage_pattern_card(item) for item in report["suggestions"]["usage_patterns"])
    opportunities_rows = "".join(render_opportunity_card(item) for item in report["opportunities"]["opportunities"])
    session_rows = "".join(render_session_row(item) for item in report["session_summaries"])

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AI 编码洞察报告</title>
  <style>
    :root {{
      --bg: #f3efe7;
      --panel: rgba(255, 252, 246, 0.88);
      --panel-strong: #fffaf1;
      --ink: #1e1b18;
      --muted: #685f57;
      --accent: #9c4027;
      --accent-soft: #f0d7c8;
      --line: rgba(30, 27, 24, 0.12);
      --shadow: 0 18px 42px rgba(74, 50, 33, 0.12);
      --success: #2f7b52;
      --warn: #b66227;
      --danger: #a2322d;
      --font: "Segoe UI", "PingFang SC", "Hiragino Sans GB", sans-serif;
      --serif: "Noto Serif SC", "Source Han Serif SC", "Songti SC", serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: var(--font);
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(156, 64, 39, 0.18), transparent 28%),
        radial-gradient(circle at right 20%, rgba(47, 123, 82, 0.14), transparent 22%),
        linear-gradient(180deg, #efe5d6 0%, var(--bg) 38%, #f6f2eb 100%);
    }}
    .shell {{
      width: min(1200px, calc(100vw - 32px));
      margin: 32px auto 56px;
      padding: 8px;
    }}
    .hero {{
      background: linear-gradient(135deg, rgba(255,250,241,0.95), rgba(247,239,226,0.86));
      border: 1px solid var(--line);
      border-radius: 28px;
      padding: 28px;
      box-shadow: var(--shadow);
      position: relative;
      overflow: hidden;
    }}
    .hero::after {{
      content: "";
      position: absolute;
      inset: auto -40px -60px auto;
      width: 240px;
      height: 240px;
      background: radial-gradient(circle, rgba(156,64,39,0.16), transparent 68%);
      transform: rotate(12deg);
    }}
    h1, h2, h3, h4 {{
      margin: 0;
      line-height: 1.2;
    }}
    h1 {{
      font-family: var(--serif);
      font-size: clamp(36px, 6vw, 64px);
      letter-spacing: -0.03em;
    }}
    h2 {{
      font-size: 22px;
      margin-bottom: 14px;
    }}
    h3 {{
      font-size: 18px;
      margin-bottom: 10px;
    }}
    p {{
      margin: 0;
      line-height: 1.65;
      color: var(--muted);
    }}
    .hero p {{
      max-width: 760px;
      margin-top: 12px;
      font-size: 16px;
    }}
    .meta {{
      display: inline-flex;
      gap: 10px;
      align-items: center;
      padding: 8px 12px;
      border-radius: 999px;
      background: rgba(156, 64, 39, 0.08);
      color: var(--accent);
      font-size: 13px;
      margin-bottom: 18px;
    }}
    .grid {{
      display: grid;
      gap: 16px;
    }}
    .overview {{
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      margin-top: 22px;
    }}
    .card, .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 18px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(10px);
    }}
    .card .label {{
      font-size: 13px;
      color: var(--muted);
      margin-bottom: 8px;
    }}
    .card .value {{
      font-size: 28px;
      font-weight: 700;
      color: var(--ink);
    }}
    .section {{
      margin-top: 18px;
    }}
    .section-title {{
      margin: 30px 0 14px;
      display: flex;
      align-items: baseline;
      gap: 10px;
    }}
    .section-title span {{
      font-size: 13px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--accent);
    }}
    .glance {{
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    }}
    .glance .panel {{
      min-height: 170px;
      background: linear-gradient(180deg, rgba(255,250,241,0.96), rgba(250,243,233,0.88));
    }}
    .chart-grid {{
      grid-template-columns: 1.2fr 0.8fr;
    }}
    .bars {{
      display: grid;
      gap: 12px;
      margin-top: 10px;
    }}
    .bar-row {{
      display: grid;
      gap: 6px;
    }}
    .bar-label {{
      display: flex;
      justify-content: space-between;
      font-size: 14px;
      color: var(--muted);
    }}
    .bar-track {{
      width: 100%;
      height: 10px;
      border-radius: 999px;
      background: rgba(30, 27, 24, 0.08);
      overflow: hidden;
    }}
    .bar-fill {{
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, var(--accent), #d6804f);
    }}
    .stat-list {{
      display: grid;
      gap: 10px;
      margin-top: 8px;
    }}
    .stat-list .row {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      color: var(--muted);
      border-bottom: 1px dashed rgba(30, 27, 24, 0.1);
      padding-bottom: 8px;
    }}
    .triple {{
      grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
    }}
    .subtle {{
      color: var(--muted);
      font-size: 14px;
    }}
    .session-table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 8px;
      font-size: 14px;
    }}
    .session-table th, .session-table td {{
      text-align: left;
      padding: 12px 10px;
      border-bottom: 1px solid rgba(30, 27, 24, 0.08);
      vertical-align: top;
    }}
    .session-table th {{
      color: var(--muted);
      font-weight: 600;
    }}
    code {{
      display: block;
      margin-top: 10px;
      padding: 12px;
      border-radius: 14px;
      background: #201b18;
      color: #f9e7d7;
      font-size: 13px;
      overflow-x: auto;
      white-space: pre-wrap;
    }}
    .tag {{
      display: inline-flex;
      padding: 4px 10px;
      border-radius: 999px;
      background: rgba(47, 123, 82, 0.1);
      color: var(--success);
      font-size: 12px;
      margin-top: 10px;
    }}
    @media (max-width: 900px) {{
      .chart-grid {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <div class="meta">AI 编码洞察 · {escape_html(report["target_date"])} · {escape_html(report["timezone"])}</div>
      <h1>AI 编码洞察</h1>
      <p>{escape_html(report["interaction_style"]["key_pattern"])} 这份报告只分析本地会话日志中符合质量过滤条件的顶层会话，帮助你回看今天的使用节奏、摩擦点和下一步可复用的工作流。</p>
      <div class="grid overview">
        {''.join(render_overview_card(label, value) for label, value in overview_cards)}
      </div>
    </section>

    <section class="section">
      <div class="section-title"><h2>一览</h2><span>At A Glance</span></div>
      <div class="grid glance">
        <article class="panel"><h3>运转良好的方面</h3><p>{escape_html(report["at_a_glance"]["whats_working"])}</p></article>
        <article class="panel"><h3>阻碍你的因素</h3><p>{escape_html(report["at_a_glance"]["whats_hindering"])}</p></article>
        <article class="panel"><h3>可以立即尝试</h3><p>{escape_html(report["at_a_glance"]["quick_wins"])}</p></article>
        <article class="panel"><h3>面向未来的工作流</h3><p>{escape_html(report["at_a_glance"]["ambitious_workflows"])}</p></article>
      </div>
    </section>

    <section class="section">
      <div class="section-title"><h2>图表与分布</h2><span>Signals</span></div>
      <div class="grid chart-grid">
        <article class="panel">
          <h3>工具使用分布</h3>
          <div class="bars">{render_bars(report["tool_counts"])}</div>
        </article>
        <article class="panel">
          <h3>语言与满意度</h3>
          <div class="stat-list">
            {render_stat_rows(report["language_counts"])}
          </div>
          <div class="tag">满意度分布</div>
          <div class="stat-list">
            {render_stat_rows(report["satisfaction_counts"])}
          </div>
        </article>
      </div>
      <div class="grid triple section">
        <article class="panel">
          <h3>并行工作检测</h3>
          <div class="stat-list">
            <div class="row"><span>重叠事件</span><strong>{report["overlap"]["overlap_events"]}</strong></div>
            <div class="row"><span>涉及会话</span><strong>{report["overlap"]["sessions_involved"]}</strong></div>
            <div class="row"><span>窗口内消息</span><strong>{report["overlap"]["user_messages_during"]}</strong></div>
          </div>
        </article>
        <article class="panel">
          <h3>工具错误</h3>
          <div class="stat-list">{render_stat_rows(report["error_counts"])}</div>
        </article>
        <article class="panel">
          <h3>代码改动概览</h3>
          <div class="stat-list">
            <div class="row"><span>新增行数</span><strong>{report["total_lines_added"]}</strong></div>
            <div class="row"><span>删除行数</span><strong>{report["total_lines_removed"]}</strong></div>
            <div class="row"><span>Git 提交</span><strong>{report["git_commits"]}</strong></div>
            <div class="row"><span>Git 推送</span><strong>{report["git_pushes"]}</strong></div>
          </div>
        </article>
      </div>
    </section>

    <section class="section">
      <div class="section-title"><h2>深度分析</h2><span>Qualitative</span></div>
      <div class="grid triple">
        {project_area_rows}
      </div>
      <div class="grid triple section">
        <article class="panel">
          <h3>交互风格分析</h3>
          <p>{escape_html(report["interaction_style"]["narrative"])}</p>
        </article>
        <article class="panel">
          <h3>做得出色的事</h3>
          <p>{escape_html(report["what_works"]["intro"])}</p>
        </article>
        <article class="panel">
          <h3>趣味回顾</h3>
          <p>{escape_html(report["fun_ending"]["headline"])}</p>
          <p style="margin-top:10px;">{escape_html(report["fun_ending"]["detail"])}</p>
        </article>
      </div>
      <div class="grid triple section">
        {workflow_rows}
      </div>
      <div class="grid triple section">
        {friction_rows}
      </div>
    </section>

    <section class="section">
      <div class="section-title"><h2>优化建议</h2><span>Next</span></div>
      <div class="grid triple">
        {prompt_rows}
      </div>
      <div class="grid triple section">
        {feature_rows}
      </div>
      <div class="grid triple section">
        {usage_rows}
      </div>
    </section>

    <section class="section">
      <div class="section-title"><h2>未来机遇</h2><span>Forward</span></div>
      <article class="panel"><p>{escape_html(report["opportunities"]["intro"])}</p></article>
      <div class="grid triple section">
        {opportunities_rows}
      </div>
    </section>

    <section class="section">
      <div class="section-title"><h2>会话摘要</h2><span>Sessions</span></div>
      <article class="panel">
        <table class="session-table">
          <thead>
            <tr><th>开始</th><th>时长</th><th>摘要</th><th>改动文件</th><th>错误</th></tr>
          </thead>
          <tbody>
            {session_rows}
          </tbody>
        </table>
      </article>
    </section>
  </main>
</body>
</html>"""


def render_overview_card(label: str, value: str) -> str:
    return f"<article class='card'><div class='label'>{escape_html(label)}</div><div class='value'>{escape_html(value)}</div></article>"


def render_project_area_card(area: dict[str, str | int]) -> str:
    return (
        "<article class='panel'>"
        f"<h3>{escape_html(str(area['name']))}</h3>"
        f"<div class='tag'>{escape_html(str(area['session_count']))} 个会话</div>"
        f"<p style='margin-top:12px;'>{escape_html(str(area['description']))}</p>"
        "</article>"
    )


def render_friction_card(item: dict[str, object]) -> str:
    examples_html = "".join(f"<div class='row'><span>{escape_html(example)}</span></div>" for example in item["examples"])
    return (
        "<article class='panel'>"
        f"<h3>{escape_html(str(item['category']))}</h3>"
        f"<p>{escape_html(str(item['description']))}</p>"
        f"<div class='stat-list' style='margin-top:12px;'>{examples_html}</div>"
        "</article>"
    )


def render_prompt_card(item: dict[str, str]) -> str:
    return (
        "<article class='panel'>"
        "<h3>系统提示词补强</h3>"
        f"<p>{escape_html(item['addition'])}</p>"
        f"<p style='margin-top:10px;'>{escape_html(item['why'])}</p>"
        f"<div class='tag'>{escape_html(item['prompt_scaffold'])}</div>"
        "</article>"
    )


def render_feature_card(item: dict[str, str]) -> str:
    return (
        "<article class='panel'>"
        f"<h3>{escape_html(item['feature'])}</h3>"
        f"<p>{escape_html(item['one_liner'])}</p>"
        f"<p style='margin-top:10px;'>{escape_html(item['why_for_you'])}</p>"
        f"<code>{escape_html(item['example_code'])}</code>"
        "</article>"
    )


def render_usage_pattern_card(item: dict[str, str]) -> str:
    return (
        "<article class='panel'>"
        f"<h3>{escape_html(item['title'])}</h3>"
        f"<p>{escape_html(item['suggestion'])}</p>"
        f"<p style='margin-top:10px;'>{escape_html(item['detail'])}</p>"
        f"<code>{escape_html(item['copyable_prompt'])}</code>"
        "</article>"
    )


def render_opportunity_card(item: dict[str, str]) -> str:
    return (
        "<article class='panel'>"
        f"<h3>{escape_html(item['title'])}</h3>"
        f"<p>{escape_html(item['whats_possible'])}</p>"
        f"<p style='margin-top:10px;'>{escape_html(item['how_to_try'])}</p>"
        f"<code>{escape_html(item['copyable_prompt'])}</code>"
        "</article>"
    )


def render_session_row(item: dict[str, object]) -> str:
    return (
        "<tr>"
        f"<td>{escape_html(str(item['start']))}</td>"
        f"<td>{escape_html(str(item['duration']))}</td>"
        f"<td>{escape_html(str(item['summary']))}</td>"
        f"<td>{escape_html(str(item['files']))}</td>"
        f"<td>{escape_html(str(item['errors']))}</td>"
        "</tr>"
    )


def render_stat_rows(items: list[tuple[str, int]]) -> str:
    if not items:
        return "<div class='row'><span>无数据</span><strong>0</strong></div>"
    return "".join(
        f"<div class='row'><span>{escape_html(str(label))}</span><strong>{value}</strong></div>" for label, value in items
    )


def render_bars(items: list[tuple[str, int]]) -> str:
    if not items:
        return "<p>无数据</p>"
    max_value = max(value for _, value in items) or 1
    rows = []
    for label, value in items:
        width = max(value / max_value * 100, 4)
        rows.append(
            "<div class='bar-row'>"
            f"<div class='bar-label'><span>{escape_html(str(label))}</span><strong>{value}</strong></div>"
            f"<div class='bar-track'><div class='bar-fill' style='width:{width:.1f}%'></div></div>"
            "</div>"
        )
    return "".join(rows)


def escape_html(text: str) -> str:
    return html.escape(text, quote=True)


def write_report(output_path: Path, html_text: str) -> None:
    output_path.write_text(html_text, encoding="utf-8")
    os.chmod(output_path, 0o600)


def main() -> int:
    args = parse_args()
    timezone = resolve_timezone(args.timezone)
    target_date = parse_target_date(args.date, timezone)
    session_paths = collect_session_paths(Path(args.sessions_root), target_date)
    sessions = load_sessions(session_paths, timezone, target_date)
    report_data = build_report_data(sessions, target_date, args.timezone)
    html_text = render_html(report_data)
    output_path = Path(args.output).resolve()
    write_report(output_path, html_text)
    print(f"Insights report written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
