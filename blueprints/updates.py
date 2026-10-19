"""
blueprints/updates.py —— 网站更新公告（预告/通知/更新）
==========================================================
页面：
    GET  /updates                        更新公告页（Tab：全部/预告/通知/更新）
前台 API（公开读，已读需登录）：
    GET  /api/updates/list?type=&page=   已发布列表（置顶在前，按发布时间倒序）
    GET  /api/updates/detail?id=         详情（登录用户自动记已读 → 小红点消失）
    POST /api/updates/read               {id} 显式标记已读（幂等）
    GET  /api/updates/my_reads           我的已读记录（/me 普通用户区）
管理 API（/me「网站更新管理」面板；update.manage 权限，admin.full 放行）：
    GET  /api/admin-panel/updates/list       全部（含草稿）
    POST /api/admin-panel/updates/create     {update_type,title,content,version_tag,is_pinned,publish}
    POST /api/admin-panel/updates/update     {id,...同上}
    POST /api/admin-panel/updates/publish    {id}   发布（二次确认在前端）
    POST /api/admin-panel/updates/unpublish  {id}   下架
    POST /api/admin-panel/updates/pin        {id,pinned}
安全：全部写操作进 audit_logs；content 服务端转义后存储（Markdown-lite 渲染在前端）。
"""

from __future__ import annotations

import html
from typing import Any, Optional

from flask import Blueprint, render_template, request

import common
import portal_db
from blueprints.admin import require_perm
from blueprints import auth as auth_bp

bp: Blueprint = Blueprint("updates", __name__)

TYPE_LABEL: dict[str, str] = {"preview": "预告", "notice": "通知", "release": "更新"}
CONTENT_MAX: int = 2000   # Markdown-lite 正文上限（字符）


# ============================================================
# 工具
# ============================================================
def _esc_content(raw: str) -> str:
    """正文存储前转义（Markdown-lite 只允许换行/**加粗**/- 列表，其余一律当纯文本）。"""
    return html.escape(str(raw or "").strip(), quote=False)[:CONTENT_MAX]


def _pub_view(r: Any, unread_ids: Optional[set[int]] = None) -> dict[str, Any]:
    """前台卡片视图（时间返回 Unix 秒，前端按 Asia/Shanghai 格式化）。"""
    uid: int = int(r["id"])
    return {
        "id": uid,
        "update_type": str(r["update_type"]),
        "type_label": TYPE_LABEL.get(str(r["update_type"]), "通知"),
        "title": str(r["title"]),
        "version_tag": str(r["version_tag"]),
        "is_pinned": bool(r["is_pinned"]),
        "publish_at": float(r["publish_at"]),
        "author": str(r["author"]) if r["author"] else "管理员",
        # 登录用户：是否未读（前端渲染红点标记）；未登录恒 False
        "unread": bool(unread_ids is not None and uid in unread_ids),
    }


def _unread_ids(uid: Optional[int]) -> Optional[set[int]]:
    """当前用户未读的已发布 id 集（未登录返回 None）。"""
    if uid is None:
        return None
    rows = portal_db.update_list_public(limit=200)
    return {int(r["id"]) for r in rows} - {
        int(r["update_id"]) for r in portal_db.update_my_reads(uid, limit=500)}


# ============================================================
# 页面
# ============================================================
@bp.route("/updates")
def updates_page():
    """更新公告页。导航不加入口——从个人中心/首页横幅/红点可达。"""
    return render_template("updates.html")


# ============================================================
# 前台 API
# ============================================================
@bp.route("/api/updates/list")
@common.api_wrap
def api_list():
    """
    已发布列表。参数：type=all|preview|notice|release，page（每页 20）
    返回：{ok, list:[...], has_more, unread_count}
    权限：公开（unread_count 仅登录用户有值）
    """
    utype: str = str(request.args.get("type", "all"))
    utype = "" if utype == "all" else utype
    page: int = max(1, int(request.args.get("page", 1) or 1))
    per: int = 20
    rows = portal_db.update_list_public(update_type=utype, limit=per + 1,
                                        offset=(page - 1) * per)
    has_more: bool = len(rows) > per
    rows = rows[:per]
    uid: Optional[int] = auth_bp.current_user_id()
    unread: Optional[set[int]] = _unread_ids(uid)
    return common.ok({
        "list": [_pub_view(r, unread) for r in rows],
        "has_more": has_more,
        "unread_count": portal_db.update_unread_count(uid) if uid else 0,
    })


