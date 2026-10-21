"""
blueprints/politics.py —— 时政学习（/politics + /api/politics/*）
=================================================================
与新闻模块同构的第三条数据链路：
  GitHub Actions 抓取时政 RSS → 写仓库 politics/YYYY-MM-DD.json
  → 本站 news_sync.sync_politics() 读回 → 入库 politics_items
  → 每日 0 点/9 点（北京）由 Worker 生成时政解读 → politics_digests/
  → 站点同步入库 politics_digests（缺则本地规则引擎惰性兜底）。

页面/接口：
  GET  /politics                 时政学习页（三选项卡：新闻 / 解读 / 词典）
  GET  /api/politics/list        时政新闻分页（每页 30，可按来源筛选；按 TTL 触发同步）
  GET  /api/politics/digest      时政解读（type=today|yesterday，惰性兜底）
  GET  /api/politics/terms       词典搜索（q 命中词条/释义；names_only=1 只回词条名）
  GET  /api/politics/status      同步状态
  POST /api/admin/politics/sync  后台：手动同步（复用 news.manage 权限，记审计）
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, render_template, request

import common
import digest_service
import news_sync
import portal_db
from blueprints import auth as auth_bp
from blueprints.admin import require_perm

bp: Blueprint = Blueprint("politics", __name__)

PER_PAGE: int = 30


@bp.route("/politics")
def politics_page():
    """时政学习页（新闻 / 解读 / 词典 三选项卡，游客可读）。"""
    return render_template("politics.html")


# ============================================================
# 时政新闻
# ============================================================
@bp.route("/api/politics/list")
@common.api_wrap
def api_politics_list():
    """
    时政新闻分页列表（时间倒序）。
    参数：page（默认 1）、source（可选）
    返回：{ok, list, total, page, per_page, sources, status, synced_now}
    权限：公开。TTL 内用缓存，过期触发同步（失败降级旧数据）。
    """
    try:
        page: int = max(1, int(request.args.get("page", "1") or 1))
    except ValueError:
        page = 1
    source: str = (request.args.get("source", "") or "").strip()

    sync_result: dict[str, Any] = news_sync.sync_politics()
    rows, total = portal_db.list_politics(page=page, per_page=PER_PAGE,
                                          source=source or None)
    return common.ok({
        "list": [{
            "id": int(r["id"]), "source": str(r["source"]), "title": str(r["title"]),
            "url": str(r["url"]), "summary": str(r["summary"]),
            "published_at": float(r["published_at"]),
        } for r in rows],
        "total": total, "page": page, "per_page": PER_PAGE,
        "sources": [{"name": n, "count": c} for n, c in portal_db.politics_sources()],
        "status": news_sync.politics_status(),
        "synced_now": not bool(sync_result.get("skipped")),
    })


@bp.route("/api/politics/status")
@common.api_wrap
def api_politics_status():
    """同步状态。权限：公开。"""
    return common.ok({"status": news_sync.politics_status()})


# ============================================================
# 时政解读
# ============================================================
@bp.route("/api/politics/digest")
@common.api_wrap
def api_politics_digest():
    """
    时政解读。参数：type=today|yesterday（默认 today）
    返回：{ok, digest:{exists, ...payload}}；未到生成时刻返回 pending 占位。
    权限：公开。缺失且已过时刻时惰性兜底（先同步 Worker 产出，再本地规则生成）。
    """
    ntype: str = str(request.args.get("type", "today"))
    if ntype not in ("today", "yesterday"):
        ntype = "today"
    digest_service.ensure_politics_digests()
    return common.ok({"digest": digest_service.politics_digest_view(ntype)})


# ============================================================
# 时政词典
# ============================================================
@bp.route("/api/politics/terms")
@common.api_wrap
def api_politics_terms():
    """
    词典搜索。
    参数：q（关键词，命中词条名或释义）、category（分类）、names_only=1（只回词条名数组）
    返回：{ok, list:[{id,term,category,definition,example}], categories, total_terms}
    权限：公开。
    """
    if str(request.args.get("names_only", "")) == "1":
        return common.ok({"names": portal_db.politics_terms_names()})
    q: str = (request.args.get("q", "") or "").strip()[:30]
    category: str = (request.args.get("category", "") or "").strip()[:20]
    rows = portal_db.politics_terms_search(q=q, category=category)
    return common.ok({
        "list": [{
            "id": int(r["id"]), "term": str(r["term"]), "category": str(r["category"]),
            "definition": str(r["definition"]), "example": str(r["example"] or ""),
        } for r in rows],
        "categories": [{"name": n, "count": c}
                       for n, c in portal_db.politics_terms_categories()],
        "total_terms": portal_db.count_politics_terms(),
        "q": q, "category": category,
    })


# ============================================================
# 后台：手动同步（复用新闻管理权限）
# ============================================================
@bp.route("/api/admin/politics/sync", methods=["POST"])
@require_perm("news.manage")
@common.api_wrap
def api_admin_politics_sync():
    """手动同步时政（忽略 TTL，受进程锁保护）。权限：news.manage。写审计。"""
    res: dict[str, Any] = news_sync.sync_politics(force=True)
    portal_db.add_audit(auth_bp.current_user_id(), "politics_sync", "",
                        f"added={res.get('added', 0)} digests={res.get('digests_added', 0)}"
                        f" error={res.get('error', '')}"[:150],
                        common.client_ip())
    return common.ok({
        "added": int(res.get("added") or 0),
        "digests_added": int(res.get("digests_added") or 0),
        "error": str(res.get("error") or ""),
        "skipped_reason": str(res.get("reason") or ""),
        "status": news_sync.politics_status(),
    })
