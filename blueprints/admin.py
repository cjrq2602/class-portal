"""
blueprints/admin.py —— 管理后台蓝图（/admin + /api/admin/*）
=============================================================
权限模型：
    进入后台需 admin.access；每个接口再按具体权限 code 校验（见 require_perm）。
    菜单由前端按 /api/auth/me 返回的 perms 动态渲染（无权限的模块不显示）。
审计：管理员一切写操作写入 audit_logs（action/target/detail/ip）。
模块：数据总览 / 用户管理 / 邀请码 / 公告 / 审计查询 / 游戏房间清理。
"""

from __future__ import annotations

import re
import secrets
import string
import time
from functools import wraps
from typing import Any, Callable, Optional

from flask import Blueprint, redirect, render_template, request

import common
import db
import portal_db
from blueprints import auth as auth_bp
from game import match

bp: Blueprint = Blueprint("admin", __name__)


@bp.before_request
def _admin_rate_limit():
    """管理端接口同样限速：每用户 20 req/s（防脚本高频扫后台）。"""
    if not request.path.startswith("/api/admin"):
        return None
    uid = auth_bp.current_user_id()
    key: str = f"admin:{uid if uid else common.client_ip()}"
    if not common.rate_check_window(key, limit=20, window_seconds=1.0):
        return common.err("请求太频繁，请稍后再试", "rate", 429)
    return None


