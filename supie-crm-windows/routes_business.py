import csv
from datetime import date
from io import BytesIO, StringIO
import re

from flask import flash, jsonify, redirect, render_template, request, send_file, session, url_for

from ai_service import build_approval_summary
from crm_constants import (
    CONTRACT_DEVIATION_THRESHOLD_PCT,
    CUSTOMER_FOLLOW_METHOD_LABELS,
    CUSTOMER_STALE_FOLLOW_DAYS,
    LOST_REASON_LABELS,
    OPPORTUNITY_CLOSE_SOON_DAYS_DEFAULT,
    OPPORTUNITY_STALL_DAYS_DEFAULT,
    OPEN_OPPORTUNITY_STAGES,
    opportunity_stage_is_rollback,
    opportunity_status_from_stage,
)

from app import (
    app,
    apply_approval,
    approval_requested_value_label,
    approval_visible_requested_values,
    compute_project_progress,
    build_contract_visibility_clause,
    build_customer_visibility_clause,
    build_opportunity_visibility_clause,
    build_project_visibility_clause,
    execute,
    execute_returning_id,
    fetch_approval_candidates,
    fetchall,
    fetchone,
    generate_invoice_no,
    get_db,
    has_module_permission,
    has_pending_approval,
    insert_opportunity_stage_log,
    log_ai_generation,
    manager_display_string,
    now_iso,
    open_high_risk_count,
    parse_float_form_value,
    parse_int_form_value,
    current_user_matches_text,
    current_user_role_codes,
    submit_approval,
)
from crm_utils import like_kw

_CONTACT_PHONE_RE = re.compile(r"^(\+?86)?1\d{10}$|^[0-9\-\s()]{7,24}$")
_CONTACT_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_CUSTOMER_DETAIL_TABS = {"overview", "contacts", "follow"}


def _approval_candidate_role_codes(module_type: str) -> list[str]:
    if module_type in {"opportunity", "contract"}:
        return ["sales_director", "management", "admin"]
    if module_type == "project":
        return ["project_director", "management", "admin"]
    return ["management", "admin"]


def _approval_scope_requested_values() -> set[str] | None:
    return approval_visible_requested_values(current_user_role_codes())


def _approval_category_options() -> list[dict[str, str]]:
    return [
        {"value": "won", "label": "商机赢单"},
        {"value": "signed", "label": "合同签约"},
        {"value": "close_project", "label": "项目结项"},
    ]


def _approval_requested_value_filter_clause(requested_value: str) -> tuple[str, list[object]]:
    requested_value = str(requested_value or "").strip()
    if not requested_value:
        return "1=1", []
    return "requested_value = ?", [requested_value]


def _approval_record_is_visible(approval: dict[str, object]) -> bool:
    visible_requested_values = _approval_scope_requested_values()
    if visible_requested_values is None:
        return True
    if not visible_requested_values:
        return False
    return str(approval.get("requested_value") or "").strip() in visible_requested_values
_DETAIL_PAGE_SIZES = (15, 50, 100)


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


def _paginate_rows(rows: list[dict[str, object]], page: int, per_page: int) -> tuple[int, list[dict[str, object]], int]:
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


def _validate_contact_optional(phone: str | None, email: str | None) -> str | None:
    if not ((phone or "").strip() or (email or "").strip()):
        return "请至少填写手机或邮箱中的一项。"
    p = (phone or "").strip()
    e = (email or "").strip()
    if p and not _CONTACT_PHONE_RE.match(p):
        return "手机号格式不合法。"
    if e and not _CONTACT_EMAIL_RE.match(e):
        return "邮箱格式不合法。"
    return None


def _clear_primary_contacts(customer_id: int) -> None:
    execute(
        "UPDATE customer_contacts SET is_primary = 0, updated_at = ? WHERE customer_id = ?",
        (now_iso(), customer_id),
    )


def _parse_iso_date(value: str | None) -> date | None:
    raw = (value or "").strip()
    if not raw:
        return None
    text = raw.replace("T", " ").strip()
    day_part = text[:10]
    try:
        return date.fromisoformat(day_part)
    except ValueError:
        return None


def _customer_detail_tab(tab: str | None, default: str = "overview") -> str:
    raw = (tab or "").strip().lower()
    if raw in _CUSTOMER_DETAIL_TABS:
        return raw
    return default


def _redirect_customer_detail_tab(customer_id: int, tab: str) -> object:
    active_tab = _customer_detail_tab(tab)
    return redirect(url_for("customer_detail", customer_id=customer_id, tab=active_tab))


def _project_belongs_customer(project_id: int | None, customer_id: int) -> bool:
    if project_id is None:
        return True
    row = fetchone(
        "SELECT id FROM projects WHERE id = ? AND customer_id = ? AND deleted_at IS NULL",
        (project_id, customer_id),
    )
    return row is not None


def _opportunity_belongs_customer(opportunity_id: int | None, customer_id: int) -> bool:
    if opportunity_id is None:
        return True
    row = fetchone(
        "SELECT id FROM opportunities WHERE id = ? AND customer_id = ?",
        (opportunity_id, customer_id),
    )
    return row is not None


def _receivable_belongs_contract(receivable_id: int | None, contract_id: int) -> bool:
    if receivable_id is None:
        return True
    row = fetchone(
        "SELECT id FROM receivables WHERE id = ? AND contract_id = ?",
        (receivable_id, contract_id),
    )
    return row is not None


