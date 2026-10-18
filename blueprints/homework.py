"""
blueprints/homework.py —— 作业追踪（/homework + /api/homework/* + 后台管理）
=========================================================================
⚠️ 隐私硬性要求（防攀比）：
   个人"我已完成"状态【仅自己可见】。实现上分两层：
   • /api/homework/list 只返回作业本身（portal_db.hw_list 不含任何完成字段）；
     再单独返回 my_done（当前登录用户的已完成 id 列表）；
   • 任何接口都不接受、也不返回他人的完成状态；homework_done 查询永远带 user_id 过滤。
   调整代码时务必守住这条：一旦把 others 的完成状态加进响应，就违反验收项。

其余规则：
  • 剩余 <24h 用橙色描边，已过期置灰标记"已过期"（由前端按 due_at 计算）；
  • 支持按科目/状态筛选；空态文案"暂无作业，好好休息"。
"""

from __future__ import annotations

import time
from typing import Any, Optional

from flask import Blueprint, render_template, request

import common
import portal_db
from blueprints import auth as auth_bp
from blueprints.admin import require_perm

bp: Blueprint = Blueprint("homework", __name__)


def hw_view(h: dict[str, Any], done_ids: Optional[set[int]] = None) -> dict[str, Any]:
    """
    作业 → 前端视图。
    注意：done 只可能来自"当前用户"的完成集合（done_ids 由调用方按本人查询得到），
    绝无他人状态来源。
    """
    due: float = float(h["due_at"])
    uid: Optional[int] = int(h["id"])
    return {
        "id": uid, "subject": str(h["subject"]), "content": str(h["content"]),
        "due_at": due,
        "expired": time.time() >= due,
        "done": bool(done_ids is not None and uid in done_ids),
    }


# ============================================================
# 页面
# ============================================================
@bp.route("/homework")
def homework_page():
    """作业追踪页。"""
    return render_template("homework.html",
                           logged_in=auth_bp.current_user_id() is not None)


# ============================================================
# 用户端 API
# ============================================================
@bp.route("/api/homework/list")
@common.api_wrap
def api_list():
    """
    作业列表。参数：subject（科目）、state（active|expired）
    返回：{ok, list, subjects, my_done:[id...], logged_in}
    权限：公开浏览内容；my_done 仅对当前登录用户填充（未登录为空数组）。
    """
    uid: Optional[int] = auth_bp.current_user_id()
    subject: str = (request.args.get("subject", "") or "").strip()
    state: str = (request.args.get("state", "") or "").strip()
    rows = portal_db.hw_list(subject=subject or None,
                             state=state if state in ("active", "expired") else None)
    done_ids: Optional[set[int]] = portal_db.hw_my_done_ids(uid) if uid else None
    return common.ok({
        "list": [hw_view(r, done_ids) for r in rows],
        "subjects": portal_db.hw_subjects(),
        "my_done": sorted(done_ids) if done_ids else [],
        "logged_in": uid is not None,
    })


@bp.route("/api/homework/toggle_done", methods=["POST"])
@common.api_wrap
def api_toggle_done():
    """
    勾选/取消"我已完成"。参数：{id, done}
    返回：{ok, id, done}
    权限：需登录。**只写自己的记录**（user_id 取自会话，不接受客户端传入）。
    """
    uid: Optional[int] = auth_bp.current_user_id()
    if uid is None:
        return common.err("登录后才能标记完成", "auth", 401)
    if not common.rate_check(f"hw:{uid}", limit=20):
        return common.err("操作太频繁", "rate", 429)
    body: dict = request.get_json(silent=True) or {}
    try:
        hid: int = int(body.get("id") or 0)
    except (TypeError, ValueError):
        return common.err("id 不合法")
    if portal_db.hw_get(hid) is None:
        return common.err("作业不存在", "not_found", 404)
    done: bool = bool(body.get("done"))
    portal_db.hw_set_done(uid, hid, done)     # 内部按 (user_id, homework_id) 幂等
    return common.ok({"id": hid, "done": done})


