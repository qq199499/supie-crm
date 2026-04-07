import csv
import secrets
from datetime import date, datetime, timedelta
from io import BytesIO, StringIO
from typing import Any

from flask import flash, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from ai_service import build_workbench_priorities
from crm_constants import OPPORTUNITY_CLOSE_SOON_DAYS_DEFAULT, OPPORTUNITY_STALL_DAYS_DEFAULT, ROLE_LABELS

from app import (
    DEFAULT_ADMIN_USER,
    _normalize_perm_keys,
    _sql_identity_in_clause,
    _replace_role_permissions,
    _role_is_active,
    admin_required,
    app,
    approval_visible_requested_values,
    current_user_identity_values,
    crm_summary,
    fetch_active_roles_by_ids,
    execute,
    execute_returning_id,
    fetch_customer_crm_todos_for_owner,
    fetch_project_attention_items,
    fetchall,
    fetchone,
    get_db,
    has_module_permission,
    get_user_role_codes,
    get_user_role_labels,
    get_user_role_rows,
    log_ai_generation,
    now_iso,
    permission_catalog_by_group,
    prepare_login_captcha,
    search_active_users,
    set_user_roles,
    session_is_system_admin,
    slug_role_code,
    uses_postgres,
)

DETAIL_PAGE_SIZES = (15, 50, 100)


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


@app.route("/login", methods=["GET", "POST"])
def login():
    # 若浏览器仍带着旧会话：先校验用户是否仍存在、数据库是否可用；否则重定向到首页会因查库失败而 500，
    # 用户会看到「登录页打不开」（实为会话重定向后的服务器错误）。
    if session.get("user_id"):
        try:
            uid = int(session["user_id"])
            row = fetchone("SELECT id FROM users WHERE id = ?", (uid,))
            if row:
                return redirect(url_for("dashboard"))
        except Exception:
            session.clear()
            flash("无法连接数据库或会话已失效，请检查数据库服务后重试。", "danger")
            return render_template("login.html", **prepare_login_captcha())
        session.clear()
    if request.method == "POST":
        user_code = (request.form.get("captcha_input") or "").strip().upper()
        expected = (session.get("captcha_code") or "").upper()
        if not expected or user_code != expected:
            flash("验证码错误，请重试。", "danger")
            return render_template("login.html", **prepare_login_captcha())
        session.pop("captcha_code", None)
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        user = fetchone("SELECT * FROM users WHERE username = ?", (username,))
        if not user:
            flash("账号或密码错误。", "danger")
            return render_template("login.html", **prepare_login_captcha())
        is_active = bool(user.get("is_active"))
        if not is_active:
            flash("账号已禁用，请联系管理员。", "danger")
            return render_template("login.html", **prepare_login_captcha())
        if not check_password_hash(user["password_hash"], password):
            flash("账号或密码错误。", "danger")
            return render_template("login.html", **prepare_login_captcha())
        # 确保默认管理员账号在库中角色为 admin（避免旧库或手工改库导致权限变成 normal）
        if user["username"] == DEFAULT_ADMIN_USER and (user.get("role") or "").strip() != "admin":
            execute(
                "UPDATE users SET role = 'admin', role_id = (SELECT id FROM roles WHERE code = 'admin' LIMIT 1), updated_at = ? WHERE id = ?",
                (now_iso(), user["id"]),
            )
            get_db().commit()
            user = fetchone("SELECT * FROM users WHERE id = ?", (user["id"],)) or user
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        session["display_name"] = user.get("display_name") or user["username"]
        role_rows = get_user_role_rows(int(user["id"]))
        if role_rows:
            primary_role = role_rows[0]
            session["role"] = str(primary_role.get("code") or user.get("role") or "normal").strip()
            session["role_id"] = int(primary_role.get("id")) if primary_role.get("id") is not None else None
            session["role_display_name"] = " / ".join(get_user_role_labels(int(user["id"])))
        else:
            session["role"] = (user.get("role") or "normal").strip()
            session["role_id"] = None
            session.pop("role_display_name", None)
        # 登录后启用永久会话，配合 app.py 里的 30 分钟寿命实现滑动过期。
        session.permanent = True
        session["last_activity_ts"] = datetime.utcnow().timestamp()
        next_url = request.args.get("next") or url_for("workbench")
        return redirect(next_url)

    return render_template("login.html", **prepare_login_captcha())


@app.route("/api/users/search")
def api_users_search():
    if not session.get("user_id"):
        return jsonify({"users": []}), 401
    q = request.args.get("q", "").strip()
    rows = search_active_users(q)
    return jsonify(
        {
            "users": [
                {"id": int(r["id"]), "username": r.get("username") or "", "display_name": r.get("display_name") or ""}
                for r in rows
            ]
        }
    )


@app.route("/logout")
def logout():
    session.clear()
    flash("已退出登录。", "success")
    return redirect(url_for("login"))


