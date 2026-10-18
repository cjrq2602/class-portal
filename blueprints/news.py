"""
blueprints/news.py —— 新闻中心（/news + /api/news/* + 后台新闻管理）
=================================================================
数据链路（关键平台约束的落地）：
  GitHub Actions（github_news_worker/）抓取 → 写入仓库 news/YYYY-MM-DD.json
  → 本站 news_sync.py 读取仓库（仅 api.github.com / raw.githubusercontent.com）
  → 入库 news_items（source+url 唯一去重）→ 页面展示
  因此"最新新闻"到可见的延迟 ≈ 抓取周期(20 分钟) + 缓存 TTL(≤5 分钟) ≈ 20~25 分钟。

页面/接口：
  GET  /news                    新闻中心页（标签一：时事新闻）
  GET  /api/news/list           分页列表（每页 30，可按来源筛选；内部按 TTL 触发同步）
  GET  /api/news/status         同步状态（"数据更新于 xx:xx"）
  GET  /api/admin/news/status   后台：同步状态 + 仓库配置（news.manage）
  POST /api/admin/news/sync     后台：手动同步（news.manage，记审计）
  GET  /api/admin/news/sources  后台：来源清单（news.manage）
"""

from __future__ import annotations

from typing import Any, Optional

from flask import Blueprint, render_template, request

import common
import news_sync
import portal_db
from blueprints import auth as auth_bp
from blueprints.admin import require_perm

bp: Blueprint = Blueprint("news", __name__)

PER_PAGE: int = 30  # 新闻每页条数（需求指定 30）


@bp.route("/news")
def news_page():
    """新闻中心页面。"""
    return render_template("news.html")


@bp.route("/api/news/list")
@common.api_wrap
def api_news_list():
    """
    新闻分页列表（时间倒序）。
    参数：page（默认 1）、source（可选，按来源筛选）
    返回：{ok, list:[{id,source,title,url,summary,published_at}], total, page, per_page, status}
    权限：公开。会顺带按 TTL 触发一次同步（失败不影响返回旧数据）。
    """
    try:
        page: int = max(1, int(request.args.get("page", "1") or 1))
    except ValueError:
        page = 1
    source: str = (request.args.get("source", "") or "").strip()

    # TTL 内直接命中缓存；过期则尝试同步（内部有进程锁与异常兜底）
    sync_result: dict[str, Any] = news_sync.sync_news()
    rows, total = portal_db.list_news(page=page, per_page=PER_PAGE,
                                      source=source or None)
    return common.ok({
        "list": [{
            "id": int(r["id"]), "source": str(r["source"]), "title": str(r["title"]),
            "url": str(r["url"]), "summary": str(r["summary"]),
            "published_at": float(r["published_at"]),
        } for r in rows],
        "total": total, "page": page, "per_page": PER_PAGE,
        "sources": [{"name": n, "count": c} for n, c in portal_db.news_sources()],
        "status": news_sync.sync_status(),
        "synced_now": not bool(sync_result.get("skipped")),
    })


@bp.route("/api/news/status")
@common.api_wrap
def api_news_status():
    """同步状态：{ok, last_sync_at, ok_flag, error, item_count, ttl, repo}。权限：公开。"""
    st: dict[str, Any] = news_sync.sync_status()
    return common.ok({"status": st})


# ============================================================
# 后台：新闻管理
# ============================================================
@bp.route("/api/admin/news/status")
@require_perm("news.manage")
@common.api_wrap
def api_admin_news_status():
    """后台同步状态（含仓库名与大分支，便于排查"仓库名写错"）。权限：news.manage"""
    st: dict[str, Any] = news_sync.sync_status()
    st["sources"] = [{"name": n, "count": c} for n, c in portal_db.news_sources()]
    return common.ok({"status": st})


@bp.route("/api/admin/news/sync", methods=["POST"])
@require_perm("news.manage")
@common.api_wrap
def api_admin_news_sync():
    """
    手动同步（忽略 TTL，仍受进程锁保护）。权限：news.manage。写审计。
    返回：{ok, added, digests_added, error, sha}
    """
    res: dict[str, Any] = news_sync.sync_news(force=True)
    portal_db.add_audit(auth_bp.current_user_id(), "news_sync", "",
                        f"added={res.get('added', 0)} digests={res.get('digests_added', 0)}"
                        f" error={res.get('error', '')}"[:150],
                        request.remote_addr or "")
    return common.ok({
        "added": int(res.get("added") or 0),
        "digests_added": int(res.get("digests_added") or 0),
        "error": str(res.get("error") or ""),
        "skipped_reason": str(res.get("reason") or ""),
        "status": news_sync.sync_status(),
    })


@bp.route("/api/admin/news/sources")
@require_perm("news.manage")
@common.api_wrap
def api_admin_news_sources():
    """本地已入库的来源与条数（用于确认哪些源真的抓到了数据）。权限：news.manage"""
    return common.ok({"list": [{"name": n, "count": c}
                               for n, c in portal_db.news_sources()]})
