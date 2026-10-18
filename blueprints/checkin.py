"""
blueprints/checkin.py —— 打卡挑战（/checkin + /api/checkin/* + 后台管理）
======================================================================
规则（页面明示，前后端同一套）：
  · 每用户每挑战每天最多打卡 1 次 —— checkin_records 主键
    (user_id, challenge_id, cdate) 兜底，重复提交天然幂等。
  · 连续天数 streak：今天已打卡从今天往前数；今天未打卡从昨天往前数；
    昨天也没打卡 → 清零为 0（断签清零）。
  · 挑战 end_date 过后自动归档（archived=1）：访问 /checkin 时惰性触发，
    幂等且不依赖定时任务（免费版定时不可靠）。
"""

from __future__ import annotations

import calendar as _cal
from datetime import datetime, timedelta
from typing import Any, Optional

from flask import Blueprint, render_template, request

import common
import portal_db
from blueprints import auth as auth_bp
from blueprints.admin import require_perm

bp: Blueprint = Blueprint("checkin", __name__)

# 挑战图标只能从这个集合里选（前端渲染 icon 用，避免任意字符串）
ICON_CHOICES: list[str] = ["target", "sun", "heart", "zap", "star", "trophy",
                          "flag", "gift", "clock", "grid", "check", "bell"]


def challenge_view(ch: Any, uid: Optional[int], today: str) -> dict[str, Any]:
    """挑战 → 前端视图（含我今天是否已打卡 + 我的 streak）。"""
    cid: int = int(ch["id"])
    dates: set[str] = portal_db.ck_dates(uid, cid) if uid else set()
    return {
        "id": cid, "name": str(ch["name"]), "icon": str(ch["icon"]),
        "start_date": str(ch["start_date"]), "end_date": str(ch["end_date"]),
        "archived": bool(ch["archived"]),
        "ongoing": str(ch["start_date"]) <= today <= str(ch["end_date"]),
        "checked_today": (today in dates) if uid else False,
        "my_streak": portal_db.calc_streak(dates, today) if uid else 0,
        "my_total": len(dates) if uid else 0,
        "participants": 0,   # 由列表接口统一填充，避免 N+1
    }


# ============================================================
# 页面
# ============================================================
@bp.route("/checkin")
def checkin_page():
    """打卡挑战页（渲染前先做一次自动归档，保证列表是最新的）。"""
    today: str = common.bj_date_str()
    try:
        portal_db.chal_archive_expired(today)
    except Exception:  # noqa: BLE001
        pass
    return render_template("checkin.html",
                           logged_in=auth_bp.current_user_id() is not None,
                           icon_choices=ICON_CHOICES,
                           today=today)


# ============================================================
# API
# ============================================================
@bp.route("/api/checkin/challenges")
@common.api_wrap
def api_challenges():
    """
    进行中挑战列表（含我的打卡状态与 streak）。
    参数：archived=1 可查看归档。返回：{ok, list, today, logged_in}
    权限：公开浏览；打卡状态仅对当前登录用户填充。
    """
    today: str = common.bj_date_str()
    uid: Optional[int] = auth_bp.current_user_id()
    incl: bool = (request.args.get("archived", "") == "1")
    # 惰性自动归档：免费版无可靠定时任务，因此每次读取用户端列表时顺手归档
    # （幂等：只有 archived=0 且 end_date < today 的才会被改，重复调用无副作用）
    if not incl:
        try:
            portal_db.chal_archive_expired(today)
        except Exception:  # noqa: BLE001
            pass
    rows = portal_db.chal_list(include_archived=incl)
    out: list[dict[str, Any]] = []
    for ch in rows:
        v = challenge_view(ch, uid, today)
        v["participants"] = len(portal_db.ck_leaderboard(int(ch["id"]), today, limit=1000))
        out.append(v)
    return common.ok({"list": out, "today": today, "logged_in": uid is not None})


@bp.route("/api/checkin/do", methods=["POST"])
@common.api_wrap
def api_do():
    """
    今日打卡。参数：{challenge_id}
    返回：{ok, already, date, streak}
    权限：需登录。每天每挑战一次（UNIQUE 兜底 → 重复请求幂等）。
    """
    uid: Optional[int] = auth_bp.current_user_id()
    if uid is None:
        return common.err("登录后才能打卡", "auth", 401)
    if not common.rate_check(f"ck:{uid}", limit=6):
        return common.err("操作太频繁", "rate", 429)
    body: dict = request.get_json(silent=True) or {}
    try:
        cid: int = int(body.get("challenge_id") or 0)
    except (TypeError, ValueError):
        return common.err("challenge_id 不合法")
    ch = portal_db.chal_get(cid)
    if ch is None:
        return common.err("挑战不存在", "not_found", 404)
    today: str = common.bj_date_str()
    if bool(ch["archived"]) or today > str(ch["end_date"]) or today < str(ch["start_date"]):
        return common.err("该挑战当前不在进行期内")
    added: bool = portal_db.ck_add(uid, cid, today)     # 幂等
    portal_db.add_audit(uid, "checkin", f"{cid}/{today}", "",
                        request.remote_addr or "")
    return common.ok({"already": not added, "date": today,
                      "streak": portal_db.ck_streak(uid, cid, today)})