@app.route("/search")
def legacy_search_redirect():
    flash("智能搜索中心已下线，已为你跳转到当前首页。", "warning")
    if has_module_permission("workbench", "view"):
        return redirect(url_for("workbench"))
    if has_module_permission("dashboard", "view") or has_module_permission("all", "view"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("change_password"))


@app.route("/account/password", methods=["GET", "POST"])
def change_password():
    if request.method == "POST":
        old_password = request.form.get("old_password", "").strip()
        new_password = request.form.get("new_password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()
        user = fetchone("SELECT * FROM users WHERE id = ?", (session["user_id"],))
        if user is None:
            session.clear()
            flash("用户不存在，请重新登录。", "danger")
            return redirect(url_for("login"))
        if not check_password_hash(user["password_hash"], old_password):
            flash("原密码错误。", "danger")
            return render_template("account_password.html")
        if len(new_password) < 6:
            flash("新密码长度不能少于6位。", "danger")
            return render_template("account_password.html")
        if new_password != confirm_password:
            flash("两次输入的新密码不一致。", "danger")
            return render_template("account_password.html")
        execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (generate_password_hash(new_password), now_iso(), session["user_id"]),
        )
        get_db().commit()
        flash("密码修改成功。", "success")
        return redirect(url_for("dashboard"))
    return render_template("account_password.html")


