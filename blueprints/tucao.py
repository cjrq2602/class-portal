"""
blueprints/tucao.py —— 吐槽墙（/tucao + /api/tucao/* + 后台吐槽审核/敏感词/审核模式）
====================================================================================
业务规则（对齐需求）：
1. 浏览公开；发帖/点赞/举报必须登录（游客可看不可写）；
2. 发帖 ≤300 字，内容按"归一化 + 脱敏"处理后再进入敏感词过滤，命中即拦截；
3. 身份三选一：real=真实昵称 / anon=随机匿名马甲（如"柠檬同学#47"）/ fixed=固定匿名；
   服务端始终存 user_id 用于风控与"我的帖子"，但【任何前台接口都不返回它】——
   列表只回 display_name（按 persona 决定的展示名），后台审核也只显示马甲。
4. 审核模式（site_settings.tucao_mode）：
     post_first （默认）先发后审 → 立即可见 status='ok'
     audit_first 先审后发      → status='pending'，仅作者本人可见
5. 点赞：tucao_likes 主键 (post_id,user_id) → 每人每帖一次，可取消（幂等）；
6. 举报：每人每帖一次（唯一索引），进后台队列；
7. 防刷：每用户每分钟最多 3 帖（DB 计数）+ 每秒请求限流（内存）；
8. 每页 20 楼；支持 按时间/按热度 排序；置顶帖恒在最前。

页面/接口：
  GET  /tucao                        吐槽墙页
  GET  /api/tucao/list              分页列表（20/页，sort=time|hot）
  POST /api/tucao/post              发帖 {content, persona}
  POST /api/tucao/like              点赞/取消 {post_id}
  POST /api/tucao/report            举报 {post_id, reason?}
  GET  /api/admin/tucao/list        审核队列（tucao.moderate）
  POST /api/admin/tucao/moderate    删/恢复/置顶/通过（tucao.moderate）
  GET  /api/admin/tucao/reports     举报队列（tucao.moderate）
  POST /api/admin/tucao/reports/handle  标记举报已处理（tucao.moderate）
  GET/POST/DELETE /api/admin/sensitive_words  敏感词库增删查（tucao.moderate）
  GET/POST /api/admin/tucao/settings          审核模式切换（tucao.moderate）
"""

from __future__ import annotations

import random
import re
import time
from typing import Any, Optional

from flask import Blueprint, render_template, request

import common
import portal_db
import sensitive
from blueprints import auth as auth_bp
from blueprints.admin import require_perm

bp: Blueprint = Blueprint("tucao", __name__)

PER_PAGE: int = 20            # 每页楼数
MAX_CONTENT: int = 300        # 单帖字数上限
POST_PER_MINUTE: int = 3      # 每用户每分钟发帖上限
ANON_ADJ: tuple[str, ...] = (
    "柠檬", "薄荷", "橘子", "葡萄", "西瓜", "草莓", "芝士", "奶盖", "拿铁",
    "布丁", "云朵", "晚风", "青柠", "海盐", "抹茶", "山竹", "柚柚", "小满",
)
MODE_KEY: str = "tucao_mode"
MODE_POST_FIRST: str = "post_first"    # 先发后审（默认）
MODE_AUDIT_FIRST: str = "audit_first"  # 先审后发

# 控制字符与多余空白清理（保留可读换行）
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def current_mode() -> str:
    """当前审核模式（默认先发后审）。"""
    mode: str = portal_db.setting_get(MODE_KEY, MODE_POST_FIRST)
    return mode if mode in (MODE_POST_FIRST, MODE_AUDIT_FIRST) else MODE_POST_FIRST


def make_anon_name() -> str:
    """随机马甲名：形如"柠檬同学#47"。"""
    return f"{random.choice(ANON_ADJ)}同学#{random.randint(10, 99)}"


def clean_content(text: str) -> str:
    """
    内容清洗：去控制字符、压缩三连以上换行、去首尾空白、截断到上限。
    注意：不在服务端做 HTML 转义（避免与前端 esc() 双重转义），
    XSS 防护统一由前端渲染时转义 + 后端拒绝 <script/javascript: 片段双保险。
    """
    s: str = _CTRL_RE.sub("", str(text or ""))
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()[:MAX_CONTENT]


