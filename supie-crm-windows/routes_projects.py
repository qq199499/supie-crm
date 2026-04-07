import csv
from datetime import date, datetime
from io import BytesIO, StringIO
from typing import Any

from flask import flash, jsonify, redirect, render_template, request, send_file, send_from_directory, session, url_for
from werkzeug.utils import secure_filename

from ai_service import build_project_progress_draft, build_project_report_draft, build_project_risk_summary
from app import (
    MILESTONE_STATUS_IMPORT_MAP,
    MILESTONE_STATUS_LABELS,
    PRIORITY_IMPORT_MAP,
    PROJECT_RECYCLE_DAYS,
    PROJECT_STAGE_LABELS,
    RISK_LEVEL_LABELS,
    RISK_STATUS_LABELS,
    TASK_STATUS_IMPORT_MAP,
    TASK_STATUS_LABELS,
    UPLOAD_DIR,
    Workbook,
    admin_required,
    app,
    attachment_public_dict,
    can_manage_project_record,
    can_manage_project_members,
    build_customer_visibility_clause,
    build_project_visibility_clause,
    current_user_matches_text,
    current_actor_name,
    ensure_excel_support,
    ensure_sqlite_attachments_schema,
    execute,
    execute_returning_id,
    fetch_approval_candidates,
    fetchall,
    fetchone,
    find_pm_user_id_for_manager_label,
    get_db,
    has_module_permission,
    has_pending_approval,
    load_workbook,
    log_ai_generation,
    log_project_activity,
    manager_display_string,
    normalize_stage,
    now_iso,
    parse_int_form_value,
    parse_date_text,
    parse_task_depends,
    purge_expired_projects,
    purge_project_forever,
    recycle_cutoff_iso,
    remove_attachment_file,
    search_member_candidates,
    search_pm_users,
    session_is_system_admin,
    submit_approval,
    set_ai_generation_accepted,
    touch_project,
    user_is_pm_user,
    user_is_project_manager,
    uses_postgres,
)

_DETAIL_PAGE_SIZES = (15, 50, 100)


def _ensure_project_member(project_id: int, user_id: int) -> None:
    if not user_id:
        return
    if fetchone("SELECT id FROM project_members WHERE project_id = ? AND user_id = ?", (project_id, user_id)):
        return
    if uses_postgres():
        execute(
            """
            INSERT INTO project_members(project_id, user_id, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            (project_id, user_id, now_iso()),
        )
    else:
        execute(
            "INSERT OR IGNORE INTO project_members(project_id, user_id, created_at) VALUES (?, ?, ?)",
            (project_id, user_id, now_iso()),
        )


def _can_manage_task_record(task: dict[str, Any]) -> bool:
    if has_module_permission("implementation", "manage"):
        return current_user_matches_text(task.get("assignee"))
    return False


def _can_manage_milestone_record(milestone: dict[str, Any]) -> bool:
    if has_module_permission("acceptance", "manage"):
        return current_user_matches_text(milestone.get("owner"))
    return False


def _can_manage_risk_record(risk: dict[str, Any]) -> bool:
    if has_module_permission("issue", "manage"):
        return current_user_matches_text(risk.get("owner"))
    return False


def _parse_detail_pagination(page_key: str = "page", per_page_key: str = "per_page") -> tuple[int, int]:
    try:
        page = max(1, int(request.args.get(page_key, "1") or 1))
    except ValueError:
        page = 1
    try:
        per_page = int(request.args.get(per_page_key, "15") or 15)
    except ValueError:
        per_page = 15
    if per_page not in _DETAIL_PAGE_SIZES:
        per_page = 15
    return page, per_page


def _paginate_rows(rows: list[dict[str, Any]], page: int, per_page: int) -> tuple[int, list[dict[str, Any]], int]:
    total = len(rows)
    total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
    if page > total_pages:
        page = total_pages
    start = (page - 1) * per_page
    return page, rows[start:start + per_page], total_pages


def _csv_attachment(filename: str, headers: list[str], rows: list[list[object]]):
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    payload = BytesIO(buffer.getvalue().encode("utf-8-sig"))
    payload.seek(0)
    return send_file(payload, as_attachment=True, download_name=filename, mimetype="text/csv; charset=utf-8")


def _active_phase_templates() -> list[dict[str, Any]]:
    db = get_db()
    cur = db.cursor()

    def _cell(row: Any, key: str, idx: int = 0) -> Any:
        if hasattr(row, "keys"):
            return row[key]
        return row[idx]

    if uses_postgres():
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'phase_templates'
            """
        )
        cols = {str(_cell(row, "column_name")) for row in cur.fetchall()}
    else:
        cur.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'phase_templates'")
        if cur.fetchone() is None:
            return []
        cur.execute("PRAGMA table_info(phase_templates)")
        cols = {str(_cell(row, "name", 1)) for row in cur.fetchall()}
    if not cols:
        return []
    filter_col = "is_active" if "is_active" in cols else ("enabled" if "enabled" in cols else None)
    order_cols = "sort_order ASC, id ASC" if "sort_order" in cols else "id ASC"
    if filter_col == "is_active":
        sql = (
            f"SELECT id, name FROM phase_templates WHERE {filter_col} IS TRUE ORDER BY {order_cols}"
            if uses_postgres()
            else f"SELECT id, name FROM phase_templates WHERE {filter_col} = 1 ORDER BY {order_cols}"
        )
    elif filter_col == "enabled":
        sql = (
            f"SELECT id, name FROM phase_templates WHERE {filter_col} IS TRUE ORDER BY {order_cols}"
            if uses_postgres()
            else f"SELECT id, name FROM phase_templates WHERE {filter_col} = 1 ORDER BY {order_cols}"
        )
    else:
        sql = f"SELECT id, name FROM phase_templates ORDER BY {order_cols}"
    return fetchall(sql)


@app.route("/projects")
def project_manage():
    if not has_module_permission("project", "view"):
        flash("无权限查看项目管理。", "danger")
        return redirect(url_for("dashboard"))
    keyword = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "").strip()
    stage_filter = request.args.get("stage", "").strip()
    risk_filter = request.args.get("risk", "").strip()
    delayed_filter = request.args.get("delayed", "").strip()
    manager_filter = request.args.get("manager", "").strip()
    try:
        customer_filter = int(request.args.get("customer_id") or 0)
    except ValueError:
        customer_filter = 0
    try:
        page = max(1, int(request.args.get("page", "1") or 1))
    except ValueError:
        page = 1
    try:
        per_page = int(request.args.get("per_page", "15") or 15)
    except ValueError:
        per_page = 15
    if per_page not in _DETAIL_PAGE_SIZES:
        per_page = 15
    sort_key = request.args.get("sort", "created_at").strip()
    if sort_key not in ("created_at", "updated_at", "name", "end_date", "progress"):
        sort_key = "created_at"
    where_parts = ["p.deleted_at IS NULL"]
    params: list[object] = []
    scope_clause, scope_params = build_project_visibility_clause("p")
    where_parts.append(scope_clause)
    params.extend(scope_params)
    if keyword:
        where_parts.append(
            "(LOWER(COALESCE(p.name, '')) LIKE LOWER(?) OR LOWER(COALESCE(c.name, '')) LIKE LOWER(?) OR "
            "LOWER(COALESCE(p.manager, '')) LIKE LOWER(?) OR LOWER(COALESCE((SELECT ct.contract_no FROM contracts ct WHERE ct.project_id = p.id ORDER BY ct.updated_at DESC LIMIT 1), '')) LIKE LOWER(?))"
        )
        like_kwd = f"%{keyword}%"
        params.extend([like_kwd, like_kwd, like_kwd, like_kwd])
    if status_filter:
        where_parts.append("p.status = ?")
        params.append(status_filter)
    if stage_filter:
        where_parts.append("p.current_stage = ?")
        params.append(stage_filter)
    if customer_filter:
        where_parts.append("p.customer_id = ?")
        params.append(customer_filter)
    if manager_filter:
        where_parts.append("p.manager = ?")
        params.append(manager_filter)
    project_rows = fetchall(
        f"""
        SELECT p.id, p.customer_id, p.name, p.project_type, p.manager, p.status, p.current_stage, p.start_date, p.end_date, p.created_at, p.updated_at,
               c.name AS customer_name,
               (SELECT ct.contract_no FROM contracts ct WHERE ct.project_id = p.id ORDER BY ct.updated_at DESC LIMIT 1) AS contract_no
        FROM projects p
        LEFT JOIN customers c ON c.id = p.customer_id
        WHERE {' AND '.join(where_parts)}
        ORDER BY p.created_at ASC
        """,
        tuple(params),
    )
    from app import annotate_projects_with_metrics

    def _project_is_delayed(p: dict[str, Any]) -> bool:
        if (p.get("delayed_milestones") or 0) > 0:
            return True
        ed = p.get("end_date")
        st = p.get("status")
        if ed and str(ed) < date.today().isoformat() and st != "closed":
            return True
        return False

    projects = []
    customer_scope_clause, customer_scope_params = build_customer_visibility_clause("c")
    customers = fetchall(
        f"SELECT id, name FROM customers c WHERE {customer_scope_clause} ORDER BY name ASC",
        tuple(customer_scope_params),
    )
    pm_users = search_pm_users("")
    phase_templates = _active_phase_templates()
    manager_scope_clause, manager_scope_params = build_project_visibility_clause("p")
    manager_rows = fetchall(
        f"SELECT DISTINCT manager AS m FROM projects p WHERE p.deleted_at IS NULL AND p.manager IS NOT NULL AND {manager_scope_clause} ORDER BY m ASC",
        tuple(manager_scope_params),
    )
    manager_options = [str(r["m"]) for r in manager_rows if r.get("m")]
    for project in project_rows:
        projects.append(project)
    annotate_projects_with_metrics(projects)
    after_risk: list[dict[str, Any]] = []
    for p in projects:
        if risk_filter == "high" and (p.get("open_high_risks") or 0) <= 0:
            continue
        if risk_filter == "clean" and (p.get("open_high_risks") or 0) > 0:
            continue
        if delayed_filter == "yes" and not _project_is_delayed(p):
            continue
        if delayed_filter == "no" and _project_is_delayed(p):
            continue
        after_risk.append(p)
    projects = after_risk
    if sort_key == "name":
        projects.sort(key=lambda x: (x.get("name") or "").lower())
    elif sort_key == "end_date":
        projects.sort(key=lambda x: x.get("end_date") or "", reverse=True)
    elif sort_key == "progress":
        projects.sort(key=lambda x: x.get("progress") or 0, reverse=True)
    elif sort_key == "updated_at":
        projects.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    else:
        # created_at：越早创建越靠前，新建在后
        projects.sort(
            key=lambda x: (x.get("created_at") or x.get("updated_at") or "", int(x.get("id") or 0)),
            reverse=False,
        )
    total = len(projects)
    total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
    if page > total_pages:
        page = total_pages
    start = (page - 1) * per_page
    projects_page = projects[start : start + per_page]
    return render_template(
        "project_manage.html",
        projects=projects_page,
        customers=customers,
        pm_users=pm_users,
        phase_templates=phase_templates,
        manager_options=manager_options,
        keyword=keyword,
        status_filter=status_filter,
        stage_filter=stage_filter,
        risk_filter=risk_filter,
        delayed_filter=delayed_filter,
        manager_filter=manager_filter,
        customer_filter=customer_filter,
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
        sort_key=sort_key,
    )


