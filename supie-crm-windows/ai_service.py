from __future__ import annotations

from datetime import date, timedelta
from typing import Any


DONE_TASK_STATUSES = {"done", "closed", "completed"}
DONE_MILESTONE_STATUSES = {"done", "closed", "completed"}
CLOSED_RISK_STATUSES = {"closed", "done", "resolved"}


def _clip(text: str | None, limit: int = 120) -> str:
    raw = " ".join((text or "").strip().split())
    if len(raw) <= limit:
        return raw
    return raw[: limit - 1].rstrip() + "…"


def _display_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "未设置"
    return text[:10]


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _parse_datetime_like(value: Any) -> date | None:
    return _parse_date(value)


def _summarize_names(items: list[dict[str, Any]], key: str = "title", limit: int = 3) -> str:
    names = [str(item.get(key) or "").strip() for item in items if str(item.get(key) or "").strip()]
    if not names:
        return "暂无"
    shown = names[:limit]
    tail = f" 等{len(names)}项" if len(names) > limit else ""
    return "、".join(shown) + tail


def _sort_by_date(items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: (_parse_date(item.get(key)) or date.max, int(item.get("id") or 0)))


def _recent_items(items: list[dict[str, Any]], key: str, days: int) -> list[dict[str, Any]]:
    cutoff = date.today() - timedelta(days=max(1, days))
    result: list[dict[str, Any]] = []
    for item in items:
        dt = _parse_datetime_like(item.get(key))
        if dt is not None and dt >= cutoff:
            result.append(item)
    return result


def _completed_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    done = [task for task in tasks if str(task.get("status") or "").strip() in DONE_TASK_STATUSES]
    done.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return done


def _pending_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pending = [task for task in tasks if str(task.get("status") or "").strip() not in DONE_TASK_STATUSES]
    pending.sort(
        key=lambda item: (
            _parse_date(item.get("planned_end")) or date.max,
            -int(item.get("progress") or 0),
            int(item.get("id") or 0),
        )
    )
    return pending


def _overdue_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    today = date.today()
    items: list[dict[str, Any]] = []
    for task in tasks:
        due = _parse_date(task.get("planned_end"))
        status = str(task.get("status") or "").strip()
        if due is not None and due < today and status not in DONE_TASK_STATUSES:
            items.append(task)
    return _sort_by_date(items, "planned_end")


def _delayed_milestones(milestones: list[dict[str, Any]]) -> list[dict[str, Any]]:
    today = date.today()
    items: list[dict[str, Any]] = []
    for milestone in milestones:
        due = _parse_date(milestone.get("due_date"))
        status = str(milestone.get("status") or "").strip()
        if due is not None and due < today and status not in DONE_MILESTONE_STATUSES:
            items.append(milestone)
    return _sort_by_date(items, "due_date")