def page_of() -> int:
    try:
        return max(1, int(request.args.get("page", "1") or 1))
    except ValueError:
        return 1


# ============================================================
# 页面
# ============================================================
@bp.route("/tucao")
def tucao_page():
    """吐槽墙页面（服务端注入登录态与审核模式，便于首屏正确渲染）。"""
    uid: Optional[int] = auth_bp.current_user_id()
    return render_template("tucao.html", max_content=MAX_CONTENT,
                           mode=current_mode(), logged_in=uid is not None)


# ============================================================
# 列表
# ============================================================
@bp.route("/api/tucao/list")
@common.api_wrap
def api_tucao_list():
    """
    分页列表。参数：page、sort=time|hot
    返回：{ok, list, total, page, per_page, mode}
    权限：公开（未登录看不到任何 pending，登录后能看到自己的 pending）。
    说明：返回值【不含 user_id】，只含 persona 决定的 display_name。
    """
    page: int = page_of()
    sort: str = (request.args.get("sort", "time") or "time").strip()
    if sort not in ("time", "hot"):
        sort = "time"
    uid: Optional[int] = auth_bp.current_user_id()
    rows, total = portal_db.tucao_list(page=page, per_page=PER_PAGE,
                                       sort=sort, viewer_id=uid)
    return common.ok({"list": rows, "total": total, "page": page,
                      "per_page": PER_PAGE, "mode": current_mode()})


# ============================================================
# 发帖
# ============================================================
@bp.route("/api/tucao/post", methods=["POST"])
@common.api_wrap
def api_tucao_post():
    """
    发帖。参数：{content, persona}（persona ∈ real|anon|fixed）
    返回：{ok, id, status, message}；status=pending 表示进入待审队列。
    权限：需登录（401=未登录）；每用户每分钟最多 3 帖。
    """
    uid: Optional[int] = auth_bp.current_user_id()
    if uid is None:
        return common.err("登录后才能发帖", "auth", 401)
    if not common.rate_check(f"tucao:{uid}", limit=POST_PER_MINUTE):
        return common.err("发帖太频繁，请稍后再试", "rate", 429)

    body: dict = request.get_json(silent=True) or {}
    content: str = clean_content(body.get("content", ""))
    persona: str = str(body.get("persona", "anon")).strip()
    if persona not in ("real", "anon", "fixed"):
        persona = "anon"

    if not content:
        return common.err("内容不能为空")
    if len(content) < 2:
        return common.err("内容太短啦")
    # 双保险：拒绝明显的脚本注入片段（渲染层还会再转义一次）
    low: str = content.lower()
    if "<script" in low or "javascript:" in low or "onerror=" in low:
        return common.err("内容包含不安全片段，请修改后重试")

    # 敏感词过滤（内置词库 + 管理员词库，命中即拦截）
    hit: Optional[str] = sensitive.check(content)
    if hit:
        portal_db.add_audit(uid, "tucao_blocked", hit,
                            f"len={len(content)}", request.remote_addr or "")
        return common.err("内容包含不合适词汇，请修改后重试", "sensitive")

    # 防刷：DB 计数（跨进程重启仍有效，比纯内存更稳）
    recent: int = portal_db.tucao_user_recent_count(uid, time.time() - 60)
    if recent >= POST_PER_MINUTE:
        return common.err("发帖太频繁：每分钟最多 3 条", "rate", 429)

    # 审核模式决定立即可见 or 待审
    mode: str = current_mode()
    status: str = "ok" if mode == MODE_POST_FIRST else "pending"
    anon_name: str = make_anon_name() if persona == "anon" else ""
    pid: int = portal_db.tucao_insert(uid, content, persona, anon_name, status)
    portal_db.add_audit(uid, "tucao_post", str(pid),
                        f"persona={persona} status={status}", request.remote_addr or "")
    return common.ok({"id": pid, "status": status,
                      "message": "已发布" if status == "ok" else "已提交，等待管理员审核"})


