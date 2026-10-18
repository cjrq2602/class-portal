"""
uploads.py —— 上传文件的校验 / 落盘 / 配额 / 回收
================================================
平台约束（必须严格遵守）：
1. **服务端零图像处理**：图片压缩一律在浏览器端用 Canvas 完成（质量 0.7、最长边 1280），
   服务端只做"校验 + 原样落盘"。本模块不含任何 PIL/图像库，也不做缩放。
2. 服务端逐条强校验：单文件 ≤ 5MB、后缀白名单、
   **MIME 双校验**（客户端声明的 content-type + 服务端魔数嗅探）、uuid 重命名防穿越。
3. 磁盘配额制：全站上传总占用 ≤ 400MB（免费版磁盘 512MB），超限直接拒绝写入。
4. 下载不走静态目录，统一经 /api/... 路由（便于计数与权限控制），
   因此文件存放在 uploads/（非 static/），避免被平台静态映射绕过计数。

目录：mysite/uploads/resources/  与  mysite/uploads/honors/
"""

from __future__ import annotations

import os
import re
import uuid
from typing import Optional

BASE_DIR: str = os.path.dirname(os.path.abspath(__file__))
UPLOAD_ROOT: str = os.path.join(BASE_DIR, "uploads")
SUB_RESOURCES: str = "resources"
SUB_HONORS: str = "honors"

MAX_FILE_BYTES: int = 5 * 1024 * 1024        # 资料库单文件 5MB
MAX_IMAGE_BYTES: int = 1 * 1024 * 1024       # 荣誉墙图片 1MB（浏览器端已压缩）
TOTAL_QUOTA_BYTES: int = 400 * 1024 * 1024   # 全站上传总配额 400MB

# 后缀白名单 → 允许的"文件魔数家族"
ALLOWED_EXT: dict[str, str] = {
    "pdf": "pdf",
    "doc": "ole", "xls": "ole", "ppt": "ole",
    "docx": "zip", "xlsx": "zip", "pptx": "zip", "zip": "zip",
    "txt": "text",
    "png": "png", "jpg": "jpg", "jpeg": "jpg",
}
IMAGE_EXT: set[str] = {"png", "jpg", "jpeg"}

# 客户端可能声明的 MIME（双校验的第二道；不同浏览器/系统会略有差异）
ALLOWED_MIME: set[str] = {
    "application/pdf",
    "application/msword", "application/vnd.ms-excel", "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/zip", "application/x-zip-compressed", "application/octet-stream",
    "text/plain", "image/png", "image/jpeg", "image/jpg",
}

# 魔数特征
_MAGIC: list[tuple[str, bytes]] = [
    ("pdf", b"%PDF"),
    ("zip", b"PK\x03\x04"),
    ("ole", b"\xd0\xcf\x11\xe0"),
    ("png", b"\x89PNG\r\n\x1a\n"),
    ("jpg", b"\xff\xd8\xff"),
]
# 安全文件名（仅用于记录原始名，落盘一律用 uuid）
_SAFE_NAME_RE = re.compile(r"[^\w\u4e00-\u9fff.\-()\[\] ]")


def ext_of(filename: str) -> str:
    """取小写后缀（无后缀返回空串）。"""
    _, _, ext = str(filename or "").rpartition(".")
    return ext.lower() if ext and "." in filename else ""


def safe_display_name(filename: str, limit: int = 120) -> str:
    """清洗原始文件名（仅用于展示与下载时的 Content-Disposition）。"""
    name: str = _SAFE_NAME_RE.sub("_", os.path.basename(str(filename or "")))
    name = name.strip() or "file"
    return name[:limit]


def sniff_family(head: bytes) -> str:
    """按魔数判断文件家族；无法识别返回 'text'（交由文本可解码性判断）。"""
    for family, magic in _MAGIC:
        if head.startswith(magic):
            return family
    return "text"