@bp.route("/api/checkin/calendar")
@common.api_wrap
def api_calendar():
    """
    我的月历热力图数据。参数：year、month（默认北京当月）、challenge_id（可选）
    返回：{ok, year, month, days:[{date, count, checked}...], total_days, active_days}
    权限：需登录（只返回自己的数据）。
    """
    uid: Optional[int] = auth_bp.current_user_id()
    if uid is None:
        return common.err("请先登录", "auth", 401)
    today: str = common.bj_date_str()
    now_bj = common.beijing_now()
    try:
        year: int = int(request.args.get("year", str(now_bj.year)))
        month: int = int(request.args.get("month", str(now_bj.month)))
    except ValueError:
        year, month = now_bj.year, now_bj.month
    if not (2000 <= year <= 2100 and 1 <= month <= 12):
        return common.err("年月不合法")

    # 统计口径：不传 challenge_id 时聚合"我在所有挑战"的打卡（个人中心用）
    prefix: str = f"{year:04d}-{month:02d}-"
    counts: dict[str, int] = {}
    for d in portal_db.ck_my_all_dates(uid):
        if d.startswith(prefix):
            counts[d] = counts.get(d, 0) + 1
    cid_raw: str = (request.args.get("challenge_id", "") or "").strip()
    if cid_raw.isdigit():
        for d in portal_db.ck_dates(uid, int(cid_raw)):
            if d.startswith(prefix):
                counts[d] = counts.get(d, 0) + 1

    total_days: int = _cal.monthrange(year, month)[1]
    days: list[dict[str, Any]] = []
    for i in range(1, total_days + 1):
        ds: str = f"{year:04d}-{month:02d}-{i:02d}"
        days.append({"date": ds, "count": counts.get(ds, 0),
                     "checked": counts.get(ds, 0) > 0,
                     "is_today": ds == today,
                     "future": ds > today})
    return common.ok({"year": year, "month": month, "days": days,
                      "total_days": total_days, "active_days": len(counts)})


@bp.route("/api/checkin/board")
@common.api_wrap
def api_board():
    """
    班级排行榜（streak 前 10 + 固定的"我的名次"）。
    参数：challenge_id
    返回：{ok, top:[...], me:{rank,streak,total,of} | null}
    权限：公开浏览。
    """
    cid_raw: str = (request.args.get("challenge_id", "") or "").strip()
    if not cid_raw.isdigit():
        return common.err("challenge_id 不合法")
    cid: int = int(cid_raw)
    if portal_db.chal_get(cid) is None:
        return common.err("挑战不存在", "not_found", 404)
    today: str = common.bj_date_str()
    uid: Optional[int] = auth_bp.current_user_id()
    return common.ok({
        "top": portal_db.ck_leaderboard(cid, today, limit=10),
        "me": portal_db.ck_my_rank(uid, cid, today) if uid else None,
        "today": today,
    })


# ============================================================
# 后台（checkin.manage）
# ============================================================
@bp.route("/api/admin/challenges")
@require_perm("checkin.manage")
@common.api_wrap
def api_admin_list():
    """挑战列表（含归档）。权限：checkin.manage"""
    today: str = common.bj_date_str()
    portal_db.chal_archive_expired(today)
    rows = portal_db.chal_list(include_archived=True)
    return common.ok({"list": [{
        "id": int(r["id"]), "name": str(r["name"]), "icon": str(r["icon"]),
        "start_date": str(r["start_date"]), "end_date": str(r["end_date"]),
        "archived": bool(r["archived"]),
        "participants": len(portal_db.ck_leaderboard(int(r["id"]), today, limit=1000)),
    } for r in rows], "today": today, "icons": ICON_CHOICES})


@bp.route("/api/admin/challenges/create", methods=["POST"])
@require_perm("checkin.manage")
@common.api_wrap
def api_admin_create():
    """
    创建挑战。参数：{name, icon, start_date, end_date}（日期为北京日期 YYYY-MM-DD）
    权限：checkin.manage。写审计。
    """
    body: dict = request.get_json(silent=True) or {}
    name: str = str(body.get("name", "")).strip()[:30]
    icon: str = str(body.get("icon", "target")).strip()
    start_date: str = str(body.get("start_date", "")).strip()[:10]
    end_date: str = str(body.get("end_date", "")).strip()[:10]
    if not name:
        return common.err("请填写挑战名称")
    if icon not in ICON_CHOICES:
        icon = "target"
    try:
        s = datetime.strptime(start_date, "%Y-%m-%d")
        e = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        return common.err("日期格式应为 YYYY-MM-DD")
    if e < s:
        return common.err("结束日期不能早于开始日期")
    cid: int = portal_db.chal_create(name, icon, start_date, end_date,
                                     auth_bp.current_user_id())
    portal_db.add_audit(auth_bp.current_user_id(), "challenge_create", name,
                        f"id={cid} {start_date}~{end_date}", request.remote_addr or "")
    return common.ok({"id": cid})


@bp.route("/api/admin/challenges/delete", methods=["POST"])
@require_perm("checkin.manage")
@common.api_wrap
def api_admin_delete():
    """删除挑战（连带删除打卡记录）。参数：{id}。权限：checkin.manage"""
    body: dict = request.get_json(silent=True) or {}
    try:
        cid: int = int(body.get("id") or 0)
    except (TypeError, ValueError):
        return common.err("id 不合法")
    portal_db.chal_delete(cid)
    portal_db.add_audit(auth_bp.current_user_id(), "challenge_delete", str(cid), "",
                        request.remote_addr or "")
    return common.ok()


@bp.route("/api/admin/challenges/archive", methods=["POST"])
@require_perm("checkin.manage")
@common.api_wrap
def api_admin_archive():
    """
    手动触发归档（把 end_date 已过的挑战标记归档）。权限：checkin.manage
    返回：{ok, archived}
    """
    n: int = portal_db.chal_archive_expired(common.bj_date_str())
    portal_db.add_audit(auth_bp.current_user_id(), "challenge_archive", "expired",
                        f"count={n}", request.remote_addr or "")
    return common.ok({"archived": n})