# ============================================================
# 点赞 / 举报
# ============================================================
@bp.route("/api/tucao/like", methods=["POST"])
@common.api_wrap
def api_tucao_like():
    """
    点赞 / 取消点赞（幂等：同一用户重复点同一帖只切换一次）。
    参数：{post_id}；返回：{ok, liked, like_count}
    权限：需登录。
    """
    uid: Optional[int] = auth_bp.current_user_id()
    if uid is None:
        return common.err("登录后才能点赞", "auth", 401)
    if not common.rate_check(f"like:{uid}", limit=10):
        return common.err("操作太频繁", "rate", 429)
    body: dict = request.get_json(silent=True) or {}
    try:
        post_id: int = int(body.get("post_id") or 0)
    except (TypeError, ValueError):
        return common.err("post_id 不合法")
    post = portal_db.tucao_get(post_id)
    if post is None or str(post["status"]) == "deleted":
        return common.err("帖子不存在", "not_found", 404)
    liked, count = portal_db.tucao_like(post_id, uid)
    return common.ok({"liked": liked, "like_count": count})


@bp.route("/api/tucao/report", methods=["POST"])
@common.api_wrap
def api_tucao_report():
    """
    举报（每人每帖一次）。参数：{post_id, reason?}
    返回：{ok, first}（first=false 表示此前已举报过）
    权限：需登录。
    """
    uid: Optional[int] = auth_bp.current_user_id()
    if uid is None:
        return common.err("登录后才能举报", "auth", 401)
    if not common.rate_check(f"report:{uid}", limit=5):
        return common.err("操作太频繁", "rate", 429)
    body: dict = request.get_json(silent=True) or {}
    try:
        post_id: int = int(body.get("post_id") or 0)
    except (TypeError, ValueError):
        return common.err("post_id 不合法")
    reason: str = str(body.get("reason", "")).strip()[:200]
    post = portal_db.tucao_get(post_id)
    if post is None or str(post["status"]) == "deleted":
        return common.err("帖子不存在", "not_found", 404)
    first: bool = portal_db.tucao_report(post_id, uid, reason)
    portal_db.add_audit(uid, "tucao_report", str(post_id), reason[:100],
                        request.remote_addr or "")
    return common.ok({"first": first})


# ============================================================
# 后台：审核队列 / 举报 / 敏感词 / 审核模式（tucao.moderate）
# ============================================================
@bp.route("/api/admin/tucao/list")
@require_perm("tucao.moderate")
@common.api_wrap
def api_admin_tucao_list():
    """
    审核队列。参数：status=all|pending|ok|deleted（默认 all）
    权限：tucao.moderate。返回内容不含 user_id（只展示马甲/身份类型）。
    """
    status: str = (request.args.get("status", "all") or "all").strip()
    rows = portal_db.tucao_admin_list(status)
    disp = {"real": "真实昵称", "anon": "随机马甲", "fixed": "固定匿名"}
    return common.ok({"list": [{
        "id": int(r["id"]), "content": str(r["content"]),
        "persona": str(r["persona"]), "persona_label": disp.get(str(r["persona"]), "匿名"),
        "anon_name": str(r["anon_name"]), "status": str(r["status"]),
        "pinned": bool(r["pinned"]), "like_count": int(r["like_count"]),
        "report_count": int(r["report_count"]), "created_at": float(r["created_at"]),
    } for r in rows]})


@bp.route("/api/admin/tucao/moderate", methods=["POST"])
@require_perm("tucao.moderate")
@common.api_wrap
def api_admin_tucao_moderate():
    """
    审核动作。参数：{post_id, action}，action ∈ delete|restore|approve|pin|unpin
    权限：tucao.moderate。写审计。危险操作（删除）前端会二次确认。
    """
    body: dict = request.get_json(silent=True) or {}
    try:
        post_id: int = int(body.get("post_id") or 0)
    except (TypeError, ValueError):
        return common.err("post_id 不合法")
    action: str = str(body.get("action", "")).strip()
    post = portal_db.tucao_get(post_id)
    if post is None:
        return common.err("帖子不存在", "not_found", 404)
    if action == "delete":
        portal_db.tucao_set_status(post_id, "deleted")
    elif action in ("restore", "approve"):
        portal_db.tucao_set_status(post_id, "ok")
    elif action in ("pin", "unpin"):
        portal_db.tucao_toggle_pin(post_id)
    else:
        return common.err("action 不合法")
    portal_db.add_audit(auth_bp.current_user_id(), f"tucao_{action}", str(post_id),
                        "", request.remote_addr or "")
    return common.ok()


