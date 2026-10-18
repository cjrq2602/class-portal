"""
blueprints/auth.py —— 用户认证蓝图（邀请码注册 / 登录 / 会话 / 改密）
====================================================================
路由：
    GET  /login /register          页面
    POST /logout                   登出（POST 防 CSRF，前端 fetch 后跳转）
    POST /api/auth/register        邀请码注册 {invite_code, username, nickname, password}
    POST /api/auth/login           登录 {username, password}
    GET  /api/auth/me              当前用户信息 + 权限列表
    POST /api/auth/change_password 改密 {old_password, new_password}
安全设计：
1. 密码 pbkdf2-sha256（common.hash_password）；
2. 连续失败 5 次锁 10 分钟（common 内存计数）；
3. 登录成功后 session.clear() 再写入 —— 防会话固定攻击；
4. Cookie HttpOnly + SameSite=Lax（工厂统一配置）；
5. 所有 POST 经工厂级 CSRF 校验。
"""

from __future__ import annotations

import re
import time
from typing import Any, Optional

from flask import Blueprint, redirect, render_template, request, session, url_for

import common
import portal_db

bp: Blueprint = Blueprint("auth", __name__)

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")   # 登录名：3~20 位字母数字下划线
NICK_MAX: int = 12                                   # 与游戏昵称上限一致
PASSWORD_MIN: int = common.PASSWORD_MIN_LEN          # 第5期起密码策略统一走 common


# ============================================================
# 会话辅助（供其他蓝图复用）
# ============================================================
def current_user_id() -> Optional[int]:
    """当前会话用户 id；未登录返回 None。"""
    uid = session.get("user_id")
    return int(uid) if uid else None


def current_user() -> Optional[Any]:
    """当前用户完整行（含角色）；未登录/已封禁仍返回行，由调用方判断 is_banned。"""
    uid = current_user_id()
    if uid is None:
        return None
    return portal_db.get_user(uid)


def user_perms() -> list[str]:
    """
    当前用户权限 code 列表。
    若角色拥有 admin.full（超级管理员）→ 展开为全部权限 code，
    保证模板 'admin.access' in nav_perms 等既有判断一律通过。
    """
    uid = current_user_id()
    if uid is None:
        return []
    codes: list[str] = portal_db.get_user_perms(uid)
    if portal_db.ADMIN_FULL_PERM in codes:
        return portal_db.all_perm_codes()
    return codes


def has_perm(code: str) -> bool:
    return code in user_perms()


def _login_session(user_id: int) -> None:
    """
    写入登录会话：先 clear 防固定攻击，再写新身份。
    第 5 期会话策略：permanent（绝对 7 天，工厂配置）+ born_at/last_active/
    admin_active 三个时间戳（before_request 据此做绝对/滑动/后台超时判定）。
    """
    csrf: str = session.get("csrf_token", "")  # 保留 CSRF 令牌避免页面表单失效
    session.clear()
    session["user_id"] = int(user_id)
    session.permanent = True
    now: float = time.time()
    session["born_at"] = now          # 绝对过期基准（7 天）
    session["last_active"] = now      # 滑动过期基准（2 小时）
    session["admin_active"] = now     # 管理后台超时基准（30 分钟）
    if csrf:
        session["csrf_token"] = csrf


# ============================================================
# 页面
# ============================================================
@bp.route("/login")
def login_page():
    if current_user_id():
        return redirect("/")
    return render_template("login.html")


@bp.route("/register")
def register_page():
    if current_user_id():
        return redirect("/")
    return render_template("register.html")


@bp.route("/logout", methods=["POST"])
@common.api_wrap
def logout():
    """登出：清空会话。前端收到 ok 后跳转首页。"""
    uid = current_user_id()
    session.clear()
    if uid:
        portal_db.add_audit(uid, "logout", "", "", request.remote_addr or "")
    return common.ok()