@app.route("/customers", methods=["GET", "POST"])
def customer_list():
    if request.method == "GET" and not has_module_permission("customer", "view"):
        flash("无权限查看客户。", "danger")
        return redirect(url_for("dashboard"))
    if request.method == "POST" and not has_module_permission("customer", "manage"):
        flash("无权限新增客户。", "danger")
        return redirect(url_for("customer_list"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        owner_user_id = parse_int_form_value(request.form.get("owner_user_id"), 0) or 0
        owner = request.form.get("owner", "").strip()
        if owner_user_id:
            owner = manager_display_string(owner_user_id)
        if "sales" in current_user_role_codes() and "sales_director" not in current_user_role_codes() and not current_user_matches_text(owner):
            owner = session.get("display_name") or session.get("username") or owner
        phone = request.form.get("phone", "").strip() or None
        email = request.form.get("email", "").strip() or None
        industry = request.form.get("industry", "").strip() or None
        level = request.form.get("level", "A").strip() or "A"
        status = request.form.get("status", "potential").strip() or "potential"
        tier = request.form.get("tier", "normal").strip() or "normal"
        tags = request.form.get("tags", "").strip() or None
        if not name or not owner:
            flash("客户名称和负责人为必填项。", "danger")
            return redirect(url_for("customer_list"))
        try:
            execute(
                """
                INSERT INTO customers(name, owner, phone, email, industry, level, status, tier, tags, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (name, owner, phone, email, industry, level, status, tier, tags, now_iso()),
            )
            get_db().commit()
            flash("客户新增成功。", "success")
        except Exception:
            get_db().rollback()
            flash("客户新增失败：客户名称可能已存在。", "danger")
        return redirect(url_for("customer_list"))

    f_owner = request.args.get("owner", "").strip()
    f_tier = request.args.get("tier", "").strip()
    f_status = request.args.get("status", "").strip()
    f_tag = request.args.get("tag", "").strip()
    sort = request.args.get("sort", "updated").strip()
    where_parts: list[str] = ["1=1"]
    params: list[object] = []
    scope_clause, scope_params = build_customer_visibility_clause("c")
    where_parts.append(scope_clause)
    params.extend(scope_params)
    if f_owner:
        where_parts.append("c.owner LIKE ?")
        params.append(like_kw(f_owner))
    if f_tier:
        where_parts.append("c.tier = ?")
        params.append(f_tier)
    if f_status:
        where_parts.append("c.status = ?")
        params.append(f_status)
    if f_tag:
        where_parts.append("COALESCE(c.tags, '') LIKE ?")
        params.append(like_kw(f_tag))
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
    where_sql = " AND ".join(where_parts)
    order_sql = "c.updated_at DESC"
    if sort == "follow":
        order_sql = (
            "CASE WHEN (SELECT MAX(f.followed_at) FROM customer_follow_ups f WHERE f.customer_id = c.id) "
            "IS NULL THEN 1 ELSE 0 END, "
            "(SELECT MAX(f.followed_at) FROM customer_follow_ups f WHERE f.customer_id = c.id) DESC, "
            "c.updated_at DESC"
        )
    total_row = fetchone(f"SELECT COUNT(1) AS c FROM customers c WHERE {where_sql}", tuple(params))
    total = int(total_row["c"]) if total_row else 0
    total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
    if page > total_pages:
        page = total_pages
    offset = (page - 1) * per_page
    sql = f"""
        SELECT c.*,
          (SELECT MAX(f.followed_at) FROM customer_follow_ups f WHERE f.customer_id = c.id) AS last_followup_at
        FROM customers c
        WHERE {where_sql}
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?
    """
    customers = fetchall(sql, tuple(params + [per_page, offset]))
    return render_template(
        "customer_list.html",
        customers=customers,
        f_owner=f_owner,
        f_tier=f_tier,
        f_status=f_status,
        f_tag=f_tag,
        sort=sort,
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
    )


@app.route("/customers/export")
def customer_list_export():
    if not has_module_permission("customer", "view"):
        flash("无权限导出客户。", "danger")
        return redirect(url_for("dashboard"))
    f_owner = request.args.get("owner", "").strip()
    f_tier = request.args.get("tier", "").strip()
    f_status = request.args.get("status", "").strip()
    f_tag = request.args.get("tag", "").strip()
    sort = request.args.get("sort", "updated").strip()
    where_parts: list[str] = ["1=1"]
    params: list[object] = []
    scope_clause, scope_params = build_customer_visibility_clause("c")
    where_parts.append(scope_clause)
    params.extend(scope_params)
    if f_owner:
        where_parts.append("c.owner LIKE ?")
        params.append(like_kw(f_owner))
    if f_tier:
        where_parts.append("c.tier = ?")
        params.append(f_tier)
    if f_status:
        where_parts.append("c.status = ?")
        params.append(f_status)
    if f_tag:
        where_parts.append("COALESCE(c.tags, '') LIKE ?")
        params.append(like_kw(f_tag))
    where_sql = " AND ".join(where_parts)
    order_sql = "c.updated_at DESC"
    if sort == "follow":
        order_sql = (
            "CASE WHEN (SELECT MAX(f.followed_at) FROM customer_follow_ups f WHERE f.customer_id = c.id) "
            "IS NULL THEN 1 ELSE 0 END, "
            "(SELECT MAX(f.followed_at) FROM customer_follow_ups f WHERE f.customer_id = c.id) DESC, "
            "c.updated_at DESC"
        )
    rows = fetchall(
        f"""
        SELECT c.*,
          (SELECT MAX(f.followed_at) FROM customer_follow_ups f WHERE f.customer_id = c.id) AS last_followup_at
        FROM customers c
        WHERE {where_sql}
        ORDER BY {order_sql}
        """,
        tuple(params),
    )
    csv_rows = [
        [
            row.get("name") or "",
            row.get("owner") or "",
            row.get("industry") or "",
            row.get("level") or "",
            row.get("tier") or "",
            row.get("status") or "",
            row.get("tags") or "",
            row.get("last_followup_at") or "",
        ]
        for row in rows
    ]
    return _csv_attachment(
        "客户列表导出.csv",
        ["客户名称", "负责人", "行业", "级别", "分级", "状态", "标签", "最近跟进"],
        csv_rows,
    )


@app.route("/customers/follow-ups")
def customer_follow_up_list():
    if not has_module_permission("customer", "view"):
        flash("无权限查看跟进记录。", "danger")
        return redirect(url_for("dashboard"))
    cid_raw = request.args.get("customer_id", "").strip()
    cid = int(cid_raw) if cid_raw.isdigit() else None
    if cid:
        rows = fetchall(
            """
            SELECT f.*, c.name AS customer_name
            FROM customer_follow_ups f
            JOIN customers c ON c.id = f.customer_id
            WHERE f.customer_id = ?
            ORDER BY f.followed_at DESC
            """,
            (cid,),
        )
    else:
        rows = fetchall(
            """
            SELECT f.*, c.name AS customer_name
            FROM customer_follow_ups f
            JOIN customers c ON c.id = f.customer_id
            ORDER BY f.followed_at DESC
            LIMIT 200
            """,
    )
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
    page, follow_ups, total_pages = _paginate_rows(rows, page, per_page)
    return render_template("customer_follow_ups.html", follow_ups=follow_ups, filter_customer_id=cid, page=page, per_page=per_page, total_pages=total_pages)


@app.route("/customers/follow-ups/export")
def customer_follow_up_export():
    if not has_module_permission("customer", "view"):
        flash("无权限导出跟进记录。", "danger")
        return redirect(url_for("dashboard"))

    cid_raw = request.args.get("customer_id", "").strip()
    cid = int(cid_raw) if cid_raw.isdigit() else None
    customer = None
    where_sql = ""
    params: tuple[object, ...] = ()
    if cid is not None:
        customer = fetchone("SELECT id, name FROM customers WHERE id = ?", (cid,))
        if customer is None:
            flash("客户不存在。", "danger")
            return redirect(url_for("customer_follow_up_list"))
        where_sql = "WHERE f.customer_id = ?"
        params = (cid,)

    rows = fetchall(
        f"""
        SELECT f.*, c.name AS customer_name, o.title AS opportunity_title
        FROM customer_follow_ups f
        JOIN customers c ON c.id = f.customer_id
        LEFT JOIN opportunities o ON o.id = f.opportunity_id
        {where_sql}
        ORDER BY f.followed_at DESC, f.id DESC
        """,
        params,
    )

    csv_text = StringIO()
    writer = csv.writer(csv_text)
    writer.writerow(["客户", "跟进时间", "跟进人", "方式", "下次跟进", "关联商机", "内容"])
    for row in rows:
        writer.writerow(
            [
                row.get("customer_name") or "",
                row.get("followed_at") or "",
                row.get("followed_by") or "",
                CUSTOMER_FOLLOW_METHOD_LABELS.get(row.get("method"), row.get("method") or ""),
                row.get("next_followup_at") or "",
                row.get("opportunity_title") or "",
                row.get("content") or "",
            ]
        )

    payload = BytesIO(csv_text.getvalue().encode("utf-8-sig"))
    payload.seek(0)
    filename = f"{customer['name']}_历史动态.csv" if customer is not None else "客户历史动态_全部.csv"
    return send_file(payload, as_attachment=True, download_name=filename, mimetype="text/csv; charset=utf-8")


@app.route("/customers/<int:customer_id>")
def customer_detail(customer_id: int):
    if not has_module_permission("customer", "view"):
        flash("无权限查看客户详情。", "danger")
        return redirect(url_for("dashboard"))
    scope_clause, scope_params = build_customer_visibility_clause("c")
    customer = fetchone(
        f"SELECT * FROM customers c WHERE c.id = ? AND {scope_clause}",
        tuple([customer_id] + scope_params),
    )
    if customer is None:
        flash("客户不存在或无权限查看。", "danger")
        return redirect(url_for("customer_list"))
    contacts = fetchall(
        "SELECT * FROM customer_contacts WHERE customer_id = ? ORDER BY is_primary DESC, id ASC",
        (customer_id,),
    )
    follow_ups = fetchall(
        """
        SELECT f.*, o.title AS opportunity_title
        FROM customer_follow_ups f
        LEFT JOIN opportunities o ON o.id = f.opportunity_id
        WHERE f.customer_id = ?
        ORDER BY f.followed_at DESC
        """,
        (customer_id,),
    )
    opp_scope_clause, opp_scope_params = build_opportunity_visibility_clause("o")
    opportunities = fetchall(
        f"SELECT * FROM opportunities o WHERE o.customer_id = ? AND {opp_scope_clause} ORDER BY updated_at DESC",
        tuple([customer_id] + opp_scope_params),
    )
    opp_sum = fetchone(
        f"SELECT COALESCE(SUM(amount), 0) AS s FROM opportunities o WHERE o.customer_id = ? AND {opp_scope_clause}",
        tuple([customer_id] + opp_scope_params),
    )
    contract_scope_clause, contract_scope_params = build_contract_visibility_clause("ct")
    contracts = fetchall(
        f"""
        SELECT ct.*, p.name AS project_name
        FROM contracts ct
        LEFT JOIN projects p ON p.id = ct.project_id AND p.deleted_at IS NULL
        WHERE ct.customer_id = ? AND {contract_scope_clause}
        ORDER BY ct.updated_at DESC
        """,
        tuple([customer_id] + contract_scope_params),
    )
    contract_sum = fetchone(
        f"SELECT COALESCE(SUM(amount), 0) AS s FROM contracts ct WHERE ct.customer_id = ? AND {contract_scope_clause}",
        tuple([customer_id] + contract_scope_params),
    )
    project_scope_clause, project_scope_params = build_project_visibility_clause("p")
    projects = fetchall(
        f"SELECT * FROM projects p WHERE p.customer_id = ? AND p.deleted_at IS NULL AND {project_scope_clause} ORDER BY created_at ASC",
        tuple([customer_id] + project_scope_params),
    )
    primary_contact = next((ct for ct in contacts if int(ct.get("is_primary") or 0) == 1), None)
    latest_follow = follow_ups[0] if follow_ups else None
    latest_followed_at = latest_follow.get("followed_at") if latest_follow else None
    next_followup_at = latest_follow.get("next_followup_at") if latest_follow else None
    stale_cutoff = date.today().toordinal() - CUSTOMER_STALE_FOLLOW_DAYS
    latest_follow_date = _parse_iso_date(str(latest_followed_at) if latest_followed_at else None)
    is_stale_follow = latest_follow_date is None or latest_follow_date.toordinal() <= stale_cutoff
    active_opportunities = [
        o for o in opportunities if str(o.get("status") or "").strip() not in ("won", "lost")
    ]
    active_opportunity_amount = sum(float(o.get("amount") or 0) for o in active_opportunities)
    active_contracts = [
        c for c in contracts if str(c.get("status") or "").strip() in ("signed", "executing")
    ]
    active_contract_amount = sum(float(c.get("amount") or 0) for c in active_contracts)
    executing_projects = [
        p for p in projects if str(p.get("status") or "").strip() == "in_progress"
    ]
    summary_metrics = {
        "latest_followed_at": latest_followed_at,
        "next_followup_at": next_followup_at,
        "primary_contact": primary_contact,
        "active_opportunity_count": len(active_opportunities),
        "active_opportunity_amount": active_opportunity_amount,
        "active_contract_count": len(active_contracts),
        "active_contract_amount": active_contract_amount,
        "executing_project_count": len(executing_projects),
        "is_stale_follow": is_stale_follow,
        "stale_days_threshold": CUSTOMER_STALE_FOLLOW_DAYS,
    }
    return render_template(
        "customer_detail.html",
        customer=customer,
        contacts=contacts,
        follow_ups=follow_ups,
        opportunities=opportunities,
        contracts=contracts,
        projects=projects,
        opportunity_amount_sum=float(opp_sum["s"]) if opp_sum else 0.0,
        contract_amount_sum=float(contract_sum["s"]) if contract_sum else 0.0,
        summary_metrics=summary_metrics,
    )


@app.route("/customers/<int:customer_id>/edit", methods=["POST"])
def customer_edit(customer_id: int):
    if not has_module_permission("customer", "manage"):
        flash("无权限编辑客户。", "danger")
        return redirect(url_for("customer_detail", customer_id=customer_id))
    scope_clause, scope_params = build_customer_visibility_clause("c")
    customer = fetchone(
        f"SELECT id FROM customers c WHERE c.id = ? AND {scope_clause}",
        tuple([customer_id] + scope_params),
    )
    if customer is None:
        flash("客户不存在或无权限编辑。", "danger")
        return redirect(url_for("customer_list"))
    name = request.form.get("name", "").strip()
    owner_user_id = parse_int_form_value(request.form.get("owner_user_id"), 0) or 0
    owner = request.form.get("owner", "").strip()
    if owner_user_id:
        owner = manager_display_string(owner_user_id)
    if "sales" in current_user_role_codes() and "sales_director" not in current_user_role_codes() and not current_user_matches_text(owner):
        owner = session.get("display_name") or session.get("username") or owner
    phone = request.form.get("phone", "").strip() or None
    email = request.form.get("email", "").strip() or None
    industry = request.form.get("industry", "").strip() or None
    level = request.form.get("level", "A").strip() or "A"
    status = request.form.get("status", "potential").strip() or "potential"
    tier = request.form.get("tier", "normal").strip() or "normal"
    tags = request.form.get("tags", "").strip() or None
    if not name or not owner:
        flash("客户名称和负责人为必填项。", "danger")
        return redirect(url_for("customer_detail", customer_id=customer_id))
    try:
        execute(
            """
            UPDATE customers
            SET name = ?, owner = ?, phone = ?, email = ?, industry = ?, level = ?,
                status = ?, tier = ?, tags = ?, updated_at = ?
            WHERE id = ?
            """,
            (name, owner, phone, email, industry, level, status, tier, tags, now_iso(), customer_id),
        )
        get_db().commit()
        flash("客户信息已保存。", "success")
    except Exception:
        get_db().rollback()
        flash("保存失败：名称可能与其他客户重复。", "danger")
    return redirect(url_for("customer_detail", customer_id=customer_id))


@app.route("/customers/<int:customer_id>/contacts", methods=["POST"])
def customer_contact_add(customer_id: int):
    if not has_module_permission("customer", "manage"):
        flash("无权限维护联系人。", "danger")
        return _redirect_customer_detail_tab(customer_id, "contacts")
    c = fetchone("SELECT id FROM customers WHERE id = ?", (customer_id,))
    if c is None:
        flash("客户不存在。", "danger")
        return redirect(url_for("customer_list"))
    name = request.form.get("name", "").strip()
    title = request.form.get("title", "").strip() or None
    phone = request.form.get("phone", "").strip() or None
    email = request.form.get("email", "").strip() or None
    note = request.form.get("note", "").strip() or None
    is_primary = request.form.get("is_primary") == "1"
    if not name:
        flash("联系人姓名为必填项。", "danger")
        return _redirect_customer_detail_tab(customer_id, "contacts")
    err = _validate_contact_optional(phone, email)
    if err:
        flash(err, "danger")
        return _redirect_customer_detail_tab(customer_id, "contacts")
    if is_primary:
        _clear_primary_contacts(customer_id)
    execute_returning_id(
        """
        INSERT INTO customer_contacts(customer_id, name, title, phone, email, is_primary, note, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            customer_id,
            name,
            title,
            phone,
            email,
            1 if is_primary else 0,
            note,
            now_iso(),
            now_iso(),
        ),
    )
    execute("UPDATE customers SET updated_at = ? WHERE id = ?", (now_iso(), customer_id))
    get_db().commit()
    flash("联系人已添加。", "success")
    return _redirect_customer_detail_tab(customer_id, "contacts")


@app.route("/customers/<int:customer_id>/contacts/<int:contact_id>/edit", methods=["POST"])
def customer_contact_edit(customer_id: int, contact_id: int):
    if not has_module_permission("customer", "manage"):
        flash("无权限维护联系人。", "danger")
        return _redirect_customer_detail_tab(customer_id, "contacts")
    row = fetchone(
        "SELECT id FROM customer_contacts WHERE id = ? AND customer_id = ?",
        (contact_id, customer_id),
    )
    if row is None:
        flash("联系人不存在。", "danger")
        return _redirect_customer_detail_tab(customer_id, "contacts")
    name = request.form.get("name", "").strip()
    title = request.form.get("title", "").strip() or None
    phone = request.form.get("phone", "").strip() or None
    email = request.form.get("email", "").strip() or None
    note = request.form.get("note", "").strip() or None
    is_primary = request.form.get("is_primary") == "1"
    if not name:
        flash("联系人姓名为必填项。", "danger")
        return _redirect_customer_detail_tab(customer_id, "contacts")
    err = _validate_contact_optional(phone, email)
    if err:
        flash(err, "danger")
        return _redirect_customer_detail_tab(customer_id, "contacts")
    if is_primary:
        _clear_primary_contacts(customer_id)
    execute(
        """
        UPDATE customer_contacts
        SET name = ?, title = ?, phone = ?, email = ?, is_primary = ?, note = ?, updated_at = ?
        WHERE id = ? AND customer_id = ?
        """,
        (name, title, phone, email, 1 if is_primary else 0, note, now_iso(), contact_id, customer_id),
    )
    execute("UPDATE customers SET updated_at = ? WHERE id = ?", (now_iso(), customer_id))
    get_db().commit()
    flash("联系人已更新。", "success")
    return _redirect_customer_detail_tab(customer_id, "contacts")


@app.route("/customers/<int:customer_id>/contacts/<int:contact_id>/delete", methods=["POST"])
def customer_contact_delete(customer_id: int, contact_id: int):
    if not has_module_permission("customer", "manage"):
        flash("无权限维护联系人。", "danger")
        return _redirect_customer_detail_tab(customer_id, "contacts")
    row = fetchone(
        "SELECT id FROM customer_contacts WHERE id = ? AND customer_id = ?",
        (contact_id, customer_id),
    )
    if row is None:
        flash("联系人不存在。", "danger")
        return _redirect_customer_detail_tab(customer_id, "contacts")
    execute("DELETE FROM customer_contacts WHERE id = ? AND customer_id = ?", (contact_id, customer_id))
    execute("UPDATE customers SET updated_at = ? WHERE id = ?", (now_iso(), customer_id))
    get_db().commit()
    flash("联系人已删除。", "success")
    return _redirect_customer_detail_tab(customer_id, "contacts")


@app.route("/customers/<int:customer_id>/follow-ups", methods=["POST"])
def customer_follow_up_add(customer_id: int):
    if not has_module_permission("customer", "manage"):
        flash("无权限写跟进记录。", "danger")
        return _redirect_customer_detail_tab(customer_id, "follow")
    c = fetchone("SELECT id FROM customers WHERE id = ?", (customer_id,))
    if c is None:
        flash("客户不存在。", "danger")
        return redirect(url_for("customer_list"))
    content = request.form.get("content", "").strip()
    method = request.form.get("method", "phone").strip() or "phone"
    followed_raw = request.form.get("followed_at", "").strip()
    if followed_raw:
        followed_at = followed_raw.replace("T", " ")
        if len(followed_at) == 16:
            followed_at = followed_at + ":00"
    else:
        followed_at = now_iso()
    next_follow_raw = request.form.get("next_followup_at", "").strip() or None
    next_followup_at = next_follow_raw if next_follow_raw else None
    opp_raw = request.form.get("opportunity_id", "").strip()
    opportunity_id = int(opp_raw) if opp_raw.isdigit() else None
    if opportunity_id:
        o = fetchone(
            "SELECT id FROM opportunities WHERE id = ? AND customer_id = ?",
            (opportunity_id, customer_id),
        )
        if o is None:
            opportunity_id = None
    if not content:
        flash("跟进内容为必填项。", "danger")
        return _redirect_customer_detail_tab(customer_id, "follow")
    follower = (session.get("display_name") or session.get("username") or "").strip() or "—"
    execute(
        """
        INSERT INTO customer_follow_ups(customer_id, followed_by, followed_at, method, content, next_followup_at, opportunity_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            customer_id,
            follower,
            followed_at,
            method,
            content,
            next_followup_at,
            opportunity_id,
            now_iso(),
        ),
    )
    execute("UPDATE customers SET updated_at = ? WHERE id = ?", (now_iso(), customer_id))
    get_db().commit()
    flash("跟进记录已保存。", "success")
    return _redirect_customer_detail_tab(customer_id, "follow")


@app.route("/customers/<int:customer_id>/delete", methods=["POST"])
def delete_customer(customer_id: int):
    if not has_module_permission("customer", "manage"):
        flash("无权限删除客户。", "danger")
        return redirect(url_for("customer_list"))
    customer = fetchone("SELECT id FROM customers WHERE id = ?", (customer_id,))
    if customer is None:
        flash("客户不存在。", "danger")
        return redirect(url_for("customer_list"))
    execute("DELETE FROM customers WHERE id = ?", (customer_id,))
    get_db().commit()
    flash("客户已删除。", "success")
    return redirect(url_for("customer_list"))


@app.route("/opportunities", methods=["GET", "POST"])
def opportunity_list():
    if request.method == "GET" and not has_module_permission("opportunity", "view"):
        flash("无权限查看商机。", "danger")
        return redirect(url_for("dashboard"))
    if request.method == "POST" and not has_module_permission("opportunity", "manage"):
        flash("无权限新增商机。", "danger")
        return redirect(url_for("opportunity_list"))
    if request.method == "POST":
        customer_id = parse_int_form_value(request.form.get("customer_id"), 0) or 0
        title = request.form.get("title", "").strip()
        amount = parse_float_form_value(request.form.get("amount"), 0.0) or 0.0
        owner_user_id = parse_int_form_value(request.form.get("owner_user_id"), 0) or 0
        owner = request.form.get("owner", "").strip()
        if owner_user_id:
            owner = manager_display_string(owner_user_id)
        if "sales" in current_user_role_codes() and "sales_director" not in current_user_role_codes() and not current_user_matches_text(owner):
            owner = session.get("display_name") or session.get("username") or owner
        stage = request.form.get("stage", "lead").strip() or "lead"
        amount_confidence = request.form.get("amount_confidence", "").strip() or None
        expected_sign_date = request.form.get("expected_sign_date", "").strip() or None
        if customer_id <= 0 or not title or not owner:
            flash("客户、商机名称、负责人为必填项。", "danger")
            return redirect(url_for("opportunity_list"))
        sts = opportunity_status_from_stage(stage)
        ts = now_iso()
        oid = execute_returning_id(
            """
            INSERT INTO opportunities(
                customer_id, title, amount, owner, status, stage, stage_started_at,
                amount_confidence, expected_sign_date, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (customer_id, title, amount, owner, sts, stage, ts, amount_confidence, expected_sign_date, ts),
        )
        insert_opportunity_stage_log(oid, None, stage, "新建商机", owner)
        get_db().commit()
        flash("商机新增成功。", "success")
        return redirect(url_for("opportunity_list"))
    f_stage = request.args.get("stage", "").strip() or None
    f_customer_id = request.args.get("customer_id", type=int)
    f_owner = request.args.get("owner", "").strip() or None
    mine = request.args.get("mine", type=int) == 1
    me = session.get("display_name") or session.get("username") or ""
    conditions: list[str] = ["1=1"]
    params: list[object] = []
    scope_clause, scope_params = build_opportunity_visibility_clause("o")
    conditions.append(scope_clause)
    params.extend(scope_params)
    if f_stage:
        conditions.append("o.stage = ?")
        params.append(f_stage)
    if f_customer_id:
        conditions.append("o.customer_id = ?")
        params.append(f_customer_id)
    if f_owner:
        conditions.append("o.owner LIKE ?")
        params.append(f"%{f_owner}%")
    if mine and me:
        conditions.append("o.owner = ?")
        params.append(me)
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
    where_sql = " AND ".join(conditions)
    total_row = fetchone(
        f"""
        SELECT COUNT(1) AS c
        FROM opportunities o
        JOIN customers c ON c.id = o.customer_id
        WHERE {where_sql}
        """,
        tuple(params),
    )
    total = int(total_row["c"]) if total_row else 0
    total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
    if page > total_pages:
        page = total_pages
    offset = (page - 1) * per_page
    opportunities = fetchall(
        f"""
        SELECT o.*, c.name AS customer_name
        FROM opportunities o
        JOIN customers c ON c.id = o.customer_id
        WHERE {where_sql}
        ORDER BY o.updated_at DESC
        LIMIT ? OFFSET ?
        """,
        tuple(params + [per_page, offset]),
    )
    funnel_rows = fetchall(
        """
        SELECT o.stage AS stage, COUNT(1) AS cnt, COALESCE(SUM(o.amount), 0) AS amt
        FROM opportunities o
        GROUP BY o.stage
        """
    )
    funnel: dict[str, tuple[int, float]] = {}
    for r in funnel_rows:
        st = str(r.get("stage") or "")
        funnel[st] = (int(r["cnt"]), float(r["amt"] or 0))
    customers = fetchall("SELECT id, name FROM customers ORDER BY name ASC")
    return render_template(
        "opportunity_list.html",
        opportunities=opportunities,
        customers=customers,
        funnel=funnel,
        filter_stage=f_stage,
        filter_customer_id=f_customer_id,
        filter_owner=f_owner,
        filter_mine=mine,
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
    )


@app.route("/opportunities/export")
def opportunity_list_export():
    if not has_module_permission("opportunity", "view"):
        flash("无权限导出商机。", "danger")
        return redirect(url_for("dashboard"))
    f_stage = request.args.get("stage", "").strip() or None
    f_customer_id = request.args.get("customer_id", type=int)
    f_owner = request.args.get("owner", "").strip() or None
    mine = request.args.get("mine", type=int) == 1
    me = session.get("display_name") or session.get("username") or ""
    conditions: list[str] = ["1=1"]
    params: list[object] = []
    scope_clause, scope_params = build_opportunity_visibility_clause("o")
    conditions.append(scope_clause)
    params.extend(scope_params)
    if f_stage:
        conditions.append("o.stage = ?")
        params.append(f_stage)
    if f_customer_id:
        conditions.append("o.customer_id = ?")
        params.append(f_customer_id)
    if f_owner:
        conditions.append("o.owner LIKE ?")
        params.append(f"%{f_owner}%")
    if mine and me:
        conditions.append("o.owner = ?")
        params.append(me)
    where_sql = " AND ".join(conditions)
    opportunities = fetchall(
        f"""
        SELECT o.*, c.name AS customer_name
        FROM opportunities o
        JOIN customers c ON c.id = o.customer_id
        WHERE {where_sql}
        ORDER BY o.updated_at DESC
        """,
        tuple(params),
    )
    rows = [
        [
            o.get("title") or "",
            o.get("customer_name") or "",
            o.get("amount") or "",
            o.get("stage") or "",
            o.get("owner") or "",
            o.get("status") or "",
            o.get("expected_sign_date") or "",
        ]
        for o in opportunities
    ]
    return _csv_attachment("商机列表导出.csv", ["商机名称", "客户名称", "预计金额", "阶段", "负责人", "状态", "预计签约"], rows)


@app.route("/opportunities/<int:opportunity_id>")
def opportunity_detail(opportunity_id: int):
    if not has_module_permission("opportunity", "view"):
        flash("无权限查看商机。", "danger")
        return redirect(url_for("dashboard"))
    scope_clause, scope_params = build_opportunity_visibility_clause("o")
    item = fetchone(
        f"""
        SELECT o.*, c.name AS customer_name, c.id AS customer_id
        FROM opportunities o
        JOIN customers c ON c.id = o.customer_id
        WHERE o.id = ? AND {scope_clause}
        """,
        tuple([opportunity_id] + scope_params),
    )
    if item is None:
        flash("商机不存在或无权限查看。", "danger")
        return redirect(url_for("opportunity_list"))
    logs = fetchall(
        """
        SELECT * FROM opportunity_stage_logs
        WHERE opportunity_id = ?
        ORDER BY changed_at DESC, id DESC
        """,
        (opportunity_id,),
    )
    linked_contracts = fetchall(
        """
        SELECT id, contract_no, amount, status, updated_at
        FROM contracts ct
        WHERE ct.opportunity_id = ?
        AND {contract_scope_clause}
        ORDER BY updated_at DESC
        """.replace("{contract_scope_clause}", build_contract_visibility_clause("ct")[0]),
        tuple([opportunity_id] + build_contract_visibility_clause("ct")[1]),
    )
    linked_contract_amount_sum = sum(float(c.get("amount") or 0) for c in linked_contracts)
    latest_stage_log = logs[0] if logs else None
    stage_started = _parse_iso_date(str(item.get("stage_started_at") or ""))
    stage_duration_days = (date.today() - stage_started).days if stage_started else None
    latest_follow = fetchone(
        """
        SELECT followed_at, next_followup_at, followed_by, method
        FROM customer_follow_ups
        WHERE opportunity_id = ?
        ORDER BY followed_at DESC, id DESC
        LIMIT 1
        """,
        (opportunity_id,),
    )
    summary_metrics = {
        "stage_duration_days": stage_duration_days,
        "last_stage_changed_at": latest_stage_log.get("changed_at") if latest_stage_log else None,
        "last_stage_changed_by": latest_stage_log.get("changed_by") if latest_stage_log else None,
        "linked_contract_amount_sum": linked_contract_amount_sum,
        "latest_followed_at": latest_follow.get("followed_at") if latest_follow else None,
        "next_followup_at": latest_follow.get("next_followup_at") if latest_follow else None,
    }
    pending_won = has_pending_approval("opportunity", opportunity_id, "won")
    approval_candidates = fetch_approval_candidates(_approval_candidate_role_codes("opportunity"))
    return render_template(
        "opportunity_detail.html",
        o=item,
        stage_logs=logs,
        linked_contracts=linked_contracts,
        pending_won=pending_won,
        summary_metrics=summary_metrics,
        approval_candidates=approval_candidates,
    )


@app.route("/opportunities/<int:opportunity_id>/change-stage", methods=["POST"])
def opportunity_change_stage(opportunity_id: int):
    if not has_module_permission("opportunity", "manage"):
        flash("无权限修改商机阶段。", "danger")
        return redirect(url_for("opportunity_detail", opportunity_id=opportunity_id))
    to_stage = request.form.get("to_stage", "").strip()
    note = request.form.get("note", "").strip() or None
    if to_stage not in OPEN_OPPORTUNITY_STAGES:
        flash("请从列表中选择有效阶段；赢单请走审批，输单请使用「标记输单」。", "warning")
        return redirect(url_for("opportunity_detail", opportunity_id=opportunity_id))
    scope_clause, scope_params = build_opportunity_visibility_clause("o")
    row = fetchone(
        f"SELECT * FROM opportunities o WHERE o.id = ? AND {scope_clause}",
        tuple([opportunity_id] + scope_params),
    )
    if row is None:
        flash("商机不存在或无权限操作。", "danger")
        return redirect(url_for("opportunity_list"))
    if str(row.get("stage") or "") in ("won", "lost"):
        flash("已结束的商机不可再调整阶段。", "warning")
        return redirect(url_for("opportunity_detail", opportunity_id=opportunity_id))
    from_stage = str(row["stage"]) if row.get("stage") else "validate"
    if opportunity_stage_is_rollback(from_stage, to_stage) and not note:
        flash("阶段回退需填写说明。", "danger")
        return redirect(url_for("opportunity_detail", opportunity_id=opportunity_id))
    who = session.get("display_name") or session.get("username") or "?"
    sts = opportunity_status_from_stage(to_stage)
    ts = now_iso()
    execute(
        """
        UPDATE opportunities SET stage = ?, stage_started_at = ?, status = ?, updated_at = ?
        WHERE id = ?
        """,
        (to_stage, ts, sts, ts, opportunity_id),
    )
    insert_opportunity_stage_log(opportunity_id, from_stage, to_stage, note, who)
    get_db().commit()
    flash("阶段已更新。", "success")
    return redirect(url_for("opportunity_detail", opportunity_id=opportunity_id))


@app.route("/opportunities/<int:opportunity_id>/mark-lost", methods=["POST"])
def opportunity_mark_lost(opportunity_id: int):
    if not has_module_permission("opportunity", "manage"):
        flash("无权限标记输单。", "danger")
        return redirect(url_for("opportunity_detail", opportunity_id=opportunity_id))
    lost_reason = request.form.get("lost_reason", "").strip()
    lost_reason_note = request.form.get("lost_reason_note", "").strip() or None
    competitor = request.form.get("competitor", "").strip() or None
    if lost_reason not in LOST_REASON_LABELS:
        flash("请选择丢单原因。", "danger")
        return redirect(url_for("opportunity_detail", opportunity_id=opportunity_id))
    if lost_reason == "other" and not lost_reason_note:
        flash("选择「其他」时请填写说明。", "danger")
        return redirect(url_for("opportunity_detail", opportunity_id=opportunity_id))
    scope_clause, scope_params = build_opportunity_visibility_clause("o")
    row = fetchone(
        f"SELECT * FROM opportunities o WHERE o.id = ? AND {scope_clause}",
        tuple([opportunity_id] + scope_params),
    )
    if row is None:
        flash("商机不存在或无权限操作。", "danger")
        return redirect(url_for("opportunity_list"))
    if str(row.get("stage") or "") in ("won", "lost"):
        flash("该商机已结束。", "warning")
        return redirect(url_for("opportunity_detail", opportunity_id=opportunity_id))
    from_stage = str(row["stage"]) if row.get("stage") else "validate"
    who = session.get("display_name") or session.get("username") or "?"
    ts = now_iso()
    execute(
        """
        UPDATE opportunities SET stage = 'lost', status = 'lost', stage_started_at = ?,
               lost_reason = ?, lost_reason_note = ?, competitor = ?, updated_at = ?
        WHERE id = ?
        """,
        (ts, lost_reason, lost_reason_note, competitor, ts, opportunity_id),
    )
    insert_opportunity_stage_log(
        opportunity_id,
        from_stage,
        "lost",
        f"输单：{LOST_REASON_LABELS.get(lost_reason, lost_reason)}",
        who,
    )
    get_db().commit()
    flash("已标记为输单。", "success")
    return redirect(url_for("opportunity_detail", opportunity_id=opportunity_id))


@app.route("/opportunities/<int:opportunity_id>/delete", methods=["POST"])
def delete_opportunity(opportunity_id: int):
    if not has_module_permission("opportunity", "manage"):
        flash("无权限删除商机。", "danger")
        return redirect(url_for("opportunity_list"))
    scope_clause, scope_params = build_opportunity_visibility_clause("o")
    item = fetchone(
        f"SELECT id FROM opportunities o WHERE o.id = ? AND {scope_clause}",
        tuple([opportunity_id] + scope_params),
    )
    if item is None:
        flash("商机不存在或无权限操作。", "danger")
        return redirect(url_for("opportunity_list"))
    execute("DELETE FROM opportunities WHERE id = ?", (opportunity_id,))
    get_db().commit()
    flash("商机已删除。", "success")
    return redirect(url_for("opportunity_list"))


@app.route("/opportunities/<int:opportunity_id>/submit-won-approval", methods=["POST"])
def submit_opportunity_won_approval(opportunity_id: int):
    if not has_module_permission("opportunity", "manage"):
        flash("无权限提交商机审批。", "danger")
        return redirect(url_for("opportunity_detail", opportunity_id=opportunity_id))
    scope_clause, scope_params = build_opportunity_visibility_clause("o")
    item = fetchone(
        f"SELECT * FROM opportunities o WHERE o.id = ? AND {scope_clause}",
        tuple([opportunity_id] + scope_params),
    )
    if item is None:
        flash("商机不存在或无权限操作。", "danger")
        return redirect(url_for("opportunity_list"))
    cur_stage = str(item.get("stage") or "").strip() or "validate"
    if cur_stage not in ("proposal", "negotiate"):
        flash("赢单审批前请将商机推进至「方案」或「谈判」阶段。", "warning")
        return redirect(url_for("opportunity_detail", opportunity_id=opportunity_id))
    if has_pending_approval("opportunity", opportunity_id, "won"):
        flash("该商机已有待处理的赢单审批。", "warning")
        return redirect(url_for("opportunity_detail", opportunity_id=opportunity_id))
    approver_user_id = parse_int_form_value(request.form.get("approver_user_id"))
    apply_note = request.form.get("apply_note", "").strip() or None
    approver = request.form.get("approver", "").strip()
    approval_candidates = fetch_approval_candidates(_approval_candidate_role_codes("opportunity"))
    candidate_map = {int(u["id"]): str(u.get("display_name") or "").strip() for u in approval_candidates}
    if approver_user_id:
        if approver_user_id not in candidate_map:
            flash("请选择有效的审批人。", "danger")
            return redirect(url_for("opportunity_detail", opportunity_id=opportunity_id))
        approver = candidate_map[approver_user_id]
    if not approver:
        flash("请选择审批人。", "danger")
        return redirect(url_for("opportunity_detail", opportunity_id=opportunity_id))
    submit_approval(
        "opportunity",
        opportunity_id,
        f"商机赢单审批：{item['title']}",
        "won",
        item["owner"],
        approver,
        apply_note,
    )
    get_db().commit()
    flash("已提交商机赢单审批。", "success")
    return redirect(url_for("opportunity_detail", opportunity_id=opportunity_id))


def _contract_execution_metrics(contract: dict, receivables: list, invoices: list) -> dict[str, object]:
    """合同执行摘要：回款/开票进度、TD-K01～K03 判定（与《05-合同管理》《02-待办》草案一致）。"""
    today_s = date.today().isoformat()
    amt = float(contract.get("amount") or 0)
    sum_received = sum(float(r.get("actual_amount") or 0) for r in receivables)
    sum_plan = sum(float(r.get("plan_amount") or 0) for r in receivables)
    sum_invoice = sum(
        float(i.get("amount") or 0)
        for i in invoices
        if str(i.get("status") or "") not in ("invalid", "red_flush")
    )
    receive_pct = (sum_received / amt) if amt > 0 else 0.0
    invoice_pct = (sum_invoice / amt) if amt > 0 else 0.0
    td_k01_rows: list[dict] = []
    for r in receivables:
        plan_amt = float(r.get("plan_amount") or 0)
        act = float(r.get("actual_amount") or 0)
        pd = str(r.get("plan_date") or "")
        if pd and pd <= today_s and act < plan_amt - 1e-9:
            td_k01_rows.append(dict(r))
    planned_due = sum(float(r.get("plan_amount") or 0) for r in receivables if str(r.get("plan_date") or "") <= today_s)
    actual_due = sum(float(r.get("actual_amount") or 0) for r in receivables if str(r.get("plan_date") or "") <= today_s)
    gap = planned_due - actual_due
    deviation_pct = (gap / planned_due) if planned_due > 1e-9 else 0.0
    td_k03 = planned_due > 0 and deviation_pct > CONTRACT_DEVIATION_THRESHOLD_PCT
    td_k02_rows = [i for i in invoices if str(i.get("status") or "") == "pending"]
    next_receivable_plan = None
    pending_receivables = []
    for row in receivables:
        plan_amt = float(row.get("plan_amount") or 0)
        actual_amt = float(row.get("actual_amount") or 0)
        remaining = plan_amt - actual_amt
        if remaining > 1e-9:
            pending_receivables.append((str(row.get("plan_date") or ""), row, remaining))
    pending_receivables.sort(key=lambda x: x[0])
    if pending_receivables:
        _, next_row, remaining_amount = pending_receivables[0]
        next_receivable_plan = {**dict(next_row), "remaining_amount": remaining_amount}
    return {
        "contract_amount": amt,
        "sum_received": sum_received,
        "sum_plan": sum_plan,
        "sum_invoice": sum_invoice,
        "receive_pct": receive_pct,
        "invoice_pct": invoice_pct,
        "td_k01_rows": td_k01_rows,
        "td_k02_rows": td_k02_rows,
        "td_k03": td_k03,
        "deviation_pct": deviation_pct,
        "planned_due": planned_due,
        "actual_due": actual_due,
        "outstanding_receivable_amount": max(0.0, amt - sum_received),
        "outstanding_invoice_amount": max(0.0, amt - sum_invoice),
        "overdue_receivable_count": len(td_k01_rows),
        "next_receivable_plan": next_receivable_plan,
        "pending_invoice_count": len(td_k02_rows),
    }


@app.route("/contracts/<int:contract_id>", methods=["GET", "POST"])
def contract_detail(contract_id: int):
    if not has_module_permission("contract", "view"):
        flash("无权限查看合同。", "danger")
        return redirect(url_for("dashboard"))
    scope_clause, scope_params = build_contract_visibility_clause("ct")
    contract = fetchone(
        f"""
        SELECT ct.*, c.name AS customer_name, c.id AS customer_id,
               p.name AS project_name, p.manager AS project_manager,
               o.title AS opportunity_title, o.id AS opportunity_db_id
        FROM contracts ct
        JOIN customers c ON c.id = ct.customer_id
        LEFT JOIN projects p ON p.id = ct.project_id AND p.deleted_at IS NULL
        LEFT JOIN opportunities o ON o.id = ct.opportunity_id
        WHERE ct.id = ? AND {scope_clause}
        """,
        tuple([contract_id] + scope_params),
    )
    if contract is None:
        flash("合同不存在或无权限查看。", "danger")
        return redirect(url_for("contract_list"))

    if request.method == "POST":
        if not has_module_permission("contract", "manage"):
            flash("无权限编辑合同。", "danger")
            return redirect(url_for("contract_detail", contract_id=contract_id))
        project_id_raw = request.form.get("project_id", "").strip()
        project_id = parse_int_form_value(project_id_raw)
        if project_id_raw and project_id is None:
            flash("项目信息无效。", "danger")
            return redirect(url_for("contract_detail", contract_id=contract_id))
        if project_id is not None:
            project_scope_clause, project_scope_params = build_project_visibility_clause("p")
            visible_project = fetchone(
                f"SELECT id FROM projects p WHERE p.id = ? AND p.deleted_at IS NULL AND {project_scope_clause}",
                tuple([project_id] + project_scope_params),
            )
            if visible_project is None:
                flash("所选项目不存在或无权限操作。", "danger")
                return redirect(url_for("contract_detail", contract_id=contract_id))
        opportunity_id_raw = request.form.get("opportunity_id", "").strip()
        opportunity_id = parse_int_form_value(opportunity_id_raw)
        if opportunity_id_raw and opportunity_id is None:
            flash("商机信息无效。", "danger")
            return redirect(url_for("contract_detail", contract_id=contract_id))
        if opportunity_id is not None:
            opp_scope_clause, opp_scope_params = build_opportunity_visibility_clause("o")
            visible_opportunity = fetchone(
                f"SELECT id FROM opportunities o WHERE o.id = ? AND {opp_scope_clause}",
                tuple([opportunity_id] + opp_scope_params),
            )
            if visible_opportunity is None:
                flash("所选商机不存在或无权限操作。", "danger")
                return redirect(url_for("contract_detail", contract_id=contract_id))
        customer_id = int(contract["customer_id"])
        if not _project_belongs_customer(project_id, customer_id):
            flash("所选项目不属于当前客户。", "danger")
            return redirect(url_for("contract_detail", contract_id=contract_id))
        if not _opportunity_belongs_customer(opportunity_id, customer_id):
            flash("所选商机不属于当前客户。", "danger")
            return redirect(url_for("contract_detail", contract_id=contract_id))
        contract_no = request.form.get("contract_no", "").strip()
        amount = parse_float_form_value(request.form.get("amount"), 0.0) or 0.0
        sign_date = request.form.get("sign_date", "").strip() or None
        end_date = request.form.get("end_date", "").strip() or None
        status = request.form.get("status", "draft").strip()
        owner_user_id = parse_int_form_value(request.form.get("owner_user_id"), 0) or 0
        owner = request.form.get("owner", "").strip() or None
        if owner_user_id:
            owner = manager_display_string(owner_user_id) or owner
        currency = request.form.get("currency", "CNY").strip() or "CNY"
        if "sales" in current_user_role_codes() and "sales_director" not in current_user_role_codes() and not current_user_matches_text(owner):
            owner = session.get("display_name") or session.get("username") or owner
        if not contract_no:
            flash("合同编号为必填项。", "danger")
            return redirect(url_for("contract_detail", contract_id=contract_id))
        execute(
            """
            UPDATE contracts SET
                project_id = ?, opportunity_id = ?, contract_no = ?, amount = ?, sign_date = ?, end_date = ?,
                status = ?, owner = ?, currency = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                project_id,
                opportunity_id,
                contract_no,
                amount,
                sign_date,
                end_date,
                status,
                owner,
                currency,
                now_iso(),
                contract_id,
            ),
        )
        get_db().commit()
        flash("合同已保存。", "success")
        return redirect(url_for("contract_detail", contract_id=contract_id))

    receivables = fetchall(
        "SELECT * FROM receivables WHERE contract_id = ? ORDER BY plan_date ASC, id ASC",
        (contract_id,),
    )
    invoices = fetchall(
        """
        SELECT i.*, c.name AS customer_name
        FROM invoices i
        JOIN contracts ct ON ct.id = i.contract_id
        JOIN customers c ON c.id = ct.customer_id
        WHERE i.contract_id = ?
        ORDER BY i.invoice_date DESC, i.id DESC
        """,
        (contract_id,),
    )
    approvals = fetchall(
        """
        SELECT * FROM approvals
        WHERE module_type = 'contract' AND module_id = ?
        ORDER BY updated_at DESC
        """,
        (contract_id,),
    )
    metrics = _contract_execution_metrics(contract, receivables, invoices)
    summary_metrics = {
        "outstanding_receivable_amount": float(metrics.get("outstanding_receivable_amount") or 0),
        "outstanding_invoice_amount": float(metrics.get("outstanding_invoice_amount") or 0),
        "overdue_receivable_count": int(metrics.get("overdue_receivable_count") or 0),
        "next_receivable_plan": metrics.get("next_receivable_plan"),
        "pending_invoice_count": int(metrics.get("pending_invoice_count") or 0),
    }
    pending_sign_approval = has_pending_approval("contract", contract_id, "signed")
    opp_scope_clause, opp_scope_params = build_opportunity_visibility_clause("o")
    opportunities = fetchall(
        f"SELECT id, title FROM opportunities o WHERE o.customer_id = ? AND {opp_scope_clause} ORDER BY updated_at DESC",
        tuple([contract["customer_id"]] + opp_scope_params),
    )
    project_scope_clause, project_scope_params = build_project_visibility_clause("p")
    projects = fetchall(
        f"SELECT id, name FROM projects p WHERE p.customer_id = ? AND p.deleted_at IS NULL AND {project_scope_clause} ORDER BY created_at ASC",
        tuple([contract["customer_id"]] + project_scope_params),
    )
    approval_candidates = fetch_approval_candidates(_approval_candidate_role_codes("contract"))
    return render_template(
        "contract_detail.html",
        contract=contract,
        receivables=receivables,
        invoices=invoices,
        approvals=approvals,
        metrics=metrics,
        opportunities=opportunities,
        projects=projects,
        today_iso=date.today().isoformat(),
        summary_metrics=summary_metrics,
        approval_candidates=approval_candidates,
        pending_sign_approval=pending_sign_approval,
    )


@app.route("/contracts", methods=["GET", "POST"])
def contract_list():
    if request.method == "GET" and not has_module_permission("contract", "view"):
        flash("无权限查看合同。", "danger")
        return redirect(url_for("dashboard"))
    filter_q = request.args.get("q", "").strip()
    filter_status = request.args.get("status", "").strip()
    filter_customer_id = request.args.get("customer_id", type=int)
    scope_clause, scope_params = build_contract_visibility_clause("ct")
    if request.method == "POST" and not has_module_permission("contract", "manage"):
        flash("无权限新增合同。", "danger")
        return redirect(url_for("contract_list"))
    if request.method == "POST":
        customer_id = parse_int_form_value(request.form.get("customer_id"), 0) or 0
        customer_scope_clause, customer_scope_params = build_customer_visibility_clause("c")
        visible_customer = fetchone(
            f"SELECT id FROM customers c WHERE c.id = ? AND {customer_scope_clause}",
            tuple([customer_id] + customer_scope_params),
        )
        if visible_customer is None:
            flash("所选客户不存在或无权限操作。", "danger")
            return redirect(url_for("contract_list"))
        project_id_raw = request.form.get("project_id", "").strip()
        project_id = parse_int_form_value(project_id_raw)
        if project_id_raw and project_id is None:
            flash("项目信息无效。", "danger")
            return redirect(url_for("contract_list"))
        if project_id is not None:
            project_scope_clause, project_scope_params = build_project_visibility_clause("p")
            visible_project = fetchone(
                f"SELECT id FROM projects p WHERE p.id = ? AND p.deleted_at IS NULL AND {project_scope_clause}",
                tuple([project_id] + project_scope_params),
            )
            if visible_project is None:
                flash("所选项目不存在或无权限操作。", "danger")
                return redirect(url_for("contract_list"))
        contract_no = request.form.get("contract_no", "").strip()
        amount = parse_float_form_value(request.form.get("amount"), 0.0) or 0.0
        sign_date = request.form.get("sign_date", "").strip() or None
        end_date = request.form.get("end_date", "").strip() or None
        status = request.form.get("status", "draft").strip()
        opportunity_id_raw = request.form.get("opportunity_id", "").strip()
        opportunity_id = parse_int_form_value(opportunity_id_raw)
        if opportunity_id_raw and opportunity_id is None:
            flash("商机信息无效。", "danger")
            return redirect(url_for("contract_list"))
        if opportunity_id is not None:
            opp_scope_clause, opp_scope_params = build_opportunity_visibility_clause("o")
            visible_opportunity = fetchone(
                f"SELECT id FROM opportunities o WHERE o.id = ? AND {opp_scope_clause}",
                tuple([opportunity_id] + opp_scope_params),
            )
            if visible_opportunity is None:
                flash("所选商机不存在或无权限操作。", "danger")
                return redirect(url_for("contract_list"))
        owner_user_id = parse_int_form_value(request.form.get("owner_user_id"), 0) or 0
        owner = request.form.get("owner", "").strip() or None
        if owner_user_id:
            owner = manager_display_string(owner_user_id) or owner
        if not owner:
            owner = session.get("display_name") or session.get("username") or None
        if "sales" in current_user_role_codes() and "sales_director" not in current_user_role_codes() and not current_user_matches_text(owner):
            owner = session.get("display_name") or session.get("username") or owner
        currency = request.form.get("currency", "CNY").strip() or "CNY"
        if customer_id <= 0 or not contract_no:
            flash("客户与合同编号为必填项。", "danger")
            return redirect(url_for("contract_list"))
        if not _project_belongs_customer(project_id, customer_id):
            flash("所选项目不属于当前客户。", "danger")
            return redirect(url_for("contract_list"))
        if not _opportunity_belongs_customer(opportunity_id, customer_id):
            flash("所选商机不属于当前客户。", "danger")
            return redirect(url_for("contract_list"))
        execute(
            """
            INSERT INTO contracts(customer_id, project_id, opportunity_id, contract_no, amount, sign_date, end_date, status, owner, currency, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                customer_id,
                project_id,
                opportunity_id,
                contract_no,
                amount,
                sign_date,
                end_date,
                status,
                owner,
                currency,
                now_iso(),
            ),
        )
        get_db().commit()
        flash("合同新增成功。", "success")
        return redirect(url_for("contract_list"))
    contract_filters: list[str] = []
    contract_params: list[object] = []
    contract_filters.append(scope_clause)
    contract_params.extend(scope_params)
    if filter_q:
        like_q = f"%{filter_q}%"
        contract_filters.append("(ct.contract_no LIKE ? OR c.name LIKE ? OR COALESCE(p.name, '') LIKE ? OR COALESCE(o.title, '') LIKE ?)")
        contract_params.extend([like_q, like_q, like_q, like_q])
    if filter_status:
        contract_filters.append("ct.status = ?")
        contract_params.append(filter_status)
    if filter_customer_id:
        contract_filters.append("ct.customer_id = ?")
        contract_params.append(filter_customer_id)
    contract_sql = """
        SELECT ct.*, c.name AS customer_name, p.name AS project_name, o.title AS opportunity_title
        FROM contracts ct
        JOIN customers c ON c.id = ct.customer_id
        LEFT JOIN projects p ON p.id = ct.project_id AND p.deleted_at IS NULL
        LEFT JOIN opportunities o ON o.id = ct.opportunity_id
    """
    if contract_filters:
        contract_sql += "\nWHERE " + " AND ".join(contract_filters)
    contract_sql += "\nORDER BY ct.updated_at DESC"
    contracts = fetchall(contract_sql, tuple(contract_params))
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
    page, contracts, total_pages = _paginate_rows(contracts, page, per_page)
    customer_list_scope_clause, customer_list_scope_params = build_customer_visibility_clause("c")
    customers = fetchall(
        f"SELECT id, name FROM customers c WHERE {customer_list_scope_clause} ORDER BY name ASC",
        tuple(customer_list_scope_params),
    )
    project_list_scope_clause, project_list_scope_params = build_project_visibility_clause("p")
    projects = fetchall(
        f"SELECT id, name FROM projects p WHERE p.deleted_at IS NULL AND {project_list_scope_clause} ORDER BY created_at ASC",
        tuple(project_list_scope_params),
    )
    opportunities = fetchall(
        """
        SELECT o.id, o.title, c.name AS customer_name
        FROM opportunities o
        JOIN customers c ON c.id = o.customer_id
        WHERE {opp_scope_clause}
        ORDER BY o.updated_at DESC
        LIMIT 300
        """.replace("{opp_scope_clause}", build_opportunity_visibility_clause("o")[0]),
        tuple(build_opportunity_visibility_clause("o")[1]),
    )
    approval_candidates = fetch_approval_candidates(_approval_candidate_role_codes("contract"))
    return render_template(
        "contract_list.html",
        contracts=contracts,
        customers=customers,
        projects=projects,
        opportunities=opportunities,
        approval_candidates=approval_candidates,
        filter_q=filter_q,
        filter_status=filter_status,
        filter_customer_id=filter_customer_id,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
    )


@app.route("/contracts/export")
def contract_list_export():
    if not has_module_permission("contract", "view"):
        flash("无权限导出合同。", "danger")
        return redirect(url_for("dashboard"))
    scope_clause, scope_params = build_contract_visibility_clause("ct")
    filter_q = request.args.get("q", "").strip()
    filter_status = request.args.get("status", "").strip()
    filter_customer_id = request.args.get("customer_id", type=int)
    contract_filters: list[str] = []
    contract_params: list[object] = []
    contract_filters.append(scope_clause)
    contract_params.extend(scope_params)
    if filter_q:
        like_q = f"%{filter_q}%"
        contract_filters.append("(ct.contract_no LIKE ? OR c.name LIKE ? OR COALESCE(p.name, '') LIKE ? OR COALESCE(o.title, '') LIKE ?)")
        contract_params.extend([like_q, like_q, like_q, like_q])
    if filter_status:
        contract_filters.append("ct.status = ?")
        contract_params.append(filter_status)
    if filter_customer_id:
        contract_filters.append("ct.customer_id = ?")
        contract_params.append(filter_customer_id)
    contract_sql = """
        SELECT ct.*, c.name AS customer_name, p.name AS project_name, o.title AS opportunity_title
        FROM contracts ct
        JOIN customers c ON c.id = ct.customer_id
        LEFT JOIN projects p ON p.id = ct.project_id AND p.deleted_at IS NULL
        LEFT JOIN opportunities o ON o.id = ct.opportunity_id
    """
    if contract_filters:
        contract_sql += "\nWHERE " + " AND ".join(contract_filters)
    contract_sql += "\nORDER BY ct.updated_at DESC"
    rows = fetchall(contract_sql, tuple(contract_params))
    csv_rows = [
        [
            c.get("contract_no") or "",
            c.get("customer_name") or "",
            c.get("project_name") or "",
            c.get("opportunity_title") or "",
            c.get("amount") or "",
            c.get("status") or "",
            c.get("sign_date") or "",
            c.get("end_date") or "",
        ]
        for c in rows
    ]
    return _csv_attachment("合同列表导出.csv", ["合同编号", "客户名称", "项目名称", "商机名称", "合同金额", "状态", "签约日期", "到期日期"], csv_rows)


@app.route("/contracts/<int:contract_id>/delete", methods=["POST"])
def delete_contract(contract_id: int):
    if not has_module_permission("contract", "manage"):
        flash("无权限删除合同。", "danger")
        return redirect(url_for("contract_list"))
    scope_clause, scope_params = build_contract_visibility_clause("ct")
    contract = fetchone(
        f"SELECT id FROM contracts ct WHERE ct.id = ? AND {scope_clause}",
        tuple([contract_id] + scope_params),
    )
    if contract is None:
        flash("合同不存在或无权限操作。", "danger")
        return redirect(url_for("contract_list"))
    execute("DELETE FROM contracts WHERE id = ?", (contract_id,))
    get_db().commit()
    flash("合同已删除。", "success")
    return redirect(url_for("contract_list"))


@app.route("/contracts/<int:contract_id>/submit-sign-approval", methods=["POST"])
def submit_contract_sign_approval(contract_id: int):
    if not has_module_permission("contract", "manage"):
        flash("无权限提交合同审批。", "danger")
        return redirect(url_for("contract_list"))
    scope_clause, scope_params = build_contract_visibility_clause("ct")
    contract = fetchone(
        f"SELECT * FROM contracts ct WHERE ct.id = ? AND {scope_clause}",
        tuple([contract_id] + scope_params),
    )
    if contract is None:
        flash("合同不存在或无权限操作。", "danger")
        return redirect(url_for("contract_list"))
    if has_pending_approval("contract", contract_id, "signed"):
        flash("该合同已有待处理的签约审批。", "warning")
        return redirect(url_for("contract_list"))
    approver_user_id = parse_int_form_value(request.form.get("approver_user_id"))
    apply_note = request.form.get("apply_note", "").strip() or None
    approver = request.form.get("approver", "").strip()
    approval_candidates = fetch_approval_candidates(_approval_candidate_role_codes("contract"))
    candidate_map = {int(u["id"]): str(u.get("display_name") or "").strip() for u in approval_candidates}
    if approver_user_id:
        if approver_user_id not in candidate_map:
            flash("请选择有效的审批人。", "danger")
            return redirect(url_for("contract_detail", contract_id=contract_id))
        approver = candidate_map[approver_user_id]
    if not approver:
        flash("请选择审批人。", "danger")
        return redirect(url_for("contract_detail", contract_id=contract_id))
    submit_approval(
        "contract",
        contract_id,
        f"合同签约审批：{contract['contract_no']}",
        "signed",
        "合同管理员",
        approver,
        apply_note,
    )
    get_db().commit()
    flash("已提交合同签约审批。", "success")
    return redirect(url_for("contract_detail", contract_id=contract_id))


@app.route("/approvals")
def approval_list():
    if not has_module_permission("approval", "handle") and not has_module_permission("approval", "view"):
        flash("无权限查看审批中心。", "danger")
        return redirect(url_for("dashboard"))
    keyword = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "").strip()
    category_filter = request.args.get("category", "").strip()
    visible_requested_values = _approval_scope_requested_values()
    if visible_requested_values is not None and not visible_requested_values:
        flash("无权限查看当前审批类别。", "danger")
        return redirect(url_for("dashboard"))
    filters = ["1=1"]
    params: list[object] = []
    if keyword:
        like_q = f"%{keyword}%"
        filters.append("(title LIKE ? OR applicant LIKE ? OR approver LIKE ? OR module_type LIKE ? OR requested_value LIKE ?)")
        params.extend([like_q, like_q, like_q, like_q, like_q])
    if status_filter:
        filters.append("status = ?")
        params.append(status_filter)
    if visible_requested_values is not None:
        visible_values = sorted(visible_requested_values)
        filters.append(f"requested_value IN ({', '.join('?' for _ in visible_values)})")
        params.extend(visible_values)
    if category_filter:
        filters.append("requested_value = ?")
        params.append(category_filter)
    sql = "SELECT * FROM approvals WHERE " + " AND ".join(filters) + " ORDER BY updated_at DESC"
    approvals_all = fetchall(sql, tuple(params))
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
    page, approvals, total_pages = _paginate_rows(approvals_all, page, per_page)
    return render_template(
        "approval_list.html",
        approvals=approvals,
        keyword=keyword,
        status_filter=status_filter,
        category_filter=category_filter,
        category_options=_approval_category_options(),
        approval_category_labels={opt["value"]: opt["label"] for opt in _approval_category_options()},
        page=page,
        per_page=per_page,
        total_pages=total_pages,
    )


@app.route("/approvals/export")
def approval_list_export():
    if not has_module_permission("approval", "handle") and not has_module_permission("approval", "view"):
        flash("无权限导出审批。", "danger")
        return redirect(url_for("dashboard"))
    keyword = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "").strip()
    category_filter = request.args.get("category", "").strip()
    visible_requested_values = _approval_scope_requested_values()
    if visible_requested_values is not None and not visible_requested_values:
        flash("无权限导出当前审批类别。", "danger")
        return redirect(url_for("dashboard"))
    filters = ["1=1"]
    params: list[object] = []
    if keyword:
        like_q = f"%{keyword}%"
        filters.append("(title LIKE ? OR applicant LIKE ? OR approver LIKE ? OR module_type LIKE ? OR requested_value LIKE ?)")
        params.extend([like_q, like_q, like_q, like_q, like_q])
    if status_filter:
        filters.append("status = ?")
        params.append(status_filter)
    if visible_requested_values is not None:
        visible_values = sorted(visible_requested_values)
        filters.append(f"requested_value IN ({', '.join('?' for _ in visible_values)})")
        params.extend(visible_values)
    if category_filter:
        filters.append("requested_value = ?")
        params.append(category_filter)
    sql = "SELECT * FROM approvals WHERE " + " AND ".join(filters) + " ORDER BY updated_at DESC"
    rows = fetchall(sql, tuple(params))
    csv_rows = [
        [
            a.get("title") or "",
            approval_requested_value_label(a.get("requested_value")),
            a.get("module_type") or "",
            a.get("applicant") or "",
            a.get("approver") or "",
            a.get("status") or "",
            a.get("updated_at") or "",
        ]
        for a in rows
    ]
    return _csv_attachment("审批列表导出.csv", ["审批标题", "审批类别", "业务模块", "申请人", "审批人", "状态", "更新时间"], csv_rows)


@app.route("/approvals/<int:approval_id>")
def approval_detail(approval_id: int):
    if not has_module_permission("approval", "handle") and not has_module_permission("approval", "view"):
        flash("无权限查看审批详情。", "danger")
        return redirect(url_for("approval_list"))
    approval = fetchone("SELECT * FROM approvals WHERE id = ?", (approval_id,))
    if approval is None:
        flash("审批记录不存在。", "danger")
        return redirect(url_for("approval_list"))
    if not _approval_record_is_visible(approval):
        flash("无权限查看该审批类别。", "danger")
        return redirect(url_for("approval_list"))
    context = _build_approval_ai_context(approval)
    module_type = str(approval.get("module_type") or "")
    module_id = int(approval.get("module_id") or 0)
    module_link = None
    module_label = "-"
    if module_type == "project":
        module_link = url_for("project_detail", project_id=module_id)
        module_label = "项目"
    elif module_type == "opportunity":
        module_link = url_for("opportunity_detail", opportunity_id=module_id)
        module_label = "商机"
    elif module_type == "contract":
        module_link = url_for("contract_detail", contract_id=module_id)
        module_label = "合同"
    requested_value = str(approval.get("requested_value") or "")
    requested_value_label = approval_requested_value_label(requested_value)
    context_labels = {
        "id": "记录编号",
        "title": "标题",
        "contract_no": "合同编号",
        "amount": "金额",
        "status": "状态",
        "customer_name": "客户名称",
        "stage": "阶段",
        "name": "项目名称",
        "current_stage": "当前阶段",
        "progress": "当前进度",
        "open_high_risk_count": "未关闭高风险",
        "overdue_task_count": "逾期任务数",
    }
    return render_template(
        "approval_detail.html",
        approval=approval,
        approval_context=context,
        approval_context_labels=context_labels,
        module_link=module_link,
        module_label=module_label,
        requested_value_label=requested_value_label,
        module_type_label={"project": "项目", "opportunity": "商机", "contract": "合同"}.get(module_type, module_type or "-"),
    )


def _build_approval_ai_context(approval: dict[str, object]) -> dict[str, object]:
    module_type = str(approval.get("module_type") or "")
    module_id = int(approval.get("module_id") or 0)
    if module_type == "contract":
        return fetchone(
            """
            SELECT ct.id, ct.contract_no, ct.amount, ct.status, c.name AS customer_name
            FROM contracts ct
            LEFT JOIN customers c ON c.id = ct.customer_id
            WHERE ct.id = ?
            """,
            (module_id,),
        ) or {}
    if module_type == "opportunity":
        return fetchone(
            """
            SELECT o.id, o.title, o.amount, o.stage, o.status, c.name AS customer_name
            FROM opportunities o
            LEFT JOIN customers c ON c.id = o.customer_id
            WHERE o.id = ?
            """,
            (module_id,),
        ) or {}
    if module_type == "project":
        project = fetchone(
            """
            SELECT p.id, p.name, p.status, p.current_stage, c.name AS customer_name
            FROM projects p
            LEFT JOIN customers c ON c.id = p.customer_id
            WHERE p.id = ? AND p.deleted_at IS NULL
            """,
            (module_id,),
        ) or {}
        if project:
            project["progress"] = compute_project_progress(module_id)
            project["open_high_risk_count"] = open_high_risk_count(module_id)
            overdue = fetchone(
                """
                SELECT COUNT(1) AS c
                FROM tasks
                WHERE project_id = ?
                  AND status NOT IN ('done', 'closed', 'completed')
                  AND planned_end IS NOT NULL
                  AND planned_end < ?
                """,
                (module_id, date.today().isoformat()),
            )
            project["overdue_task_count"] = int(overdue["c"]) if overdue else 0
        return project
    return {}


@app.route("/approvals/<int:approval_id>/ai/summary", methods=["POST"])
def approval_ai_summary(approval_id: int):
    if not has_module_permission("approval", "handle") and not has_module_permission("approval", "view"):
        return jsonify({"error": "无权限访问审批 AI 能力。"}), 403
    approval = fetchone("SELECT * FROM approvals WHERE id = ?", (approval_id,))
    if approval is None:
        return jsonify({"error": "审批记录不存在。"}), 404
    context = _build_approval_ai_context(approval)
    result = build_approval_summary(approval, context)
    generation_id = log_ai_generation(
        "approval_summary",
        "approval",
        approval_id,
        {
            "module_type": approval.get("module_type"),
            "module_id": approval.get("module_id"),
            "status": approval.get("status"),
            "context_keys": sorted(context.keys()),
        },
        result,
    )
    get_db().commit()
    return jsonify({**result, "context": context, "generation_id": generation_id, "generated_at": now_iso()})


@app.route("/approvals/<int:approval_id>/approve", methods=["POST"])
def approve_item(approval_id: int):
    if not has_module_permission("approval", "handle"):
        flash("无权限审批。", "danger")
        return redirect(url_for("approval_list"))
    approval = fetchone("SELECT * FROM approvals WHERE id = ?", (approval_id,))
    if approval is None:
        flash("审批记录不存在。", "danger")
        return redirect(url_for("approval_list"))
    if not _approval_record_is_visible(approval):
        flash("无权限处理该审批类别。", "danger")
        return redirect(url_for("approval_list"))
    if approval["status"] != "pending":
        flash("该审批已处理。", "warning")
        return redirect(url_for("approval_list"))
    comment = request.form.get("comment", "").strip() or "审批通过"
    apply_approval(approval)
    execute(
        "UPDATE approvals SET status = 'approved', comment = ?, updated_at = ? WHERE id = ?",
        (comment, now_iso(), approval_id),
    )
    get_db().commit()
    flash("审批已通过并执行。", "success")
    return redirect(url_for("approval_list"))


@app.route("/approvals/<int:approval_id>/reject", methods=["POST"])
def reject_item(approval_id: int):
    if not has_module_permission("approval", "handle"):
        flash("无权限审批。", "danger")
        return redirect(url_for("approval_list"))
    approval = fetchone("SELECT * FROM approvals WHERE id = ?", (approval_id,))
    if approval is None:
        flash("审批记录不存在。", "danger")
        return redirect(url_for("approval_list"))
    if not _approval_record_is_visible(approval):
        flash("无权限处理该审批类别。", "danger")
        return redirect(url_for("approval_list"))
    if approval["status"] != "pending":
        flash("该审批已处理。", "warning")
        return redirect(url_for("approval_list"))
    comment = request.form.get("comment", "").strip() or "审批驳回"
    execute(
        "UPDATE approvals SET status = 'rejected', comment = ?, updated_at = ? WHERE id = ?",
        (comment, now_iso(), approval_id),
    )
    get_db().commit()
    flash("审批已驳回。", "success")
    return redirect(url_for("approval_list"))


@app.route("/receivables", methods=["GET", "POST"])
def receivable_list():
    if request.method == "GET" and not has_module_permission("receivable", "view"):
        flash("无权限查看回款。", "danger")
        return redirect(url_for("dashboard"))
    contract_filter = request.args.get("contract_id", type=int)
    filter_q = request.args.get("q", "").strip()
    filter_status = request.args.get("status", "").strip()
    contract_scope_clause, contract_scope_params = build_contract_visibility_clause("ct")
    filter_contract = None
    if contract_filter:
        filter_contract = fetchone(
            f"SELECT id, contract_no FROM contracts ct WHERE ct.id = ? AND {contract_scope_clause}",
            tuple([contract_filter] + contract_scope_params),
        )
    if request.method == "POST" and not has_module_permission("receivable", "manage"):
        flash("无权限新增回款计划。", "danger")
        return redirect(url_for("receivable_list", contract_id=contract_filter) if contract_filter else url_for("receivable_list"))
    if request.method == "POST":
        contract_id = parse_int_form_value(request.form.get("contract_id"), 0) or 0
        selected_contract = fetchone(
            f"SELECT id FROM contracts ct WHERE ct.id = ? AND {contract_scope_clause}",
            tuple([contract_id] + contract_scope_params),
        )
        if selected_contract is None:
            flash("所选合同不存在或无权限操作。", "danger")
            return redirect(url_for("receivable_list", contract_id=contract_filter) if contract_filter else url_for("receivable_list"))
        plan_date = request.form.get("plan_date", "").strip()
        plan_amount = parse_float_form_value(request.form.get("plan_amount"), 0.0) or 0.0
        note = request.form.get("note", "").strip() or None
        if contract_id <= 0 or not plan_date or plan_amount <= 0:
            flash("合同、计划回款日期、计划回款金额为必填项。", "danger")
            return redirect(
                url_for("receivable_list", contract_id=contract_filter) if contract_filter else url_for("receivable_list")
            )
        execute(
            """
            INSERT INTO receivables(contract_id, plan_date, plan_amount, actual_date, actual_amount, status, note, updated_at)
            VALUES (?, ?, ?, NULL, 0, 'planned', ?, ?)
            """,
            (contract_id, plan_date, plan_amount, note, now_iso()),
        )
        get_db().commit()
        flash("回款计划新增成功。", "success")
        return redirect(url_for("receivable_list", contract_id=contract_filter) if contract_filter else url_for("receivable_list"))

    execute(
        """
        UPDATE receivables
        SET status = 'overdue', updated_at = ?
        WHERE status IN ('planned', 'partial', 'overdue') AND plan_date < ? AND (actual_amount IS NULL OR actual_amount < plan_amount)
        """,
        (now_iso(), date.today().isoformat()),
    )
    get_db().commit()
    receivable_filters: list[str] = []
    receivable_params: list[object] = []
    receivable_filters.append(contract_scope_clause)
    receivable_params.extend(contract_scope_params)
    if contract_filter:
        receivable_filters.append("r.contract_id = ?")
        receivable_params.append(contract_filter)
    if filter_q:
        like_q = f"%{filter_q}%"
        receivable_filters.append("(ct.contract_no LIKE ? OR c.name LIKE ?)")
        receivable_params.extend([like_q, like_q])
    if filter_status:
        receivable_filters.append("r.status = ?")
        receivable_params.append(filter_status)
    receivable_sql = """
        SELECT r.*, ct.contract_no, c.name AS customer_name
        FROM receivables r
        JOIN contracts ct ON ct.id = r.contract_id
        JOIN customers c ON c.id = ct.customer_id
    """
    if receivable_filters:
        receivable_sql += "\nWHERE " + " AND ".join(receivable_filters)
    receivable_sql += "\nORDER BY r.plan_date ASC, r.id ASC"
    receivables_all = fetchall(receivable_sql, tuple(receivable_params))
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
    page, receivables, total_pages = _paginate_rows(receivables_all, page, per_page)
    contracts = fetchall(
        """
        SELECT ct.id, ct.contract_no, c.name AS customer_name
        FROM contracts ct
        JOIN customers c ON c.id = ct.customer_id
        WHERE {contract_scope_clause}
        ORDER BY ct.updated_at DESC
        """.replace("{contract_scope_clause}", contract_scope_clause)
    )
    return render_template(
        "receivable_list.html",
        receivables=receivables,
        contracts=contracts,
        filter_contract=filter_contract,
        filter_q=filter_q,
        filter_status=filter_status,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
    )


@app.route("/receivables/export")
def receivable_list_export():
    if not has_module_permission("receivable", "view"):
        flash("无权限导出回款。", "danger")
        return redirect(url_for("dashboard"))
    contract_filter = request.args.get("contract_id", type=int)
    filter_q = request.args.get("q", "").strip()
    filter_status = request.args.get("status", "").strip()
    contract_scope_clause, contract_scope_params = build_contract_visibility_clause("ct")
    receivable_filters: list[str] = []
    receivable_params: list[object] = []
    receivable_filters.append(contract_scope_clause)
    receivable_params.extend(contract_scope_params)
    if contract_filter:
        receivable_filters.append("r.contract_id = ?")
        receivable_params.append(contract_filter)
    if filter_q:
        like_q = f"%{filter_q}%"
        receivable_filters.append("(ct.contract_no LIKE ? OR c.name LIKE ?)")
        receivable_params.extend([like_q, like_q])
    if filter_status:
        receivable_filters.append("r.status = ?")
        receivable_params.append(filter_status)
    receivable_sql = """
        SELECT r.*, ct.contract_no, c.name AS customer_name
        FROM receivables r
        JOIN contracts ct ON ct.id = r.contract_id
        JOIN customers c ON c.id = ct.customer_id
    """
    if receivable_filters:
        receivable_sql += "\nWHERE " + " AND ".join(receivable_filters)
    receivable_sql += "\nORDER BY r.plan_date ASC, r.id ASC"
    rows = fetchall(receivable_sql, tuple(receivable_params))
    csv_rows = [
        [
            r.get("contract_no") or "",
            r.get("customer_name") or "",
            r.get("plan_date") or "",
            r.get("plan_amount") or "",
            r.get("actual_date") or "",
            r.get("actual_amount") or "",
            r.get("status") or "",
            r.get("note") or "",
        ]
        for r in rows
    ]
    return _csv_attachment("回款列表导出.csv", ["合同编号", "客户名称", "计划日期", "计划金额", "实际日期", "实际金额", "状态", "备注"], csv_rows)


@app.route("/receivables/<int:receivable_id>/receive", methods=["POST"])
def receive_receivable(receivable_id: int):
    if not has_module_permission("receivable", "manage"):
        flash("无权限登记回款。", "danger")
        return redirect(url_for("receivable_list"))
    scope_clause, scope_params = build_contract_visibility_clause("ct")
    row = fetchone(
        f"""
        SELECT r.*
        FROM receivables r
        JOIN contracts ct ON ct.id = r.contract_id
        JOIN customers c ON c.id = ct.customer_id
        WHERE r.id = ? AND {scope_clause}
        """,
        tuple([receivable_id] + scope_params),
    )
    if row is None:
        flash("回款计划不存在或无权限操作。", "danger")
        return redirect(url_for("receivable_list"))
    actual_date = request.form.get("actual_date", "").strip() or None
    actual_amount = parse_float_form_value(request.form.get("actual_amount"), 0.0) or 0.0
    note = request.form.get("note", "").strip() or row.get("note")
    plan_amount = float(row.get("plan_amount") or 0.0)
    if actual_amount < 0:
        flash("实际回款金额不能为负数。", "danger")
        cid = request.form.get("contract_id", type=int) or request.args.get("contract_id", type=int)
        return redirect(url_for("receivable_list", contract_id=cid) if cid else url_for("receivable_list"))
    if actual_amount > plan_amount:
        flash("实际回款金额不能超过计划回款金额。", "danger")
        cid = request.form.get("contract_id", type=int) or request.args.get("contract_id", type=int)
        return redirect(url_for("receivable_list", contract_id=cid) if cid else url_for("receivable_list"))
    if actual_amount >= plan_amount:
        status = "received"
    elif actual_amount > 0:
        status = "partial"
    else:
        status = row["status"]
    execute(
        """
        UPDATE receivables
        SET actual_date = ?, actual_amount = ?, status = ?, note = ?, updated_at = ?
        WHERE id = ?
        """,
        (actual_date, actual_amount, status, note, now_iso(), receivable_id),
    )
    get_db().commit()
    flash("回款记录已更新。", "success")
    cid = request.form.get("contract_id", type=int) or request.args.get("contract_id", type=int)
    return redirect(url_for("receivable_list", contract_id=cid) if cid else url_for("receivable_list"))


@app.route("/receivables/<int:receivable_id>/delete", methods=["POST"])
def delete_receivable(receivable_id: int):
    if not has_module_permission("receivable", "manage"):
        flash("无权限删除回款计划。", "danger")
        return redirect(url_for("receivable_list"))
    scope_clause, scope_params = build_contract_visibility_clause("ct")
    row = fetchone(
        f"""
        SELECT r.id
        FROM receivables r
        JOIN contracts ct ON ct.id = r.contract_id
        JOIN customers c ON c.id = ct.customer_id
        WHERE r.id = ? AND {scope_clause}
        """,
        tuple([receivable_id] + scope_params),
    )
    if row is None:
        flash("回款计划不存在或无权限操作。", "danger")
        return redirect(url_for("receivable_list"))
    execute("DELETE FROM receivables WHERE id = ?", (receivable_id,))
    get_db().commit()
    flash("回款计划已删除。", "success")
    cid = request.form.get("contract_id", type=int) or request.args.get("contract_id", type=int)
    return redirect(url_for("receivable_list", contract_id=cid) if cid else url_for("receivable_list"))


@app.route("/invoices", methods=["GET", "POST"])
def invoice_list():
    if not has_module_permission("invoice", "view") and not has_module_permission("receivable", "manage"):
        flash("无权限访问开票模块。", "danger")
        return redirect(url_for("dashboard"))
    contract_filter = request.args.get("contract_id", type=int)
    filter_q = request.args.get("q", "").strip()
    filter_invoice_type = request.args.get("invoice_type", "").strip()
    filter_status = request.args.get("status", "").strip()
    contract_scope_clause, contract_scope_params = build_contract_visibility_clause("ct")
    filter_contract = None
    if contract_filter:
        filter_contract = fetchone(
            f"SELECT id, contract_no FROM contracts ct WHERE ct.id = ? AND {contract_scope_clause}",
            tuple([contract_filter] + contract_scope_params),
        )

    if request.method == "POST":
        if not has_module_permission("invoice", "manage"):
            flash("无权限新增开票记录。", "danger")
            return redirect(url_for("invoice_list", contract_id=contract_filter) if contract_filter else url_for("invoice_list"))
        contract_id = parse_int_form_value(request.form.get("contract_id"), 0) or 0
        selected_contract = fetchone(
            f"SELECT id FROM contracts ct WHERE ct.id = ? AND {contract_scope_clause}",
            tuple([contract_id] + contract_scope_params),
        )
        if selected_contract is None:
            flash("所选合同不存在或无权限操作。", "danger")
            return redirect(url_for("invoice_list", contract_id=contract_filter) if contract_filter else url_for("invoice_list"))
        receivable_id_raw = request.form.get("receivable_id", "").strip()
        receivable_id = parse_int_form_value(receivable_id_raw)
        if receivable_id_raw and receivable_id is None:
            flash("回款计划信息无效。", "danger")
            redir_cid = contract_id or contract_filter
            return redirect(url_for("invoice_list", contract_id=redir_cid) if redir_cid else url_for("invoice_list"))
        if not _receivable_belongs_contract(receivable_id, contract_id):
            flash("所选回款计划不属于当前合同。", "danger")
            redir_cid = contract_id or contract_filter
            return redirect(url_for("invoice_list", contract_id=redir_cid) if redir_cid else url_for("invoice_list"))
        amount = parse_float_form_value(request.form.get("amount"), 0.0) or 0.0
        invoice_date = request.form.get("invoice_date", "").strip()
        invoice_type = request.form.get("invoice_type", "").strip()
        invoice_code = request.form.get("invoice_code", "").strip()
        status = request.form.get("status", "issued").strip()
        if contract_id <= 0 or amount <= 0 or not invoice_date or not invoice_type or not invoice_code:
            flash("合同、金额、开票日期、发票类型、发票号码为必填项。", "danger")
            redir_cid = contract_id or contract_filter
            return redirect(url_for("invoice_list", contract_id=redir_cid) if redir_cid else url_for("invoice_list"))
        try:
            execute(
                """
                INSERT INTO invoices(invoice_no, contract_id, receivable_id, amount, invoice_date, invoice_type, invoice_code, status, created_by, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    generate_invoice_no(),
                    contract_id,
                    receivable_id,
                    amount,
                    invoice_date,
                    invoice_type,
                    invoice_code,
                    status,
                    session.get("display_name") or session.get("username"),
                    now_iso(),
                ),
            )
            get_db().commit()
            flash("开票记录新增成功。", "success")
        except Exception:
            get_db().rollback()
            flash("开票记录新增失败：发票号码可能已存在。", "danger")
        redir_cid = contract_id or contract_filter
        return redirect(url_for("invoice_list", contract_id=redir_cid) if redir_cid else url_for("invoice_list"))

    invoice_filters: list[str] = []
    invoice_params: list[object] = []
    invoice_filters.append(contract_scope_clause)
    invoice_params.extend(contract_scope_params)
    if contract_filter:
        invoice_filters.append("i.contract_id = ?")
        invoice_params.append(contract_filter)
    if filter_q:
        like_q = f"%{filter_q}%"
        invoice_filters.append("(i.invoice_code LIKE ? OR ct.contract_no LIKE ? OR c.name LIKE ?)")
        invoice_params.extend([like_q, like_q, like_q])
    if filter_invoice_type:
        invoice_filters.append("i.invoice_type = ?")
        invoice_params.append(filter_invoice_type)
    if filter_status:
        invoice_filters.append("i.status = ?")
        invoice_params.append(filter_status)

    invoice_sql = """
        SELECT i.*, c.name AS customer_name, ct.contract_no
        FROM invoices i
        JOIN contracts ct ON ct.id = i.contract_id
        JOIN customers c ON c.id = ct.customer_id
    """
    if invoice_filters:
        invoice_sql += "\nWHERE " + " AND ".join(invoice_filters)
    invoice_sql += "\nORDER BY i.invoice_date DESC, i.id DESC"
    invoices = fetchall(invoice_sql, tuple(invoice_params))
    contracts = fetchall(
        """
        SELECT ct.id, ct.contract_no, c.name AS customer_name
        FROM contracts ct
        JOIN customers c ON c.id = ct.customer_id
        WHERE {contract_scope_clause}
        ORDER BY ct.updated_at DESC
        """.replace("{contract_scope_clause}", contract_scope_clause)
    )
    if contract_filter:
        receivables = fetchall(
            """
            SELECT r.id, r.contract_id, r.plan_date, r.plan_amount
            FROM receivables r
            JOIN contracts ct ON ct.id = r.contract_id
            JOIN customers c ON c.id = ct.customer_id
            WHERE r.contract_id = ?
            AND {contract_scope_clause}
            ORDER BY r.plan_date ASC, r.id ASC
            """.replace("{contract_scope_clause}", contract_scope_clause),
            tuple([contract_filter] + contract_scope_params),
        )
    else:
        receivables = fetchall(
            """
            SELECT r.id, r.contract_id, r.plan_date, r.plan_amount
            FROM receivables r
            JOIN contracts ct ON ct.id = r.contract_id
            JOIN customers c ON c.id = ct.customer_id
            WHERE {contract_scope_clause}
            ORDER BY r.updated_at DESC
            """.replace("{contract_scope_clause}", contract_scope_clause),
            tuple(contract_scope_params),
        )
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
    page, invoices, total_pages = _paginate_rows(invoices, page, per_page)
    return render_template(
        "invoice_list.html",
        invoices=invoices,
        contracts=contracts,
        receivables=receivables,
        filter_contract=filter_contract,
        filter_q=filter_q,
        filter_invoice_type=filter_invoice_type,
        filter_status=filter_status,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
    )


@app.route("/invoices/export")
def invoice_list_export():
    if not has_module_permission("invoice", "view") and not has_module_permission("receivable", "manage"):
        flash("无权限导出开票。", "danger")
        return redirect(url_for("dashboard"))
    contract_filter = request.args.get("contract_id", type=int)
    filter_q = request.args.get("q", "").strip()
    filter_invoice_type = request.args.get("invoice_type", "").strip()
    filter_status = request.args.get("status", "").strip()
    contract_scope_clause, contract_scope_params = build_contract_visibility_clause("ct")
    invoice_filters: list[str] = []
    invoice_params: list[object] = []
    invoice_filters.append(contract_scope_clause)
    invoice_params.extend(contract_scope_params)
    if contract_filter:
        invoice_filters.append("i.contract_id = ?")
        invoice_params.append(contract_filter)
    if filter_q:
        like_q = f"%{filter_q}%"
        invoice_filters.append("(i.invoice_code LIKE ? OR ct.contract_no LIKE ? OR c.name LIKE ?)")
        invoice_params.extend([like_q, like_q, like_q])
    if filter_invoice_type:
        invoice_filters.append("i.invoice_type = ?")
        invoice_params.append(filter_invoice_type)
    if filter_status:
        invoice_filters.append("i.status = ?")
        invoice_params.append(filter_status)
    invoice_sql = """
        SELECT i.*, c.name AS customer_name, ct.contract_no
        FROM invoices i
        JOIN contracts ct ON ct.id = i.contract_id
        JOIN customers c ON c.id = ct.customer_id
    """
    if invoice_filters:
        invoice_sql += "\nWHERE " + " AND ".join(invoice_filters)
    invoice_sql += "\nORDER BY i.invoice_date DESC, i.id DESC"
    rows = fetchall(invoice_sql, tuple(invoice_params))
    csv_rows = [
        [
            i.get("invoice_code") or "",
            i.get("contract_no") or "",
            i.get("customer_name") or "",
            i.get("amount") or "",
            i.get("invoice_date") or "",
            i.get("invoice_type") or "",
            i.get("status") or "",
        ]
        for i in rows
    ]
    return _csv_attachment("开票列表导出.csv", ["发票号码", "合同编号", "客户名称", "金额", "开票日期", "类型", "状态"], csv_rows)


@app.route("/invoices/<int:invoice_id>/delete", methods=["POST"])
def delete_invoice(invoice_id: int):
    if not has_module_permission("invoice", "manage"):
        flash("无权限删除开票记录。", "danger")
        return redirect(url_for("invoice_list"))
    scope_clause, scope_params = build_contract_visibility_clause("ct")
    row = fetchone(
        f"""
        SELECT i.id
        FROM invoices i
        JOIN contracts ct ON ct.id = i.contract_id
        JOIN customers c ON c.id = ct.customer_id
        WHERE i.id = ? AND {scope_clause}
        """,
        tuple([invoice_id] + scope_params),
    )
    if row is None:
        flash("开票记录不存在或无权限操作。", "danger")
        return redirect(url_for("invoice_list"))
    execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))
    get_db().commit()
    flash("开票记录已删除。", "success")
    cid = request.form.get("contract_id", type=int) or request.args.get("contract_id", type=int)
    return redirect(url_for("invoice_list", contract_id=cid) if cid else url_for("invoice_list"))
