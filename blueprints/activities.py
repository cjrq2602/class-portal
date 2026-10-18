"""
blueprints/activities.py —— 活动/比赛（/activities + /api/activities/* + 后台管理）
=================================================================================
要点：
1. 状态自动计算（全部基于当前时间，与北京时间无关——时间是绝对值，展示时才转北京）：
     upcoming(未开始) → reg_open(报名中) → reg_closed(报名截止) → ongoing(进行中) → ended(已结束)
   判定顺序：先比 end_at / start_at，再看报名开关与截止，保证唯一且无歧义。
2. 报名：UNIQUE(user,activity) 保证幂等；仅"报名中且未满员"可报；取消可在结束前任意时刻。
3. 管理员：发布/编辑/下架（隐藏不删）/查看报名名单/标签库增删。全部写审计。
4. 时间输入：前端 <input type="datetime-local"> 的值语义为北京时间，
   服务端用 common.parse_bj_local() 反算 UTC（避免差 8 小时）。
"""

from __future__ import annotations

import time
from typing import Any, Optional

from flask import Blueprint, render_template, request

import common
import portal_db
from blueprints import auth as auth_bp
from blueprints.admin import require_perm

bp: Blueprint = Blueprint("activities", __name__)

TYPE_LABEL: dict[str, str] = {"activity": "活动", "contest": "比赛"}
STATUS_LABEL: dict[str, str] = {
    "upcoming": "未开始", "reg_open": "报名中", "reg_closed": "报名截止",
    "ongoing": "进行中", "ended": "已结束",
}


def act_status(a: Any, now: Optional[float] = None) -> str:
    """活动状态机（唯一判定入口，前后端共用同一套语义）。"""
    ts: float = now if now is not None else time.time()
    if ts >= float(a["end_at"]):
        return "ended"
    if ts >= float(a["start_at"]):
        return "ongoing"
    if bool(a["reg_enabled"]):
        return "reg_open" if ts < float(a["reg_deadline"]) else "reg_closed"
    return "upcoming"


def act_view(a: Any, reg_ids: Optional[set[int]] = None) -> dict[str, Any]:
    """活动 → 前端视图（含状态、是否已报名、是否满员）。"""
    status: str = act_status(a)
    limit: int = int(a["reg_limit"] or 0)
    count: int = int(a["reg_count"]) if "reg_count" in a.keys() else 0
    return {
        "id": int(a["id"]), "title": str(a["title"]),
        "atype": str(a["atype"]), "atype_label": TYPE_LABEL.get(str(a["atype"]), "活动"),
        "start_at": float(a["start_at"]), "end_at": float(a["end_at"]),
        "place": str(a["place"]), "detail": str(a["detail"]),
        "reg_enabled": bool(a["reg_enabled"]),
        "reg_deadline": float(a["reg_deadline"]),
        "reg_limit": limit, "reg_count": count,
        "reg_full": bool(limit and count >= limit),
        "status": status, "status_label": STATUS_LABEL.get(status, status),
        "can_register": status == "reg_open" and not (limit and count >= limit),
        "can_cancel": status != "ended",
        "tags": a.get("tags", []) if isinstance(a, dict) else [],
        "registered": (reg_ids is not None and int(a["id"]) in reg_ids),
    }


# ============================================================
# 页面
# ============================================================
@bp.route("/activities")
def activities_page():
    """活动/比赛列表页。"""
    return render_template("activities.html",
                           logged_in=auth_bp.current_user_id() is not None)


# ============================================================
# 用户端 API
# ============================================================
@bp.route("/api/activities/list")
@common.api_wrap
def api_list():
    """
    活动列表。参数：atype（activity|contest）、tag（标签 id）
    返回：{ok, list, tags}（list 已含状态、已报人数、是否已报名）
    权限：公开浏览（未登录 also 可看；registered 恒为 false）。
    """
    atype: str = (request.args.get("atype", "") or "").strip()
    tag: str = (request.args.get("tag", "") or "").strip()
    uid: Optional[int] = auth_bp.current_user_id()
    reg_ids: Optional[set[int]] = portal_db.act_my_reg_ids(uid) if uid else None
    rows = portal_db.act_public_list(
        atype=atype if atype in TYPE_LABEL else None,
        tag_id=int(tag) if tag.isdigit() else None)
    return common.ok({
        "list": [act_view(r, reg_ids) for r in rows],
        "tags": [{"id": int(t["id"]), "name": str(t["name"]), "color": str(t["color"])}
                 for t in portal_db.tag_list()],
    })


