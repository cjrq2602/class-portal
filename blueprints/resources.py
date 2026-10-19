"""
blueprints/resources.py —— 学习资料库（/resources + /api/resources/* + 后台管理）
==============================================================================
关键约束落实：
1. **图片压缩在浏览器端完成**（Canvas，质量 0.7、最长边 1280，见 static/js/upload.js）；
   服务端零图像处理——本文件不含任何图像库调用。
2. 服务端逐条强校验（uploads.validate_bytes）：单文件 ≤5MB、后缀白名单、
   MIME 双校验（客户端 content-type + 服务端魔数嗅探）、uuid 重命名。
3. 磁盘配额 400MB：写入前检查，超限拒绝（uploads.storage_report）。
4. 上传限频：每用户每分钟 5 次。
5. 下载不走静态目录，统一走 /api/resources/download/<id>（计数 + 权限集中）。
6. 学科标签存 site_settings（resource_subjects，JSON 数组），管理员可改。
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

from flask import Blueprint, render_template, request, send_file

import common
import portal_db
import uploads
from blueprints import auth as auth_bp
from blueprints.admin import require_perm

bp: Blueprint = Blueprint("resources", __name__)

SUBJECTS_KEY: str = "resource_subjects"
DEFAULT_SUBJECTS: list[str] = ["语文", "数学", "英语", "物理", "化学", "生物", "其他"]
UPLOAD_PER_MINUTE: int = 5


def subjects() -> list[str]:
    """学科标签（site_settings 存储，缺省用内置列表）。"""
    raw: str = portal_db.setting_get(SUBJECTS_KEY, "")
    if not raw:
        return list(DEFAULT_SUBJECTS)
    try:
        arr = json.loads(raw)
        if isinstance(arr, list) and arr:
            return [str(x)[:12] for x in arr][:30]
    except Exception:  # noqa: BLE001
        pass
    return list(DEFAULT_SUBJECTS)


# ============================================================
# 页面
# ============================================================
@bp.route("/resources")
def resources_page():
    """学习资料库页。"""
    return render_template("resources.html", subjects=subjects(),
                           logged_in=auth_bp.current_user_id() is not None,
                           max_file_mb=uploads.MAX_FILE_BYTES // (1024 * 1024))


# ============================================================
# 用户端 API
# ============================================================
@bp.route("/api/resources/list")
@common.api_wrap
def api_list():
    """
    资料列表。参数：subject、sort（time|hot）
    返回：{ok, list, subjects, storage}
    权限：公开浏览。
    """
    subject: str = (request.args.get("subject", "") or "").strip()
    sort: str = (request.args.get("sort", "time") or "time").strip()
    uid: Optional[int] = auth_bp.current_user_id()
    rows = portal_db.res_list(subject=subject or None,
                              sort="hot" if sort == "hot" else "time")
    rep = uploads.storage_report()
    return common.ok({
        "list": [{
            "id": int(r["id"]), "title": str(r["title"]), "subject": str(r["subject"]),
            "rtype": str(r["rtype"]), "link": str(r["link"]),
            "file_name": str(r["file_name"]), "file_size": int(r["file_size"]),
            "size_text": uploads.human_size(int(r["file_size"])) if r["file_size"] else "",
            "uploader": str(r["uploader"] or "同学"),
            "downloads": int(r["downloads"]), "created_at": float(r["created_at"]),
            "mine": uid is not None and int(r["uploader_id"] or -1) == uid,
        } for r in rows],
        "subjects": subjects(),
        "counts": [{"name": n, "count": c} for n, c in portal_db.res_subjects()],
        "storage": {"percent": rep["percent"], "used_text": uploads.human_size(rep["used"]),
                    "quota_text": uploads.human_size(rep["quota"])},
    })


@bp.route("/api/resources/upload", methods=["POST"])
@common.api_wrap
def api_upload():
    """
    上传资料（外链或文件，multipart/form-data）。
    表单字段：mode=link|file、title、subject、link（mode=link）、file（mode=file）
    返回：{ok, id}
    权限：需登录。文件侧强校验见 uploads.validate_bytes；超出配额或校验失败一律拒绝。
    """
    uid: Optional[int] = auth_bp.current_user_id()
    if uid is None:
        return common.err("登录后才能上传资料", "auth", 401)
    # 每用户每分钟最多 5 次上传（用分钟级窗口，而非默认的每秒窗口）
    if not common.rate_check_window(f"upload:{uid}", UPLOAD_PER_MINUTE, 60.0):
        return common.err("上传太频繁：每分钟最多 5 次", "rate", 429)

    mode: str = str(request.form.get("mode", "link")).strip()
    title: str = str(request.form.get("title", "")).strip()[:80]
    subject: str = str(request.form.get("subject", "其他")).strip()[:12]
    if subject not in subjects():
        subject = "其他"
    if not title:
        return common.err("请填写资料标题")

    # ---- 外链模式 ----
    if mode == "link":
        link: str = str(request.form.get("link", "")).strip()[:500]
        if not link.startswith(("http://", "https://")):
            return common.err("链接必须以 http:// 或 https:// 开头")
        rid: int = portal_db.res_create(title, subject, "link", link, "",
                                        "", 0, uid)
        portal_db.add_audit(uid, "resource_upload", title, "type=link",
                            common.client_ip())
        return common.ok({"id": rid})

    # ---- 文件模式 ----
    f = request.files.get("file")
    if f is None or not f.filename:
        return common.err("请选择要上传的文件")
    data: bytes = f.read()
    ok, msg = uploads.validate_bytes(f.filename, f.mimetype or "", data,
                                     is_image=False)
    if not ok:
        return common.err(msg)

    # 配额检查（写入前，避免产生"已落盘但未入库"的孤儿文件）
    rep = uploads.storage_report()
    if rep["used"] + len(data) > rep["quota"]:
        return common.err(
            f"全站上传配额已满（{uploads.human_size(rep['used'])} / "
            f"{uploads.human_size(rep['quota'])}），请先删除部分旧文件", "quota", 507)

    rel, size = uploads.save_bytes(uploads.SUB_RESOURCES, f.filename, data)
    rid = portal_db.res_create(title, subject, "file", "", rel,
                               uploads.safe_display_name(f.filename), size, uid)
    portal_db.add_audit(uid, "resource_upload", title,
                        f"type=file size={size}", common.client_ip())
    return common.ok({"id": rid, "size_text": uploads.human_size(size)})


@bp.route("/api/resources/delete", methods=["POST"])
@common.api_wrap
def api_delete():
    """
    删除资料。参数：{id}。
    权限：上传者本人 或 resource.manage。删除记录的同时删磁盘文件以回收配额。
    """
    uid: Optional[int] = auth_bp.current_user_id()
    if uid is None:
        return common.err("请先登录", "auth", 401)
    body: dict = request.get_json(silent=True) or {}
    try:
        rid: int = int(body.get("id") or 0)
    except (TypeError, ValueError):
        return common.err("id 不合法")
    row = portal_db.res_get(rid)
    if row is None:
        return common.err("资料不存在", "not_found", 404)
    is_owner: bool = int(row["uploader_id"] or -1) == uid
    if not is_owner and "resource.manage" not in auth_bp.user_perms():
        return common.err("只能删除自己上传的资料", "forbidden", 403)
    freed: int = uploads.delete_file(str(row["file_path"])) if row["file_path"] else 0
    portal_db.res_delete(rid)
    portal_db.add_audit(uid, "resource_delete", str(rid), f"freed={freed}",
                        common.client_ip())
    return common.ok({"freed": freed, "freed_text": uploads.human_size(freed)})


@bp.route("/api/resources/download/<int:res_id>")
def api_download(res_id: int):
    """
    下载文件（外链类型则 302 跳到原链接）。
    权限：公开。每次下载计数 +1（因此文件不放在 static/ 下，避免绕过计数）。
    """
    row = portal_db.res_get(res_id)
    if row is None:
        return common.err("资料不存在", "not_found", 404)
    if str(row["rtype"]) == "link":
        from flask import redirect
        return redirect(str(row["link"]))
    abs_path: Optional[str] = uploads.abs_of(str(row["file_path"]))
    if abs_path is None:
        return common.err("文件已丢失，请联系管理员", "not_found", 404)
    portal_db.res_bump_download(res_id)
    return send_file(abs_path, as_attachment=True,
                     download_name=str(row["file_name"] or "resource"))


@bp.route("/api/resources/mine")
@common.api_wrap
def api_mine():
    """
    我的资料上传（个人中心用）。返回：{ok, list, count, total_size_text}
    权限：需登录（只返回自己上传的）。
    """
    uid: Optional[int] = auth_bp.current_user_id()
    if uid is None:
        return common.err("请先登录", "auth", 401)
    all_rows = portal_db.res_list(limit=1000)
    mine = [r for r in all_rows if int(r["uploader_id"] or -1) == uid]
    total: int = sum(int(r["file_size"] or 0) for r in mine)
    return common.ok({
        "list": [{
            "id": int(r["id"]), "title": str(r["title"]), "subject": str(r["subject"]),
            "rtype": str(r["rtype"]), "link": str(r["link"]),
            "file_name": str(r["file_name"]), "downloads": int(r["downloads"]),
            "size_text": uploads.human_size(int(r["file_size"])) if r["file_size"] else "",
            "created_at": float(r["created_at"]),
        } for r in mine],
        "count": len(mine),
        "total_size_text": uploads.human_size(total),
    })


# ============================================================
# 后台（resource.manage）
# ============================================================
@bp.route("/api/admin/resources/list")
@require_perm("resource.manage")
@common.api_wrap
def api_admin_list():
    """后台资料管理列表（含上传者与占用）。权限：resource.manage"""
    rows = portal_db.res_list(limit=500)
    rep = uploads.storage_report()
    return common.ok({"list": [{
        "id": int(r["id"]), "title": str(r["title"]), "subject": str(r["subject"]),
        "rtype": str(r["rtype"]), "link": str(r["link"]),
        "file_name": str(r["file_name"]), "file_size": int(r["file_size"]),
        "size_text": uploads.human_size(int(r["file_size"])) if r["file_size"] else "",
        "uploader": str(r["uploader"] or "同学"), "downloads": int(r["downloads"]),
        "created_at": float(r["created_at"]),
    } for r in rows],
        "storage": {"used": rep["used"], "quota": rep["quota"], "disk": rep["disk"],
                    "percent": rep["percent"],
                    "used_text": uploads.human_size(rep["used"]),
                    "quota_text": uploads.human_size(rep["quota"]),
                    "disk_text": uploads.human_size(rep["disk"])}})


@bp.route("/api/admin/resources/storage")
@require_perm("resource.manage")
@common.api_wrap
def api_storage():
    """存储用量仪表数据（进度条）。权限：resource.manage"""
    rep = uploads.storage_report()
    return common.ok({
        "used": rep["used"], "quota": rep["quota"], "disk": rep["disk"],
        "percent": rep["percent"],
        "used_text": uploads.human_size(rep["used"]),
        "quota_text": uploads.human_size(rep["quota"]),
        "disk_text": uploads.human_size(rep["disk"]),
    })


@bp.route("/api/admin/resources/subjects")
@require_perm("resource.manage")
@common.api_wrap
def api_admin_subjects_get():
    """读取学科标签。权限：resource.manage"""
    return common.ok({"subjects": subjects()})


@bp.route("/api/admin/resources/subjects/set", methods=["POST"])
@require_perm("resource.manage")
@common.api_wrap
def api_admin_subjects_set():
    """
    设置学科标签。参数：{subjects:[...]}（1~30 个，每个 ≤12 字）。权限：resource.manage
    """
    body: dict = request.get_json(silent=True) or {}
    arr = body.get("subjects")
    if not isinstance(arr, list):
        return common.err("subjects 必须是数组")
    clean: list[str] = []
    for x in arr:
        s: str = str(x).strip()[:12]
        if s and s not in clean:
            clean.append(s)
    if not clean:
        return common.err("至少保留一个学科标签")
    portal_db.setting_put(SUBJECTS_KEY, json.dumps(clean[:30], ensure_ascii=False))
    portal_db.add_audit(auth_bp.current_user_id(), "resource_subjects_set",
                        ",".join(clean), "", common.client_ip())
    return common.ok({"subjects": clean})
