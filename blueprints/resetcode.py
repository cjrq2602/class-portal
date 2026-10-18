"""
blueprints/resetcode.py —— 密码重置码管理（/me「账号管理」面板，user.manage）
==============================================================================
设计（类智慧团建）：
1. 管理员搜索用户 → 生成 8 位一次性码（明文仅本次响应显示一次，库里只存 HMAC 哈希）；
2. 硬性规则：
   a. 禁止给 superadmin 生成（越权防护，返回明确错误）；
   b. 不能给自己生成（自己改密走 /me 正常改密流程）；
   c. 生成与使用均进 audit_logs（使用侧的审计在 auth.py /api/auth/reset-by-code）；
   d. 面板只显示剩余有效期倒计时与状态（未用/已用/已过期），永不显示码本身；
3. 同一用户同时只允许 1 个活跃码（生成新码自动作废旧码，见 portal_db.reset_code_create）。
路由：
    GET  /api/reset-code/search_users?kw=   模糊搜索（用户名/昵称，前 20 条）
    POST /api/reset-code/generate           {user_id} 生成重置码
    GET  /api/reset-code/status?user_id=    最近一码状态 + 剩余有效期（不含码）
"""

from __future__ import annotations

import time
from typing import Any, Optional

from flask import Blueprint, request

import common
import portal_db
from blueprints.admin import require_perm
from blueprints import auth as auth_bp

bp: Blueprint = Blueprint("resetcode", __name__)


def _status_of(row: Any) -> tuple[str, int]:
    """码状态与剩余秒数：active(未用) / used(已用或已被新码作废) / expired(已过期)。"""
    now: float = time.time()
    if row["used_at"]:
        return "used", 0
    if float(row["expires_at"]) <= now:
        return "expired", 0
    return "active", int(float(row["expires_at"]) - now)


def _code_status_payload(user_id: int) -> dict[str, Any]:
    """面板状态视图（绝不包含码本身/哈希）。"""
    row = portal_db.reset_code_latest(user_id)
    if row is None:
        return {"has_code": False, "status": "none", "remaining_seconds": 0}
    status, remain = _status_of(row)
    return {
        "has_code": True,
        "status": status,
        "remaining_seconds": remain if status == "active" else 0,
        "created_at": float(row["created_at"]),
        "creator": str(row["creator"]) if row["creator"] else "管理员",
    }


@bp.route("/api/reset-code/search_users")
@require_perm("user.manage")
@common.api_wrap
def api_search_users():
    """
    模糊搜索用户（用户名/昵称 LIKE，前 20 条；%_ 通配符转义防注入歧义）。
    返回：{ok, list:[{id,username,nickname,role_display,is_banned,is_superadmin,code}]}
    权限：user.manage
    """
    kw: str = str(request.args.get("kw", "")).strip()[:20]
    if not kw:
        return common.ok({"list": []})
    # LIKE 通配符转义：用户输入的 % 和 _ 按字面匹配
    like: str = "%" + kw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    import db as _db  # 局部导入：仅本函数需要的轻量查询
    with _db.get_db() as conn:
        rows = conn.execute(
            "SELECT u.id, u.username, u.nickname, u.is_banned,"
            " r.name AS role_name, r.display_name AS role_display"
            " FROM users u JOIN roles r ON r.id=u.role_id"
            " WHERE u.username LIKE ? ESCAPE '\\' OR u.nickname LIKE ? ESCAPE '\\'"
            " ORDER BY u.id LIMIT 20", (like, like)).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        uid: int = int(r["id"])
        out.append({
            "id": uid, "username": str(r["username"]),
            "nickname": str(r["nickname"]),
            "role_display": str(r["role_display"]),
            "is_banned": bool(r["is_banned"]),
            "is_superadmin": str(r["role_name"]) == portal_db.SUPERADMIN_ROLE,
            "code": _code_status_payload(uid),
        })
    return common.ok({"list": out})


@bp.route("/api/reset-code/generate", methods=["POST"])
@require_perm("user.manage")
@common.api_wrap
def api_generate():
    """
    为目标用户生成重置码。参数：{user_id}
    返回：{ok, code(明文仅此一次), expires_at, remaining_seconds}
    权限：user.manage
    错误码：forbidden_superadmin（目标是超管）/ forbidden_self（目标是自己）
    """
    if not common.rate_check_window(
            f"rcgen:{auth_bp.current_user_id()}", limit=10, window_seconds=60.0):
        return common.err("操作太频繁，请稍后再试", "rate", 429)
    body: dict = request.get_json(silent=True) or {}
    target_id: int = int(body.get("user_id", 0) or 0)
    target = portal_db.get_user(target_id)
    if target is None:
        return common.err("用户不存在", "not_found", 404)

    operator_id: Optional[int] = auth_bp.current_user_id()
    # 硬性规则 a：禁止给 superadmin 生成重置码（越权防护）
    if str(target["role_name"]) == portal_db.SUPERADMIN_ROLE:
        return common.err("不能给超级管理员生成重置码", "forbidden_superadmin", 403)
    # 硬性规则 b：不能给自己生成（自己改密走 /me 正常改密流程）
    if operator_id is not None and target_id == operator_id:
        return common.err("不能给自己生成重置码，请直接使用修改密码", "forbidden_self", 403)

    code: str = common.make_reset_code()  # 明文：仅本次响应返回，不落库不落日志
    expires_at: float = time.time() + common.RESET_CODE_TTL_SECONDS
    portal_db.reset_code_create(target_id, common.hash_reset_code(code),
                                int(operator_id or 0), expires_at)
    # 硬性规则 c：生成进审计（不含码）
    portal_db.add_audit(operator_id, "reset_code_gen",
                        str(target["username"]),
                        f"expires_in={common.RESET_CODE_TTL_SECONDS}s",
                        request.remote_addr or "")
    return common.ok({
        "code": code,
        "target_username": str(target["username"]),
        "target_nickname": str(target["nickname"]),
        "expires_at": expires_at,
        "remaining_seconds": common.RESET_CODE_TTL_SECONDS,
    })


@bp.route("/api/reset-code/status")
@require_perm("user.manage")
@common.api_wrap
def api_status():
    """
    目标用户最近一码状态（倒计时由前端按 remaining_seconds 本地递减）。
    参数：user_id  返回：{ok, code:{has_code,status,remaining_seconds,created_at,creator}}
    权限：user.manage（不含码本身——明文只在 generate 的响应出现过一次）
    """
    target_id: int = int(request.args.get("user_id", 0) or 0)
    if portal_db.get_user(target_id) is None:
        return common.err("用户不存在", "not_found", 404)
    return common.ok({"code": _code_status_payload(target_id)})