@bp.route("/api/updates/detail")
@common.api_wrap
def api_detail():
    """
    详情。参数：id
    返回：{ok, item:{...,content}}
    权限：公开；登录用户访问即记已读（小红点机制的核心：点开即读）。
    """
    update_id: int = int(request.args.get("id", 0) or 0)
    row = portal_db.update_get(update_id)
    if row is None or not bool(row["is_published"]):
        return common.err("内容不存在或未发布", "not_found", 404)
    uid: Optional[int] = auth_bp.current_user_id()
    if uid is not None:
        portal_db.update_mark_read(uid, update_id)  # 幂等
    item = _pub_view(row, None)
    item["content"] = str(row["content"])
    return common.ok({"item": item,
                      "unread_count": portal_db.update_unread_count(uid) if uid else 0})


@bp.route("/api/updates/read", methods=["POST"])
@common.api_wrap
def api_read():
    """
    显式标记已读（幂等）。参数：{id}
    返回：{ok, unread_count}
    权限：需登录（未登录返回 401——红点机制只对登录用户生效）
    """
    uid: Optional[int] = auth_bp.current_user_id()
    if uid is None:
        return common.err("请先登录", "auth", 401)
    body: dict = request.get_json(silent=True) or {}
    update_id: int = int(body.get("id", 0) or 0)
    row = portal_db.update_get(update_id)
    if row is None or not bool(row["is_published"]):
        return common.err("内容不存在", "not_found", 404)
    portal_db.update_mark_read(uid, update_id)
    return common.ok({"unread_count": portal_db.update_unread_count(uid)})


@bp.route("/api/updates/my_reads")
@common.api_wrap
def api_my_reads():
    """
    我的已读更新记录。返回：{ok, list:[{id,update_type,title,version_tag,read_at}]}
    权限：需登录
    """
    uid: Optional[int] = auth_bp.current_user_id()
    if uid is None:
        return common.err("请先登录", "auth", 401)
    rows = portal_db.update_my_reads(uid)
    return common.ok({"list": [{
        "id": int(r["id"]), "update_type": str(r["update_type"]),
        "type_label": TYPE_LABEL.get(str(r["update_type"]), "通知"),
        "title": str(r["title"]), "version_tag": str(r["version_tag"]),
        "read_at": float(r["read_at"]),
    } for r in rows]})


# ============================================================
# 管理 API（update.manage；/me 个人中心面板调用，不进后台也能用）
# ============================================================
def _audit(action: str, target: str = "", detail: str = "") -> None:
    portal_db.add_audit(auth_bp.current_user_id(), action, target, detail,
                        common.client_ip())


def _validate_payload(body: dict) -> tuple[str, str, str, str, bool]:
    """表单校验 + 转义。返回 (type, title, content, version_tag, is_pinned)。"""
    update_type: str = str(body.get("update_type", "")).strip()
    if update_type not in portal_db.UPDATE_TYPES:
        raise ValueError("类型必须是 preview/notice/release")
    title: str = str(body.get("title", "")).strip()
    if not title:
        raise ValueError("标题不能为空")
    content: str = _esc_content(str(body.get("content", "")))
    if not content:
        raise ValueError("内容不能为空")
    version_tag: str = str(body.get("version_tag", "")).strip()
    if update_type == "release" and not version_tag:
        raise ValueError("更新（release）必须填写版本号，如 v1.5.0")
    if update_type != "release":
        version_tag = ""
    return update_type, title[:60], content, version_tag[:20], bool(body.get("is_pinned"))


