"""
blueprints/digest.py —— 每日总结（/digest + /api/digest/* + 后台总结管理）
=======================================================================
时间逻辑（北京时间；实现见 common.digest_spec 与 digest_service）：
  • 北京 00:00 → 【昨日总结】(type=yesterday, date=昨天, 窗口=昨天全天)
  • 北京 09:00 → 【今日总结】(type=today,     date=今天, 窗口=今天 00:00~09:00)
  • 0~9 点访问：今日总结卡显示"将于 09:00 更新"占位
  • 惰性兜底：访问时若"已过时刻却缺失" → 先同步 GitHub，再本地规则引擎补生成

页面/接口：
  GET  /digest                   每日总结页（两张卡 + 历史归档）
  GET  /api/digest/view          今日/昨日 + 归档列表（内部执行惰性兜底）
  GET  /api/digest/detail        指定 (type,date) 的完整要点
  POST /api/admin/digest/regenerate  手动重生成（news.manage）
  POST /api/admin/digest/edit        手动编辑要点（news.manage，标记 edited）
"""

from __future__ import annotations

from typing import Any, Optional

from flask import Blueprint, render_template, request

import common
import digest_service
import portal_db
from blueprints import auth as auth_bp
from blueprints.admin import require_perm

bp: Blueprint = Blueprint("digest", __name__)


@bp.route("/digest")
def digest_page():
    """
    每日总结页面。
    渲染前先跑一次惰性兜底（幂等、只在缺失时活），保证页面永不空白。
    """
    digest_service.ensure_digests()
    return render_template(
        "digest.html",
        ready_at=common.today_digest_ready_at(),
        yesterday=digest_service.digest_view("yesterday"),
        today=digest_service.digest_view("today"),
        archive=digest_service.archive(limit=40),
    )


@bp.route("/api/digest/view")
@common.api_wrap
def api_digest_view():
    """
    今日/昨日总结 + 历史归档。
    返回：{ok, yesterday, today, archive, ready_at}
    权限：公开。会执行惰性兜底（幂等）。
    """
    digest_service.ensure_digests()
    return common.ok({
        "yesterday": digest_service.digest_view("yesterday"),
        "today": digest_service.digest_view("today"),
        "archive": digest_service.archive(limit=40),
        "ready_at": common.today_digest_ready_at(),
    })


@bp.route("/api/digest/detail")
@common.api_wrap
def api_digest_detail():
    """
    取指定总结详情。
    参数：type=today|yesterday、date=YYYY-MM-DD
    返回：{ok, digest}（不存在时 digest.exists=false）
    权限：公开。
    """
    ntype: str = (request.args.get("type", "") or "").strip()
    ndate: str = (request.args.get("date", "") or "").strip()
    if ntype not in ("today", "yesterday"):
        return common.err("type 必须为 today 或 yesterday")
    if len(ndate) != 10:
        return common.err("date 格式应为 YYYY-MM-DD")
    row = portal_db.get_digest(ntype, ndate)
    if row is None:
        return common.ok({"digest": {"exists": False, "type": ntype, "date": ndate}})
    import json
    try:
        payload: dict[str, Any] = json.loads(str(row["payload"]))
    except Exception:  # noqa: BLE001
        payload = {"type": ntype, "date": ndate, "points": [], "keywords": [],
                   "news_count": 0}
    payload.update({"exists": True, "generator": str(row["generator"]),
                    "edited": bool(row["edited"]),
                    "updated_at": float(row["updated_at"])})
    return common.ok({"digest": payload})


# ============================================================
# 后台：总结管理
# ============================================================
@bp.route("/api/admin/digest/regenerate", methods=["POST"])
@require_perm("news.manage")
@common.api_wrap
def api_digest_regenerate():
    """
    手动重生成。参数：{type, date?}。权限：news.manage。写审计。
    注意：会覆盖同 (type,date) 并把 edited 置回 0。
    """
    body: dict = request.get_json(silent=True) or {}
    ntype: str = str(body.get("type", "")).strip()
    ndate: str = str(body.get("date", "")).strip() or ""
    if ntype not in ("today", "yesterday"):
        return common.err("type 必须为 today 或 yesterday")
    if ndate and len(ndate) != 10:
        return common.err("date 格式应为 YYYY-MM-DD")
    payload: dict[str, Any] = digest_service.regenerate(ntype, ndate or None,
                                                       generator="manual")
    portal_db.add_audit(auth_bp.current_user_id(), "digest_regenerate",
                        f"{ntype}/{payload.get('date')}",
                        f"points={len(payload.get('points') or [])}",
                        request.remote_addr or "")
    return common.ok({"digest": payload})


@bp.route("/api/admin/digest/edit", methods=["POST"])
@require_perm("news.manage")
@common.api_wrap
def api_digest_edit():
    """
    手动编辑要点文案。参数：{type, date, points:[文案...], keywords:[...]}
    权限：news.manage。写入后 edited=1，GitHub 同步不再覆盖。
    """
    body: dict = request.get_json(silent=True) or {}
    ntype: str = str(body.get("type", "")).strip()
    ndate: str = str(body.get("date", "")).strip()
    points: list[str] = [str(x) for x in (body.get("points") or [])]
    keywords: list[str] = [str(x) for x in (body.get("keywords") or [])]
    if ntype not in ("today", "yesterday") or len(ndate) != 10:
        return common.err("参数不合法（type/date）")
    try:
        payload: dict[str, Any] = digest_service.save_edited(ntype, ndate, points, keywords)
    except ValueError as e:
        return common.err(str(e))
    portal_db.add_audit(auth_bp.current_user_id(), "digest_edit", f"{ntype}/{ndate}",
                        f"points={len(points)}", request.remote_addr or "")
    return common.ok({"digest": payload})


@bp.route("/api/admin/digest/list")
@require_perm("news.manage")
@common.api_wrap
def api_digest_list():
    """全部总结归档（后台管理列表）。权限：news.manage"""
    digest_service.ensure_digests()
    return common.ok({"list": digest_service.archive(limit=200)})
