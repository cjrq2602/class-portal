"""
blueprints/honors.py —— 荣誉墙（/honors + /api/honors/* + 后台管理）
==================================================================
要点：
1. 卡片流：奖项 / 获奖人 / 日期 / 描述 / 可选图片；置顶优先、其余时间倒序。
2. 图片：浏览器端 Canvas 压缩到最长边 ≤1280、质量 0.7 后再上传（static/js/upload.js），
   服务端只校验（≤1MB、后缀白名单、MIME 双校验）并原样落盘 —— 服务端零图像处理。
3. honor.manage 维护（增/改/删/置顶）；图片经 /api/honors/image/<id> 读取。
4. 空态文案"还没有荣誉，第一块奖牌等着你们来拿"。
"""

from __future__ import annotations

from typing import Any, Optional

from flask import Blueprint, render_template, request, send_file

import common
import portal_db
import uploads
from blueprints import auth as auth_bp
from blueprints.admin import require_perm

bp: Blueprint = Blueprint("honors", __name__)


def honor_view(r: Any) -> dict[str, Any]:
    return {
        "id": int(r["id"]), "title": str(r["title"]), "owner": str(r["owner"]),
        "date": str(r["hdate"]), "description": str(r["description"]),
        "has_image": bool(str(r["image_path"])),
        "is_pinned": bool(r["is_pinned"]), "created_at": float(r["created_at"]),
    }