@bp.route("/api/activities/register", methods=["POST"])
@common.api_wrap
def api_register():
    """一键报名。参数 {id}。权限：需登录。仅"报名中且未满员"可报。"""
    uid: Optional[int] = auth_bp.current_user_id()
    if uid is None:
        return common.err("登录后才能报名", "auth", 401)
    if not common.rate_check(f"act:{uid}", limit=6):
        return common.err("操作太频繁", "rate", 429)
    body: dict = request.get_json(silent=True) or {}
    try:
        aid: int = int(body.get("id") or 0)
    except (TypeError, ValueError):
        return common.err("id 不合法")
    a = portal_db.act_get(aid)
    if a is None or bool(a["hidden"]):
        return common.err("活动不存在", "not_found", 404)
    # ⚠️ 注意：act_get 返回的是原始行（不含 reg_count 子查询列），
    #    这里必须显式查一次当前报名数，否则"人数上限"判断会失效 → 允许超额报名。
    status: str = act_status(a)
    if status != "reg_open":
        return common.err(f"当前状态（{STATUS_LABEL.get(status, status)}）不可报名")
    limit: int = int(a["reg_limit"] or 0)
    count: int = portal_db.act_reg_count(aid)
    if limit and count >= limit:
        return common.err("名额已满", "full")
    added: bool = portal_db.act_register(aid, uid)
    portal_db.add_audit(uid, "activity_register", str(aid), "",
                        request.remote_addr or "")
    count = portal_db.act_reg_count(aid)
    return common.ok({"registered": True, "already": not added, "reg_count": count})


@bp.route("/api/activities/unregister", methods=["POST"])
@common.api_wrap
def api_unregister():
    """取消报名。参数 {id}。权限：需登录。结束前均可取消。"""
    uid: Optional[int] = auth_bp.current_user_id()
    if uid is None:
        return common.err("请先登录", "auth", 401)
    body: dict = request.get_json(silent=True) or {}
    try:
        aid: int = int(body.get("id") or 0)
    except (TypeError, ValueError):
        return common.err("id 不合法")
    a = portal_db.act_get(aid)
    if a is None:
        return common.err("活动不存在", "not_found", 404)
    if act_status(a) == "ended":
        return common.err("活动已结束，无法取消")
    removed: bool = portal_db.act_unregister(aid, uid)
    portal_db.add_audit(uid, "activity_unregister", str(aid), "",
                        request.remote_addr or "")
    return common.ok({"registered": False, "removed": removed,
                      "reg_count": portal_db.act_reg_count(aid)})


@bp.route("/api/activities/upcoming")
@common.api_wrap
def api_upcoming():
    """
    首页 Hero 倒计时用：取"最近一个未结束的活动"（优先进行中，其次最早开始）。
    返回：{ok, item:{id,title,status,target_ts,atype_label} | null}
    权限：公开。
    """
    rows = portal_db.act_public_list()
    now: float = time.time()
    live: list[dict[str, Any]] = []
    for r in rows:
        st: str = act_status(r, now)
        if st == "ended":
            continue
        live.append({"id": int(r["id"]), "title": str(r["title"]),
                     "atype_label": TYPE_LABEL.get(str(r["atype"]), "活动"),
                     "status": st, "status_label": STATUS_LABEL.get(st, st),
                     "start_at": float(r["start_at"]), "end_at": float(r["end_at"]),
                     # 倒计时目标：进行中→结束时刻；未开始→开始时刻
                     "target_ts": float(r["end_at"]) if st == "ongoing"
                     else float(r["start_at"])})
    # 进行中优先，其余按开始时间升序
    live.sort(key=lambda x: (0 if x["status"] == "ongoing" else 1, x["start_at"]))
    return common.ok({"item": live[0] if live else None})