@app.route("/projects/export")
def project_manage_export():
    if not has_module_permission("project", "view"):
        flash("无权限导出项目管理列表。", "danger")
        return redirect(url_for("dashboard"))
    keyword = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "").strip()
    stage_filter = request.args.get("stage", "").strip()
    risk_filter = request.args.get("risk", "").strip()
    delayed_filter = request.args.get("delayed", "").strip()
    manager_filter = request.args.get("manager", "").strip()
    try:
        customer_filter = int(request.args.get("customer_id") or 0)
    except ValueError:
        customer_filter = 0
    scope_clause, scope_params = build_project_visibility_clause("p")
    project_rows = fetchall(
        f"""
        SELECT p.id, p.customer_id, p.name, p.project_type, p.manager, p.status, p.current_stage, p.start_date, p.end_date, p.created_at, p.updated_at,
               c.name AS customer_name,
               (SELECT ct.contract_no FROM contracts ct WHERE ct.project_id = p.id ORDER BY ct.updated_at DESC LIMIT 1) AS contract_no
        FROM projects p
        LEFT JOIN customers c ON c.id = p.customer_id
        WHERE p.deleted_at IS NULL AND {scope_clause}
        ORDER BY p.created_at ASC
        """,
        tuple(scope_params),
    )
    from app import annotate_projects_with_metrics

    def _project_is_delayed(p: dict[str, Any]) -> bool:
        if (p.get("delayed_milestones") or 0) > 0:
            return True
        ed = p.get("end_date")
        st = p.get("status")
        if ed and str(ed) < date.today().isoformat() and st != "closed":
            return True
        return False

    projects = []
    for project in project_rows:
        if keyword:
            merged = f"{project['name']} {project.get('customer_name') or ''} {project.get('manager') or ''} {project.get('contract_no') or ''}".lower()
            if keyword.lower() not in merged:
                continue
        if status_filter and project["status"] != status_filter:
            continue
        if stage_filter and project["current_stage"] != stage_filter:
            continue
        if customer_filter and int(project.get("customer_id") or 0) != customer_filter:
            continue
        if manager_filter and (project.get("manager") or "") != manager_filter:
            continue
        projects.append(project)
    annotate_projects_with_metrics(projects)
    after_risk: list[dict[str, Any]] = []
    for p in projects:
        if risk_filter == "high" and (p.get("open_high_risks") or 0) <= 0:
            continue
        if risk_filter == "clean" and (p.get("open_high_risks") or 0) > 0:
            continue
        if delayed_filter == "yes" and not _project_is_delayed(p):
            continue
        if delayed_filter == "no" and _project_is_delayed(p):
            continue
        after_risk.append(p)
    projects = after_risk
    if request.args.get("sort", "created_at").strip() == "name":
        projects.sort(key=lambda x: (x.get("name") or "").lower())
    elif request.args.get("sort", "created_at").strip() == "end_date":
        projects.sort(key=lambda x: x.get("end_date") or "", reverse=True)
    elif request.args.get("sort", "created_at").strip() == "progress":
        projects.sort(key=lambda x: x.get("progress") or 0, reverse=True)
    elif request.args.get("sort", "created_at").strip() == "updated_at":
        projects.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    else:
        projects.sort(key=lambda x: (x.get("created_at") or x.get("updated_at") or "", int(x.get("id") or 0)), reverse=False)
    rows = [
        [
            p.get("name") or "",
            p.get("customer_name") or "",
            p.get("contract_no") or "",
            p.get("manager") or "",
            p.get("current_stage") or "",
            p.get("status") or "",
            p.get("progress") or 0,
            p.get("open_high_risks") or 0,
            p.get("delayed_milestones") or 0,
        ]
        for p in projects
    ]
    return _csv_attachment("项目列表导出.csv", ["项目名称", "客户名称", "合同编号", "项目经理", "当前阶段", "状态", "进度", "高风险数", "延期里程碑"], rows)


@app.route("/projects/<int:project_id>/delete", methods=["POST"])
@admin_required
def delete_project(project_id: int):
    """仅管理员可将项目移入回收站（软删除），30 天内可在回收站恢复。"""
    project = fetchone("SELECT id, name FROM projects WHERE id = ? AND deleted_at IS NULL", (project_id,))
    if project is None:
        flash("项目不存在或已在回收站。", "danger")
        return redirect(url_for("project_manage"))
    execute(
        "UPDATE projects SET deleted_at = ?, updated_at = ? WHERE id = ? AND deleted_at IS NULL",
        (now_iso(), now_iso(), project_id),
    )
    log_project_activity(project_id, "项目移入回收站", "project", str(project.get("name") or ""), None)
    get_db().commit()
    flash("项目已移入回收站，管理员可在「项目回收站」中恢复或等待自动清理。", "success")
    return redirect(url_for("project_manage"))


@app.route("/projects/recycle")
@admin_required
def project_recycle():
    """项目回收站：仅管理员；进入时自动清理超过保留期的记录。"""
    purge_expired_projects()
    rows = fetchall(
        """
        SELECT p.id, p.name, p.manager, p.customer_id, p.deleted_at, p.updated_at,
               c.name AS customer_name
        FROM projects p
        LEFT JOIN customers c ON c.id = p.customer_id
        WHERE p.deleted_at IS NOT NULL
        ORDER BY p.deleted_at DESC
        """
    )
    cutoff = recycle_cutoff_iso()
    items: list[dict[str, Any]] = []
    for r in rows:
        da = r.get("deleted_at")
        can_restore = da is not None and str(da) >= cutoff
        items.append({**dict(r), "can_restore": can_restore})
    return render_template("project_recycle.html", items=items, recycle_days=PROJECT_RECYCLE_DAYS, cutoff_iso=cutoff)


@app.route("/projects/<int:project_id>/restore", methods=["POST"])
@admin_required
def restore_project(project_id: int):
    row = fetchone(
        "SELECT id, name, deleted_at FROM projects WHERE id = ? AND deleted_at IS NOT NULL",
        (project_id,),
    )
    if row is None:
        flash("项目不存在或不在回收站。", "danger")
        return redirect(url_for("project_recycle"))
    if str(row.get("deleted_at") or "") < recycle_cutoff_iso():
        flash("该项目已超过可恢复期限，可能已被系统自动清理。", "danger")
        return redirect(url_for("project_recycle"))
    execute(
        "UPDATE projects SET deleted_at = NULL, updated_at = ? WHERE id = ?",
        (now_iso(), project_id),
    )
    log_project_activity(project_id, "从回收站恢复项目", "project", str(row.get("name") or ""), None)
    get_db().commit()
    flash("项目已恢复。", "success")
    return redirect(url_for("project_recycle"))


@app.route("/projects/<int:project_id>/purge-forever", methods=["POST"])
@admin_required
def purge_project_now(project_id: int):
    """管理员立即彻底删除回收站中的项目（不等 30 天）。"""
    row = fetchone("SELECT id, name FROM projects WHERE id = ? AND deleted_at IS NOT NULL", (project_id,))
    if row is None:
        flash("项目不存在或不在回收站。", "danger")
        return redirect(url_for("project_recycle"))
    purge_project_forever(project_id)
    get_db().commit()
    flash("项目已彻底删除。", "success")
    return redirect(url_for("project_recycle"))