# ============================================================
# 页面
# ============================================================
@bp.route("/honors")
def honors_page():
    """荣誉墙页。"""
    return render_template("honors.html",
                           can_manage="honor.manage" in auth_bp.user_perms(),
                           max_image_mb=uploads.MAX_IMAGE_BYTES // (1024 * 1024))


# ============================================================
# API
# ============================================================
@bp.route("/api/honors/list")
@common.api_wrap
def api_list():
    """荣誉列表（置顶在前 + 时间倒序）。权限：公开。"""
    rows = portal_db.honor_list(limit=100)
    return common.ok({"list": [honor_view(r) for r in rows]})


@bp.route("/api/honors/pinned")
@common.api_wrap
def api_pinned():
    """
    首页"荣誉精选"：置顶一条（没有置顶则最新一条）。
    返回：{ok, item | null}
    """
    r = portal_db.honor_pinned_one()
    return common.ok({"item": honor_view(r) if r is not None else None})


@bp.route("/api/honors/image/<int:honor_id>")
def api_image(honor_id: int):
    """读取荣誉图片（仅图片类型；文件不放在 static/ 下，统一走本路由）。"""
    row = portal_db.honor_get(honor_id)
    if row is None or not str(row["image_path"]):
        return common.err("图片不存在", "not_found", 404)
    abs_path: Optional[str] = uploads.abs_of(str(row["image_path"]))
    if abs_path is None:
        return common.err("图片已丢失", "not_found", 404)
    # 图片直接内联展示（非附件下载），因此 as_attachment=False
    return send_file(abs_path, as_attachment=False)


# ============================================================
# 后台（honor.manage）
# ============================================================
@bp.route("/api/admin/honors/create", methods=["POST"])
@require_perm("honor.manage")
@common.api_wrap
def api_create():
    """
    新增荣誉（multipart/form-data，图片可选）。
    表单：title、owner、hdate（北京日期 YYYY-MM-DD）、description、image（可选）
    权限：honor.manage。图片 ≤1MB 且必须已是浏览器压缩后的 png/jpg。写审计。
    """
    if not common.rate_check("honor_upload:" + str(auth_bp.current_user_id()), limit=10):
        return common.err("操作太频繁", "rate", 429)
    title: str = str(request.form.get("title", "")).strip()[:60]
    if not title:
        return common.err("奖项名称不能为空")
    owner: str = str(request.form.get("owner", "")).strip()[:60]
    hdate: str = str(request.form.get("hdate", "")).strip()[:10]
    description: str = str(request.form.get("description", "")).strip()[:500]

    image_rel: str = ""
    img = request.files.get("image")
    if img is not None and img.filename:
        data: bytes = img.read()
        ok, msg = uploads.validate_bytes(img.filename, img.mimetype or "", data,
                                        is_image=True)
        if not ok:
            return common.err(msg)
        rep = uploads.storage_report()
        if rep["used"] + len(data) > rep["quota"]:
            return common.err("全站上传配额已满，请先清理旧文件", "quota", 507)
        image_rel, _size = uploads.save_bytes(uploads.SUB_HONORS, img.filename, data)

    hid: int = portal_db.honor_create(title, owner, hdate, description, image_rel,
                                      auth_bp.current_user_id())
    portal_db.add_audit(auth_bp.current_user_id(), "honor_create", title,
                        f"id={hid} img={bool(image_rel)}", request.remote_addr or "")
    return common.ok({"id": hid})


@bp.route("/api/admin/honors/update", methods=["POST"])
@require_perm("honor.manage")
@common.api_wrap
def api_update():
    """编辑荣誉文字字段。参数：{id,title,owner,hdate,description}。权限：honor.manage"""
    body: dict = request.get_json(silent=True) or {}
    try:
        hid: int = int(body.get("id") or 0)
    except (TypeError, ValueError):
        return common.err("id 不合法")
    if portal_db.honor_get(hid) is None:
        return common.err("荣誉不存在", "not_found", 404)
    title: str = str(body.get("title", "")).strip()[:60]
    if not title:
        return common.err("奖项名称不能为空")
    portal_db.honor_update(hid, title, str(body.get("owner", "")).strip()[:60],
                           str(body.get("hdate", "")).strip()[:10],
                           str(body.get("description", "")).strip()[:500])
    portal_db.add_audit(auth_bp.current_user_id(), "honor_update", str(hid), "",
                        request.remote_addr or "")
    return common.ok()


@bp.route("/api/admin/honors/pin", methods=["POST"])
@require_perm("honor.manage")
@common.api_wrap
def api_pin():
    """置顶/取消置顶。参数：{id}。权限：honor.manage"""
    body: dict = request.get_json(silent=True) or {}
    try:
        hid: int = int(body.get("id") or 0)
    except (TypeError, ValueError):
        return common.err("id 不合法")
    if portal_db.honor_get(hid) is None:
        return common.err("荣誉不存在", "not_found", 404)
    pinned: bool = portal_db.honor_toggle_pin(hid)
    portal_db.add_audit(auth_bp.current_user_id(),
                        "honor_pin" if pinned else "honor_unpin", str(hid), "",
                        request.remote_addr or "")
    return common.ok({"is_pinned": pinned})


@bp.route("/api/admin/honors/delete", methods=["POST"])
@require_perm("honor.manage")
@common.api_wrap
def api_delete():
    """删除荣誉（连带删除图片文件回收配额）。参数：{id}。权限：honor.manage"""
    body: dict = request.get_json(silent=True) or {}
    try:
        hid: int = int(body.get("id") or 0)
    except (TypeError, ValueError):
        return common.err("id 不合法")
    row = portal_db.honor_get(hid)
    if row is None:
        return common.err("荣誉不存在", "not_found", 404)
    freed: int = 0
    if str(row["image_path"]):
        freed = uploads.delete_file(str(row["image_path"]))
    portal_db.honor_delete(hid)
    portal_db.add_audit(auth_bp.current_user_id(), "honor_delete", str(hid),
                        f"freed={freed}", request.remote_addr or "")
    return common.ok({"freed_text": uploads.human_size(freed)})


@bp.route("/api/admin/honors/list")
@require_perm("honor.manage")
@common.api_wrap
def api_admin_list():
    """后台荣誉列表。权限：honor.manage"""
    rows = portal_db.honor_list(limit=200)
    return common.ok({"list": [honor_view(r) for r in rows]})