# ============================================================
# 后台：活动与标签管理（activity.manage）
# ============================================================
@bp.route("/api/admin/activities/list")
@require_perm("activity.manage")
@common.api_wrap
def api_admin_list():
    """后台活动列表（含已下架 + 报名人数）。权限：activity.manage"""
    rows = portal_db.act_admin_list()
    now: float = time.time()
    out: list[dict[str, Any]] = []
    for r in rows:
        st: str = act_status(r, now)
        d = dict(r)
        d.update({"tags": [{"id": int(t["id"]), "name": str(t["name"]),
                            "color": str(t["color"])}
                           for t in portal_db.tag_list() if int(t["id"]) in
                           set(portal_db.act_tag_ids(int(r["id"])))],
                  "status": st, "status_label": STATUS_LABEL.get(st, st),
                  "atype_label": TYPE_LABEL.get(str(r["atype"]), "活动"),
                  "start_input": common.bj_local_value(float(r["start_at"])),
                  "reg_deadline_input": common.bj_local_value(float(r["reg_deadline"]))
                  if float(r["reg_deadline"]) else ""})
        out.append(d)
    return common.ok({"list": out})


@bp.route("/api/admin/activities/create", methods=["POST"])
@require_perm("activity.manage")
@common.api_wrap
def api_admin_create():
    """
    发布活动。参数：{title, atype, tag_ids[], start_input, end_input, place,
    detail, reg_enabled, reg_deadline_input, reg_limit}
    权限：activity.manage。时间输入按北京时间解析。写审计。
    """
    body: dict = request.get_json(silent=True) or {}
    title: str = str(body.get("title", "")).strip()[:80]
    atype: str = str(body.get("atype", "activity")).strip()
    if not title:
        return common.err("标题不能为空")
    if atype not in TYPE_LABEL:
        return common.err("类型只能是 activity 或 contest")
    try:
        start_at: float = common.parse_bj_local(str(body.get("start_input", "")))
        end_at: float = common.parse_bj_local(str(body.get("end_input", "")))
    except ValueError as e:
        return common.err(f"时间格式有误：{e}")
    if end_at <= start_at:
        return common.err("结束时间必须晚于开始时间")
    reg_enabled: bool = bool(body.get("reg_enabled"))
    reg_deadline: float = 0.0
    if reg_enabled:
        try:
            reg_deadline = common.parse_bj_local(str(body.get("reg_deadline_input", "")))
        except ValueError as e:
            return common.err(f"报名截止时间格式有误：{e}")
        if reg_deadline > end_at:
            return common.err("报名截止不应晚于活动结束时间")
    try:
        reg_limit: int = max(0, min(10000, int(body.get("reg_limit") or 0)))
    except (TypeError, ValueError):
        return common.err("人数上限必须是非负整数")
    aid: int = portal_db.act_create(title, atype, start_at, end_at,
                                    str(body.get("place", "")).strip()[:60],
                                    str(body.get("detail", "")).strip()[:2000],
                                    reg_enabled, reg_deadline, reg_limit,
                                    auth_bp.current_user_id())
    tag_ids: list[int] = [int(x) for x in (body.get("tag_ids") or []) if str(x).isdigit()]
    if tag_ids:
        portal_db.act_set_tags(aid, tag_ids)
    portal_db.add_audit(auth_bp.current_user_id(), "activity_create", title,
                        f"id={aid}", request.remote_addr or "")
    return common.ok({"id": aid})


@bp.route("/api/admin/activities/update", methods=["POST"])
@require_perm("activity.manage")
@common.api_wrap
def api_admin_update():
    """编辑活动。参数：{id, ...同 create}。权限：activity.manage。写审计。"""
    body: dict = request.get_json(silent=True) or {}
    try:
        aid: int = int(body.get("id") or 0)
    except (TypeError, ValueError):
        return common.err("id 不合法")
    a = portal_db.act_get(aid)
    if a is None:
        return common.err("活动不存在", "not_found", 404)
    fields: dict[str, Any] = {}
    if body.get("title") is not None:
        t: str = str(body.get("title")).strip()[:80]
        if not t:
            return common.err("标题不能为空")
        fields["title"] = t
    if body.get("atype") in TYPE_LABEL:
        fields["atype"] = str(body["atype"])
    try:
        if body.get("start_input"):
            fields["start_at"] = common.parse_bj_local(str(body["start_input"]))
        if body.get("end_input"):
            fields["end_at"] = common.parse_bj_local(str(body["end_input"]))
    except ValueError as e:
        return common.err(f"时间格式有误：{e}")
    s_at: float = float(fields.get("start_at", a["start_at"]))
    e_at: float = float(fields.get("end_at", a["end_at"]))
    if e_at <= s_at:
        return common.err("结束时间必须晚于开始时间")
    fields["reg_enabled"] = 1 if body.get("reg_enabled") else 0
    if body.get("reg_deadline_input"):
        try:
            fields["reg_deadline"] = common.parse_bj_local(str(body["reg_deadline_input"]))
        except ValueError as e:
            return common.err(f"报名截止时间格式有误：{e}")
    if body.get("reg_limit") is not None:
        try:
            fields["reg_limit"] = max(0, min(10000, int(body["reg_limit"] or 0)))
        except (TypeError, ValueError):
            return common.err("人数上限必须是非负整数")
    if body.get("place") is not None:
        fields["place"] = str(body["place"]).strip()[:60]
    if body.get("detail") is not None:
        fields["detail"] = str(body["detail"]).strip()[:2000]
    portal_db.act_update(aid, **fields)
    if body.get("tag_ids") is not None:
        portal_db.act_set_tags(aid, [int(x) for x in body["tag_ids"] if str(x).isdigit()])
    portal_db.add_audit(auth_bp.current_user_id(), "activity_update", str(aid), "",
                        request.remote_addr or "")
    return common.ok()


