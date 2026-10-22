"""
blueprints/attendance.py —— 日常考勤（定位签到，/attendance + /api/attendance/*）
==============================================================================
规则（页面明示，前后端同一套）：
  · 发起人需 attendance.manage 权限；发起时取发起人实时定位为考勤点；
  · 签到半径默认 300 米（GPS 漂移容差：有效距离 = 实测距离 - min(上报精度, 30m)）；
  · 距离判定【服务端复核】（Haversine），前端只作体验展示；
  · 同一时刻只允许一场进行中的考勤；每人每场只能签一次（UNIQUE 兜底）；
  · 到期会话惰性关闭，不依赖定时任务；
  · 发起时可关联点名模块的名单分组（如 燃气2602）：按「账号昵称 = 名单姓名」
    自动对账，管理端可见应到/已签/未签名单，并可对未签到者手动补签；
  · 场次与签到名单保留 2 天（ATT_RETENTION_DAYS），过期惰性清除。
坐标系说明：浏览器 Geolocation 返回 WGS-84 坐标，两端同源，直接比较无纠偏问题。
"""

from __future__ import annotations

import time
from typing import Any, Optional

from flask import Blueprint, render_template, request

import common
import portal_db
from blueprints import auth as auth_bp
from blueprints.admin import require_perm

bp: Blueprint = Blueprint("attendance", __name__)

RADIUS_M: int = 300           # 默认签到半径（2026-10-22 起 100 → 300 米）
ACCURACY_TOLERANCE_M: float = 30.0   # GPS 精度容差上限
MAX_TITLE_LEN: int = 30
DURATION_CHOICES: tuple[float, ...] = (0.5, 1, 2, 5, 10)   # 分钟（0.5 = 30 秒快闪考勤）
DEFAULT_DURATION_MIN: float = 2.0


def _can_manage(uid: Optional[int]) -> bool:
    if uid is None:
        return False
    perms: list[str] = portal_db.get_user_perms(uid)
    return portal_db.ADMIN_FULL_PERM in perms or "attendance.manage" in perms


def _group_name(group_id: int) -> str:
    """名单分组名（0 或不存在返回空串）。"""
    if group_id <= 0:
        return ""
    for g in portal_db.roster_groups():
        if int(g["id"]) == group_id:
            return str(g["name"])
    return ""


def _session_view(s: Any, with_coords: bool = True) -> dict[str, Any]:
    gid: int = int(s["group_id"]) if "group_id" in s.keys() else 0
    v: dict[str, Any] = {
        "id": int(s["id"]), "title": str(s["title"]),
        "radius_m": int(s["radius_m"]), "status": str(s["status"]),
        "creator_name": str(s["creator_name"]),
        "ends_at": float(s["ends_at"]), "created_at": float(s["created_at"]),
        "group_id": gid, "group_name": _group_name(gid),
    }
    if with_coords:
        v["lat"] = float(s["lat"])
        v["lng"] = float(s["lng"])
    return v


# ============================================================
# 页面
# ============================================================
@bp.route("/attendance")
def attendance_page():
    try:
        portal_db.att_close_expired()
    except Exception:  # noqa: BLE001
        pass
    uid: Optional[int] = auth_bp.current_user_id()
    return render_template("attendance.html",
                           can_manage=_can_manage(uid),
                           logged_in=uid is not None,
                           radius_m=RADIUS_M)


# ============================================================
# 状态查询（登录用户：当前会话 + 我是否已签）
# ============================================================
@bp.route("/api/attendance/state", methods=["GET"])
@common.api_wrap
def api_state():
    uid: Optional[int] = auth_bp.current_user_id()
    if uid is None:
        return common.err("请先登录", "auth", 401)
    portal_db.att_close_expired()
    s = portal_db.att_open_session()
    can: bool = _can_manage(uid)
    out: dict[str, Any] = {"can_manage": can,
                           "server_ts": time.time(), "session": None}
    if can:
        # 发起表单的名单分组下拉用（点名模块的分组，考勤复用）
        out["groups"] = [{"id": int(g["id"]), "name": str(g["name"]),
                          "count": int(g["active_count"])}
                         for g in portal_db.roster_groups()]
    if s is not None:
        out["session"] = _session_view(s)
        out["session"]["checked_in"] = portal_db.att_has_record(int(s["id"]), uid)
        out["session"]["record_count"] = len(portal_db.att_session_records(int(s["id"])))
    return common.ok(out)