def _open_high_risks(risks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = [
        risk
        for risk in risks
        if str(risk.get("status") or "").strip() not in CLOSED_RISK_STATUSES
        and str(risk.get("level") or "").strip() == "high"
    ]
    items.sort(key=lambda item: (_parse_date(item.get("due_date")) or date.max, int(item.get("id") or 0)))
    return items


def _blocked_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = []
    for task in tasks:
        blocked_reason = str(task.get("blocked_reason") or "").strip()
        status = str(task.get("status") or "").strip()
        if blocked_reason or status == "blocked":
            items.append(task)
    items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return items


def _section(title: str, lines: list[str], empty_copy: str) -> dict[str, str]:
    body = "\n".join(line for line in lines if line.strip()) or empty_copy
    return {"title": title, "body": body}


def build_project_progress_draft(
    project: dict[str, Any],
    tasks: list[dict[str, Any]],
    milestones: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    progress_entries: list[dict[str, Any]],
    days: int = 7,
) -> dict[str, Any]:
    recent_done = _recent_items(_completed_tasks(tasks), "updated_at", days)
    recent_updates = _recent_items(tasks, "updated_at", days)
    overdue_tasks = _overdue_tasks(tasks)
    delayed_milestones = _delayed_milestones(milestones)
    high_risks = _open_high_risks(risks)
    blocked_tasks = _blocked_tasks(tasks)
    pending_tasks = _pending_tasks(tasks)
    latest_progress = progress_entries[0] if progress_entries else None

    completed_lines = [
        f"近{days}天完成任务：{_summarize_names(recent_done)}。"
        if recent_done
        else f"近{days}天暂无标记为完成的任务，最近有更新的任务为：{_summarize_names(recent_updates)}。"
    ]
    if latest_progress and str(latest_progress.get("body") or "").strip():
        completed_lines.append(f"最近一次项目进展提到：{_clip(str(latest_progress.get('body') or ''), 90)}")

    risk_lines: list[str] = []
    if delayed_milestones:
        risk_lines.append(
            f"延期里程碑 {len(delayed_milestones)} 项，优先关注：{_summarize_names(delayed_milestones)}。"
        )
    if overdue_tasks:
        risk_lines.append(f"逾期任务 {len(overdue_tasks)} 项，主要集中在：{_summarize_names(overdue_tasks)}。")
    if high_risks:
        risk_lines.append(f"高风险未闭环 {len(high_risks)} 项，包括：{_summarize_names(high_risks)}。")
    if blocked_tasks:
        top_block = blocked_tasks[0]
        reason = _clip(str(top_block.get("blocked_reason") or "状态已标记为阻塞"), 80)
        risk_lines.append(f"当前阻塞项：{top_block.get('title') or '未命名任务'}，原因：{reason}")

    plan_lines: list[str] = []
    for task in pending_tasks[:3]:
        due = _display_date(task.get("planned_end"))
        plan_lines.append(
            f"推进任务「{task.get('title') or '未命名任务'}」，当前进度 {int(task.get('progress') or 0)}%，计划完成日期 {due}。"
        )
    if not plan_lines:
        plan_lines.append("当前待办任务较少，可转入验收、复盘或资料归档。")

    support_lines: list[str] = []
    if high_risks:
        support_lines.append("建议管理层优先协调高风险项负责人，明确关闭时间和升级路径。")
    if blocked_tasks:
        support_lines.append("建议对阻塞任务做跨团队协调，确认依赖项和解锁动作。")
    if delayed_milestones and not support_lines:
        support_lines.append("建议确认延期里程碑的重排计划，并同步客户或管理层预期。")

    summary = (
        f"{project.get('name') or '当前项目'}在近{days}天内"
        f"{'已有阶段性完成项' if recent_done else '以推进中任务为主'}，"
        f"当前重点关注延期、逾期和高风险项。"
    )
    citations = [
        f"已完成任务 {len(recent_done)} 项",
        f"待推进任务 {len(pending_tasks)} 项",
        f"逾期任务 {len(overdue_tasks)} 项",
        f"延期里程碑 {len(delayed_milestones)} 项",
        f"高风险未闭环 {len(high_risks)} 项",
    ]

    return {
        "summary": summary,
        "sections": [
            _section("本期完成", completed_lines, "本期暂无可自动归纳的完成项，请补充人工说明。"),
            _section("风险与阻塞", risk_lines, "当前未发现明显风险与阻塞，可继续按计划推进。"),
            _section("下期计划", plan_lines, "请补充下期计划。"),
            _section("需协调事项", support_lines, "当前暂无需升级协调事项。"),
        ],
        "citations": citations,
    }


def build_project_risk_summary(
    project: dict[str, Any],
    tasks: list[dict[str, Any]],
    milestones: list[dict[str, Any]],
    risks: list[dict[str, Any]],
) -> dict[str, Any]:
    overdue_tasks = _overdue_tasks(tasks)
    delayed_milestones = _delayed_milestones(milestones)
    high_risks = _open_high_risks(risks)
    blocked_tasks = _blocked_tasks(tasks)

    issue_count = len(overdue_tasks) + len(delayed_milestones) + len(high_risks)
    if issue_count == 0 and not blocked_tasks:
        summary = f"{project.get('name') or '当前项目'}暂无明显红色风险，建议按既定排期持续跟进。"
    else:
        summary = (
            f"{project.get('name') or '当前项目'}当前风险偏高，"
            f"主要由延期里程碑、逾期任务和高风险未闭环共同造成。"
        )

    recommendations: list[str] = []
    if delayed_milestones:
        recommendations.append(
            f"优先重排里程碑「{delayed_milestones[0].get('title') or '未命名里程碑'}」的交付节奏，并同步负责人。"
        )
    if overdue_tasks:
        recommendations.append(
            f"确认逾期任务「{overdue_tasks[0].get('title') or '未命名任务'}」的实际完成时间和恢复计划。"
        )
    if high_risks:
        recommendations.append(
            f"高风险项「{high_risks[0].get('title') or '未命名风险'}」需要明确关闭动作和责任人。"
        )
    if blocked_tasks and len(recommendations) < 3:
        blocked = blocked_tasks[0]
        recommendations.append(
            f"阻塞任务「{blocked.get('title') or '未命名任务'}」建议先处理依赖问题：{_clip(str(blocked.get('blocked_reason') or ''), 50)}"
        )
    if not recommendations:
        recommendations.append("当前可以继续执行既定计划，并保持风险与里程碑每周复核。")

    citations = [
        f"逾期任务 {len(overdue_tasks)} 项",
        f"延期里程碑 {len(delayed_milestones)} 项",
        f"高风险未闭环 {len(high_risks)} 项",
        f"阻塞任务 {len(blocked_tasks)} 项",
    ]

    return {
        "summary": summary,
        "sections": [
            _section(
                "核心风险",
                [
                    f"逾期任务：{_summarize_names(overdue_tasks)}。" if overdue_tasks else "",
                    f"延期里程碑：{_summarize_names(delayed_milestones)}。" if delayed_milestones else "",
                    f"高风险项：{_summarize_names(high_risks)}。" if high_risks else "",
                ],
                "当前暂无需要升级处理的核心风险。",
            ),
            _section("优先建议", [f"{idx + 1}. {item}" for idx, item in enumerate(recommendations[:3])], "暂无建议。"),
        ],
        "citations": citations,
        "priorities": recommendations[:3],
    }


def build_project_report_draft(
    project: dict[str, Any],
    tasks: list[dict[str, Any]],
    milestones: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    progress_entries: list[dict[str, Any]],
    period: str = "week",
) -> dict[str, Any]:
    days = 30 if period == "month" else 7
    progress_draft = build_project_progress_draft(project, tasks, milestones, risks, progress_entries, days=days)
    title = "项目月报草稿" if period == "month" else "项目周报草稿"
    summary = f"{project.get('name') or '当前项目'}{title}已生成，可直接用于内部同步后再人工润色。"
    sections = [{"title": "报告概览", "body": progress_draft["summary"]}] + progress_draft["sections"]
    return {
        "summary": summary,
        "sections": sections,
        "citations": progress_draft["citations"] + [f"报告周期 近{days}天"],
        "report_title": title,
    }


def build_workbench_priorities(
    role: str,
    todo_items: list[dict[str, Any]],
    attention_items: list[dict[str, Any]],
    cards: list[dict[str, Any]],
) -> dict[str, Any]:
    merged: list[dict[str, Any]] = []
    today = date.today()

    def score_item(item: dict[str, Any]) -> int:
        item_type = str(item.get("item_type") or "")
        name = str(item.get("item_name") or "")
        score = 0
        if "审批" in item_type:
            score += 100
        if "逾期" in item_type or "延期" in item_type:
            score += 90
        if "风险" in item_type:
            score += 80
        if "临近" in item_type:
            score += 70
        if "回款" in item_type:
            score += 65
        if "停滞" in item_type:
            score += 60
        due = _parse_date(item.get("due_at"))
        if due is not None:
            if due < today:
                score += 20
            elif due == today:
                score += 10
        if "高风险" in name:
            score += 15
        return score

    for item in attention_items:
        merged.append(
            {
                "item_type": item.get("item_type"),
                "item_name": item.get("item_name"),
                "due_at": item.get("due_at"),
                "item_link": item.get("link"),
                "reason": f"该事项来自项目异常清单，关联项目 {item.get('project_name') or '未命名项目'}。",
                "summary": f"该事项来自项目异常清单，关联项目 {item.get('project_name') or '未命名项目'}。",
            }
        )
    for item in todo_items:
        merged.append(
            {
                "item_type": item.get("item_type"),
                "item_name": item.get("item_name"),
                "due_at": item.get("due_at"),
                "item_link": item.get("item_link"),
                "reason": "该事项已经进入当前角色待办，建议优先处理可直接跳转的条目。",
                "summary": "该事项已经进入当前角色待办，建议优先处理可直接跳转的条目。",
            }
        )

    merged.sort(key=score_item, reverse=True)
    top_items = merged[:3]
    summary = (
        f"当前角色为 {role or 'normal'}，建议优先处理 {len(top_items)} 项高优先级事项。"
        if top_items
        else "当前暂无需要 AI 额外排序的事项，可按现有待办继续推进。"
    )
    card_hints = [f"{card.get('title')}: {int(card.get('value') or 0)}" for card in cards[:4]]
    citations = card_hints + [f"待办总数 {len(todo_items)}", f"异常提醒 {len(attention_items)}"]
    return {
        "summary": summary,
        "priorities": top_items,
        "citations": citations,
    }


def build_approval_summary(approval: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    module_type = str(approval.get("module_type") or "")
    summary_parts = [
        f"审批类型：{module_type or '未知'}",
        f"申请标题：{approval.get('title') or '未命名审批'}",
        f"申请人：{approval.get('applicant') or '未填写'}",
    ]
    key_points: list[str] = []
    questions: list[str] = []
    citations: list[str] = [f"审批状态：{approval.get('status') or 'pending'}", f"申请值：{approval.get('requested_value') or '-'}"]

    if module_type == "contract":
        summary_parts.append(
            f"合同号：{context.get('contract_no') or '未关联'}，金额 {context.get('amount') or 0}。"
        )
        key_points.append(f"当前合同状态：{context.get('status') or '未知'}。")
        if context.get("customer_name"):
            key_points.append(f"所属客户：{context['customer_name']}。")
        questions.append("请确认合同关键条款、金额和签署前置条件是否已核实。")
    elif module_type == "opportunity":
        summary_parts.append(
            f"商机：{context.get('title') or '未关联'}，金额 {context.get('amount') or 0}，当前阶段 {context.get('stage') or '未知'}。"
        )
        if context.get("customer_name"):
            key_points.append(f"关联客户：{context['customer_name']}。")
        questions.append("请确认赢单依据是否充分，后续合同或交付准备是否已同步。")
    elif module_type == "project":
        summary_parts.append(
            f"项目：{context.get('name') or '未关联'}，状态 {context.get('status') or '未知'}，阶段 {context.get('current_stage') or '未知'}。"
        )
        key_points.append(f"项目进度约 {context.get('progress') or 0}% 。")
        key_points.append(f"逾期任务 {context.get('overdue_task_count') or 0} 项，高风险 {context.get('open_high_risk_count') or 0} 项。")
        questions.append("请确认结项条件、遗留问题和验收资料是否齐备。")
        citations.extend(
            [
                f"项目进度 {context.get('progress') or 0}%",
                f"逾期任务 {context.get('overdue_task_count') or 0} 项",
                f"高风险 {context.get('open_high_risk_count') or 0} 项",
            ]
        )
    else:
        questions.append("请人工复核审批对象的关键背景信息。")

    return {
        "summary": " ".join(summary_parts),
        "sections": [
            _section("关键点", key_points, "暂无额外关键点。"),
            _section("待确认问题", questions, "暂无待确认问题。"),
        ],
        "citations": citations,
    }