def validate_bytes(filename: str, content_type: str, data: bytes,
                   is_image: bool = False) -> tuple[bool, str]:
    """
    逐条强校验（先看大小，再看后缀，再看双 MIME），返回 (是否通过, 错误信息)。
    """
    if not data:
        return False, "文件内容为空"
    limit: int = MAX_IMAGE_BYTES if is_image else MAX_FILE_BYTES
    if len(data) > limit:
        return False, f"文件超过 {human_size(limit)} 上限（当前 {human_size(len(data))}）"

    ext: str = ext_of(filename)
    if not ext:
        return False, "文件缺少扩展名"
    expected: Optional[str] = ALLOWED_EXT.get(ext)
    if expected is None:
        return False, f"不支持的文件类型 .{ext}（允许：{', '.join(sorted(ALLOWED_EXT))}）"
    if is_image and ext not in IMAGE_EXT:
        return False, "图片仅支持 png / jpg"

    # 双校验之一：客户端声明的 MIME（缺失或 octet-stream 时放行到魔数判定）
    ctype: str = (content_type or "").split(";")[0].strip().lower()
    if ctype and ctype not in ALLOWED_MIME:
        return False, f"声明的类型 {ctype} 不在允许列表内"

    # 双校验之二：服务端魔数嗅探（防止改后缀绕过；txt 用可解码性兜底）
    family: str = sniff_family(data[:16])
    if expected == "text":
        if family != "text":
            return False, "文件内容与 .txt 后缀不符"
    elif family != expected:
        return False, f"文件内容与 .{ext} 后缀不符（疑似改后缀绕过）"
    return True, ""


def human_size(n: int) -> str:
    """字节 → 人类可读（用于界面展示）。"""
    v: float = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024 or unit == "GB":
            return f"{v:.1f} {unit}" if unit != "B" else f"{int(v)} B"
        v /= 1024
    return f"{int(n)} B"


def ensure_dirs() -> None:
    """确保上传目录存在（幂等）。"""
    for sub in (SUB_RESOURCES, SUB_HONORS):
        os.makedirs(os.path.join(UPLOAD_ROOT, sub), exist_ok=True)


def save_bytes(subdir: str, filename: str, data: bytes) -> tuple[str, int]:
    """
    落盘（uuid 重命名，杜绝路径穿越与同名覆盖）。返回 (相对路径, 字节数)。
    调用方必须已通过 validate_bytes 与配额检查。
    """
    ensure_dirs()
    ext: str = ext_of(filename)
    rel: str = f"{subdir}/{uuid.uuid4().hex}{'.' + ext if ext else ''}"
    abs_path: str = os.path.join(UPLOAD_ROOT, rel)
    with open(abs_path, "wb") as f:
        f.write(data)
    return rel, len(data)


def abs_of(rel_path: str) -> Optional[str]:
    """
    相对路径 → 绝对路径（严格校验，防 ../ 穿越）。
    非法或不存在返回 None。
    """
    safe: str = str(rel_path or "").strip()
    if not safe or ".." in safe or safe.startswith("/") or "\\" in safe:
        return None
    if "/" not in safe:
        return None
    sub: str = safe.split("/", 1)[0]
    if sub not in (SUB_RESOURCES, SUB_HONORS):
        return None
    abs_path: str = os.path.join(UPLOAD_ROOT, *safe.split("/"))
    return abs_path if os.path.isfile(abs_path) else None


def delete_file(rel_path: str) -> int:
    """删除磁盘文件并返回释放的字节数（回收配额）；文件不存在返回 0。"""
    abs_path: Optional[str] = abs_of(rel_path)
    if abs_path is None:
        return 0
    try:
        size: int = os.path.getsize(abs_path)
        os.remove(abs_path)
        return size
    except OSError:
        return 0


def storage_report() -> dict[str, int]:
    """
    存储用量报告（后台"存储用量"仪表用）。
    used   = 数据库记录的 file 类型字节合计（权威值，与配额口径一致）
    quota  = 配额上限
    disk   = uploads/ 目录实际占用（含 honors 图片等，供交叉核对）
    """
    used: int = 0
    try:
        import portal_db
        used = portal_db.storage_used_bytes()
    except Exception:  # noqa: BLE001
        used = 0
    disk: int = 0
    for root, _dirs, files in os.walk(UPLOAD_ROOT):
        for fn in files:
            try:
                disk += os.path.getsize(os.path.join(root, fn))
            except OSError:
                continue
    return {"used": used, "quota": TOTAL_QUOTA_BYTES, "disk": disk,
            "percent": int(round(used * 100.0 / TOTAL_QUOTA_BYTES)) if TOTAL_QUOTA_BYTES else 0}