# ============================================================
# API
# ============================================================
@bp.route("/api/auth/register", methods=["POST"])
@common.api_wrap
def api_register():
    """
    邀请码注册。
    参数：{invite_code, username, nickname, password}
    返回：{ok, user:{id,username,nickname,role_name}}
    权限：匿名（需有效邀请码）
    """
    if not common.rate_check("ip:" + request.remote_addr, limit=3):
        return common.err("请求太频繁，请稍后再试", "rate", 429)
    body: dict = request.get_json(silent=True) or {}
    invite_code: str = str(body.get("invite_code", "")).strip().upper()
    username: str = str(body.get("username", "")).strip().lower()
    nickname: str = str(body.get("nickname", "")).strip()[:NICK_MAX]
    password: str = str(body.get("password", ""))

    # ---- 邀请码校验（限次数 / 有效期 / 可作废） ----
    if not invite_code:
        return common.err("请输入邀请码")
    invite = portal_db.get_invite_by_code(invite_code)
    if invite is None:
        return common.err("邀请码不存在")
    if invite["revoked_at"]:
        return common.err("邀请码已作废")
    if time.time() > float(invite["expires_at"]):
        return common.err("邀请码已过期")
    if int(invite["used_count"]) >= int(invite["max_uses"]):
        return common.err("邀请码已被用完")

    # ---- 表单校验 ----
    if not USERNAME_RE.match(username):
        return common.err("用户名需为 3~20 位字母、数字或下划线")
    if not nickname:
        return common.err("昵称不能为空")
    pw_err: Optional[str] = common.check_password_strength(password)
    if pw_err:
        return common.err(pw_err)
    if portal_db.get_user_by_username(username) is not None:
        return common.err("用户名已被占用")

    # ---- 创建用户（member 角色）并消耗邀请码 ----
    member = portal_db.get_role_by_name("member")
    if member is None:
        return common.err("系统角色未初始化，请联系管理员", "internal", 500)
    uid: int = portal_db.create_user(username, nickname,
                                     common.hash_password(password), int(member["id"]))
    portal_db.consume_invite(int(invite["id"]))
    portal_db.add_audit(uid, "register", username,
                        f"invite={invite_code}", request.remote_addr or "")
    _login_session(uid)  # 注册成功自动登录
    return common.ok({"user": {"id": uid, "username": username,
                                "nickname": nickname, "role_name": "member"}})


@bp.route("/api/auth/login", methods=["POST"])
@common.api_wrap
def api_login():
    """
    登录。参数：{username, password}
    返回：{ok, user:{id,username,nickname,role_name,perms}}
    权限：匿名；连续失败 5 次锁 10 分钟。
    """
    if not common.rate_check("ip:" + request.remote_addr, limit=5):
        return common.err("请求太频繁，请稍后再试", "rate", 429)
    body: dict = request.get_json(silent=True) or {}
    username: str = str(body.get("username", "")).strip().lower()
    password: str = str(body.get("password", ""))
    if not username or not password:
        return common.err("请输入用户名和密码")

    if common.login_locked(username):
        return common.err("失败次数过多，账号已锁定 10 分钟", "locked", 423)

    user = portal_db.get_user_by_username(username)
    ip: str = request.remote_addr or ""
    ua: str = request.headers.get("User-Agent", "")
    if user is None or not common.verify_password(password, str(user["password_hash"])):
        common.login_fail(username)
        portal_db.add_audit(user["id"] if user else None, "login_fail", username,
                            "", ip)
        portal_db.login_log_add(int(user["id"]) if user else None,
                                username, False, ip, ua)
        return common.err("用户名或密码错误", "auth", 401)

    if bool(user["is_banned"]):
        portal_db.login_log_add(int(user["id"]), username, False, ip, ua)
        return common.err("账号已被封禁，请联系管理员", "banned", 403)

    common.login_success(username)
    _login_session(int(user["id"]))
    portal_db.add_audit(int(user["id"]), "login", username, "", ip)
    portal_db.login_log_add(int(user["id"]), username, True, ip, ua)
    # 超管登录额外留痕（安全审计要求：其登录与所有操作必进 audit_logs）
    if str(user["role_name"]) == portal_db.SUPERADMIN_ROLE:
        portal_db.add_audit(int(user["id"]), "superadmin_login", username,
                            ua[:120], ip)
    return common.ok({"user": {
        "id": int(user["id"]), "username": user["username"],
        "nickname": user["nickname"], "role_name": user["role_name"],
        "perms": user_perms(),
    }})


@bp.route("/api/auth/me", methods=["GET"])
@common.api_wrap
def api_me():
    """
    当前用户信息。返回：{ok, logged_in, user?, perms}
    权限：任意（未登录返回 logged_in=false）
    """
    user = current_user()
    if user is None:
        return common.ok({"logged_in": False})
    stats = portal_db.get_game_stats(int(user["id"]))
    return common.ok({
        "logged_in": True,
        "user": {
            "id": int(user["id"]), "username": user["username"],
            "nickname": user["nickname"], "role_name": user["role_name"],
            "role_display": user["role_display"],
            "is_banned": bool(user["is_banned"]),
            "created_at": float(user["created_at"]),
            "game_wins": int(stats["wins"]) if stats else 0,
            "game_losses": int(stats["losses"]) if stats else 0,
        },
        "perms": user_perms(),
    })


