"""
blueprints/quote.py —— 每日一句（/quote + /api/quote/* + 后台审核）
==================================================================
要点：
1. **一天一换的轮换规则**：取北京日期的序数对"已审核句子总数"取模，
   再按 id 升序取第 offset 条 —— 同一天内多次访问结果稳定，跨天自动更换。
   实现：offset = date.toordinal() % approved_count
2. 投稿：登录用户提交（≤100 字 + 可选署名）→ 一律进 pending 队列，先审后显。
3. 点赞：quote_likes 主键 (quote_id,user_id) → 每人每句一次，可取消（幂等）。
4. quote.moderate 后台审核（approved / rejected）与删除，全部写审计。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from flask import Blueprint, render_template, request

import common
import portal_db
from blueprints import auth as auth_bp
from blueprints.admin import require_perm

bp: Blueprint = Blueprint("quote", __name__)

MAX_CONTENT: int = 100


def today_quote() -> Optional[dict[str, Any]]:
    """
    取"今天的每日一句"。
    轮换：offset = 北京日期序数 % 已审核总数；无已审核句子时返回 None。
    """
    total: int = portal_db.quote_approved_count()
    if total <= 0:
        return None
    try:
        ordinal: int = datetime.strptime(common.bj_date_str(), "%Y-%m-%d").toordinal()
    except ValueError:
        ordinal = 0
    row = portal_db.quote_by_offset(ordinal % total)
    if row is None:
        return None
    return {"id": int(row["id"]), "content": str(row["content"]),
            "author": str(row["author"]) or "匿名同学",
            "like_count": int(row["like_count"])}


# ============================================================
# 页面
# ============================================================
@bp.route("/quote")
def quote_page():
    """每日一句列表页（含投稿入口）。"""
    return render_template("quote.html",
                           logged_in=auth_bp.current_user_id() is not None,
                           max_content=MAX_CONTENT)


# ============================================================
# API
# ============================================================
@bp.route("/api/quote/today")
@common.api_wrap
def api_today():
    """
    今日一句（首页横幅用）。返回：{ok, item | null, total}
    权限：公开；liked 仅按当前登录用户填充。
    """
    item = today_quote()
    if item is not None:
        uid: Optional[int] = auth_bp.current_user_id()
        if uid is not None:
            # 复用列表接口的 liked 计算（只查当前用户，limit=1 开销可忽略）
            one = [x for x in portal_db.quote_list(status="approved", limit=200,
                                                   viewer_id=uid)
                   if x["id"] == item["id"]]
            item["liked"] = bool(one and one[0]["liked"])
        else:
            item["liked"] = False
    return common.ok({"item": item, "total": portal_db.quote_approved_count()})


@bp.route("/api/quote/list")
@common.api_wrap
def api_list():
    """
    已审核句子列表（按点赞降序）。返回：{ok, list, total}
    权限：公开（liked 仅按当前登录用户填充）。
    """
    uid: Optional[int] = auth_bp.current_user_id()
    rows = portal_db.quote_list(status=None, limit=200, viewer_id=uid)
    return common.ok({"list": rows, "total": portal_db.quote_approved_count()})


@bp.route("/api/quote/mine")
@common.api_wrap
def api_mine():
    """我的投稿（含 pending/rejected，个人中心用）。返回：{ok, list}。权限：需登录。"""
    uid: Optional[int] = auth_bp.current_user_id()
    if uid is None:
        return common.err("请先登录", "auth", 401)
    # 依次取三种状态并合并（purpose: 展示"审核中/已通过/未通过"）
    out: list[dict[str, Any]] = []
    for st in ("pending", "approved", "rejected"):
        out.extend(portal_db.quote_list(status=st, limit=50, viewer_id=uid,
                                        user_id=uid))
    out.sort(key=lambda x: -x["created_at"])
    return common.ok({"list": out})


@bp.route("/api/quote/submit", methods=["POST"])
@common.api_wrap
def api_submit():
    """
    投稿每日一句。参数：{content, author?}
    返回：{ok, id, status:"pending"}
    权限：需登录。≤100 字；一律先审后显。
    """
    uid: Optional[int] = auth_bp.current_user_id()
    if uid is None:
        return common.err("登录后才能投稿", "auth", 401)
    if not common.rate_check_window(f"quote:{uid}", 3, 60.0):
        return common.err("投稿太频繁：每分钟最多 3 次", "rate", 429)
    body: dict = request.get_json(silent=True) or {}
    content: str = str(body.get("content", "")).strip()[:MAX_CONTENT]
    author: str = str(body.get("author", "")).strip()[:20]
    if len(content) < 2:
        return common.err("内容太短啦")
    # 复用吐槽墙同一套敏感词过滤，保证全站口径一致
    import sensitive
    hit: Optional[str] = sensitive.check(content)
    if hit:
        return common.err("内容包含不合适词汇，请修改后重试", "sensitive")
    qid: int = portal_db.quote_create(content, author, uid)
    portal_db.add_audit(uid, "quote_submit", str(qid), "", request.remote_addr or "")
    return common.ok({"id": qid, "status": "pending",
                      "message": "已提交，等待管理员审核"})


@bp.route("/api/quote/like", methods=["POST"])
@common.api_wrap
def api_like():
    """
    点赞/取消（幂等）。参数：{id}。返回：{ok, liked, like_count}。权限：需登录。
    """
    uid: Optional[int] = auth_bp.current_user_id()
    if uid is None:
        return common.err("登录后才能点赞", "auth", 401)
    if not common.rate_check(f"qlike:{uid}", limit=10):
        return common.err("操作太频繁", "rate", 429)
    body: dict = request.get_json(silent=True) or {}
    try:
        qid: int = int(body.get("id") or 0)
    except (TypeError, ValueError):
        return common.err("id 不合法")
    row = portal_db.quote_get(qid)
    if row is None or str(row["status"]) != "approved":
        return common.err("句子不存在或未通过审核", "not_found", 404)
    liked, count = portal_db.quote_like(qid, uid)
    return common.ok({"liked": liked, "like_count": count})


# ============================================================
# 后台（quote.moderate）
# ============================================================
@bp.route("/api/admin/quotes")
@require_perm("quote.moderate")
@common.api_wrap
def api_admin_list():
    """
    审核队列。参数：status=pending|approved|rejected|all（默认 pending）
    权限：quote.moderate
    """
    status: str = (request.args.get("status", "pending") or "pending").strip()
    if status == "all":
        rows: list[dict[str, Any]] = []
        for st in ("pending", "approved", "rejected"):
            rows.extend(portal_db.quote_list(status=st, limit=200))
        rows.sort(key=lambda x: -x["created_at"])
    else:
        rows = portal_db.quote_list(status=status, limit=300)
    return common.ok({"list": rows, "pending": portal_db.quote_pending_count(),
                      "approved": portal_db.quote_approved_count()})


@bp.route("/api/admin/quotes/review", methods=["POST"])
@require_perm("quote.moderate")
@common.api_wrap
def api_admin_review():
    """
    审核。参数：{id, action: approve|reject}
    权限：quote.moderate。写审计。
    """
    body: dict = request.get_json(silent=True) or {}
    try:
        qid: int = int(body.get("id") or 0)
    except (TypeError, ValueError):
        return common.err("id 不合法")
    action: str = str(body.get("action", "")).strip()
    if action not in ("approve", "reject"):
        return common.err("action 必须是 approve 或 reject")
    if portal_db.quote_get(qid) is None:
        return common.err("句子不存在", "not_found", 404)
    status: str = "approved" if action == "approve" else "rejected"
    portal_db.quote_review(qid, status, auth_bp.current_user_id())
    portal_db.add_audit(auth_bp.current_user_id(), f"quote_{action}", str(qid), "",
                        request.remote_addr or "")
    return common.ok({"status": status})


@bp.route("/api/admin/quotes/delete", methods=["POST"])
@require_perm("quote.moderate")
@common.api_wrap
def api_admin_delete():
    """删除句子。参数：{id}。权限：quote.moderate。写审计。"""
    body: dict = request.get_json(silent=True) or {}
    try:
        qid: int = int(body.get("id") or 0)
    except (TypeError, ValueError):
        return common.err("id 不合法")
    portal_db.quote_delete(qid)
    portal_db.add_audit(auth_bp.current_user_id(), "quote_delete", str(qid), "",
                        request.remote_addr or "")
    return common.ok()