@app.route("/projects/new", methods=["GET", "POST"])
def create_project():
    if not has_module_permission("project", "manage"):
        flash("无权限创建项目。", "danger")
        return redirect(url_for("dashboard"))
    customer_scope_clause, customer_scope_params = build_customer_visibility_clause("c")
    customers = fetchall(
        f"SELECT id, name FROM customers c WHERE {customer_scope_clause} ORDER BY name ASC",
        tuple(customer_scope_params),
    )
    if request.method == "POST":
        customer_id_raw = request.form.get("customer_id", "").strip()
        name = request.form.get("name", "").strip()
        project_type = request.form.get("project_type", "").strip()
        status = request.form.get("status", "in_progress").strip()
        current_stage = normalize_stage(request.form.get("current_stage", "init").strip())
        start_date = request.form.get("start_date", "").strip() or None
        end_date = request.form.get("end_date", "").strip() or None
        description = request.form.get("description", "").strip()
        customer_id = parse_int_form_value(customer_id_raw)
        manager_user_id = parse_int_form_value(request.form.get("manager_user_id"), 0) or 0
        ptid = parse_int_form_value(request.form.get("phase_template_id", "0"), 0) or 0
        if not manager_user_id or not user_is_pm_user(manager_user_id):
            flash("请从下拉列表中选择项目经理（须为「项目经理」角色用户）。", "danger")
            return redirect(url_for("project_manage"))
        manager = manager_display_string(manager_user_id)
        if not manager:
            flash("项目经理无效。", "danger")
            return redirect(url_for("project_manage"))

        if not customer_id or not name or not project_type or not manager:
            flash("客户、项目名称、项目类型、项目经理为必填项。", "danger")
            return redirect(url_for("project_manage"))

        project_id = execute_returning_id(
            """
            INSERT INTO projects(customer_id, name, project_type, manager, status, current_stage, start_date, end_date, description, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (customer_id, name, project_type, manager, status, current_stage, start_date, end_date, description, now_iso(), now_iso()),
        )
        _ensure_project_member(project_id, manager_user_id)
        execute(
            "INSERT INTO project_stage_logs(project_id, stage, note, changed_at) VALUES (?, ?, ?, ?)",
            (project_id, current_stage, "项目创建", now_iso()),
        )
        log_project_activity(project_id, "创建项目", "project", name, f"类型 {project_type}，项目经理 {manager}")
        if ptid > 0:
            try:
                instantiate_project_phases_from_template(project_id, ptid)
            except ValueError as exc:
                flash(str(exc), "warning")
        get_db().commit()
        flash("项目创建成功。", "success")
        return redirect(url_for("project_manage"))
    return redirect(url_for("project_manage"))


def _redirect_project_entity(project_id: int):
    """任务/里程碑/风险保存后：若从全屏管理页提交则回到对应页面。"""
    ref = request.referrer or ""
    if f"/projects/{project_id}/tasks-manage" in ref:
        return redirect(url_for("project_tasks_manage", project_id=project_id))
    if f"/projects/{project_id}/milestones-manage" in ref:
        return redirect(url_for("project_milestones_manage", project_id=project_id))
    if f"/projects/{project_id}/risks-manage" in ref:
        return redirect(url_for("project_risks_manage", project_id=project_id))
    return redirect(url_for("project_detail", project_id=project_id))


def _project_detail_context(project_id: int, project: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, Any]:
    if not has_module_permission("project", "view"):
        flash("无权限查看项目详情。", "danger")
        return None, redirect(url_for("dashboard"))
    if project is None:
        scope_clause, scope_params = build_project_visibility_clause("p")
        project = fetchone(
            f"""
            SELECT p.*, c.name AS customer_name
            FROM projects p
            LEFT JOIN customers c ON c.id = p.customer_id
            WHERE p.id = ? AND p.deleted_at IS NULL AND {scope_clause}
            """,
            tuple([project_id] + scope_params),
        )
    if project is None:
        flash("项目不存在、已删除或无权限查看。", "danger")
        return None, redirect(url_for("dashboard"))

    ensure_sqlite_attachments_schema()
    customers = fetchall("SELECT id, name FROM customers ORDER BY name ASC")
    tasks = fetchall("SELECT * FROM tasks WHERE project_id = ? ORDER BY updated_at DESC", (project_id,))
    for _t in tasks:
        _t["id"] = int(_t["id"])
    milestones = fetchall("SELECT * FROM milestones WHERE project_id = ? ORDER BY due_date ASC", (project_id,))
    for _m in milestones:
        _m["id"] = int(_m["id"])
    risks = fetchall("SELECT * FROM risks WHERE project_id = ? ORDER BY updated_at DESC", (project_id,))
    project_attachments = fetchall(
        "SELECT * FROM attachments WHERE project_id = ? AND task_id IS NULL AND milestone_id IS NULL ORDER BY uploaded_at DESC",
        (project_id,),
    )
    task_attachment_rows = fetchall(
        "SELECT * FROM attachments WHERE project_id = ? AND task_id IS NOT NULL ORDER BY uploaded_at DESC",
        (project_id,),
    )
    milestone_attachment_rows = fetchall(
        "SELECT * FROM attachments WHERE project_id = ? AND milestone_id IS NOT NULL ORDER BY uploaded_at DESC",
        (project_id,),
    )
    task_attachments_by_id: dict[int, list[dict[str, Any]]] = {}
    for attachment in task_attachment_rows:
        task_attachments_by_id.setdefault(int(attachment["task_id"]), []).append(attachment)
    milestone_attachments_by_id: dict[int, list[dict[str, Any]]] = {}
    for attachment in milestone_attachment_rows:
        milestone_attachments_by_id.setdefault(int(attachment["milestone_id"]), []).append(attachment)
    if session_is_system_admin():
        activity_logs = fetchall(
            "SELECT * FROM project_activity_logs WHERE project_id = ? ORDER BY created_at DESC LIMIT 50",
            (project_id,),
        )
    else:
        activity_logs = []
    progress_entries = fetchall(
        """
        SELECT e.*, u.display_name AS author_display_name, u.username AS author_username
        FROM project_progress_entries e
        LEFT JOIN users u ON u.id = e.created_by
        WHERE e.project_id = ?
        ORDER BY e.created_at DESC
        """,
        (project_id,),
    )
    can_fill_progress = user_is_project_manager(project)
    project_members = fetchall(
        """
        SELECT u.id, u.username, u.display_name, m.created_at,
               COALESCE(NULLIF(TRIM(r.code), ''), NULLIF(TRIM(u.role), '')) AS role_code
        FROM project_members m
        JOIN users u ON u.id = m.user_id
        LEFT JOIN roles r ON r.id = u.role_id
        WHERE m.project_id = ?
        ORDER BY m.created_at ASC
        """,
        (project_id,),
    )
    manager_user_id = find_pm_user_id_for_manager_label(project.get("manager") or "")
    if manager_user_id and not any(int(m["id"]) == int(manager_user_id) for m in project_members):
        _ensure_project_member(project_id, manager_user_id)
        member_row = fetchone(
            """
            SELECT u.id, u.username, u.display_name, m.created_at,
                   COALESCE(NULLIF(TRIM(r.code), ''), NULLIF(TRIM(u.role), '')) AS role_code
            FROM project_members m
            JOIN users u ON u.id = m.user_id
            LEFT JOIN roles r ON r.id = u.role_id
            WHERE m.project_id = ? AND m.user_id = ?
            """,
            (project_id, manager_user_id),
        )
        if member_row:
            project_members.append(member_row)
    project_member_options = [
        {"id": int(m["id"]), "username": m.get("username") or "", "display_name": m.get("display_name") or ""}
        for m in project_members
    ]
    can_manage_members = can_manage_project_members(project)
    task_title_by_id = {int(t["id"]): t["title"] for t in tasks}
    by_tid = {int(t["id"]): t for t in tasks}
    task_pred_pending: dict[int, bool] = {}
    for t in tasks:
        dep = t.get("depends_on_task_id")
        if not dep:
            continue
        pred = by_tid.get(int(dep))
        if pred and pred.get("status") != "done":
            task_pred_pending[int(t["id"])] = True
    project_attachments_public = [attachment_public_dict(a) for a in project_attachments]
    # JSON 对象键均为字符串，便于前端用 String(id) 稳定取任务/里程碑附件列表
    task_attachments_public_by_id = {
        str(tid): [attachment_public_dict(a) for a in lst] for tid, lst in task_attachments_by_id.items()
    }
    milestone_attachments_public_by_id = {
        str(mid): [attachment_public_dict(a) for a in lst] for mid, lst in milestone_attachments_by_id.items()
    }
    today_iso = date.today().isoformat()
    done_task_statuses = {"done", "closed", "completed"}
    progress_values = [
        float(t.get("progress"))
        for t in tasks
        if t.get("progress") is not None and str(t.get("progress")).strip() != ""
    ]
    progress_value = int(round(sum(progress_values) / len(progress_values))) if progress_values else 0
    completed_task_count = sum(1 for t in tasks if str(t.get("status") or "").strip() in done_task_statuses)
    pending_task_count = len(tasks) - completed_task_count
    overdue_task_count = sum(
        1
        for t in tasks
        if str(t.get("planned_end") or "").strip()
        and str(t.get("planned_end")) < today_iso
        and str(t.get("status") or "").strip() not in done_task_statuses
    )
    delayed_milestones = sum(
        1
        for m in milestones
        if str(m.get("due_date") or "").strip()
        and str(m.get("due_date")) < today_iso
        and str(m.get("status") or "").strip() not in done_task_statuses
    )
    next_milestone_due_date = None
    milestone_candidates = [
        str(m.get("due_date") or "").strip()
        for m in milestones
        if str(m.get("due_date") or "").strip() and str(m.get("status") or "").strip() not in done_task_statuses
    ]
    milestone_candidates.sort()
    if milestone_candidates:
        next_milestone_due_date = milestone_candidates[0]
    open_high_risks = sum(
        1
        for r in risks
        if str(r.get("status") or "").strip() != "closed" and str(r.get("level") or "").strip() == "high"
    )
    pending_close_approval = has_pending_approval("project", project_id, "close_project")
    summary_metrics = {
        "pending_task_count": pending_task_count,
        "overdue_task_count": overdue_task_count,
        "completed_task_count": completed_task_count,
        "delayed_milestone_count": delayed_milestones,
        "next_milestone_due_date": next_milestone_due_date,
        "open_high_risk_count": open_high_risks,
        "pending_close_approval": pending_close_approval,
    }
    approval_candidates = fetch_approval_candidates(["project_director", "management", "admin"])
    ctx = {
        "project": project,
        "customers": customers,
        "progress": progress_value,
        "tasks": tasks,
        "milestones": milestones,
        "risks": risks,
        "attachments": project_attachments,
        "project_attachments_public": project_attachments_public,
        "task_attachments_by_id": task_attachments_by_id,
        "task_attachments_public_by_id": task_attachments_public_by_id,
        "milestone_attachments_by_id": milestone_attachments_by_id,
        "milestone_attachments_public_by_id": milestone_attachments_public_by_id,
        "activity_logs": activity_logs,
        "task_title_by_id": task_title_by_id,
        "task_pred_pending": task_pred_pending,
        "progress_entries": progress_entries,
        "can_fill_progress": can_fill_progress,
        "project_members": project_members,
        "project_member_options": project_member_options,
        "manager_user_id": manager_user_id,
        "can_manage_members": can_manage_members,
        "can_manage_phases": user_is_project_manager(project),
        "summary_metrics": summary_metrics,
        "approval_candidates": approval_candidates,
    }
    return ctx, None


@app.route("/projects/<int:project_id>")
def project_detail(project_id: int):
    ctx, err = _project_detail_context(project_id)
    if err is not None:
        return err
    return render_template("project_detail.html", **ctx)


def _render_project_manage_page(project_id: int, template_name: str):
    ctx, err = _project_detail_context(project_id)
    if err is not None:
        return err
    if not has_module_permission("project", "manage"):
        flash("无权限管理该项目。", "danger")
        return redirect(url_for("project_detail", project_id=project_id))
    return render_template(template_name, **ctx)


def _load_project_ai_context(project_id: int, *, require_progress_owner: bool = False) -> tuple[dict[str, Any] | None, Any]:
    if not has_module_permission("project", "view"):
        return None, (jsonify({"error": "无权限访问该项目。"}), 403)
    project = fetchone(
        """
        SELECT p.*, c.name AS customer_name
        FROM projects p
        LEFT JOIN customers c ON c.id = p.customer_id
        WHERE p.id = ? AND p.deleted_at IS NULL
        """,
        (project_id,),
    )
    if project is None:
        return None, (jsonify({"error": "项目不存在或已删除。"}), 404)
    if require_progress_owner and not user_is_project_manager(project):
        return None, (jsonify({"error": "仅项目经理可生成进展草稿。"}), 403)
    ctx, err = _project_detail_context(project_id)
    if err is not None or ctx is None:
        return None, (jsonify({"error": "项目上下文加载失败。"}), 500)
    return ctx, None


@app.route("/projects/<int:project_id>/tasks-manage")
def project_tasks_manage(project_id: int):
    return _render_project_manage_page(project_id, "project_manage_tasks.html")


@app.route("/projects/<int:project_id>/milestones-manage")
def project_milestones_manage(project_id: int):
    return _render_project_manage_page(project_id, "project_manage_milestones.html")


@app.route("/projects/<int:project_id>/risks-manage")
def project_risks_manage(project_id: int):
    return _render_project_manage_page(project_id, "project_manage_risks.html")


@app.route("/projects/<int:project_id>/ai/progress-draft", methods=["POST"])
def project_ai_progress_draft(project_id: int):
    ctx, err = _load_project_ai_context(project_id, require_progress_owner=True)
    if err is not None:
        return err
    payload = request.get_json(silent=True) or request.form
    time_range = str((payload.get("time_range") if payload else "") or (payload.get("range") if payload else "") or "7d").strip().lower()
    days = 14 if time_range in {"14d", "two_weeks", "2w"} else 7
    result = build_project_progress_draft(
        ctx["project"],
        ctx["tasks"],
        ctx["milestones"],
        ctx["risks"],
        ctx["progress_entries"],
        days=days,
    )
    generation_id = log_ai_generation(
        "project_progress_draft",
        "project",
        project_id,
        {
            "time_range": time_range,
            "task_count": len(ctx["tasks"]),
            "milestone_count": len(ctx["milestones"]),
            "risk_count": len(ctx["risks"]),
            "progress_entry_count": len(ctx["progress_entries"]),
        },
        result,
    )
    get_db().commit()
    return jsonify({**result, "generation_id": generation_id, "generated_at": now_iso(), "time_range": time_range})


@app.route("/projects/<int:project_id>/ai/risk-summary", methods=["POST"])
def project_ai_risk_summary(project_id: int):
    ctx, err = _load_project_ai_context(project_id)
    if err is not None:
        return err
    result = build_project_risk_summary(
        ctx["project"],
        ctx["tasks"],
        ctx["milestones"],
        ctx["risks"],
    )
    generation_id = log_ai_generation(
        "project_risk_summary",
        "project",
        project_id,
        {
            "task_count": len(ctx["tasks"]),
            "milestone_count": len(ctx["milestones"]),
            "risk_count": len(ctx["risks"]),
            "summary_metrics": ctx.get("summary_metrics") or {},
        },
        result,
    )
    get_db().commit()
    return jsonify(
        {
            **result,
            "generation_id": generation_id,
            "generated_at": now_iso(),
            "recommendations": result.get("priorities") or [],
        }
    )


@app.route("/projects/<int:project_id>/ai/report-draft", methods=["POST"])
def project_ai_report_draft(project_id: int):
    ctx, err = _load_project_ai_context(project_id)
    if err is not None:
        return err
    payload = request.get_json(silent=True) or request.form
    period_raw = str(
        (payload.get("period") if payload else "")
        or (payload.get("report_type") if payload else "")
        or (payload.get("range") if payload else "")
        or "week"
    ).strip().lower()
    period = "month" if period_raw in {"month", "monthly", "30d"} else "week"
    result = build_project_report_draft(
        ctx["project"],
        ctx["tasks"],
        ctx["milestones"],
        ctx["risks"],
        ctx["progress_entries"],
        period=period,
    )
    generation_id = log_ai_generation(
        "project_report_draft",
        "project",
        project_id,
        {
            "period": period,
            "task_count": len(ctx["tasks"]),
            "milestone_count": len(ctx["milestones"]),
            "risk_count": len(ctx["risks"]),
            "progress_entry_count": len(ctx["progress_entries"]),
        },
        result,
    )
    get_db().commit()
    coverage = "近30天" if period == "month" else "近7天"
    return jsonify({**result, "generation_id": generation_id, "generated_at": now_iso(), "coverage": coverage})


@app.route("/projects/<int:project_id>/progress", methods=["POST"])
def add_project_progress(project_id: int):
    if not has_module_permission("project", "view"):
        flash("无权限访问该项目。", "danger")
        return redirect(url_for("dashboard"))
    project = fetchone(
        """
        SELECT p.*, c.name AS customer_name
        FROM projects p
        LEFT JOIN customers c ON c.id = p.customer_id
        WHERE p.id = ? AND p.deleted_at IS NULL
        """,
        (project_id,),
    )
    if project is None:
        flash("项目不存在或已删除。", "danger")
        return redirect(url_for("dashboard"))
    if not user_is_project_manager(project):
        flash("仅项目经理可填写项目进展。", "danger")
        return redirect(url_for("project_detail", project_id=project_id))
    body = (request.form.get("body") or "").strip()
    if not body:
        flash("进展内容不能为空。", "danger")
        return redirect(url_for("project_detail", project_id=project_id))
    if len(body) > 8000:
        body = body[:8000]
    generation_id_raw = (request.form.get("generation_id") or "").strip()
    uid = session.get("user_id")
    execute(
        """
        INSERT INTO project_progress_entries(project_id, body, created_by, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (project_id, body, uid, now_iso()),
    )
    if generation_id_raw.isdigit():
        set_ai_generation_accepted(int(generation_id_raw), True)
    log_project_activity(project_id, "登记项目进展", "progress", body[:120], None)
    touch_project(project_id)
    get_db().commit()
    flash("项目进展已提交。", "success")
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/projects/<int:project_id>/progress/<int:entry_id>/delete", methods=["POST"])
@admin_required
def delete_project_progress(project_id: int, entry_id: int):
    row = fetchone(
        "SELECT id FROM project_progress_entries WHERE id = ? AND project_id = ?",
        (entry_id, project_id),
    )
    if row is None:
        flash("记录不存在。", "danger")
        return redirect(url_for("project_detail", project_id=project_id))
    execute("DELETE FROM project_progress_entries WHERE id = ? AND project_id = ?", (entry_id, project_id))
    touch_project(project_id)
    get_db().commit()
    flash("已删除该条进展。", "success")
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/api/users/pm-search")
def api_users_pm_search():
    if not session.get("user_id"):
        return jsonify({"users": []}), 401
    if not has_module_permission("project", "manage"):
        return jsonify({"users": []}), 403
    q = request.args.get("q", "").strip()
    rows = search_pm_users(q)
    return jsonify(
        {
            "users": [
                {"id": int(r["id"]), "username": r.get("username") or "", "display_name": r.get("display_name") or ""}
                for r in rows
            ]
        }
    )


@app.route("/api/projects/<int:project_id>/member-candidates")
def api_project_member_candidates(project_id: int):
    if not session.get("user_id"):
        return jsonify({"users": []}), 401
    if not has_module_permission("project", "view"):
        return jsonify({"users": []}), 403
    project = fetchone("SELECT * FROM projects WHERE id = ? AND deleted_at IS NULL", (project_id,))
    if project is None:
        return jsonify({"users": []}), 404
    if not can_manage_project_members(project):
        return jsonify({"users": []}), 403
    q = request.args.get("q", "").strip()
    rows = search_member_candidates(project_id, q)
    return jsonify(
        {
            "users": [
                {"id": int(r["id"]), "username": r.get("username") or "", "display_name": r.get("display_name") or ""}
                for r in rows
            ]
        }
    )


@app.route("/projects/<int:project_id>/members/add", methods=["POST"])
def add_project_member(project_id: int):
    if not has_module_permission("project", "view"):
        flash("无权限访问该项目。", "danger")
        return redirect(url_for("dashboard"))
    scope_clause, scope_params = build_project_visibility_clause("p")
    project = fetchone(
        f"SELECT * FROM projects p WHERE p.id = ? AND p.deleted_at IS NULL AND {scope_clause}",
        tuple([project_id] + scope_params),
    )
    if project is None:
        flash("项目不存在或无权限操作。", "danger")
        return redirect(url_for("dashboard"))
    if not can_manage_project_members(project):
        flash("无权限管理项目成员。", "danger")
        return redirect(url_for("project_detail", project_id=project_id))
    uid = parse_int_form_value(request.form.get("user_id"), 0) or 0
    if not uid:
        flash("请选择要加入的用户。", "danger")
        return redirect(url_for("project_detail", project_id=project_id))
    if not fetchone("SELECT id FROM users WHERE id = ?", (uid,)):
        flash("用户不存在。", "danger")
        return redirect(url_for("project_detail", project_id=project_id))
    if fetchone("SELECT id FROM project_members WHERE project_id = ? AND user_id = ?", (project_id, uid)):
        flash("该用户已在项目成员中。", "warning")
        return redirect(url_for("project_detail", project_id=project_id))
    execute(
        "INSERT INTO project_members(project_id, user_id, created_at) VALUES (?, ?, ?)",
        (project_id, uid, now_iso()),
    )
    u = fetchone("SELECT display_name, username FROM users WHERE id = ?", (uid,))
    label = (u.get("display_name") or u.get("username") or str(uid)) if u else str(uid)
    log_project_activity(project_id, "添加项目成员", "member", label, None)
    touch_project(project_id)
    get_db().commit()
    flash("已添加项目成员。", "success")
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/projects/<int:project_id>/members/<int:user_id>/remove", methods=["POST"])
def remove_project_member(project_id: int, user_id: int):
    if not has_module_permission("project", "view"):
        flash("无权限访问该项目。", "danger")
        return redirect(url_for("dashboard"))
    scope_clause, scope_params = build_project_visibility_clause("p")
    project = fetchone(
        f"SELECT * FROM projects p WHERE p.id = ? AND p.deleted_at IS NULL AND {scope_clause}",
        tuple([project_id] + scope_params),
    )
    if project is None:
        flash("项目不存在或无权限操作。", "danger")
        return redirect(url_for("dashboard"))
    if not can_manage_project_members(project):
        flash("无权限管理项目成员。", "danger")
        return redirect(url_for("project_detail", project_id=project_id))
    if not fetchone("SELECT id FROM project_members WHERE project_id = ? AND user_id = ?", (project_id, user_id)):
        flash("记录不存在。", "danger")
        return redirect(url_for("project_detail", project_id=project_id))
    execute("DELETE FROM project_members WHERE project_id = ? AND user_id = ?", (project_id, user_id))
    u = fetchone("SELECT display_name, username FROM users WHERE id = ?", (user_id,))
    label = (u.get("display_name") or u.get("username") or str(user_id)) if u else str(user_id)
    log_project_activity(project_id, "移除项目成员", "member", label, None)
    touch_project(project_id)
    get_db().commit()
    flash("已移除项目成员。", "success")
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/projects/<int:project_id>/update-info", methods=["POST"])
def update_project_info(project_id: int):
    scope_clause, scope_params = build_project_visibility_clause("p")
    project = fetchone(
        f"SELECT id FROM projects p WHERE p.id = ? AND p.deleted_at IS NULL AND {scope_clause}",
        tuple([project_id] + scope_params),
    )
    if project is None:
        flash("项目不存在或无权限操作。", "danger")
        return redirect(url_for("dashboard"))
    if not can_manage_project_record(project):
        flash("无权限更新项目基础信息。", "danger")
        return redirect(url_for("project_detail", project_id=project_id))

    customer_id_raw = request.form.get("customer_id", "").strip()
    name = request.form.get("name", "").strip()
    project_type = request.form.get("project_type", "").strip()
    status = request.form.get("status", "").strip()
    start_date = request.form.get("start_date", "").strip() or None
    end_date = request.form.get("end_date", "").strip() or None
    description = request.form.get("description", "").strip()
    manager_user_id = parse_int_form_value(request.form.get("manager_user_id"), 0) or 0
    current_stage_raw = request.form.get("current_stage", "").strip()
    current_stage = normalize_stage(current_stage_raw) if current_stage_raw else None
    if manager_user_id and user_is_pm_user(manager_user_id):
        manager = manager_display_string(manager_user_id)
    else:
        legacy = request.form.get("manager", "").strip()
        puid = find_pm_user_id_for_manager_label(legacy)
        if puid and user_is_pm_user(puid):
            manager = manager_display_string(puid)
            manager_user_id = puid
        else:
            flash("请从下拉列表中选择项目经理（须为「项目经理」角色用户）。", "danger")
            return redirect(url_for("project_detail", project_id=project_id))

    if not customer_id_raw or not name or not project_type or not manager or not status:
        flash("客户、项目名称、项目类型、项目经理和状态为必填项。", "danger")
        return redirect(url_for("project_detail", project_id=project_id))

    customer_id = parse_int_form_value(customer_id_raw)
    if customer_id is None:
        flash("客户信息无效。", "danger")
        return redirect(url_for("project_detail", project_id=project_id))

    customer = fetchone("SELECT id FROM customers WHERE id = ?", (customer_id,))
    if customer is None:
        flash("客户不存在。", "danger")
        return redirect(url_for("project_detail", project_id=project_id))

    existing = fetchone("SELECT current_stage FROM projects WHERE id = ?", (project_id,))
    stage_changed = current_stage is not None and existing is not None and str(existing["current_stage"] or "") != current_stage
    execute(
        """
        UPDATE projects
        SET customer_id = ?, name = ?, project_type = ?, manager = ?, status = ?, current_stage = COALESCE(?, current_stage), start_date = ?, end_date = ?, description = ?, updated_at = ?
        WHERE id = ? AND deleted_at IS NULL
        """,
        (customer_id, name, project_type, manager, status, current_stage, start_date, end_date, description, now_iso(), project_id),
    )
    _ensure_project_member(project_id, manager_user_id)
    if stage_changed:
        execute(
            "INSERT INTO project_stage_logs(project_id, stage, note, changed_at) VALUES (?, ?, ?, ?)",
            (project_id, current_stage, "编辑信息更新阶段", now_iso()),
        )
        log_project_activity(project_id, "更新项目阶段", "stage", PROJECT_STAGE_LABELS.get(current_stage, current_stage), "编辑信息更新阶段")
    log_project_activity(project_id, "更新项目信息", "project", name)
    get_db().commit()
    flash("项目基础信息已更新。", "success")
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/projects/<int:project_id>/tasks/template")
def download_task_template(project_id: int):
    if not has_module_permission("project", "manage"):
        flash("无权限下载任务模板。", "danger")
        return redirect(url_for("project_detail", project_id=project_id))
    if not ensure_excel_support():
        flash("未安装 Excel 处理组件，请联系管理员安装 openpyxl。", "danger")
        return redirect(url_for("project_detail", project_id=project_id))
    project = fetchone("SELECT id, name FROM projects WHERE id = ? AND deleted_at IS NULL", (project_id,))
    if project is None:
        flash("项目不存在。", "danger")
        return redirect(url_for("project_manage"))

    wb = Workbook()
    ws = wb.active
    ws.title = "任务导入模板"
    ws.append(["任务名称*", "负责人*", "状态", "优先级", "计划完成日期", "进度", "阻塞原因"])
    ws.append(["需求评审", "张三", "待开始", "中", "2026-04-30", 0, ""])
    ws.append(["接口联调", "李四", "进行中", "高", "2026-05-10", 30, "等待第三方接口"])
    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    filename = f"{project['name']}_任务导入模板.xlsx"
    return send_file(
        bio,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/projects/<int:project_id>/milestones/template")
def download_milestone_template(project_id: int):
    if not has_module_permission("project", "manage"):
        flash("无权限下载里程碑模板。", "danger")
        return redirect(url_for("project_detail", project_id=project_id))
    if not ensure_excel_support():
        flash("未安装 Excel 处理组件，请联系管理员安装 openpyxl。", "danger")
        return redirect(url_for("project_detail", project_id=project_id))
    project = fetchone("SELECT id, name FROM projects WHERE id = ? AND deleted_at IS NULL", (project_id,))
    if project is None:
        flash("项目不存在。", "danger")
        return redirect(url_for("project_manage"))

    wb = Workbook()
    ws = wb.active
    ws.title = "里程碑导入模板"
    ws.append(["里程碑名称*", "负责人*", "截止日期*", "状态"])
    ws.append(["方案评审通过", "张三", "2026-04-20", "未完成"])
    ws.append(["联调完成", "李四", "2026-05-12", "延期"])
    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    filename = f"{project['name']}_里程碑导入模板.xlsx"
    return send_file(
        bio,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/projects/<int:project_id>/tasks/import", methods=["POST"])
def import_tasks(project_id: int):
    if not has_module_permission("project", "manage"):
        flash("无权限导入任务。", "danger")
        return redirect(url_for("project_detail", project_id=project_id))
    if not ensure_excel_support():
        flash("未安装 Excel 处理组件，请联系管理员安装 openpyxl。", "danger")
        return redirect(url_for("project_detail", project_id=project_id))
    project = fetchone("SELECT id FROM projects WHERE id = ? AND deleted_at IS NULL", (project_id,))
    if project is None:
        flash("项目不存在。", "danger")
        return redirect(url_for("project_manage"))

    file = request.files.get("file")
    if file is None or file.filename.strip() == "":
        flash("请选择任务导入文件。", "danger")
        return redirect(url_for("project_detail", project_id=project_id))
    if not file.filename.lower().endswith(".xlsx"):
        flash("仅支持 .xlsx 文件导入。", "danger")
        return redirect(url_for("project_detail", project_id=project_id))

    wb = load_workbook(file, data_only=True)
    ws = wb.active
    imported = 0
    errors = []
    ts = now_iso()
    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        title = str(row[0]).strip() if row[0] is not None else ""
        assignee = str(row[1]).strip() if row[1] is not None else ""
        status_raw = str(row[2]).strip() if row[2] is not None else "todo"
        priority_raw = str(row[3]).strip() if row[3] is not None else "medium"
        planned_end = parse_date_text(row[4])
        progress_raw = row[5]
        blocked_reason = str(row[6]).strip() if row[6] is not None else None

        if not title and not assignee and row[2] is None and row[3] is None and row[4] is None and row[5] is None:
            continue
        if not title or not assignee:
            errors.append(f"第{row_idx}行：任务名称和负责人必填。")
            continue
        status = TASK_STATUS_IMPORT_MAP.get(status_raw, None)
        if status is None:
            errors.append(f"第{row_idx}行：状态无效（{status_raw}）。")
            continue
        priority = PRIORITY_IMPORT_MAP.get(priority_raw, None)
        if priority is None:
            errors.append(f"第{row_idx}行：优先级无效（{priority_raw}）。")
            continue
        if row[4] is not None and planned_end is None:
            errors.append(f"第{row_idx}行：计划完成日期格式无效。")
            continue
        try:
            progress = int(progress_raw) if progress_raw not in (None, "") else 0
        except Exception:
            errors.append(f"第{row_idx}行：进度必须是数字。")
            continue
        if progress < 0 or progress > 100:
            errors.append(f"第{row_idx}行：进度需在0-100之间。")
            continue

        execute(
            """
            INSERT INTO tasks(project_id, title, assignee, status, progress, priority, planned_end, blocked_reason, depends_on_task_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (project_id, title, assignee, status, progress, priority, planned_end, blocked_reason, ts),
        )
        imported += 1

    touch_project(project_id)
    get_db().commit()
    if errors:
        flash("任务导入部分完成：" + f"成功{imported}条；失败{len(errors)}条。", "warning")
        flash("；".join(errors[:5]), "warning")
    else:
        flash(f"任务导入成功，共{imported}条。", "success")
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/projects/<int:project_id>/milestones/import", methods=["POST"])
def import_milestones(project_id: int):
    if not has_module_permission("project", "manage"):
        flash("无权限导入里程碑。", "danger")
        return redirect(url_for("project_detail", project_id=project_id))
    if not ensure_excel_support():
        flash("未安装 Excel 处理组件，请联系管理员安装 openpyxl。", "danger")
        return redirect(url_for("project_detail", project_id=project_id))
    project = fetchone("SELECT id FROM projects WHERE id = ? AND deleted_at IS NULL", (project_id,))
    if project is None:
        flash("项目不存在。", "danger")
        return redirect(url_for("project_manage"))

    file = request.files.get("file")
    if file is None or file.filename.strip() == "":
        flash("请选择里程碑导入文件。", "danger")
        return redirect(url_for("project_detail", project_id=project_id))
    if not file.filename.lower().endswith(".xlsx"):
        flash("仅支持 .xlsx 文件导入。", "danger")
        return redirect(url_for("project_detail", project_id=project_id))

    wb = load_workbook(file, data_only=True)
    ws = wb.active
    imported = 0
    errors = []
    ts = now_iso()
    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        title = str(row[0]).strip() if row[0] is not None else ""
        owner = str(row[1]).strip() if row[1] is not None else ""
        due_date = parse_date_text(row[2])
        status_raw = str(row[3]).strip() if row[3] is not None else "open"
        if not title and not owner and row[2] is None and row[3] is None:
            continue
        if not title or not owner or due_date is None:
            errors.append(f"第{row_idx}行：里程碑名称、负责人、截止日期必填且格式有效。")
            continue
        status = MILESTONE_STATUS_IMPORT_MAP.get(status_raw, None)
        if status is None:
            errors.append(f"第{row_idx}行：状态无效（{status_raw}）。")
            continue
        execute(
            """
            INSERT INTO milestones(project_id, title, owner, due_date, status, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (project_id, title, owner, due_date, status, ts),
        )
        imported += 1

    touch_project(project_id)
    get_db().commit()
    if errors:
        flash("里程碑导入部分完成：" + f"成功{imported}条；失败{len(errors)}条。", "warning")
        flash("；".join(errors[:5]), "warning")
    else:
        flash(f"里程碑导入成功，共{imported}条。", "success")
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/projects/<int:project_id>/attachments/new", methods=["POST"])
def upload_attachment(project_id: int):
    if not has_module_permission("project", "manage"):
        flash("无权限上传附件。", "danger")
        return redirect(url_for("dashboard"))
    project = fetchone("SELECT id FROM projects WHERE id = ? AND deleted_at IS NULL", (project_id,))
    if project is None:
        flash("项目不存在，无法上传附件。", "danger")
        return redirect(url_for("dashboard"))

    ensure_sqlite_attachments_schema()
    file = request.files.get("file")
    category = request.form.get("category", "合同").strip() or "合同"
    target_type = request.form.get("target_type", "project").strip() or "project"
    target_id_raw = request.form.get("target_id", "").strip()
    if file is None or file.filename.strip() == "":
        flash("请选择要上传的附件。", "danger")
        return redirect(url_for("project_detail", project_id=project_id))

    task_id = None
    milestone_id = None
    if target_type == "task":
        if not target_id_raw:
            flash("请选择要关联的任务。", "danger")
            return redirect(url_for("project_detail", project_id=project_id))
        try:
            target_id = int(target_id_raw)
        except ValueError:
            flash("任务目标无效。", "danger")
            return redirect(url_for("project_detail", project_id=project_id))
        task = fetchone("SELECT id, project_id FROM tasks WHERE id = ?", (target_id,))
        if task is None or int(task["project_id"]) != project_id:
            flash("任务不存在或不属于该项目。", "danger")
            return redirect(url_for("project_detail", project_id=project_id))
        task_id = int(task["id"])
    elif target_type == "milestone":
        if not target_id_raw:
            flash("请选择要关联的里程碑。", "danger")
            return redirect(url_for("project_detail", project_id=project_id))
        try:
            target_id = int(target_id_raw)
        except ValueError:
            flash("里程碑目标无效。", "danger")
            return redirect(url_for("project_detail", project_id=project_id))
        milestone = fetchone("SELECT id, project_id FROM milestones WHERE id = ?", (target_id,))
        if milestone is None or int(milestone["project_id"]) != project_id:
            flash("里程碑不存在或不属于该项目。", "danger")
            return redirect(url_for("project_detail", project_id=project_id))
        milestone_id = int(milestone["id"])
    elif target_type != "project":
        flash("附件关联类型无效。", "danger")
        return redirect(url_for("project_detail", project_id=project_id))

    safe_name = secure_filename(file.filename)
    if not safe_name:
        flash("附件文件名无效。", "danger")
        return redirect(url_for("project_detail", project_id=project_id))

    stored_name = f"{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{safe_name}"
    save_path = UPLOAD_DIR / stored_name
    file.save(save_path)
    file_size = save_path.stat().st_size
    uploader = (session.get("display_name") or session.get("username") or "").strip() or "系统"

    try:
        execute(
            """
            INSERT INTO attachments(project_id, task_id, milestone_id, category, file_name, stored_name, uploaded_at, uploaded_by, file_size)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (project_id, task_id, milestone_id, category, file.filename, stored_name, now_iso(), uploader, file_size),
        )
        touch_project(project_id)
        get_db().commit()
    except Exception:
        get_db().rollback()
        try:
            save_path.unlink(missing_ok=True)
        except OSError:
            pass
        app.logger.exception("upload_attachment 数据库写入失败 project_id=%s", project_id)
        flash(
            "附件未能写入数据库（已删除刚上传的临时文件）。若表结构曾过旧，请刷新页面后重新上传。",
            "danger",
        )
        return redirect(url_for("project_detail", project_id=project_id))

    flash("附件上传成功。", "success")
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/attachments/<int:attachment_id>/download")
def download_attachment(attachment_id: int):
    attachment = fetchone("SELECT * FROM attachments WHERE id = ?", (attachment_id,))
    if attachment is None:
        flash("附件不存在。", "danger")
        return redirect(url_for("dashboard"))
    return send_from_directory(
        app.config["UPLOAD_FOLDER"],
        attachment["stored_name"],
        as_attachment=True,
        download_name=attachment["file_name"],
    )


@app.route("/attachments/<int:attachment_id>/delete", methods=["POST"])
def delete_attachment(attachment_id: int):
    if not has_module_permission("project", "manage"):
        flash("无权限删除附件。", "danger")
        return redirect(url_for("dashboard"))
    attachment = fetchone("SELECT * FROM attachments WHERE id = ?", (attachment_id,))
    if attachment is None:
        flash("附件不存在。", "danger")
        return redirect(url_for("dashboard"))

    remove_attachment_file(attachment["stored_name"])
    execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))
    touch_project(attachment["project_id"])
    get_db().commit()
    flash("附件已删除。", "success")
    return redirect(url_for("project_detail", project_id=attachment["project_id"]))


@app.route("/projects/<int:project_id>/stage/update", methods=["POST"])
def update_project_stage(project_id: int):
    if not has_module_permission("project", "manage"):
        flash("无权限更新项目阶段。", "danger")
        return redirect(url_for("dashboard"))
    project = fetchone("SELECT id, current_stage FROM projects WHERE id = ? AND deleted_at IS NULL", (project_id,))
    if project is None:
        flash("项目不存在。", "danger")
        return redirect(url_for("dashboard"))

    stage = normalize_stage(request.form.get("current_stage", "init").strip())
    note = request.form.get("note", "").strip() or "阶段更新"
    execute(
        "UPDATE projects SET current_stage = ?, updated_at = ? WHERE id = ? AND deleted_at IS NULL",
        (stage, now_iso(), project_id),
    )
    execute(
        "INSERT INTO project_stage_logs(project_id, stage, note, changed_at) VALUES (?, ?, ?, ?)",
        (project_id, stage, note, now_iso()),
    )
    log_project_activity(project_id, "更新项目阶段", "stage", PROJECT_STAGE_LABELS.get(stage, stage), note)
    get_db().commit()
    flash("项目阶段已更新。", "success")
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/projects/<int:project_id>/submit-close-approval", methods=["POST"])
def submit_project_close_approval(project_id: int):
    scope_clause, scope_params = build_project_visibility_clause("p")
    project = fetchone(
        f"SELECT id, name, manager, status FROM projects p WHERE p.id = ? AND p.deleted_at IS NULL AND {scope_clause}",
        tuple([project_id] + scope_params),
    )
    if project is None:
        flash("项目不存在或无权限操作。", "danger")
        return redirect(url_for("project_detail", project_id=project_id))
    if not can_manage_project_record(project):
        flash("无权限提交项目结项审批。", "danger")
        return redirect(url_for("project_detail", project_id=project_id))
    if (project.get("status") or "").strip() == "closed":
        flash("项目已结项，不能再次提交结项审批。", "warning")
        return redirect(url_for("project_detail", project_id=project_id))
    if has_pending_approval("project", project_id, "close_project"):
        flash("该项目已有待处理的结项审批。", "warning")
        return redirect(url_for("project_detail", project_id=project_id))
    approver_user_id = parse_int_form_value(request.form.get("approver_user_id"))
    apply_note = request.form.get("apply_note", "").strip() or None
    approver = request.form.get("approver", "").strip()
    approval_candidates = fetch_approval_candidates(["project_director", "management", "admin"])
    candidate_map = {int(u["id"]): str(u.get("display_name") or "").strip() for u in approval_candidates}
    if approver_user_id:
        if approver_user_id not in candidate_map:
            flash("请选择有效的审批人。", "danger")
            return redirect(url_for("project_detail", project_id=project_id))
        approver = candidate_map[approver_user_id]
    if not approver:
        flash("请选择审批人。", "danger")
        return redirect(url_for("project_detail", project_id=project_id))
    applicant = project["manager"] or "项目经理"
    submit_approval(
        "project",
        project_id,
        f"项目结项审批：{project['name']}",
        "close_project",
        applicant,
        approver,
        apply_note,
    )
    get_db().commit()
    flash("已提交项目结项审批。", "success")
    return redirect(url_for("project_detail", project_id=project_id))


@app.route("/projects/<int:project_id>/tasks/new", methods=["POST"])
def create_task(project_id: int):
    scope_clause, scope_params = build_project_visibility_clause("p")
    project = fetchone(
        f"SELECT id, manager FROM projects p WHERE p.id = ? AND p.deleted_at IS NULL AND {scope_clause}",
        tuple([project_id] + scope_params),
    )
    if project is None or not can_manage_project_record(project):
        flash("无权限新增任务。", "danger")
        return _redirect_project_entity(project_id)
    title = request.form.get("title", "").strip()
    assignee_user_id = parse_int_form_value(request.form.get("assignee_user_id"), 0) or 0
    assignee = request.form.get("assignee", "").strip()
    if assignee_user_id:
        assignee = manager_display_string(assignee_user_id)
    status = request.form.get("status", "todo").strip()
    priority = request.form.get("priority", "medium").strip()
    planned_end = request.form.get("planned_end", "").strip() or None
    progress = parse_int_form_value(request.form.get("progress"), None)
    blocked_reason = request.form.get("blocked_reason", "").strip() or None
    depends_on = parse_task_depends(project_id, request.form.get("depends_on_task_id"))

    if not title or not assignee:
        flash("任务名称和负责人为必填项。", "danger")
        return _redirect_project_entity(project_id)
    if progress is None or progress < 0 or progress > 100:
        flash("任务进度必须是 0 到 100 的整数。", "danger")
        return _redirect_project_entity(project_id)

    execute_returning_id(
        """
        INSERT INTO tasks(project_id, title, assignee, status, progress, priority, planned_end, blocked_reason, depends_on_task_id, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (project_id, title, assignee, status, progress, priority, planned_end, blocked_reason, depends_on, now_iso()),
    )
    log_project_activity(project_id, "新增任务", "task", title, f"负责人 {assignee}")
    touch_project(project_id)
    get_db().commit()
    flash("任务新增成功。", "success")
    return _redirect_project_entity(project_id)


@app.route("/tasks/<int:task_id>/update", methods=["POST"])
def update_task(task_id: int):
    task = fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if task is None:
        flash("任务不存在。", "danger")
        return redirect(url_for("dashboard"))
    scope_clause, scope_params = build_project_visibility_clause("p")
    project = fetchone(
        f"SELECT id, manager FROM projects p WHERE p.id = ? AND p.deleted_at IS NULL AND {scope_clause}",
        tuple([int(task["project_id"])] + scope_params),
    )
    if project is None:
        flash("项目不存在或无权限操作。", "danger")
        return _redirect_project_entity(int(task["project_id"]))
    if not (can_manage_project_record(project) or _can_manage_task_record(task)):
        flash("无权限更新该任务。", "danger")
        return _redirect_project_entity(int(task["project_id"]))

    title = request.form.get("title", "").strip()
    assignee_user_id = parse_int_form_value(request.form.get("assignee_user_id"), 0) or 0
    assignee = request.form.get("assignee", "").strip()
    if assignee_user_id:
        assignee = manager_display_string(assignee_user_id)
    priority = request.form.get("priority", task.get("priority") or "medium").strip()
    planned_end = request.form.get("planned_end", "").strip() or None
    status = request.form.get("status", task["status"]).strip()
    progress = parse_int_form_value(request.form.get("progress"), None)
    blocked_reason = request.form.get("blocked_reason", "").strip() or None
    actual_end = request.form.get("actual_end", "").strip() or None
    depends_on = parse_task_depends(int(task["project_id"]), request.form.get("depends_on_task_id"), exclude_task_id=task_id)

    if not title or not assignee:
        flash("任务名称和负责人为必填项。", "danger")
        return _redirect_project_entity(int(task["project_id"]))
    if progress is None or progress < 0 or progress > 100:
        flash("任务进度必须是 0 到 100 的整数。", "danger")
        return _redirect_project_entity(int(task["project_id"]))

    execute(
        """
        UPDATE tasks
        SET title = ?, assignee = ?, priority = ?, planned_end = ?, status = ?, progress = ?, blocked_reason = ?, actual_end = ?, depends_on_task_id = ?, updated_at = ?
        WHERE id = ?
        """,
        (title, assignee, priority, planned_end, status, progress, blocked_reason, actual_end, depends_on, now_iso(), task_id),
    )
    log_project_activity(int(task["project_id"]), "更新任务", "task", title, f"状态 {TASK_STATUS_LABELS.get(status, status)} 进度 {progress}%")
    touch_project(task["project_id"])
    get_db().commit()
    flash("任务更新成功。", "success")
    return _redirect_project_entity(int(task["project_id"]))


@app.route("/tasks/<int:task_id>/delete", methods=["POST"])
def delete_task(task_id: int):
    task = fetchone("SELECT id, project_id, title FROM tasks WHERE id = ?", (task_id,))
    if task is None:
        flash("任务不存在。", "danger")
        return redirect(url_for("dashboard"))
    scope_clause, scope_params = build_project_visibility_clause("p")
    project = fetchone(
        f"SELECT id, manager FROM projects p WHERE p.id = ? AND p.deleted_at IS NULL AND {scope_clause}",
        tuple([int(task["project_id"])] + scope_params),
    )
    if project is None or not can_manage_project_record(project):
        flash("无权限删除任务。", "danger")
        return _redirect_project_entity(int(task["project_id"]))

    attachments = fetchall("SELECT id, stored_name FROM attachments WHERE task_id = ?", (task_id,))
    for attachment in attachments:
        remove_attachment_file(attachment["stored_name"])
        execute("DELETE FROM attachments WHERE id = ?", (attachment["id"],))
    execute("UPDATE tasks SET depends_on_task_id = NULL WHERE depends_on_task_id = ?", (task_id,))
    log_project_activity(int(task["project_id"]), "删除任务", "task", str(task.get("title") or ""))
    execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    touch_project(task["project_id"])
    get_db().commit()
    flash("任务已删除。", "success")
    return _redirect_project_entity(int(task["project_id"]))


@app.route("/projects/<int:project_id>/milestones/new", methods=["POST"])
def create_milestone(project_id: int):
    scope_clause, scope_params = build_project_visibility_clause("p")
    project = fetchone(
        f"SELECT id, manager FROM projects p WHERE p.id = ? AND p.deleted_at IS NULL AND {scope_clause}",
        tuple([project_id] + scope_params),
    )
    if project is None or not can_manage_project_record(project):
        flash("无权限新增里程碑。", "danger")
        return _redirect_project_entity(project_id)
    title = request.form.get("title", "").strip()
    owner_user_id = parse_int_form_value(request.form.get("owner_user_id"), 0) or 0
    owner = request.form.get("owner", "").strip()
    if owner_user_id:
        owner = manager_display_string(owner_user_id)
    due_date = request.form.get("due_date", "").strip()
    status = request.form.get("status", "open").strip()
    if not title or not owner or not due_date:
        flash("里程碑名称、负责人、截止日期为必填项。", "danger")
        return _redirect_project_entity(project_id)

    execute(
        """
        INSERT INTO milestones(project_id, title, owner, due_date, status, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (project_id, title, owner, due_date, status, now_iso()),
    )
    log_project_activity(project_id, "新增里程碑", "milestone", title, f"截止 {due_date}")
    touch_project(project_id)
    get_db().commit()
    flash("里程碑新增成功。", "success")
    return _redirect_project_entity(project_id)


@app.route("/milestones/<int:milestone_id>/update", methods=["POST"])
def update_milestone(milestone_id: int):
    milestone = fetchone("SELECT * FROM milestones WHERE id = ?", (milestone_id,))
    if milestone is None:
        flash("里程碑不存在。", "danger")
        return redirect(url_for("dashboard"))
    scope_clause, scope_params = build_project_visibility_clause("p")
    project = fetchone(
        f"SELECT id, manager FROM projects p WHERE p.id = ? AND p.deleted_at IS NULL AND {scope_clause}",
        tuple([int(milestone["project_id"])] + scope_params),
    )
    if project is None:
        flash("项目不存在或无权限操作。", "danger")
        return _redirect_project_entity(int(milestone["project_id"]))
    if not (can_manage_project_record(project) or _can_manage_milestone_record(milestone)):
        flash("无权限更新该里程碑。", "danger")
        return _redirect_project_entity(int(milestone["project_id"]))

    title = request.form.get("title", "").strip()
    owner_user_id = parse_int_form_value(request.form.get("owner_user_id"), 0) or 0
    owner = request.form.get("owner", "").strip()
    if owner_user_id:
        owner = manager_display_string(owner_user_id)
    due_date = request.form.get("due_date", "").strip()
    status = request.form.get("status", milestone["status"]).strip()
    if not title or not owner or not due_date:
        flash("里程碑名称、负责人、截止日期为必填项。", "danger")
        return _redirect_project_entity(int(milestone["project_id"]))

    execute(
        "UPDATE milestones SET title = ?, owner = ?, due_date = ?, status = ?, updated_at = ? WHERE id = ?",
        (title, owner, due_date, status, now_iso(), milestone_id),
    )
    log_project_activity(
        int(milestone["project_id"]),
        "更新里程碑",
        "milestone",
        title,
        f"状态 {MILESTONE_STATUS_LABELS.get(status, status)}",
    )
    touch_project(milestone["project_id"])
    get_db().commit()
    flash("里程碑更新成功。", "success")
    return _redirect_project_entity(int(milestone["project_id"]))


@app.route("/milestones/<int:milestone_id>/delete", methods=["POST"])
def delete_milestone(milestone_id: int):
    milestone = fetchone("SELECT id, project_id FROM milestones WHERE id = ?", (milestone_id,))
    if milestone is None:
        flash("里程碑不存在。", "danger")
        return redirect(url_for("dashboard"))
    scope_clause, scope_params = build_project_visibility_clause("p")
    project = fetchone(
        f"SELECT id, manager FROM projects p WHERE p.id = ? AND p.deleted_at IS NULL AND {scope_clause}",
        tuple([int(milestone["project_id"])] + scope_params),
    )
    if project is None or not can_manage_project_record(project):
        flash("无权限删除里程碑。", "danger")
        return _redirect_project_entity(int(milestone["project_id"]))

    attachments = fetchall("SELECT id, stored_name FROM attachments WHERE milestone_id = ?", (milestone_id,))
    for attachment in attachments:
        remove_attachment_file(attachment["stored_name"])
        execute("DELETE FROM attachments WHERE id = ?", (attachment["id"],))
    execute("DELETE FROM milestones WHERE id = ?", (milestone_id,))
    touch_project(milestone["project_id"])
    get_db().commit()
    flash("里程碑已删除。", "success")
    return _redirect_project_entity(int(milestone["project_id"]))


@app.route("/projects/<int:project_id>/risks/new", methods=["POST"])
def create_risk(project_id: int):
    scope_clause, scope_params = build_project_visibility_clause("p")
    project = fetchone(
        f"SELECT id, manager FROM projects p WHERE p.id = ? AND p.deleted_at IS NULL AND {scope_clause}",
        tuple([project_id] + scope_params),
    )
    if project is None or not can_manage_project_record(project):
        flash("无权限新增风险。", "danger")
        return _redirect_project_entity(project_id)
    title = request.form.get("title", "").strip()
    level = request.form.get("level", "medium").strip()
    owner_user_id = parse_int_form_value(request.form.get("owner_user_id"), 0) or 0
    owner = request.form.get("owner", "").strip()
    if owner_user_id:
        owner = manager_display_string(owner_user_id)
    status = request.form.get("status", "open").strip()
    due_date = request.form.get("due_date", "").strip() or None
    mitigation = request.form.get("mitigation", "").strip() or None
    if not title or not owner:
        flash("风险名称和负责人为必填项。", "danger")
        return _redirect_project_entity(project_id)

    execute(
        """
        INSERT INTO risks(project_id, title, level, status, owner, due_date, mitigation, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (project_id, title, level, status, owner, due_date, mitigation, now_iso()),
    )
    log_project_activity(project_id, "新增风险", "risk", title, f"等级 {RISK_LEVEL_LABELS.get(level, level)}")
    touch_project(project_id)
    get_db().commit()
    flash("风险新增成功。", "success")
    return _redirect_project_entity(project_id)


@app.route("/risks/<int:risk_id>/update", methods=["POST"])
def update_risk(risk_id: int):
    risk = fetchone("SELECT * FROM risks WHERE id = ?", (risk_id,))
    if risk is None:
        flash("风险不存在。", "danger")
        return redirect(url_for("dashboard"))
    scope_clause, scope_params = build_project_visibility_clause("p")
    project = fetchone(
        f"SELECT id, manager FROM projects p WHERE p.id = ? AND p.deleted_at IS NULL AND {scope_clause}",
        tuple([int(risk["project_id"])] + scope_params),
    )
    if project is None:
        flash("项目不存在或无权限操作。", "danger")
        return _redirect_project_entity(int(risk["project_id"]))
    if not (can_manage_project_record(project) or _can_manage_risk_record(risk)):
        flash("无权限更新该风险。", "danger")
        return _redirect_project_entity(int(risk["project_id"]))

    title = request.form.get("title", "").strip()
    owner_user_id = parse_int_form_value(request.form.get("owner_user_id"), 0) or 0
    owner = request.form.get("owner", "").strip()
    if owner_user_id:
        owner = manager_display_string(owner_user_id)
    level = request.form.get("level", risk.get("level") or "medium").strip()
    due_date = request.form.get("due_date", "").strip() or None
    mitigation = request.form.get("mitigation", "").strip() or None
    status = request.form.get("status", risk["status"]).strip()
    if not title or not owner:
        flash("风险名称和负责人为必填项。", "danger")
        return _redirect_project_entity(int(risk["project_id"]))

    execute(
        "UPDATE risks SET title = ?, level = ?, status = ?, owner = ?, due_date = ?, mitigation = ?, updated_at = ? WHERE id = ?",
        (title, level, status, owner, due_date, mitigation, now_iso(), risk_id),
    )
    log_project_activity(
        int(risk["project_id"]),
        "更新风险",
        "risk",
        title,
        f"状态 {RISK_STATUS_LABELS.get(status, status)}",
    )
    touch_project(risk["project_id"])
    get_db().commit()
    flash("风险更新成功。", "success")
    return _redirect_project_entity(int(risk["project_id"]))


@app.route("/risks/<int:risk_id>/delete", methods=["POST"])
def delete_risk(risk_id: int):
    risk = fetchone("SELECT id, project_id FROM risks WHERE id = ?", (risk_id,))
    if risk is None:
        flash("风险不存在。", "danger")
        return redirect(url_for("dashboard"))
    scope_clause, scope_params = build_project_visibility_clause("p")
    project = fetchone(
        f"SELECT id, manager FROM projects p WHERE p.id = ? AND p.deleted_at IS NULL AND {scope_clause}",
        tuple([int(risk["project_id"])] + scope_params),
    )
    if project is None or not can_manage_project_record(project):
        flash("无权限删除风险。", "danger")
        return _redirect_project_entity(int(risk["project_id"]))

    execute("DELETE FROM risks WHERE id = ?", (risk_id,))
    touch_project(risk["project_id"])
    get_db().commit()
    flash("风险已删除。", "success")
    return _redirect_project_entity(int(risk["project_id"]))


def _phase_require_all(ph: dict[str, Any]) -> bool:
    r = ph.get("require_all_deliverables")
    if r is None:
        return True
    if isinstance(r, bool):
        return r
    try:
        return int(r) != 0
    except (TypeError, ValueError):
        return True