@bp.route("/api/admin/tucao/reports")
@require_perm("tucao.moderate")
@common.api_wrap
def api_admin_tucao_reports():
    """举报队列（未处理优先）。权限：tucao.moderate"""
    rows = portal_db.tucao_reports_list(limit=100)
    return common.ok({"list": [{
        "id": int(r["id"]), "post_id": int(r["post_id"]),
        "reason": str(r["reason"]), "handled": bool(r["handled"]),
        "created_at": float(r["created_at"]),
        "post_content": str(r["post_content"] or "")[:80],
        "post_status": str(r["post_status"]),
    } for r in rows]})


@bp.route("/api/admin/tucao/reports/handle", methods=["POST"])
@require_perm("tucao.moderate")
@common.api_wrap
def api_admin_tucao_report_handle():
    """标记举报已处理。参数：{report_id}。权限：tucao.moderate"""
    body: dict = request.get_json(silent=True) or {}
    try:
        rid: int = int(body.get("report_id") or 0)
    except (TypeError, ValueError):
        return common.err("report_id 不合法")
    portal_db.tucao_report_handle(rid)
    return common.ok()


@bp.route("/api/admin/sensitive_words")
@require_perm("tucao.moderate")
@common.api_wrap
def api_sensitive_list():
    """敏感词库（管理员部分，不含内置词）。权限：tucao.moderate"""
    rows = portal_db.sensitive_list()
    return common.ok({
        "builtin": list(sensitive.BUILTIN_WORDS),
        "list": [{"id": int(r["id"]), "word": str(r["word"]),
                  "creator": str(r["creator"] or "系统"),
                  "created_at": float(r["created_at"])} for r in rows],
    })


@bp.route("/api/admin/sensitive_words/add", methods=["POST"])
@require_perm("tucao.moderate")
@common.api_wrap
def api_sensitive_add():
    """新增敏感词。参数：{word}。权限：tucao.moderate"""
    body: dict = request.get_json(silent=True) or {}
    word: str = str(body.get("word", "")).strip()[:30]
    if not word:
        return common.err("敏感词不能为空")
    ok_added: bool = portal_db.sensitive_add(word, auth_bp.current_user_id())
    if not ok_added:
        return common.err("该词已存在")
    portal_db.add_audit(auth_bp.current_user_id(), "sensitive_add", word, "",
                        request.remote_addr or "")
    return common.ok()


@bp.route("/api/admin/sensitive_words/delete", methods=["POST"])
@require_perm("tucao.moderate")
@common.api_wrap
def api_sensitive_delete():
    """删除敏感词（仅限管理员添加的词）。参数：{id}。权限：tucao.moderate"""
    body: dict = request.get_json(silent=True) or {}
    try:
        wid: int = int(body.get("id") or 0)
    except (TypeError, ValueError):
        return common.err("id 不合法")
    portal_db.sensitive_delete(wid)
    portal_db.add_audit(auth_bp.current_user_id(), "sensitive_delete", str(wid), "",
                        request.remote_addr or "")
    return common.ok()


@bp.route("/api/admin/tucao/settings")
@require_perm("tucao.moderate")
@common.api_wrap
def api_tucao_settings_get():
    """当前审核模式。权限：tucao.moderate"""
    return common.ok({"mode": current_mode()})


@bp.route("/api/admin/tucao/settings/set", methods=["POST"])
@require_perm("tucao.moderate")
@common.api_wrap
def api_tucao_settings_set():
    """
    切换审核模式。参数：{mode}，mode ∈ post_first|audit_first。权限：tucao.moderate
    """
    body: dict = request.get_json(silent=True) or {}
    mode: str = str(body.get("mode", "")).strip()
    if mode not in (MODE_POST_FIRST, MODE_AUDIT_FIRST):
        return common.err("mode 不合法")
    portal_db.setting_put(MODE_KEY, mode)
    portal_db.add_audit(auth_bp.current_user_id(), "tucao_mode", mode, "",
                        request.remote_addr or "")
    return common.ok({"mode": mode})
