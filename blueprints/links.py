"""
blueprints/links.py —— 资源导航（友情链接）前台蓝图
====================================================
路由：
    GET  /links                  页面（顶部分类 Tab + 搜索框 + 卡片网格）
    GET  /api/links/list         全部启用链接（按分类分组）
    POST /api/links/submit       学生推荐链接 {name,url,category_id?,description?}
安全与约束：
1. 全部链接 target=_blank rel="noopener noreferrer"（模板侧保证）；
2. 禁外链 favicon —— 图标一律彩色首字母方块（前端 CSS 实现）；
3. URL 仅允许 http/https（防 javascript: 协议注入）；
4. 推荐进审核队列（link_submissions），link.manage 审批后才上墙；
5. 推荐限频：每用户每分钟 3 次。
"""

from __future__ import annotations

import re
from typing import Any, Optional

from flask import Blueprint, render_template, request

import common
import portal_db
from blueprints import auth as auth_bp

bp: Blueprint = Blueprint("links", __name__)

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


@bp.route("/links")
def links_page():
    """资源导航页：数据经 /api/links/list 拉取（骨架屏后回填）。"""
    return render_template("links.html")


@bp.route("/api/links/list")
@common.api_wrap
def api_links_list():
    """
    全部启用链接 + 分类。返回：{ok, categories:[...], links:[...]}
    权限：公开。链接带分类名/颜色；前端按分类 Tab + 搜索框过滤。
    """
    cats = portal_db.link_cat_list()
    links = portal_db.link_list_active()
    return common.ok({
        "categories": [{"id": int(c["id"]), "name": str(c["name"]),
                        "color": str(c["color"]), "sort": int(c["sort"])}
                       for c in cats],
        "links": [{"id": int(l["id"]), "name": str(l["name"]),
                   "url": str(l["url"]), "category_id": int(l["category_id"]),
                   "cat_name": str(l["cat_name"]), "cat_color": str(l["cat_color"]),
                   "description": str(l["description"])}
                  for l in links],
    })


@bp.route("/api/links/submit", methods=["POST"])
@common.api_wrap
def api_links_submit():
    """
    学生推荐链接。参数：{name, url, category_id?, description?}
    返回：{ok, id}
    权限：需登录；进审核队列（pending），审批通过后上墙。限频 3 次/分钟。
    """
    uid: Optional[int] = auth_bp.current_user_id()
    if uid is None:
        return common.err("请先登录后再推荐链接", "auth", 401)
    if not common.rate_check_window(f"linksub:{uid}", limit=3, window_seconds=60.0):
        return common.err("推荐太频繁，请稍后再试", "rate", 429)
    body: dict[str, Any] = request.get_json(silent=True) or {}
    name: str = str(body.get("name", "")).strip()[:30]
    url: str = str(body.get("url", "")).strip()[:200]
    cat_raw = body.get("category_id")
    category_id: Optional[int] = int(cat_raw) if cat_raw else None
    description: str = str(body.get("description", "")).strip()[:80]
    if not name:
        return common.err("请填写网站名称")
    if not _URL_RE.match(url):
        return common.err("链接需以 http:// 或 https:// 开头")
    if category_id is not None:
        exists = any(int(c["id"]) == category_id
                     for c in portal_db.link_cat_list())
        if not exists:
            return common.err("分类不存在")
    sub_id: int = portal_db.link_sub_add(name, url, category_id, description, uid)
    portal_db.add_audit(uid, "link_submit", name, url[:80],
                        common.client_ip())
    return common.ok({"id": sub_id, "msg": "已提交，审核通过后展示"})