@bp.route("/api/homework/latest")
@common.api_wrap
def api_latest():
    """
    首页用：最新 3 条未过期作业（含当前用户完成状态）。
    返回：{ok, list, logged_in}
    """
    uid: Optional[int] = auth_bp.current_user_id()
    rows = portal_db.hw_latest(limit=3)
    done_ids: Optional[set[int]] = portal_db.hw_my_done_ids(uid) if uid else None
    return common.ok({"list": [hw_view(r, done_ids) for r in rows],
                      "logged_in": uid is not None})


# ============================================================
# 后台（homework.manage）
# ============================================================
@bp.route("/api/admin/homework/list")
@require_perm("homework.manage")
@common.api_wrap
def api_admin_list():
    """
    后台作业管理列表。权限：homework.manage。
    ⚠️ 不返回任何完成人数/完成率——避免管理员界面成为攀比工具的来源。
    """
    rows = portal_db.hw_list(limit=500)
    return common.ok({"list": [{
        "id": int(r["id"]), "subject": str(r["subject"]), "content": str(r["content"]),
        "due_at": float(r["due_at"]), "expired": time.time() >= float(r["due_at"]),
        "due_input": common.bj_local_value(float(r["due_at"])),
    } for r in rows], "subjects": portal_db.hw_subjects()})


@bp.route("/api/admin/homework/create", methods=["POST"])
@require_perm("homework.manage")
@common.api_wrap
def api_admin_create():
    """
    发布作业。参数：{subject, content, due_input}
    权限：homework.manage。内容 ≤500 字。时间按北京时间解析。写审计。
    """
    body: dict = request.get_json(silent=True) or {}
    subject: str = str(body.get("subject", "")).strip()[:20]
    content: str = str(body.get("content", "")).strip()[:500]
    if not subject:
        return common.err("请选择科目")
    if not content:
        return common.err("作业内容不能为空")
    try:
        due_at: float = common.parse_bj_local(str(body.get("due_input", "")))
    except ValueError as e:
        return common.err(f"截止时间格式有误：{e}")
    hid: int = portal_db.hw_create(subject, content, due_at, auth_bp.current_user_id())
    portal_db.add_audit(auth_bp.current_user_id(), "homework_create", subject,
                        f"id={hid}", request.remote_addr or "")
    return common.ok({"id": hid})


@bp.route("/api/admin/homework/update", methods=["POST"])
@require_perm("homework.manage")
@common.api_wrap
def api_admin_update():
    """编辑作业。参数：{id, subject, content, due_input}。权限：homework.manage"""
    body: dict = request.get_json(silent=True) or {}
    try:
        hid: int = int(body.get("id") or 0)
    except (TypeError, ValueError):
        return common.err("id 不合法")
    subject: str = str(body.get("subject", "")).strip()[:20]
    content: str = str(body.get("content", "")).strip()[:500]
    if not subject or not content:
        return common.err("科目与内容均不能为空")
    try:
        due_at: float = common.parse_bj_local(str(body.get("due_input", "")))
    except ValueError as e:
        return common.err(f"截止时间格式有误：{e}")
    portal_db.hw_update(hid, subject, content, due_at)
    portal_db.add_audit(auth_bp.current_user_id(), "homework_update", str(hid), "",
                        request.remote_addr or "")
    return common.ok()


@bp.route("/api/admin/homework/delete", methods=["POST"])
@require_perm("homework.manage")
@common.api_wrap
def api_admin_delete():
    """删除作业（连带删除所有完成记录，由外键级联）。参数：{id}。权限：homework.manage"""
    body: dict = request.get_json(silent=True) or {}
    try:
        hid: int = int(body.get("id") or 0)
    except (TypeError, ValueError):
        return common.err("id 不合法")
    portal_db.hw_delete(hid)
    portal_db.add_audit(auth_bp.current_user_id(), "homework_delete", str(hid), "",
                        request.remote_addr or "")
    return common.ok()