# ============================================================
# 发起 / 结束（attendance.manage）
# ============================================================
@bp.route("/api/attendance/create", methods=["POST"])
@common.api_wrap
@require_perm("attendance.manage")
def api_create():
    body: dict = request.get_json(silent=True) or {}
    uid: int = int(auth_bp.current_user_id())
    title: str = str(body.get("title", "")).strip()[:MAX_TITLE_LEN] or "日常考勤"
    try:
        lat: float = float(body.get("lat"))
        lng: float = float(body.get("lng"))
        minutes: float = float(body.get("minutes", DEFAULT_DURATION_MIN))
        group_id: int = int(body.get("group_id", 0) or 0)
    except (TypeError, ValueError):
        return common.err("定位参数非法")
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return common.err("定位参数越界")
    if minutes not in DURATION_CHOICES:
        minutes = DEFAULT_DURATION_MIN
    if group_id > 0 and not _group_name(group_id):
        return common.err("名单分组不存在")
    if group_id < 0:
        group_id = 0

    portal_db.att_close_expired()
    if portal_db.att_open_session() is not None:
        return common.err("已有进行中的考勤，请先结束")

    sid: int = portal_db.att_create_session(title, lat, lng, RADIUS_M, uid,
                                            minutes, group_id)
    portal_db.add_audit(uid, "attendance.create", str(sid),
                        f"title={title} minutes={minutes} group_id={group_id}",
                        common.client_ip())
    return common.ok({"session_id": sid})


@bp.route("/api/attendance/close", methods=["POST"])
@common.api_wrap
@require_perm("attendance.manage")
def api_close():
    body: dict = request.get_json(silent=True) or {}
    try:
        sid: int = int(body.get("session_id"))
    except (TypeError, ValueError):
        return common.err("参数非法")
    if portal_db.att_get_session(sid) is None:
        return common.err("考勤不存在", "not_found", 404)
    portal_db.att_close_session(sid)
    portal_db.add_audit(int(auth_bp.current_user_id()), "attendance.close", str(sid),
                        "", common.client_ip())
    return common.ok()


# ============================================================
# 签到（登录用户；服务端 Haversine 复核）
# ============================================================
@bp.route("/api/attendance/checkin", methods=["POST"])
@common.api_wrap
def api_checkin():
    uid: Optional[int] = auth_bp.current_user_id()
    if uid is None:
        return common.err("请先登录", "auth", 401)
    if not common.rate_check(f"att:{uid}"):
        return common.err("操作太频繁", "rate_limit", 429)

    body: dict = request.get_json(silent=True) or {}
    try:
        lat: float = float(body.get("lat"))
        lng: float = float(body.get("lng"))
        accuracy: float = max(0.0, float(body.get("accuracy", 0.0) or 0.0))
    except (TypeError, ValueError):
        return common.err("定位参数非法")
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return common.err("定位参数越界")

    portal_db.att_close_expired()
    s = portal_db.att_open_session()
    if s is None:
        return common.err("当前没有进行中的考勤")
    sid: int = int(s["id"])

    if portal_db.att_has_record(sid, uid):
        return common.ok({"already": True, "msg": "本场已签到"})

    # 服务端复核距离（前端展示不算数）；GPS 漂移容差：减去上报精度（封顶 30m）
    dist: float = portal_db.haversine_m(float(s["lat"]), float(s["lng"]), lat, lng)
    effective: float = dist - min(accuracy, ACCURACY_TOLERANCE_M)
    if effective > float(s["radius_m"]):
        return common.err(f"距考勤点约 {int(dist)} 米，超出 {int(s['radius_m'])} 米范围")

    portal_db.att_add_record(sid, uid, lat, lng, dist)
    return common.ok({"distance_m": round(dist, 1)})