# ============================================================
# 权限装饰器
# ============================================================
def require_perm(code: str) -> Callable:
    """接口级权限校验：未登录 401；无权限 403。admin.full（超管）放行一切。"""
    def deco(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            uid = auth_bp.current_user_id()
            if uid is None:
                return common.err("请先登录", "auth", 401)
            user = portal_db.get_user(uid)
            if user is None or bool(user["is_banned"]):
                return common.err("账号不可用", "auth", 401)
            perms: list[str] = portal_db.get_user_perms(uid)
            if portal_db.ADMIN_FULL_PERM in perms or code in perms:
                return fn(*args, **kwargs)
            return common.err("没有该操作权限", "forbidden", 403)
        return wrapper
    return deco


def _is_root_or_full(uid: int) -> bool:
    """root 角色 或 拥有 admin.full（登录日志查询的准入条件）。"""
    user = portal_db.get_user(uid)
    if user is None:
        return False
    if str(user["role_name"]) == "root":
        return True
    return portal_db.ADMIN_FULL_PERM in portal_db.get_user_perms(uid)


def _audit(action: str, target: str = "", detail: str = "") -> None:
    """管理员写操作留痕（user_id 取当前会话）。"""
    portal_db.add_audit(auth_bp.current_user_id(), action, target, detail,
                        common.client_ip())


def _gen_invite_code(length: int = 8) -> str:
    """生成邀请码（大写字母数字，去易混淆字符，保证唯一）。"""
    alphabet: str = "".join(c for c in string.ascii_uppercase + string.digits
                            if c not in "O0I1")
    while True:
        code: str = "".join(secrets.choice(alphabet) for _ in range(length))
        if portal_db.get_invite_by_code(code) is None:
            return code


# ============================================================
# 页面
# ============================================================
@bp.route("/admin")
def admin_page():
    """后台单页外壳：无 admin.access 重定向登录/首页。超管（admin.full）直接放行。"""
    uid = auth_bp.current_user_id()
    if uid is None:
        return redirect("/login")
    perms: list[str] = portal_db.get_user_perms(uid)
    if "admin.access" not in perms and portal_db.ADMIN_FULL_PERM not in perms:
        return render_template("admin.html", no_access=True)
    return render_template("admin.html", no_access=False)


# ============================================================
# 数据总览
# ============================================================
@bp.route("/api/admin/overview")
@require_perm("admin.access")
@common.api_wrap
def api_overview():
    """
    总览数据。返回：{ok, user_count, active_rooms, audit_count, invite_count}
    权限：admin.access
    """
    with db.get_db() as conn:
        active_rooms: int = int(conn.execute(
            "SELECT COUNT(*) FROM rooms WHERE status IN ('waiting','playing')"
        ).fetchone()[0])
        invite_count: int = int(conn.execute(
            "SELECT COUNT(*) FROM invites WHERE revoked_at IS NULL AND expires_at > ?",
            (time.time(),),
        ).fetchone()[0])
    return common.ok({
        "user_count": portal_db.count_users(),
        "active_rooms": active_rooms,
        "audit_count": portal_db.count_audit(),
        "invite_count": invite_count,
    })


# ============================================================
# 用户管理
# ============================================================
@bp.route("/api/admin/users")
@require_perm("user.manage")
@common.api_wrap
def api_users():
    """用户列表（最新 200）。权限：user.manage"""
    rows = portal_db.list_users(limit=200)
    return common.ok({"list": [
        {"id": int(r["id"]), "username": r["username"], "nickname": r["nickname"],
         "role_name": r["role_name"], "role_display": r["role_display"],
         "role_id": int(r["role_id"]), "is_banned": bool(r["is_banned"]),
         "created_at": float(r["created_at"])}
        for r in rows
    ]})


@bp.route("/api/admin/roles")
@require_perm("user.manage")
@common.api_wrap
def api_roles():
    """角色列表（改角色下拉用）。权限：user.manage"""
    rows = portal_db.list_roles()
    return common.ok({"list": [
        {"id": int(r["id"]), "name": r["name"], "display_name": r["display_name"]}
        for r in rows
    ]})


@bp.route("/api/admin/users/ban", methods=["POST"])
@require_perm("user.manage")
@common.api_wrap
def api_ban():
    """
    封禁/解封。参数：{user_id, banned}。权限：user.manage。禁止封禁自己。
    超管保护：superadmin 不可被任何其他管理员封禁；
    即使是超管操作，系统也必须保证至少存在 1 个未封禁超管。
    """
    body: dict = request.get_json(silent=True) or {}
    target_id: int = int(body.get("user_id", 0) or 0)
    banned: bool = bool(body.get("banned"))
    target = portal_db.get_user(target_id)
    if target is None:
        return common.err("用户不存在")
    operator_id: int = auth_bp.current_user_id() or 0
    if target_id == operator_id:
        return common.err("不能对自己执行封禁操作")
    target_is_sa: bool = str(target["role_name"]) == portal_db.SUPERADMIN_ROLE
    if target_is_sa and not portal_db.is_superadmin(operator_id):
        return common.err("超级管理员不可被封禁", "forbidden", 403)
    if target_is_sa and banned and not bool(target["is_banned"]):
        if portal_db.count_superadmins() <= 1:
            return common.err("系统必须至少保留一个超级管理员", "forbidden", 403)
    portal_db.set_banned(target_id, banned)
    _audit("ban_user" if banned else "unban_user", target["username"])
    return common.ok()


@bp.route("/api/admin/users/reset_password", methods=["POST"])
@require_perm("user.manage")
@common.api_wrap
def api_reset_password():
    """
    重置密码（返回临时密码，仅本次响应可见）。权限：user.manage。
    超管保护：非超管不得重置超管密码（等于接管账号）。
    """
    body: dict = request.get_json(silent=True) or {}
    target_id: int = int(body.get("user_id", 0) or 0)
    target = portal_db.get_user(target_id)
    if target is None:
        return common.err("用户不存在")
    if str(target["role_name"]) == portal_db.SUPERADMIN_ROLE and \
            not portal_db.is_superadmin(auth_bp.current_user_id() or 0):
        return common.err("超级管理员密码仅超管本人可重置", "forbidden", 403)
    temp: str = common.gen_password(12)
    portal_db.set_password(target_id, common.hash_password(temp))
    _audit("reset_password", target["username"])
    return common.ok({"temp_password": temp})


@bp.route("/api/admin/users/role", methods=["POST"])
@require_perm("user.manage")
@common.api_wrap
def api_set_role():
    """
    修改角色。参数：{user_id, role_id}。权限：user.manage。禁止改自己的角色。
    超管保护：非超管不得改动超管角色（降级=夺权）；最后一个超管不可被降级。
    """
    body: dict = request.get_json(silent=True) or {}
    target_id: int = int(body.get("user_id", 0) or 0)
    role_id: int = int(body.get("role_id", 0) or 0)
    target = portal_db.get_user(target_id)
    if target is None:
        return common.err("用户不存在")
    operator_id: int = auth_bp.current_user_id() or 0
    if target_id == operator_id:
        return common.err("不能修改自己的角色")
    with db.get_db() as conn:
        role = conn.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
    if role is None:
        return common.err("角色不存在")
    target_is_sa: bool = str(target["role_name"]) == portal_db.SUPERADMIN_ROLE
    new_is_sa: bool = str(role["name"]) == portal_db.SUPERADMIN_ROLE
    if target_is_sa and not portal_db.is_superadmin(operator_id):
        return common.err("超级管理员的角色不可被修改", "forbidden", 403)
    if target_is_sa and not new_is_sa and portal_db.count_superadmins() <= 1:
        return common.err("系统必须至少保留一个超级管理员", "forbidden", 403)
    portal_db.set_role(target_id, role_id)
    _audit("set_role", target["username"], f"role={role['name']}")
    return common.ok()


# ============================================================
# 邀请码管理
# ============================================================
@bp.route("/api/admin/invites")
@require_perm("invite.manage")
@common.api_wrap
def api_invites():
    """邀请码列表（最新 100）。权限：invite.manage"""
    rows = portal_db.list_invites(limit=100)
    now: float = time.time()
    return common.ok({"list": [
        {"id": int(r["id"]), "code": r["code"], "max_uses": int(r["max_uses"]),
         "used_count": int(r["used_count"]), "expires_at": float(r["expires_at"]),
         "revoked": r["revoked_at"] is not None,
         "expired": now > float(r["expires_at"]),
         "creator": r["creator"] or "系统", "created_at": float(r["created_at"])}
        for r in rows
    ]})


@bp.route("/api/admin/invites/generate", methods=["POST"])
@require_perm("invite.manage")
@common.api_wrap
def api_gen_invite():
    """生成邀请码。参数：{max_uses?, valid_days?}。权限：invite.manage"""
    body: dict = request.get_json(silent=True) or {}
    max_uses: int = max(1, min(100, int(body.get("max_uses", common.INVITE_DEFAULT_MAX_USES) or 1)))
    valid_days: int = max(1, min(90, int(body.get("valid_days", common.INVITE_DEFAULT_VALID_DAYS) or 7)))
    code: str = _gen_invite_code()
    expires_at: float = time.time() + valid_days * 86400
    portal_db.create_invite(code, max_uses, expires_at, auth_bp.current_user_id())
    _audit("gen_invite", code, f"max_uses={max_uses} days={valid_days}")
    return common.ok({"code": code, "expires_at": expires_at})


@bp.route("/api/admin/invites/revoke", methods=["POST"])
@require_perm("invite.manage")
@common.api_wrap
def api_revoke_invite():
    """作废邀请码。参数：{invite_id}。权限：invite.manage"""
    body: dict = request.get_json(silent=True) or {}
    invite_id: int = int(body.get("invite_id", 0) or 0)
    invite = None
    with db.get_db() as conn:
        invite = conn.execute("SELECT * FROM invites WHERE id=?", (invite_id,)).fetchone()
    if invite is None:
        return common.err("邀请码不存在")
    portal_db.revoke_invite(invite_id)
    _audit("revoke_invite", str(invite["code"]))
    return common.ok()


# ============================================================
# 公告管理
# ============================================================
@bp.route("/api/admin/announcements")
@require_perm("announcement.manage")
@common.api_wrap
def api_admin_announcements():
    """公告列表（最新 50）。权限：announcement.manage"""
    rows = portal_db.list_announcements(limit=50)
    return common.ok({"list": [
        {"id": int(r["id"]), "title": r["title"], "content": r["content"],
         "author": r["author"] or "管理员", "created_at": float(r["created_at"])}
        for r in rows
    ]})


@bp.route("/api/admin/announcements/create", methods=["POST"])
@require_perm("announcement.manage")
@common.api_wrap
def api_create_announcement():
    """发布公告。参数：{title, content?}。权限：announcement.manage"""
    body: dict = request.get_json(silent=True) or {}
    title: str = str(body.get("title", "")).strip()[:60]
    content: str = str(body.get("content", "")).strip()[:2000]
    if not title:
        return common.err("标题不能为空")
    ann_id: int = portal_db.add_announcement(title, content, auth_bp.current_user_id())
    _audit("create_announcement", title)
    return common.ok({"id": ann_id})


@bp.route("/api/admin/announcements/delete", methods=["POST"])
@require_perm("announcement.manage")
@common.api_wrap
def api_delete_announcement():
    """删除公告。参数：{id}。权限：announcement.manage"""
    body: dict = request.get_json(silent=True) or {}
    ann_id: int = int(body.get("id", 0) or 0)
    portal_db.delete_announcement(ann_id)
    _audit("delete_announcement", str(ann_id))
    return common.ok()


# ============================================================
# 审计日志
# ============================================================
@bp.route("/api/admin/audit")
@require_perm("audit.view")
@common.api_wrap
def api_audit():
    """审计日志（最新 100）。权限：audit.view"""
    rows = portal_db.list_audit(limit=100)
    return common.ok({"list": [
        {"id": int(r["id"]), "username": r["username"] or "系统",
         "action": r["action"], "target": r["target"], "detail": r["detail"],
         "ip": r["ip"], "created_at": float(r["created_at"])}
        for r in rows
    ]})


# ============================================================
# 游戏房间清理（手动触发，替代不可靠的定时任务）
# ============================================================
@bp.route("/api/admin/cleanup_rooms", methods=["POST"])
@require_perm("game.manage")
@common.api_wrap
def api_cleanup_rooms():
    """
    手动清理 10 分钟无活动房间（标记 abandoned）。
    权限：game.manage。返回本次清理数量。
    """
    count: int = match.cleanup_idle_rooms()
    _audit("cleanup_rooms", "", f"cleaned={count}")
    return common.ok({"cleaned": count})


# ============================================================
# 登录日志（仅 root 角色或 admin.full 可查）
# ============================================================
@bp.route("/api/admin/login_logs")
@require_perm("admin.access")
@common.api_wrap
def api_login_logs():
    """
    登录日志分页查询。参数：page?, only_fail?
    权限：admin.access 且（root 角色 或 admin.full）——普通管理员不可见。
    """
    uid: int = auth_bp.current_user_id() or 0
    if not _is_root_or_full(uid):
        return common.err("仅超级管理员可查看登录日志", "forbidden", 403)
    page: int = max(1, int(request.args.get("page", 1) or 1))
    only_fail: bool = request.args.get("only_fail") == "1"
    per: int = 50
    rows = portal_db.login_logs_list(limit=per, offset=(page - 1) * per,
                                     only_fail=only_fail)
    return common.ok({
        "list": [{
            "id": int(r["id"]), "user_id": r["user_id"],
            "username": r["username"], "nickname": r["nickname"] or "",
            "success": bool(r["success"]), "ip": r["ip"], "ua": r["ua"],
            "created_at": float(r["created_at"]),
        } for r in rows],
        "total": portal_db.login_logs_count(only_fail=only_fail),
        "page": page, "per": per,
    })


# ============================================================
# 资源导航（友情链接）管理 —— link.manage
# ============================================================
@bp.route("/api/admin/links/categories")
@require_perm("link.manage")
@common.api_wrap
def api_link_cats():
    """分类列表。权限：link.manage"""
    return common.ok({"list": [
        {"id": int(c["id"]), "name": c["name"], "color": c["color"],
         "sort": int(c["sort"])}
        for c in portal_db.link_cat_list()
    ]})


@bp.route("/api/admin/links/categories/save", methods=["POST"])
@require_perm("link.manage")
@common.api_wrap
def api_link_cat_save():
    """新增/更新分类。参数：{id?, name, color?, sort?}。权限：link.manage"""
    body: dict = request.get_json(silent=True) or {}
    cat_id: int = int(body.get("id", 0) or 0)
    name: str = str(body.get("name", "")).strip()[:20]
    color: str = str(body.get("color", "#4F6EF7")).strip()[:20]
    sort: int = int(body.get("sort", 0) or 0)
    if not name:
        return common.err("分类名不能为空")
    if not re.match(r"^#[0-9A-Fa-f]{6}$", color):
        return common.err("颜色需为 #RRGGBB 格式")
    if cat_id > 0:
        portal_db.link_cat_update(cat_id, name, color, sort)
        _audit("link_cat_update", name)
        return common.ok({"id": cat_id})
    new_id: int = portal_db.link_cat_add(name, color, sort)
    _audit("link_cat_add", name)
    return common.ok({"id": new_id})


@bp.route("/api/admin/links/categories/delete", methods=["POST"])
@require_perm("link.manage")
@common.api_wrap
def api_link_cat_delete():
    """删除分类（级联删其下链接）。参数：{id}。权限：link.manage"""
    body: dict = request.get_json(silent=True) or {}
    cat_id: int = int(body.get("id", 0) or 0)
    affected: int = portal_db.link_cat_delete(cat_id)
    _audit("link_cat_delete", str(cat_id), f"links_affected={affected}")
    return common.ok({"links_affected": affected})


@bp.route("/api/admin/links/list")
@require_perm("link.manage")
@common.api_wrap
def api_links_all():
    """全部链接（含停用）。权限：link.manage"""
    return common.ok({"list": [
        {"id": int(l["id"]), "name": l["name"], "url": l["url"],
         "category_id": int(l["category_id"]), "cat_name": l["cat_name"],
         "description": l["description"], "sort": int(l["sort"]),
         "is_active": bool(l["is_active"]), "created_at": float(l["created_at"])}
        for l in portal_db.link_list_all()
    ]})


def _valid_url(url: str) -> bool:
    """友链 URL 白名单校验：仅 http/https，防 javascript: 等协议注入。"""
    u: str = (url or "").strip().lower()
    return u.startswith("http://") or u.startswith("https://")


@bp.route("/api/admin/links/save", methods=["POST"])
@require_perm("link.manage")
@common.api_wrap
def api_link_save():
    """新增/更新链接。参数：{id?, name, url, category_id, description?, sort?, is_active?}"""
    body: dict = request.get_json(silent=True) or {}
    link_id: int = int(body.get("id", 0) or 0)
    name: str = str(body.get("name", "")).strip()[:30]
    url: str = str(body.get("url", "")).strip()[:200]
    category_id: int = int(body.get("category_id", 0) or 0)
    description: str = str(body.get("description", "")).strip()[:80]
    sort: int = int(body.get("sort", 0) or 0)
    is_active: bool = bool(body.get("is_active", True))
    if not name:
        return common.err("链接名称不能为空")
    if not _valid_url(url):
        return common.err("链接需以 http:// 或 https:// 开头")
    if category_id <= 0:
        return common.err("请选择分类")
    if link_id > 0:
        portal_db.link_update(link_id, name, url, category_id,
                              description, sort, is_active)
        _audit("link_update", name)
        return common.ok({"id": link_id})
    new_id: int = portal_db.link_add(name, url, category_id, description,
                                     sort, auth_bp.current_user_id())
    _audit("link_add", name)
    return common.ok({"id": new_id})


@bp.route("/api/admin/links/delete", methods=["POST"])
@require_perm("link.manage")
@common.api_wrap
def api_link_delete():
    """删除链接。参数：{id}。权限：link.manage"""
    body: dict = request.get_json(silent=True) or {}
    link_id: int = int(body.get("id", 0) or 0)
    portal_db.link_delete(link_id)
    _audit("link_delete", str(link_id))
    return common.ok()


@bp.route("/api/admin/links/submissions")
@require_perm("link.manage")
@common.api_wrap
def api_link_subs():
    """学生推荐审核队列。参数：status?=pending。权限：link.manage"""
    status: str = str(request.args.get("status", "pending"))
    if status not in ("pending", "approved", "rejected"):
        status = "pending"
    return common.ok({"list": [
        {"id": int(s["id"]), "name": s["name"], "url": s["url"],
         "category_id": s["category_id"], "cat_name": s["cat_name"] or "",
         "description": s["description"],
         "submitter": s["submitter"] or "匿名",
         "created_at": float(s["created_at"])}
        for s in portal_db.link_sub_list(status=status)
    ], "status": status})


@bp.route("/api/admin/links/submissions/review", methods=["POST"])
@require_perm("link.manage")
@common.api_wrap
def api_link_sub_review():
    """审批学生推荐。参数：{id, action: approve|reject}。权限：link.manage（幂等）"""
    body: dict = request.get_json(silent=True) or {}
    sub_id: int = int(body.get("id", 0) or 0)
    action: str = str(body.get("action", ""))
    reviewer: int = auth_bp.current_user_id() or 0
    sub = portal_db.link_sub_get(sub_id)
    if sub is None:
        return common.err("推荐不存在")
    if action == "approve":
        new_id = portal_db.link_sub_approve(sub_id, reviewer)
        if new_id is None:
            return common.err("该推荐已被处理", "conflict", 409)
        _audit("link_sub_approve", str(sub["name"]), f"link_id={new_id}")
        return common.ok({"link_id": new_id})
    if action == "reject":
        if not portal_db.link_sub_reject(sub_id, reviewer):
            return common.err("该推荐已被处理", "conflict", 409)
        _audit("link_sub_reject", str(sub["name"]))
        return common.ok()
    return common.err("action 需为 approve 或 reject")


# ============================================================
# 站点设置（可后台修改、全站生效的全局配置） —— admin.access
# ============================================================
@bp.route("/api/admin/settings/github")
@require_perm("admin.access")
@common.api_wrap
def api_setting_github_get():
    """读取站点 GitHub 仓库链接。权限：admin.access"""
    return common.ok({"github_url": portal_db.setting_get("github_url", "")})


@bp.route("/api/admin/settings/github", methods=["POST"])
@require_perm("admin.access")
@common.api_wrap
def api_setting_github_set():
    """修改站点 GitHub 仓库链接。参数：{github_url}。权限：admin.access"""
    body: dict = request.get_json(silent=True) or {}
    url: str = str(body.get("github_url", "")).strip()[:200]
    if url and not _valid_url(url):
        return common.err("链接需以 http:// 或 https:// 开头")
    portal_db.setting_put("github_url", url)
    _audit("set_github_url", url)
    return common.ok()
