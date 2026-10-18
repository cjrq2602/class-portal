"""
blueprints/picker.py —— 随机点名（/picker，教师工具）
====================================================
要点：
1. 名单 roster.manage 维护（class_members：name 唯一 + 座位号 + 是否在册），
   支持从 users 一键导入昵称并自动去重（靠 name 唯一约束）。
2. 抽取：服务端负责"过滤 + 随机"，把候选集返回给前端做翻滚动画；
   过滤规则（客户端传参）：
     · exclude_drawn=true → 排除本次会话已点过的名字
     · absent_ids=[...]   → 排除本次被勾选的请假者
   候选为空时返回明确错误（前端提示"没有可抽取的人了"）。
3. 翻滚动画、鼓点音效（Web Audio 程序化生成）、全屏（Fullscreen API + CSS 降级）
   全部在 static/js/picker.js 实现，服务端不参与表现层。
4. 登录即可用（教师工具，无需额外权限；名单维护才需 roster.manage）。
"""

from __future__ import annotations

import random
from typing import Any

from flask import Blueprint, render_template, request

import common
import portal_db
from blueprints import auth as auth_bp
from blueprints.admin import require_perm

bp: Blueprint = Blueprint("picker", __name__)


# ============================================================
# 页面
# ============================================================
@bp.route("/picker")
def picker_page():
    """随机点名页。"""
    return render_template("picker.html",
                           logged_in=auth_bp.current_user_id() is not None,
                           can_manage="roster.manage" in auth_bp.user_perms())


# ============================================================
# API
# ============================================================
@bp.route("/api/picker/roster")
@common.api_wrap
def api_roster():
    """
    可用名单（仅在册成员）。返回：{ok, list:[{id,name,seat_no}], active_count}
    权限：需登录（教师工具）。
    """
    uid = auth_bp.current_user_id()
    if uid is None:
        return common.err("请先登录", "auth", 401)
    rows = portal_db.member_list(active_only=True)
    return common.ok({
        "list": [{"id": int(r["id"]), "name": str(r["name"]),
                  "seat_no": int(r["seat_no"])} for r in rows],
        "active_count": portal_db.member_count(),
    })


@bp.route("/api/picker/draw", methods=["POST"])
@common.api_wrap
def api_draw():
    """
    抽取一人。
    参数：{exclude_drawn?: bool, drawn_names?: [str], absent_ids?: [int]}
    返回：{ok, picked:{id,name,seat_no}, candidates:int, skipped:{absent:int,drawn:int}}
    权限：需登录。
    """
    uid = auth_bp.current_user_id()
    if uid is None:
        return common.err("请先登录", "auth", 401)
    if not common.rate_check(f"draw:{uid}", limit=8):
        return common.err("操作太频繁", "rate", 429)

    body: dict = request.get_json(silent=True) or {}
    exclude_drawn: bool = bool(body.get("exclude_drawn"))
    drawn_names: set[str] = {str(x) for x in (body.get("drawn_names") or [])}
    absent_ids: set[int] = set()
    for x in (body.get("absent_ids") or []):
        try:
            absent_ids.add(int(x))
        except (TypeError, ValueError):
            continue

    rows = portal_db.member_list(active_only=True)
    if not rows:
        return common.err("名单为空，请先在后台维护或从用户导入", "empty", 409)

    candidates: list[Any] = []
    skipped_absent: int = 0
    skipped_drawn: int = 0
    for r in rows:
        if int(r["id"]) in absent_ids:
            skipped_absent += 1
            continue
        if exclude_drawn and str(r["name"]) in drawn_names:
            skipped_drawn += 1
            continue
        candidates.append(r)

    if not candidates:
        return common.err("没有可抽取的人了（都点过了或都请假了）", "exhausted", 409)

    picked = random.choice(candidates)          # 服务端随机，防前端可预测
    portal_db.add_audit(uid, "picker_draw", str(picked["name"]), "",
                        request.remote_addr or "")
    return common.ok({
        "picked": {"id": int(picked["id"]), "name": str(picked["name"]),
                   "seat_no": int(picked["seat_no"])},
        "candidates": len(candidates),
        "skipped": {"absent": skipped_absent, "drawn": skipped_drawn},
    })


# ============================================================
# 后台：名单维护（roster.manage）
# ============================================================
@bp.route("/api/admin/roster")
@require_perm("roster.manage")
@common.api_wrap
def api_admin_roster():
    """完整名单（含停用）。权限：roster.manage"""
    rows = portal_db.member_list(active_only=False)
    return common.ok({"list": [{
        "id": int(r["id"]), "name": str(r["name"]), "seat_no": int(r["seat_no"]),
        "is_active": bool(r["is_active"]),
    } for r in rows], "active_count": portal_db.member_count()})


@bp.route("/api/admin/roster/add", methods=["POST"])
@require_perm("roster.manage")
@common.api_wrap
def api_admin_roster_add():
    """新增成员。参数：{name, seat_no?}。权限：roster.manage（重复名字返回错误）"""
    body: dict = request.get_json(silent=True) or {}
    name: str = str(body.get("name", "")).strip()[:20]
    if not name:
        return common.err("姓名不能为空")
    try:
        seat: int = max(0, int(body.get("seat_no") or 0))
    except (TypeError, ValueError):
        seat = 0
    if not portal_db.member_add(name, seat):
        return common.err("该姓名已存在（名单自动去重）")
    portal_db.add_audit(auth_bp.current_user_id(), "roster_add", name, "",
                        request.remote_addr or "")
    return common.ok()


@bp.route("/api/admin/roster/update", methods=["POST"])
@require_perm("roster.manage")
@common.api_wrap
def api_admin_roster_update():
    """编辑成员（改名/座位/启用停用）。参数：{id, name, seat_no, is_active}"""
    body: dict = request.get_json(silent=True) or {}
    try:
        mid: int = int(body.get("id") or 0)
    except (TypeError, ValueError):
        return common.err("id 不合法")
    name: str = str(body.get("name", "")).strip()[:20]
    if not name:
        return common.err("姓名不能为空")
    try:
        seat: int = max(0, int(body.get("seat_no") or 0))
    except (TypeError, ValueError):
        seat = 0
    portal_db.member_update(mid, name, seat, bool(body.get("is_active", True)))
    portal_db.add_audit(auth_bp.current_user_id(), "roster_update", str(mid), name,
                        request.remote_addr or "")
    return common.ok()


@bp.route("/api/admin/roster/delete", methods=["POST"])
@require_perm("roster.manage")
@common.api_wrap
def api_admin_roster_delete():
    """删除成员。参数：{id}。权限：roster.manage（前端需二次确认）"""
    body: dict = request.get_json(silent=True) or {}
    try:
        mid: int = int(body.get("id") or 0)
    except (TypeError, ValueError):
        return common.err("id 不合法")
    portal_db.member_delete(mid)
    portal_db.add_audit(auth_bp.current_user_id(), "roster_delete", str(mid), "",
                        request.remote_addr or "")
    return common.ok()


@bp.route("/api/admin/roster/import_users", methods=["POST"])
@require_perm("roster.manage")
@common.api_wrap
def api_admin_roster_import():
    """
    从 users 一键导入昵称（自动去重）。权限：roster.manage
    返回：{ok, added, total}（added=本次新增，重复的昵称被跳过）
    """
    added: int = portal_db.member_import_from_users()
    total: int = portal_db.member_count()
    portal_db.add_audit(auth_bp.current_user_id(), "roster_import_users", "users",
                        f"added={added} total={total}", request.remote_addr or "")
    return common.ok({"added": added, "total": total})