# ============================================================
# 名单与历史
# ============================================================
@bp.route("/api/attendance/records", methods=["GET"])
@common.api_wrap
@require_perm("attendance.manage")
def api_records():
    try:
        sid: int = int(request.args.get("session_id", "0"))
    except ValueError:
        return common.err("参数非法")
    s = portal_db.att_get_session(sid)
    if s is None:
        return common.err("考勤不存在", "not_found", 404)
    rows = portal_db.att_session_records(sid)
    out: dict[str, Any] = {
        "session": _session_view(s, with_coords=False),
        "records": [{"nickname": r["nickname"], "distance_m": round(float(r["distance_m"]), 1),
                     "manual": bool(r["manual"]) if "manual" in r.keys() else False,
                     "created_at": float(r["created_at"])} for r in rows],
    }
    # 关联了名单分组：附应到对账（已签/未签/是否有账号），未签到排在前面
    gid: int = int(s["group_id"]) if "group_id" in s.keys() else 0
    if gid > 0:
        roster = portal_db.att_session_roster(sid, gid)
        out["roster"] = [
            {"member_id": int(r["member_id"]), "name": str(r["name"]),
             "has_account": r["user_id"] is not None,
             "checked_in": r["record_id"] is not None,
             "manual": bool(r["manual"]) if r["record_id"] is not None else False,
             "checked_at": float(r["checked_at"]) if r["checked_at"] is not None else None}
            for r in roster]
        out["unsigned_count"] = sum(1 for x in out["roster"] if not x["checked_in"])
    return common.ok(out)


# ============================================================
# 手动补签（attendance.manage；把未到同学从「未签到」名单里消掉）
# ============================================================
@bp.route("/api/attendance/manual_checkin", methods=["POST"])
@common.api_wrap
@require_perm("attendance.manage")
def api_manual_checkin():
    body: dict = request.get_json(silent=True) or {}
    try:
        sid: int = int(body.get("session_id"))
        member_id: int = int(body.get("member_id"))
    except (TypeError, ValueError):
        return common.err("参数非法")

    s = portal_db.att_get_session(sid)
    if s is None:
        return common.err("考勤不存在", "not_found", 404)
    gid: int = int(s["group_id"]) if "group_id" in s.keys() else 0
    if gid <= 0:
        return common.err("本场考勤没有关联名单分组")

    # 在该场对账名单里找到这位同学（顺带完成归属校验）
    target = None
    for r in portal_db.att_session_roster(sid, gid):
        if int(r["member_id"]) == member_id:
            target = r
            break
    if target is None:
        return common.err("该同学不在本场考勤名单里", "not_found", 404)
    if target["record_id"] is not None:
        return common.err("该同学已签到，无需补签")
    if target["user_id"] is None:
        return common.err("「" + str(target["name"]) + "」还没有注册账号，"
                          "需先用真实姓名注册后才能补签")

    portal_db.att_add_record(sid, int(target["user_id"]),
                             float(s["lat"]), float(s["lng"]), 0.0, manual=True)
    op_uid: int = int(auth_bp.current_user_id())
    portal_db.add_audit(op_uid, "attendance.manual_checkin", str(sid),
                        f"member={target['name']}", common.client_ip())
    return common.ok({"name": str(target["name"])})


@bp.route("/api/attendance/history", methods=["GET"])
@common.api_wrap
@require_perm("attendance.manage")
def api_history():
    portal_db.att_close_expired()   # 顺手做 2 天留存清理
    rows = portal_db.att_recent_sessions(20)
    return common.ok({"sessions": [
        {"id": int(r["id"]), "title": str(r["title"]), "status": str(r["status"]),
         "creator_name": str(r["creator_name"]), "count": int(r["cnt"]),
         "group_id": int(r["group_id"]) if "group_id" in r.keys() else 0,
         "group_name": _group_name(int(r["group_id"]))
                       if "group_id" in r.keys() and int(r["group_id"]) > 0 else "",
         "created_at": float(r["created_at"]), "ends_at": float(r["ends_at"])}
        for r in rows]})


@bp.route("/api/attendance/my", methods=["GET"])
@common.api_wrap
def api_my():
    uid: Optional[int] = auth_bp.current_user_id()
    if uid is None:
        return common.err("请先登录", "auth", 401)
    rows = portal_db.att_my_records(uid, 20)
    return common.ok({"records": [
        {"title": str(r["title"]), "distance_m": round(float(r["distance_m"]), 1),
         "created_at": float(r["created_at"])} for r in rows]})