@app.route("/users", methods=["GET", "POST"])
@admin_required
def user_list():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        display_name = request.form.get("display_name", "").strip()
        password = request.form.get("password", "").strip()
        role_ids = request.form.getlist("role_ids")
        roles = fetch_active_roles_by_ids(role_ids)
        if not roles:
            flash("请选择至少一个有效角色。", "danger")
            return redirect(url_for("user_list"))
        if not username or not display_name or len(password) < 6:
            flash("账号、姓名必填，且密码至少6位。", "danger")
            return redirect(url_for("user_list"))
        try:
            primary_role = roles[0]
            execute(
                """
                INSERT INTO users(username, password_hash, display_name, role, role_id, is_active, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    username,
                    generate_password_hash(password),
                    display_name,
                    str(primary_role["code"]).strip(),
                    int(primary_role["id"]),
                    True,
                    now_iso(),
                ),
            )
            user_row = fetchone("SELECT id FROM users WHERE username = ?", (username,))
            if not user_row:
                raise RuntimeError("创建用户后无法读取ID")
            user_id = int(user_row["id"])
            set_user_roles(user_id, [int(role["id"]) for role in roles])
            get_db().commit()
            flash("用户新增成功。", "success")
        except Exception:
            get_db().rollback()
            flash("用户新增失败：账号可能已存在。", "danger")
        return redirect(url_for("user_list"))

    try:
        page = max(1, int(request.args.get("page", "1") or 1))
    except ValueError:
        page = 1
    try:
        per_page = int(request.args.get("per_page", "15") or 15)
    except ValueError:
        per_page = 15
    if per_page not in DETAIL_PAGE_SIZES:
        per_page = 15
    total_row = fetchone("SELECT COUNT(1) AS c FROM users")
    total = int(total_row["c"]) if total_row else 0
    total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
    if page > total_pages:
        page = total_pages
    offset = (page - 1) * per_page
    users = fetchall(
        "SELECT * FROM users ORDER BY id ASC LIMIT ? OFFSET ?"
        if not uses_postgres()
        else "SELECT * FROM users ORDER BY id ASC LIMIT %s OFFSET %s",
        (per_page, offset),
    )
    roles = fetchall(
        "SELECT id, code, name, sort_order, is_active FROM roles WHERE COALESCE(is_active, TRUE) AND code != 'normal' ORDER BY sort_order, name"
        if uses_postgres()
        else "SELECT id, code, name, sort_order, is_active FROM roles WHERE COALESCE(is_active, 1) = 1 AND code != 'normal' ORDER BY sort_order, name"
    )
    role_rows_by_user: dict[int, list[dict[str, Any]]] = {}
    user_ids = [int(user["id"]) for user in users]
    if user_ids:
        placeholders = ", ".join("?" for _ in user_ids)
        role_rows = fetchall(
            f"""
            SELECT ur.user_id, r.id AS id, r.code, r.name, r.sort_order, r.is_active
            FROM user_roles ur
            JOIN roles r ON r.id = ur.role_id
            WHERE ur.user_id IN ({placeholders})
            ORDER BY r.sort_order, r.name, r.id
            """,
            tuple(user_ids),
        )
        for row in role_rows:
            if not _role_is_active(row):
                continue
            role_rows_by_user.setdefault(int(row["user_id"]), []).append(row)
    role_names_by_code = {str(r["code"]): str(r["name"]) for r in roles if r.get("code")}
    for user in users:
        assigned = role_rows_by_user.get(int(user["id"])) or get_user_role_rows(int(user["id"]))
        user["assigned_roles"] = assigned
        user["assigned_role_ids"] = [int(r["id"]) for r in assigned if r.get("id") is not None]
        user["assigned_role_labels"] = [str(r.get("name") or r.get("code") or "").strip() for r in assigned if str(r.get("name") or r.get("code") or "").strip()]
        user["assigned_role_label_text"] = " / ".join(user["assigned_role_labels"]) if user["assigned_role_labels"] else role_names_by_code.get(str(user.get("role") or ""), ROLE_LABELS.get(str(user.get("role") or ""), str(user.get("role") or "系统管理员")))
        user["assigned_role_count"] = len(user["assigned_role_ids"])
    return render_template("user_list.html", users=users, roles=roles, page=page, per_page=per_page, total_pages=total_pages, total=total)


@app.route("/users/export")
@admin_required
def user_list_export():
    users = fetchall("SELECT * FROM users ORDER BY id ASC")
    roles = fetchall(
        "SELECT id, code, name, sort_order, is_active FROM roles WHERE COALESCE(is_active, TRUE) AND code != 'normal' ORDER BY sort_order, name"
        if uses_postgres()
        else "SELECT id, code, name, sort_order, is_active FROM roles WHERE COALESCE(is_active, 1) = 1 AND code != 'normal' ORDER BY sort_order, name"
    )
    role_rows_by_user: dict[int, list[dict[str, Any]]] = {}
    for row in fetchall(
        """
        SELECT ur.user_id, r.id AS role_id, r.code, r.name, r.sort_order, r.is_active
        FROM user_roles ur
        JOIN roles r ON r.id = ur.role_id
        ORDER BY r.sort_order, r.name, r.id
        """
    ):
        if not _role_is_active(row):
            continue
        role_rows_by_user.setdefault(int(row["user_id"]), []).append(row)
    role_names_by_code = {str(r["code"]): str(r["name"]) for r in roles if r.get("code")}
    csv_rows = []
    for user in users:
        assigned = role_rows_by_user.get(int(user["id"])) or get_user_role_rows(int(user["id"]))
        labels = [str(r.get("name") or r.get("code") or "").strip() for r in assigned if str(r.get("name") or r.get("code") or "").strip()]
        csv_rows.append([
            user.get("username") or "",
            user.get("display_name") or "",
            " / ".join(labels) if labels else role_names_by_code.get(str(user.get("role") or ""), ROLE_LABELS.get(str(user.get("role") or ""), str(user.get("role") or "系统管理员"))),
            "启用" if user.get("is_active") else "禁用",
        ])
    return _csv_attachment("用户列表导出.csv", ["账号", "姓名", "角色", "状态"], csv_rows)


@app.route("/users/<int:user_id>/toggle-active", methods=["POST"])
@admin_required
def toggle_user_active(user_id: int):
    user = fetchone("SELECT * FROM users WHERE id = ?", (user_id,))
    if user is None:
        flash("用户不存在。", "danger")
        return redirect(url_for("user_list"))
    if user["username"] == DEFAULT_ADMIN_USER and user.get("is_active"):
        flash("默认管理员不可被禁用。", "danger")
        return redirect(url_for("user_list"))
    new_active = not bool(user.get("is_active"))
    execute("UPDATE users SET is_active = ?, updated_at = ? WHERE id = ?", (new_active, now_iso(), user_id))
    get_db().commit()
    flash("用户状态已更新。", "success")
    return redirect(url_for("user_list"))


@app.route("/users/<int:user_id>/reset-password", methods=["POST"])
@admin_required
def reset_user_password(user_id: int):
    user = fetchone("SELECT * FROM users WHERE id = ?", (user_id,))
    if user is None:
        flash("用户不存在。", "danger")
        return redirect(url_for("user_list"))
    new_password = request.form.get("new_password", "").strip()
    if len(new_password) < 6:
        flash("重置密码至少6位。", "danger")
        return redirect(url_for("user_list"))
    execute(
        "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
        (generate_password_hash(new_password), now_iso(), user_id),
    )
    get_db().commit()
    flash("密码重置成功。", "success")
    return redirect(url_for("user_list"))


@app.route("/users/<int:user_id>/role", methods=["POST"])
@admin_required
def change_user_role(user_id: int):
    user = fetchone("SELECT * FROM users WHERE id = ?", (user_id,))
    if user is None:
        flash("用户不存在。", "danger")
        return redirect(url_for("user_list"))
    if user["username"] == DEFAULT_ADMIN_USER:
        flash("默认管理员的角色不可修改。", "danger")
        return redirect(url_for("user_list"))
    roles = fetch_active_roles_by_ids(request.form.getlist("role_ids"))
    if not roles:
        flash("请至少保留一个有效角色。", "danger")
        return redirect(url_for("user_list"))
    set_user_roles(user_id, [int(r["id"]) for r in roles])
    get_db().commit()
    flash("用户角色已更新为多角色配置。", "success")
    return redirect(url_for("user_list"))


@app.route("/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id: int):
    user = fetchone("SELECT * FROM users WHERE id = ?", (user_id,))
    if user is None:
        flash("用户不存在。", "danger")
        return redirect(url_for("user_list"))
    if user["username"] == DEFAULT_ADMIN_USER:
        flash("默认管理员账号不可删除。", "danger")
        return redirect(url_for("user_list"))
    if user_id == session.get("user_id"):
        flash("不能删除当前登录账号。", "danger")
        return redirect(url_for("user_list"))
    execute("UPDATE project_progress_entries SET created_by = NULL WHERE created_by = ?", (user_id,))
    execute("DELETE FROM users WHERE id = ?", (user_id,))
    get_db().commit()
    flash("用户已删除。", "success")
    return redirect(url_for("user_list"))


@app.route("/roles")
@admin_required
def role_list():
    roles = fetchall(
        """
        SELECT r.*, (SELECT COUNT(1) FROM user_roles ur WHERE ur.role_id = r.id) AS user_count
        FROM roles r
        WHERE r.code != 'normal'
        ORDER BY r.sort_order, r.name
        """
    )
    try:
        page = max(1, int(request.args.get("page", "1") or 1))
    except ValueError:
        page = 1
    try:
        per_page = int(request.args.get("per_page", "15") or 15)
    except ValueError:
        per_page = 15
    if per_page not in DETAIL_PAGE_SIZES:
        per_page = 15
    page, roles, total_pages = _paginate_rows(roles, page, per_page)
    return render_template("role_list.html", roles=roles, page=page, per_page=per_page, total_pages=total_pages)


@app.route("/roles/export")
@admin_required
def role_list_export():
    roles = fetchall(
        """
        SELECT r.*, (SELECT COUNT(1) FROM user_roles ur WHERE ur.role_id = r.id) AS user_count
        FROM roles r
        WHERE r.code != 'normal'
        ORDER BY r.sort_order, r.name
        """
    )
    csv_rows = [
        [
            r.get("name") or "",
            r.get("code") or "",
            r.get("user_count") or 0,
            "系统内置" if r.get("is_system") else "自定义",
        ]
        for r in roles
    ]
    return _csv_attachment("角色列表导出.csv", ["角色名称", "角色编码", "用户数量", "类型"], csv_rows)


@app.route("/roles/new", methods=["GET", "POST"])
@admin_required
def role_new():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("角色名称为必填项。", "danger")
            return redirect(url_for("role_new"))
        code = slug_role_code(name)
        while fetchone("SELECT id FROM roles WHERE code = ?", (code,)):
            code = f"{code}_{secrets.token_hex(3)}"
        row_m = fetchone("SELECT COALESCE(MAX(sort_order), 0) AS m FROM roles")
        next_sort = int(row_m["m"]) + 1 if row_m else 1
        perms = _normalize_perm_keys(request.form.getlist("perms"))
        if not perms:
            flash("请至少勾选一个权限，或选择「全部权限」。", "danger")
            return redirect(url_for("role_new"))
        rid = execute_returning_id(
            """
            INSERT INTO roles(code, name, sort_order, is_system, is_active, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (code, name, next_sort, False, True, now_iso()),
        )
        _replace_role_permissions(rid, perms)
        get_db().commit()
        flash("角色已创建。", "success")
        return redirect(url_for("role_list"))
    return render_template("role_edit.html", role=None, selected=set(), groups=permission_catalog_by_group())


@app.route("/roles/<int:role_id>/edit", methods=["GET", "POST"])
@admin_required
def role_edit(role_id: int):
    role = fetchone("SELECT * FROM roles WHERE id = ?", (role_id,))
    if role is None:
        flash("角色不存在。", "danger")
        return redirect(url_for("role_list"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("角色名称为必填项。", "danger")
            return redirect(url_for("role_edit", role_id=role_id))
        execute(
            "UPDATE roles SET name = ?, updated_at = ? WHERE id = ?",
            (name, now_iso(), role_id),
        )
        perms = _normalize_perm_keys(request.form.getlist("perms"))
        if not perms:
            flash("请至少保留一项权限。", "danger")
            return redirect(url_for("role_edit", role_id=role_id))
        _replace_role_permissions(role_id, perms)
        get_db().commit()
        flash("角色已保存。", "success")
        return redirect(url_for("role_list"))
    rows = fetchall("SELECT perm_key FROM role_permissions WHERE role_id = ?", (role_id,))
    selected = {str(x["perm_key"]) for x in rows}
    return render_template("role_edit.html", role=role, selected=selected, groups=permission_catalog_by_group())


@app.route("/roles/<int:role_id>/delete", methods=["POST"])
@admin_required
def role_delete(role_id: int):
    role = fetchone("SELECT * FROM roles WHERE id = ?", (role_id,))
    if role is None:
        flash("角色不存在。", "danger")
        return redirect(url_for("role_list"))
    if role.get("is_system") in (True, 1):
        flash("系统内置角色不可删除。", "danger")
        return redirect(url_for("role_list"))
    n = fetchone("SELECT COUNT(1) AS c FROM user_roles WHERE role_id = ?", (role_id,))
    if n and int(n["c"]) > 0:
        flash("仍有用户绑定该角色，无法删除。", "danger")
        return redirect(url_for("role_list"))
    execute("DELETE FROM roles WHERE id = ?", (role_id,))
    get_db().commit()
    flash("角色已删除。", "success")
    return redirect(url_for("role_list"))


def _attach_opportunity_todo_link(row: dict[str, Any]) -> dict[str, Any]:
    """商机待办行补充详情链接（TD-O01 / TD-O02 / 跟进）。"""
    r = dict(row)
    oid = r.pop("opportunity_id", None)
    if oid is not None:
        r["item_link"] = url_for("opportunity_detail", opportunity_id=int(oid))
    return r


def _merge_workbench_todos_with_customer(base_rows: list, display_name: str) -> list:
    """在「我的待办」中合并客户 TD-C01～C03（与 customers.owner = display_name 对齐）。"""
    merged: list[dict[str, Any]] = []
    if has_module_permission("customer", "view"):
        seen_customers: set[tuple[str, int]] = set()
        for owner_name in [session.get("display_name"), session.get("username"), display_name]:
            owner = (owner_name or "").strip()
            if not owner:
                continue
            for t in fetch_customer_crm_todos_for_owner(owner):
                key = (str(t["todo_code"]), int(t["customer_id"]))
                if key in seen_customers:
                    continue
                seen_customers.add(key)
                merged.append(
                    {
                        "item_type": t["item_type"],
                        "item_name": t["item_name"],
                        "due_at": t.get("due_at") or "-",
                        "item_link": url_for("customer_detail", customer_id=t["customer_id"]),
                    }
                )
    for row in base_rows:
        r = dict(row)
        if "item_link" not in r:
            r["item_link"] = None
        merged.append(r)
    return merged[:24]


def _build_personal_workbench_context() -> dict[str, Any]:
    cards: list[dict[str, Any]] = []
    todo_items: list[dict[str, Any]] = []
    role_codes = set(get_user_role_codes(int(session["user_id"]))) if session.get("user_id") else {str(session.get("role", "normal")).strip()}
    if not role_codes:
        role_codes = {str(session.get("role", "normal")).strip() or "normal"}
    display_name = (session.get("display_name") or session.get("username") or "").strip()
    username = (session.get("username") or "").strip()
    user_id = int(session["user_id"]) if session.get("user_id") else None
    raw_identity_values = [value for value in {display_name, username} if value]
    identity_values = set(current_user_identity_values())

    def append_cards(items: list[dict[str, Any]]) -> None:
        existing = {str(card.get("title") or "") for card in cards}
        for item in items:
            title = str(item.get("title") or "")
            if title and title not in existing:
                cards.append(item)
                existing.add(title)

    def append_todos(items: list[dict[str, Any]]) -> None:
        existing = {
            (
                str(item.get("item_type") or ""),
                str(item.get("item_name") or ""),
                str(item.get("due_at") or ""),
                str(item.get("item_link") or ""),
            )
            for item in todo_items
        }
        for item in items:
            key = (
                str(item.get("item_type") or ""),
                str(item.get("item_name") or ""),
                str(item.get("due_at") or ""),
                str(item.get("item_link") or ""),
            )
            if key not in existing:
                todo_items.append(item)
                existing.add(key)
    def build_in_clause(column_sql: str, values: list[object]) -> tuple[str, list[object]]:
        cleaned = [str(v).strip().lower() for v in values if str(v).strip()]
        if not cleaned:
            return "0=1", []
        placeholders = ", ".join("?" for _ in cleaned)
        return f"LOWER(TRIM(COALESCE({column_sql}, ''))) IN ({placeholders})", cleaned

    def build_related_project_clause(alias: str = "p") -> tuple[str, list[object]]:
        clauses: list[str] = []
        params: list[object] = []
        if identity_values:
            clause, clause_params = _sql_identity_in_clause(f"{alias}.manager", identity_values)
            if clause != "0=1":
                clauses.append(clause)
                params.extend(clause_params)
        if user_id is not None:
            clauses.append(f"EXISTS (SELECT 1 FROM project_members pm WHERE pm.project_id = {alias}.id AND pm.user_id = ?)")
            params.append(user_id)
        if not clauses:
            return "0=1", []
        return "(" + " OR ".join(clauses) + ")", params

    def build_id_clause(column_sql: str, ids: list[int]) -> tuple[str, list[object]]:
        cleaned_ids = [int(pid) for pid in ids]
        if not cleaned_ids:
            return "0=1", []
        placeholders = ", ".join("?" for _ in cleaned_ids)
        return f"{column_sql} IN ({placeholders})", cleaned_ids

    opportunity_owner_clause, opportunity_owner_params = build_in_clause("o.owner", raw_identity_values)
    contract_owner_clause, contract_owner_params = build_in_clause("ct.owner", raw_identity_values)
    invoice_creator_clause, invoice_creator_params = build_in_clause("i.created_by", raw_identity_values)
    related_project_clause, related_project_params = build_related_project_clause("p")

    related_project_rows = fetchall(
        """
        SELECT DISTINCT p.id
        FROM projects p
        LEFT JOIN project_members pm ON pm.project_id = p.id
        WHERE p.deleted_at IS NULL
          AND {related_project_clause}
        """.format(related_project_clause=related_project_clause),
        tuple(related_project_params),
    )
    related_project_ids = [int(row["id"]) for row in related_project_rows]
    related_project_id_clause, related_project_id_params = build_id_clause("p.id", related_project_ids)

    tracking = fetchone(
        """
        SELECT COUNT(1) AS c
        FROM opportunities o
        WHERE {opportunity_owner_clause}
          AND COALESCE(o.stage, '') NOT IN ('won', 'lost')
        """.format(opportunity_owner_clause=opportunity_owner_clause),
        tuple(opportunity_owner_params),
    )
    draft_contract = fetchone(
        """
        SELECT COUNT(1) AS c
        FROM contracts ct
        WHERE {contract_owner_clause}
          AND ct.status = 'draft'
        """.format(contract_owner_clause=contract_owner_clause),
        tuple(contract_owner_params),
    )
    append_cards([
        {"title": "跟进中商机", "value": int(tracking["c"]) if tracking else 0, "link": url_for("opportunity_list")},
        {"title": "待签约合同", "value": int(draft_contract["c"]) if draft_contract else 0, "link": url_for("contract_list")},
    ])

    stall_days = OPPORTUNITY_STALL_DAYS_DEFAULT
    soon_days = OPPORTUNITY_CLOSE_SOON_DAYS_DEFAULT
    cutoff_s = (datetime.now() - timedelta(days=stall_days)).strftime("%Y-%m-%d %H:%M:%S")
    today_s = date.today().isoformat()
    soon_end = (date.today() + timedelta(days=soon_days)).isoformat()
    sales_base: list[dict[str, Any]] = []
    for owner_name in raw_identity_values:
        sales_base.extend(
            _attach_opportunity_todo_link(r)
            for r in fetchall(
                """
                SELECT o.id AS opportunity_id,
                       o.title || ' [阶段停滞]' AS item_name,
                       o.stage_started_at AS due_at,
                       'TD-O01 停滞' AS item_type
                FROM opportunities o
                WHERE o.owner = ?
                  AND COALESCE(o.stage, '') NOT IN ('won', 'lost')
                  AND o.stage_started_at IS NOT NULL
                  AND o.stage_started_at < ?
                ORDER BY o.stage_started_at ASC
                LIMIT 6
                """,
                (owner_name, cutoff_s),
            )
        )
        sales_base.extend(
            _attach_opportunity_todo_link(r)
            for r in fetchall(
                """
                SELECT o.id AS opportunity_id,
                       o.title || ' [关单临近]' AS item_name,
                       o.expected_sign_date AS due_at,
                       'TD-O02 临近' AS item_type
                FROM opportunities o
                WHERE o.owner = ?
                  AND COALESCE(o.stage, '') NOT IN ('won', 'lost')
                  AND o.expected_sign_date IS NOT NULL
                  AND o.expected_sign_date <= ?
                  AND o.expected_sign_date >= ?
                ORDER BY o.expected_sign_date ASC
                LIMIT 6
                """,
                (owner_name, soon_end, today_s),
            )
        )
    sales_base.extend(
        _attach_opportunity_todo_link(r)
        for r in fetchall(
            """
            SELECT o.id AS opportunity_id,
                   o.title AS item_name,
                   o.expected_sign_date AS due_at,
                   '商机跟进' AS item_type
            FROM opportunities o
            WHERE {opportunity_owner_clause}
              AND COALESCE(o.stage, '') NOT IN ('won', 'lost')
            ORDER BY o.updated_at DESC
            LIMIT 8
            """.format(opportunity_owner_clause=opportunity_owner_clause),
            tuple(opportunity_owner_params),
        )
    )
    append_todos(_merge_workbench_todos_with_customer(sales_base, display_name))

    today_iso = date.today().isoformat()
    overdue = fetchone(
        """
        SELECT COUNT(1) AS c
        FROM receivables r
        JOIN contracts ct ON ct.id = r.contract_id
        WHERE {contract_owner_clause}
          AND r.plan_date <= ?
          AND COALESCE(r.actual_amount, 0) < r.plan_amount
        """.format(contract_owner_clause=contract_owner_clause),
        tuple(contract_owner_params) + (today_iso,),
    )
    invoice_count = fetchone(
        """
        SELECT COUNT(1) AS c
        FROM invoices i
        WHERE {invoice_creator_clause}
        """.format(invoice_creator_clause=invoice_creator_clause),
        tuple(invoice_creator_params),
    )
    append_cards([
        {"title": "逾期应收", "value": int(overdue["c"]) if overdue else 0, "link": url_for("receivable_list")},
        {"title": "开票记录", "value": int(invoice_count["c"]) if invoice_count else 0, "link": url_for("invoice_list")},
    ])
    finance_rows = fetchall(
        """
        SELECT r.id AS receivable_id,
               ct.id AS contract_id,
               ct.contract_no AS item_name,
               r.plan_date AS due_at,
               'TD-K01 回款' AS item_type
        FROM receivables r
        JOIN contracts ct ON ct.id = r.contract_id
        WHERE {contract_owner_clause}
          AND r.plan_date <= ?
          AND COALESCE(r.actual_amount, 0) < r.plan_amount
        ORDER BY r.plan_date ASC, r.id ASC
        LIMIT 8
        """.format(contract_owner_clause=contract_owner_clause),
        tuple(contract_owner_params) + (today_iso,),
    )
    if finance_rows:
        append_todos(
            _merge_workbench_todos_with_customer(
                [
                    {
                        **dict(row),
                        "item_link": url_for("contract_detail", contract_id=int(row["contract_id"])),
                    }
                    for row in finance_rows
                ],
                display_name,
            )
        )

    visible_requested_values = approval_visible_requested_values(role_codes)
    approval_filters: list[str] = ["a.status = 'pending'"]
    approval_params: list[object] = []
    if visible_requested_values is not None:
        if not visible_requested_values:
            approval_filters.append("1 = 0")
        else:
            visible_values = sorted(visible_requested_values)
            approval_filters.append(f"a.requested_value IN ({', '.join('?' for _ in visible_values)})")
            approval_params.extend(visible_values)
    pending = fetchone(
        """
        SELECT COUNT(1) AS c
        FROM approvals a
        WHERE {filters}
        """.format(filters=" AND ".join(approval_filters)),
        tuple(approval_params),
    )
    delayed = fetchone(
        """
        SELECT COUNT(DISTINCT p.id) AS c
        FROM projects p
        LEFT JOIN project_members pm ON pm.project_id = p.id
        WHERE p.deleted_at IS NULL
          AND p.status = 'blocked'
          AND {related_project_clause}
        """.format(related_project_clause=related_project_clause),
        tuple(related_project_params),
    )
    append_cards([
        {"title": "待审批", "value": int(pending["c"]) if pending else 0, "link": url_for("approval_list")},
        {"title": "阻塞项目", "value": int(delayed["c"]) if delayed else 0, "link": url_for("dashboard")},
    ])
    approval_rows: list[dict[str, Any]] = []
    if visible_requested_values is None:
        approval_rows = fetchall(
            """
            SELECT a.id AS approval_id,
                   a.title AS item_name,
                   a.updated_at AS due_at,
                   '审批事项' AS item_type
            FROM approvals a
            WHERE a.status = 'pending'
            ORDER BY a.updated_at DESC
            LIMIT 8
            """
        )
    elif visible_requested_values:
        visible_values = sorted(visible_requested_values)
        approval_rows = fetchall(
            """
            SELECT a.id AS approval_id,
                   a.title AS item_name,
                   a.updated_at AS due_at,
                   '审批事项' AS item_type
            FROM approvals a
            WHERE a.status = 'pending'
              AND a.requested_value IN ({placeholders})
            ORDER BY a.updated_at DESC
            LIMIT 8
            """.format(placeholders=", ".join("?" for _ in visible_values)),
            tuple(visible_values),
        )
    if approval_rows:
        append_todos(
            _merge_workbench_todos_with_customer(
                [
                    {
                        **dict(row),
                        "item_link": url_for("approval_detail", approval_id=int(row["approval_id"])),
                    }
                    for row in approval_rows
                ],
                display_name,
            )
        )

    in_progress = fetchone(
        """
        SELECT COUNT(1) AS c
        FROM projects p
        WHERE p.deleted_at IS NULL
          AND p.status = 'in_progress'
          AND {related_project_id_clause}
        """.format(related_project_id_clause=related_project_id_clause),
        tuple(related_project_id_params),
    )
    issue_count = fetchone(
        """
        SELECT COUNT(1) AS c
        FROM risks r
        JOIN projects p ON p.id = r.project_id
        WHERE p.deleted_at IS NULL
          AND r.status != 'closed'
          AND {related_project_id_clause}
        """.format(related_project_id_clause=related_project_id_clause),
        tuple(related_project_id_params),
    )
    append_cards([
        {"title": "进行中项目", "value": int(in_progress["c"]) if in_progress else 0, "link": url_for("dashboard")},
        {"title": "未闭环风险", "value": int(issue_count["c"]) if issue_count else 0, "link": url_for("dashboard")},
    ])
    if related_project_ids:
        append_todos(
            _merge_workbench_todos_with_customer(
                fetchall(
                    """
                    SELECT t.title AS item_name,
                           t.updated_at AS due_at,
                           '任务跟进' AS item_type
                    FROM tasks t
                    JOIN projects p ON p.id = t.project_id
                    WHERE p.deleted_at IS NULL
                      AND t.status IN ('todo', 'doing', 'blocked')
                      AND {related_project_id_clause}
                    ORDER BY t.updated_at DESC
                    LIMIT 8
                    """.format(related_project_id_clause=related_project_id_clause),
                    tuple(related_project_id_params),
                ),
                display_name,
            )
        )

    dynamics = fetchall(
        """
        SELECT l.note AS content, l.changed_at AS created_at, '阶段动态' AS source
        FROM project_stage_logs l
        JOIN projects p ON p.id = l.project_id
        WHERE p.deleted_at IS NULL
          AND {related_project_id_clause}
        ORDER BY l.changed_at DESC
        LIMIT 3
        """.format(related_project_id_clause=related_project_id_clause),
        tuple(related_project_id_params),
    )
    attention_items: list[dict[str, Any]] = []
    if related_project_ids:
        attention_items = fetch_project_attention_items(project_ids=related_project_ids)
    return {
        "cards": cards,
        "todo_items": todo_items,
        "dynamics": dynamics,
        "attention_items": attention_items,
    }


def _build_workbench_context() -> dict[str, Any]:
    return _build_personal_workbench_context()


@app.route("/workbench")
def workbench():
    if not has_module_permission("workbench", "view"):
        flash("无权限访问工作台。", "danger")
        return redirect(url_for("dashboard"))
    return render_template("workbench.html", **_build_workbench_context())


@app.route("/workbench/ai/priorities")
def workbench_ai_priorities():
    if not has_module_permission("workbench", "view"):
        return jsonify({"error": "无权限访问工作台 AI 能力。"}), 403
    payload = _build_workbench_context()
    role_codes = get_user_role_codes(int(session["user_id"])) if session.get("user_id") else []
    role_hint = " / ".join(role_codes) if role_codes else str(session.get("role", "normal"))
    result = build_workbench_priorities(
        role_hint,
        payload.get("todo_items") or [],
        payload.get("attention_items") or [],
        payload.get("cards") or [],
    )
    generation_id = log_ai_generation(
        "workbench_priorities",
        "workbench",
        None,
        {
            "role": role_hint,
            "todo_count": len(payload.get("todo_items") or []),
            "attention_count": len(payload.get("attention_items") or []),
            "card_count": len(payload.get("cards") or []),
        },
        result,
    )
    get_db().commit()
    priorities = result.get("priorities") or []
    return jsonify({**result, "items": priorities, "generation_id": generation_id, "generated_at": now_iso()})


@app.route("/")
def dashboard():
    if not has_module_permission("dashboard", "view") and not has_module_permission("all", "view"):
        flash("无权限查看项目总览。", "danger")
        return redirect(url_for("workbench"))
    summary_row = fetchone(
        """
        SELECT
            COUNT(1) AS project_count,
            SUM(CASE WHEN status = 'not_started' THEN 1 ELSE 0 END) AS not_started_count,
            SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) AS in_progress_count,
            SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) AS closed_count,
            SUM(CASE WHEN status = 'blocked' THEN 1 ELSE 0 END) AS blocked_count
        FROM projects
        WHERE deleted_at IS NULL
        """
    )
    summary = {
        "project_count": int(summary_row["project_count"]) if summary_row else 0,
        "not_started_count": int(summary_row["not_started_count"]) if summary_row and summary_row["not_started_count"] is not None else 0,
        "in_progress_count": int(summary_row["in_progress_count"]) if summary_row and summary_row["in_progress_count"] is not None else 0,
        "closed_count": int(summary_row["closed_count"]) if summary_row and summary_row["closed_count"] is not None else 0,
        "blocked_count": int(summary_row["blocked_count"]) if summary_row and summary_row["blocked_count"] is not None else 0,
    }
    return render_template(
        "dashboard.html",
        summary=summary,
        crm_summary=crm_summary(),
    )