@bp.route("/api/admin/activities/hide", methods=["POST"])
@require_perm("activity.manage")
@common.api_wrap
def api_admin_hide():
    """下架/恢复（隐藏不删）。参数：{id, hidden}。权限：activity.manage。写审计。"""
    body: dict = request.get_json(silent=True) or {}
    try:
        aid: int = int(body.get("id") or 0)
    except (TypeError, ValueError):
        return common.err("id 不合法")
    if portal_db.act_get(aid) is None:
        return common.err("活动不存在", "not_found", 404)
    hidden: bool = bool(body.get("hidden"))
    portal_db.act_update(aid, hidden=1 if hidden else 0)
    portal_db.add_audit(auth_bp.current_user_id(),
                        "activity_hide" if hidden else "activity_show", str(aid), "",
                        request.remote_addr or "")
    return common.ok()


@bp.route("/api/admin/activities/registrants")
@require_perm("activity.manage")
@common.api_wrap
def api_admin_registrants():
    """报名名单。参数：?id=。权限：activity.manage"""
    try:
        aid: int = int(request.args.get("id", "0") or 0)
    except ValueError:
        return common.err("id 不合法")
    rows = portal_db.act_registrants(aid)
    return common.ok({"list": [{"user_id": int(r["id"]), "username": str(r["username"]),
                                "nickname": str(r["nickname"]),
                                "created_at": float(r["created_at"])} for r in rows]})


@bp.route("/api/admin/activity_tags")
@require_perm("activity.manage")
@common.api_wrap
def api_admin_tags():
    """标签库。权限：activity.manage"""
    return common.ok({"list": [{"id": int(t["id"]), "name": str(t["name"]),
                                "color": str(t["color"])} for t in portal_db.tag_list()]})


@bp.route("/api/admin/activity_tags/create", methods=["POST"])
@require_perm("activity.manage")
@common.api_wrap
def api_admin_tag_create():
    """新增标签。参数：{name, color}。权限：activity.manage"""
    body: dict = request.get_json(silent=True) or {}
    name: str = str(body.get("name", "")).strip()[:20]
    color: str = str(body.get("color", "#4F6EF7")).strip()[:16]
    if not name:
        return common.err("标签名不能为空")
    if not color.startswith("#"):
        color = "#4F6EF7"
    try:
        tid: int = portal_db.tag_create(name, color)
    except Exception:  # noqa: BLE001 —— 名称唯一约束
        return common.err("标签已存在")
    portal_db.add_audit(auth_bp.current_user_id(), "tag_create", name, "",
                        request.remote_addr or "")
    return common.ok({"id": tid})


@bp.route("/api/admin/activity_tags/delete", methods=["POST"])
@require_perm("activity.manage")
@common.api_wrap
def api_admin_tag_delete():
    """删除标签（同时解除活动关联）。参数：{id}。权限：activity.manage"""
    body: dict = request.get_json(silent=True) or {}
    try:
        tid: int = int(body.get("id") or 0)
    except (TypeError, ValueError):
        return common.err("id 不合法")
    portal_db.tag_delete(tid)
    portal_db.add_audit(auth_bp.current_user_id(), "tag_delete", str(tid), "",
                        request.remote_addr or "")
    return common.ok()