@bp.route("/api/admin-panel/updates/list")
@require_perm("update.manage")
@common.api_wrap
def api_admin_list():
    """全部更新（含草稿）。返回：{ok, list:[...]}  权限：update.manage"""
    rows = portal_db.update_list_admin()
    return common.ok({"list": [{
        "id": int(r["id"]), "update_type": str(r["update_type"]),
        "type_label": TYPE_LABEL.get(str(r["update_type"]), "通知"),
        "title": str(r["title"]), "content": str(r["content"]),
        "version_tag": str(r["version_tag"]),
        "is_published": bool(r["is_published"]),
        "is_pinned": bool(r["is_pinned"]),
        "publish_at": float(r["publish_at"]),
        "created_at": float(r["created_at"]),
        "author": str(r["author"]) if r["author"] else "管理员",
    } for r in rows]})


@bp.route("/api/admin-panel/updates/create", methods=["POST"])
@require_perm("update.manage")
@common.api_wrap
def api_admin_create():
    """
    新建（草稿）。参数：{update_type,title,content,version_tag,is_pinned,publish}
    publish=true 时创建后立即发布。返回：{ok, id}
    """
    body: dict = request.get_json(silent=True) or {}
    utype, title, content, vtag, pinned = _validate_payload(body)
    uid: Optional[int] = auth_bp.current_user_id()
    new_id: int = portal_db.update_add(utype, title, content, vtag, pinned, uid)
    if bool(body.get("publish")):
        portal_db.update_set_published(new_id, True)
    _audit("update_create", f"#{new_id} {title}",
           f"type={utype} publish={bool(body.get('publish'))}")
    return common.ok({"id": new_id})


@bp.route("/api/admin-panel/updates/update", methods=["POST"])
@require_perm("update.manage")
@common.api_wrap
def api_admin_update():
    """编辑。参数：{id, update_type,title,content,version_tag,is_pinned}"""
    body: dict = request.get_json(silent=True) or {}
    update_id: int = int(body.get("id", 0) or 0)
    if portal_db.update_get(update_id) is None:
        return common.err("记录不存在", "not_found", 404)
    utype, title, content, vtag, pinned = _validate_payload(body)
    portal_db.update_edit(update_id, utype, title, content, vtag, pinned)
    _audit("update_edit", f"#{update_id} {title}", f"type={utype}")
    return common.ok()


@bp.route("/api/admin-panel/updates/publish", methods=["POST"])
@require_perm("update.manage")
@common.api_wrap
def api_admin_publish():
    """发布（前端 Modal 二次确认）。参数：{id}"""
    body: dict = request.get_json(silent=True) or {}
    update_id: int = int(body.get("id", 0) or 0)
    row = portal_db.update_get(update_id)
    if row is None:
        return common.err("记录不存在", "not_found", 404)
    portal_db.update_set_published(update_id, True)
    _audit("update_publish", f"#{update_id} {row['title']}")
    return common.ok()


@bp.route("/api/admin-panel/updates/unpublish", methods=["POST"])
@require_perm("update.manage")
@common.api_wrap
def api_admin_unpublish():
    """下架（前台不可见，数据保留）。参数：{id}"""
    body: dict = request.get_json(silent=True) or {}
    update_id: int = int(body.get("id", 0) or 0)
    row = portal_db.update_get(update_id)
    if row is None:
        return common.err("记录不存在", "not_found", 404)
    portal_db.update_set_published(update_id, False)
    _audit("update_unpublish", f"#{update_id} {row['title']}")
    return common.ok()


@bp.route("/api/admin-panel/updates/pin", methods=["POST"])
@require_perm("update.manage")
@common.api_wrap
def api_admin_pin():
    """置顶/取消置顶。参数：{id, pinned}"""
    body: dict = request.get_json(silent=True) or {}
    update_id: int = int(body.get("id", 0) or 0)
    pinned: bool = bool(body.get("pinned"))
    row = portal_db.update_get(update_id)
    if row is None:
        return common.err("记录不存在", "not_found", 404)
    portal_db.update_set_pinned(update_id, pinned)
    _audit("update_pin" if pinned else "update_unpin", f"#{update_id} {row['title']}")
    return common.ok()
