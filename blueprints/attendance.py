"""
blueprints/attendance.py —— 日常考勤（定位签到，/attendance + /api/attendance/*）
==============================================================================
规则（页面明示，前后端同一套）：
  · 发起人需 attendance.manage 权限；发起时取发起人实时定位为考勤点；
  · 签到半径默认 100 米（GPS 漂移容差：有效距离 = 实测距离 - min(上报精度, 30m)）；
  · 距离判定【服务端复核】（Haversine），前端只作体验展示；
  · 同一时刻只允许一场进行中的考勤；每人每场只能签一次（UNIQUE 兜底）；
  · 到期会话惰性关闭，不依赖定时任务。
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


def _session_view(s: Any, with_coords: bool = True) -> dict[str, Any]:
    v: dict[str, Any] = {
        "id": int(s["id"]), "title": str(s["title"]),
        "radius_m": int(s["radius_m"]), "status": str(s["status"]),
        "creator_name": str(s["creator_name"]),
        "ends_at": float(s["ends_at"]), "created_at": float(s["created_at"]),
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
    out: dict[str, Any] = {"can_manage": _can_manage(uid),
                           "server_ts": time.time(), "session": None}
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
    except (TypeError, ValueError):
        return common.err("定位参数非法")
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return common.err("定位参数越界")
    if minutes not in DURATION_CHOICES:
        minutes = DEFAULT_DURATION_MIN

    portal_db.att_close_expired()
    if portal_db.att_open_session() is not None:
        return common.err("已有进行中的考勤，请先结束")

    sid: int = portal_db.att_create_session(title, lat, lng, RADIUS_M, uid, minutes)
    portal_db.add_audit(uid, "attendance.create", str(sid),
                        f"title={title} minutes={minutes}", common.client_ip())
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
    return common.ok({
        "session": _session_view(s, with_coords=False),
        "records": [{"nickname": r["nickname"], "distance_m": round(float(r["distance_m"]), 1),
                     "created_at": float(r["created_at"])} for r in rows],
    })


@bp.route("/api/attendance/history", methods=["GET"])
@common.api_wrap
@require_perm("attendance.manage")
def api_history():
    rows = portal_db.att_recent_sessions(20)
    return common.ok({"sessions": [
        {"id": int(r["id"]), "title": str(r["title"]), "status": str(r["status"]),
         "creator_name": str(r["creator_name"]), "count": int(r["cnt"]),
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