@bp.route("/api/auth/change_password", methods=["POST"])
@common.api_wrap
def api_change_password():
    """
    修改密码。参数：{old_password, new_password}
    权限：需登录（401=未登录）
    """
    uid = current_user_id()
    if uid is None:
        return common.err("请先登录", "auth", 401)
    body: dict = request.get_json(silent=True) or {}
    old_password: str = str(body.get("old_password", ""))
    new_password: str = str(body.get("new_password", ""))
    user = portal_db.get_user(uid)
    if user is None:
        return common.err("用户不存在", "auth", 401)
    if not common.verify_password(old_password, str(user["password_hash"])):
        return common.err("原密码错误")
    pw_err: Optional[str] = common.check_password_strength(new_password)
    if pw_err:
        return common.err(pw_err)
    portal_db.set_password(uid, common.hash_password(new_password))
    # 改密时间戳：使其他设备旧会话全部失效；本会话 born_at 重盖为现在以保活
    portal_db.bump_password_changed_at(uid)
    session["born_at"] = time.time()
    portal_db.add_audit(uid, "change_password", user["username"], "",
                        request.remote_addr or "")
    return common.ok()


# ============================================================
# 第六期：凭重置码改密（忘记密码流程；无需登录）
# ============================================================
@bp.route("/reset-password")
def reset_password_page():
    """凭重置码修改密码页（登录页「忘记密码？」与 /me 设置区均可达）。"""
    return render_template("reset_password.html")


@bp.route("/api/auth/reset-by-code", methods=["POST"])
@common.api_wrap
def api_reset_by_code():
    """
    凭重置码改密。参数：{username, code, new_password}
    返回：{ok}
    权限：匿名（防爆破：同一目标用户输错 5 次锁 10 分钟，423=locked）
    校验链：用户存在 → 未锁定 → 有活跃码 → 未过期 → 未使用 → HMAC 恒定时间比对
            → 码归属该用户（码本就按 target_user_id 索引取，天然归属校验）
    通过：改密 + 码标已用 + password_changed_at 刷新（全部旧会话强制失效）+ 审计。
    """
    if not common.rate_check_window("rc:" + (request.remote_addr or ""),
                                    limit=10, window_seconds=60.0):
        return common.err("请求太频繁，请稍后再试", "rate", 429)
    body: dict = request.get_json(silent=True) or {}
    username: str = str(body.get("username", "")).strip().lower()
    code: str = str(body.get("code", "")).strip().upper()
    new_password: str = str(body.get("new_password", ""))
    if not username or not code or not new_password:
        return common.err("请填写用户名、重置码与新密码")

    user = portal_db.get_user_by_username(username)
    # 用户不存在也走统一文案，避免枚举有效用户名
    if user is None:
        return common.err("用户名或重置码不正确", "auth", 401)
    uid: int = int(user["id"])

    if common.reset_locked(uid):
        return common.err("输错次数过多，该账号的重置已锁定 10 分钟", "locked", 423)

    active = portal_db.reset_code_active(uid)
    if active is None:
        # 无活跃码（从未生成/已过期/已用）——不计输错（非爆破路径）
        return common.err("重置码不存在或已过期，请联系管理员重新获取", "code_invalid", 400)

    if not common.verify_reset_code(code, str(active["code_hash"])):
        common.reset_fail(uid)  # 仅哈希不匹配计入防爆破
        portal_db.add_audit(uid, "reset_code_fail", username, "",
                            request.remote_addr or "")
        left: int = common.RESET_MAX_FAILS
        return common.err(f"重置码不正确（连续输错 {left} 次将锁定 10 分钟）",
                          "code_mismatch", 401)

    pw_err: Optional[str] = common.check_password_strength(new_password)
    if pw_err:
        return common.err(pw_err)

    portal_db.set_password(uid, common.hash_password(new_password))
    portal_db.reset_code_mark_used(int(active["id"]))   # 一次性：用后即废
    portal_db.bump_password_changed_at(uid)             # 全部旧会话强制失效
    common.reset_fail_clear(uid)
    # 审计：谁（目标用户）于何时使用了码（生成侧审计在 resetcode.py）
    portal_db.add_audit(uid, "reset_code_use", username,
                        f"code_id={int(active['id'])}", request.remote_addr or "")
    return common.ok()


@bp.route("/api/auth/update_nickname", methods=["POST"])
@common.api_wrap
def api_update_nickname():
    """
    修改昵称（个人中心设置项）。参数：{nickname}
    返回：{ok, nickname}
    权限：需登录。昵称 ≤12 字，与游戏内昵称上限一致。
    """
    uid = current_user_id()
    if uid is None:
        return common.err("请先登录", "auth", 401)
    if not common.rate_check(f"nick:{uid}", limit=5):
        return common.err("操作太频繁", "rate", 429)
    body: dict = request.get_json(silent=True) or {}
    nickname: str = str(body.get("nickname", "")).strip()[:NICK_MAX]
    if not nickname:
        return common.err("昵称不能为空")
    user = portal_db.get_user(uid)
    if user is None:
        return common.err("用户不存在", "auth", 401)
    portal_db.set_nickname(uid, nickname)
    portal_db.add_audit(uid, "update_nickname", str(user["nickname"]),
                        f"-> {nickname}", request.remote_addr or "")
    return common.ok({"nickname": nickname})
