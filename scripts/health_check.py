#!/usr/bin/env python3
"""
scripts/health_check.py —— 全站体检（第 5 期任务一）
=====================================================
职责：对数据库 / 存储 / 业务 / 代码 / 日志五个维度做只读体检，
输出 Markdown 体检报告（问题按 P0致命/P1严重/P2一般/P3建议 分级），
并对每个 P0/P1 给出对应修复指引（修复代码见各任务的落地文件）。

用法：
    cd mysite
    python scripts/health_check.py                 # 报告打到 stdout
    python scripts/health_check.py --out report.md # 同时写入文件
    python scripts/health_check.py --db /path/nightwatch.db

平台约束：纯标准库；只读操作（除 PRAGMA integrity_check 外不写库）；
可在 PythonAnywhere 免费版直接运行（秒级完成）。
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Optional

# ---- 让脚本在 mysite/ 或 mysite/scripts/ 下都能 import 到项目模块 ----
_SCRIPT_DIR: str = os.path.dirname(os.path.abspath(__file__))
_MYSITE_DIR: str = os.path.dirname(_SCRIPT_DIR)
if _MYSITE_DIR not in sys.path:
    sys.path.insert(0, _MYSITE_DIR)

import db as game_db  # noqa: E402  游戏层（DB_PATH / 连接）

DB_PATH: str = game_db.DB_PATH
UPLOAD_ROOT: str = os.path.join(_MYSITE_DIR, "uploads")
TEMPLATE_DIR: str = os.path.join(_MYSITE_DIR, "templates")
STATIC_DIR: str = os.path.join(_MYSITE_DIR, "static")
PY_DIRS: list[str] = [_MYSITE_DIR, os.path.join(_MYSITE_DIR, "blueprints"),
                      os.path.join(_MYSITE_DIR, "game")]

# ---- 四期 + 第五期应有业务表（游戏 6 + 门户 36 + 第5期新增 4） ----
EXPECTED_TABLES: list[str] = [
    # 游戏旧表（6，结构一字不改）
    "players", "match_queue", "rooms", "room_players", "game_state", "chat_messages",
    # 第 1 期（8）
    "users", "roles", "permissions", "role_permissions",
    "invites", "audit_logs", "announcements", "game_stats",
    # 第 2 期（8）
    "news_items", "news_sync_state", "news_digests", "tucao_posts",
    "tucao_likes", "tucao_reports", "sensitive_words", "site_settings",
    # 第 3 期（9）
    "activities", "activity_tags", "activity_tag_map", "activity_registrations",
    "homework", "homework_done", "resources", "duty_schedule", "honors",
    # 第 4 期（5）
    "quotes", "quote_likes", "challenges", "checkin_records", "class_members",
    # 第 5 期新增（4）
    "login_logs", "link_categories", "friendly_links", "link_submissions",
]
# 第 5 期才出现的表：缺失不算致命（未跑 migrate_part5 属预期），按 P1 提示迁移
PART5_TABLES: set[str] = {"login_logs", "link_categories",
                          "friendly_links", "link_submissions"}

# ---- 关键索引（体检确认查询有索引托底；缺失按 P2 提示补） ----
EXPECTED_INDEXES: dict[str, str] = {
    "uq_news_source_url": "news_items(source,url) 唯一",
    "uq_digest_type_date": "news_digests(ntype,ndate) 唯一",
    "idx_tucao_status": "吐槽列表按状态+时间",
    "idx_act_start": "活动按开始时间",
    "idx_hw_due": "作业按截止",
    "idx_ck_chal_date": "打卡记录按挑战+日期",
    "idx_audit_created": "审计按时间",
    # 第 5 期补强（migrate_part5 落地）
    "idx_news_created": "news_items(created_at) 首页最新新闻",
    "idx_tucao_created": "tucao_posts(created_at) 首页热门吐槽",
    "idx_login_logs_user": "login_logs(user_id,created_at)",
    "idx_links_cat": "friendly_links(category_id,is_active,sort)",
}

UPLOAD_QUOTA_BYTES: int = 400 * 1024 * 1024  # 与 uploads.TOTAL_QUOTA_BYTES 保持一致
ROOM_STALE_SECONDS: int = 600                # 10 分钟无活动房间
ERROR_LOG: str = os.path.join(_MYSITE_DIR, "error.log")


# ============================================================
# 问题收集器
# ============================================================
@dataclass
class Issue:
    """单条体检发现。sev ∈ P0致命/P1严重/P2一般/P3建议。"""
    sev: str
    title: str
    detail: str
    fix: str = ""          # P0/P1 必须给修复指引


@dataclass
class Section:
    """报告小节：标题 + 结论行 + 问题列表。"""
    name: str
    ok_lines: list[str] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)


SECTIONS: list[Section] = []


def _sec(name: str) -> Section:
    s = Section(name=name)
    SECTIONS.append(s)
    return s


# ============================================================
# 一、数据库体检
# ============================================================
def check_database(conn: sqlite3.Connection) -> None:
    s = _sec("一、数据库")
    # ---- 完整性 ----
    row = conn.execute("PRAGMA integrity_check").fetchone()
    if row and row[0] == "ok":
        s.ok_lines.append("PRAGMA integrity_check = ok")
    else:
        s.issues.append(Issue(
            "P0", "数据库完整性校验失败", f"integrity_check 返回: {row[0] if row else '无结果'}",
            "立即停服备份 nightwatch.db，用 sqlite3 命令行 .recover 导出重建；"
            "恢复后重跑本脚本确认 ok。"))

    # ---- 表齐全性 ----
    existing: set[str] = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    missing: list[str] = [t for t in EXPECTED_TABLES if t not in existing]
    missing_p5: list[str] = [t for t in missing if t in PART5_TABLES]
    missing_base: list[str] = [t for t in missing if t not in PART5_TABLES]
    if not missing:
        s.ok_lines.append(f"全部 {len(EXPECTED_TABLES)} 张业务表齐全")
    if missing_base:
        s.issues.append(Issue(
            "P0", f"缺失基础业务表 {len(missing_base)} 张", ", ".join(missing_base),
            "依次跑 python migrate_part1.py / migrate_part2.py / migrate_part3.py，"
            "再跑 python migrate_v4_final.py 校验退出码为 0。"))
    if missing_p5:
        s.issues.append(Issue(
            "P1", f"第 5 期新表未建 {len(missing_p5)} 张", ", ".join(missing_p5),
            "跑 python migrate_part5.py（幂等）后重跑本脚本。"))

    # ---- 索引齐全性 ----
    idx_existing: set[str] = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    idx_missing: list[str] = [k for k in EXPECTED_INDEXES if k not in idx_existing]
    if not idx_missing:
        s.ok_lines.append(f"全部 {len(EXPECTED_INDEXES)} 个关键索引在位")
    else:
        p5_idx = [k for k in idx_missing if k.startswith(("idx_news_created",
                  "idx_tucao_created", "idx_login_logs", "idx_links"))]
        base_idx = [k for k in idx_missing if k not in p5_idx]
        if base_idx:
            s.issues.append(Issue(
                "P1", f"缺失关键索引 {len(base_idx)} 个",
                "; ".join(f"{k}({EXPECTED_INDEXES[k]})" for k in base_idx),
                "跑对应期次 migrate 脚本补建；补建后 ANALYZE 一下更佳。"))
        if p5_idx:
            s.issues.append(Issue(
                "P2", "第 5 期计划补强索引未建",
                "; ".join(f"{k}({EXPECTED_INDEXES[k]})" for k in p5_idx),
                ""))

    # ---- 孤儿数据（外键悬空） ----
    orphan_checks: list[tuple[str, str, str]] = [
        # (描述, 统计 SQL, 清理 SQL 建议)
        ("room_players 无对应 room",
         "SELECT COUNT(*) FROM room_players rp LEFT JOIN rooms r ON r.id=rp.room_id WHERE r.id IS NULL",
         "DELETE FROM room_players WHERE room_id NOT IN (SELECT id FROM rooms)"),
        ("game_state 无对应 room",
         "SELECT COUNT(*) FROM game_state g LEFT JOIN rooms r ON r.id=g.room_id WHERE r.id IS NULL",
         "DELETE FROM game_state WHERE room_id NOT IN (SELECT id FROM rooms)"),
        ("chat_messages 无对应 room",
         "SELECT COUNT(*) FROM chat_messages c LEFT JOIN rooms r ON r.id=c.room_id WHERE r.id IS NULL",
         "DELETE FROM chat_messages WHERE room_id NOT IN (SELECT id FROM rooms)"),
        ("homework_done 无对应 homework",
         "SELECT COUNT(*) FROM homework_done d LEFT JOIN homework h ON h.id=d.homework_id WHERE h.id IS NULL",
         "DELETE FROM homework_done WHERE homework_id NOT IN (SELECT id FROM homework)"),
        ("quote_likes 无对应 quote",
         "SELECT COUNT(*) FROM quote_likes l LEFT JOIN quotes q ON q.id=l.quote_id WHERE q.id IS NULL",
         "DELETE FROM quote_likes WHERE quote_id NOT IN (SELECT id FROM quotes)"),
        ("checkin_records 无对应 challenge",
         "SELECT COUNT(*) FROM checkin_records c LEFT JOIN challenges h ON h.id=c.challenge_id WHERE h.id IS NULL",
         "DELETE FROM checkin_records WHERE challenge_id NOT IN (SELECT id FROM challenges)"),
        ("activity_registrations 无对应 activity",
         "SELECT COUNT(*) FROM activity_registrations a LEFT JOIN activities t ON t.id=a.activity_id WHERE t.id IS NULL",
         "DELETE FROM activity_registrations WHERE activity_id NOT IN (SELECT id FROM activities)"),
        ("tucao_likes 无对应 post",
         "SELECT COUNT(*) FROM tucao_likes l LEFT JOIN tucao_posts p ON p.id=l.post_id WHERE p.id IS NULL",
         "DELETE FROM tucao_likes WHERE post_id NOT IN (SELECT id FROM tucao_posts)"),
        ("tucao_reports 无对应 post",
         "SELECT COUNT(*) FROM tucao_reports l LEFT JOIN tucao_posts p ON p.id=l.post_id WHERE p.id IS NULL",
         "DELETE FROM tucao_reports WHERE post_id NOT IN (SELECT id FROM tucao_posts)"),
        ("users 无对应 role",
         "SELECT COUNT(*) FROM users u LEFT JOIN roles r ON r.id=u.role_id WHERE r.id IS NULL",
         "把悬空用户的 role_id 改回 member 角色 id"),
    ]
    orphans: list[str] = []
    for desc, sql, fix_sql in orphan_checks:
        try:
            n: int = int(conn.execute(sql).fetchone()[0])
        except sqlite3.Error:
            continue  # 表未建时跳过（前面已报缺失）
        if n > 0:
            orphans.append(f"{desc} × {n}")
            s.issues.append(Issue(
                "P1", f"孤儿数据：{desc}", f"共 {n} 行悬空引用",
                f"评估后执行清理：{fix_sql}（migrate_part5.py 已内置幂等清理）。"))
    if not orphans:
        s.ok_lines.append("无孤儿数据（10 类外键悬空检查全过）")

    # ---- 行数概览（P3 信息） ----
    counts: list[str] = []
    for t in ("users", "news_items", "tucao_posts", "audit_logs",
              "checkin_records", "resources"):
        if t in existing:
            counts.append(f"{t}={int(conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0])}")
    s.ok_lines.append("行数概览：" + "，".join(counts))


# ============================================================
# 二、存储体检
# ============================================================
def check_storage(conn: sqlite3.Connection) -> None:
    s = _sec("二、存储")
    # ---- uploads 用量 vs 配额 ----
    used: int = 0
    disk_files: dict[str, int] = {}  # rel_path -> size
    if os.path.isdir(UPLOAD_ROOT):
        for root, _dirs, files in os.walk(UPLOAD_ROOT):
            for f in files:
                ap: str = os.path.join(root, f)
                rel: str = os.path.relpath(ap, UPLOAD_ROOT).replace(os.sep, "/")
                try:
                    size: int = os.path.getsize(ap)
                except OSError:
                    size = 0
                disk_files[rel] = size
                used += size
    percent: int = int(round(used * 100.0 / UPLOAD_QUOTA_BYTES)) if UPLOAD_QUOTA_BYTES else 0
    if percent >= 90:
        s.issues.append(Issue(
            "P0", "上传配额即将耗尽", f"已用 {used} 字节（{percent}% / 400MB）",
            "后台→资料库清理低价值文件；或联系同学分批下载后删除。"
            "uploads.enough_quota 会在写盘前拦截超限（507）。"))
    elif percent >= 70:
        s.issues.append(Issue("P2", "上传配额使用偏高",
                              f"已用 {percent}% / 400MB", ""))
    else:
        s.ok_lines.append(f"uploads 用量 {used // 1024} KB / 400MB（{percent}%）")

    # ---- 磁盘孤儿文件（在盘上不在 resources/honors 表里） ----
    known: set[str] = set()
    for table in ("resources", "honors"):
        try:
            rows = conn.execute(f"SELECT file_path FROM {table}").fetchall()
            known.update(str(r[0]) for r in rows if r[0])
        except sqlite3.Error:
            pass
    orphans: list[str] = [rel for rel in disk_files if rel not in known]
    if orphans:
        total: int = sum(disk_files[r] for r in orphans)
        s.issues.append(Issue(
            "P1", f"磁盘孤儿文件 {len(orphans)} 个",
            f"合计 {total // 1024} KB：" + ", ".join(orphans[:10])
            + (" …" if len(orphans) > 10 else ""),
            "确认为无主文件后删除回收配额（migrate_part5.py --gc 可自动清理）。"))
    else:
        s.ok_lines.append("无磁盘孤儿文件")

    # ---- 反向：表里有记录但磁盘丢失 ----
    missing_files: list[str] = [
        rel for rel in known
        if rel and not os.path.isfile(os.path.join(UPLOAD_ROOT, rel))]
    if missing_files:
        s.issues.append(Issue(
            "P1", f"数据库记录的文件在磁盘缺失 {len(missing_files)} 个",
            ", ".join(missing_files[:10]),
            "后台删除这些记录，或从备份恢复文件。"))

    # ---- WAL 文件大小 ----
    wal_path: str = DB_PATH + "-wal"
    if os.path.isfile(wal_path):
        wal_size: int = os.path.getsize(wal_path)
        if wal_size > 64 * 1024 * 1024:
            s.issues.append(Issue(
                "P1", "WAL 文件过大", f"{wal_size // 1024 // 1024} MB",
                "在低峰期执行 PRAGMA wal_checkpoint(TRUNCATE); 回收。"))
        else:
            s.ok_lines.append(f"WAL 文件 {wal_size // 1024} KB（正常）")
    else:
        s.ok_lines.append("无 WAL 残留文件")


# ============================================================
# 三、业务体检
# ============================================================
def check_business(conn: sqlite3.Connection) -> None:
    s = _sec("三、业务")
    now: float = time.time()

    # ---- 过期邀请码 ----
    try:
        n: int = int(conn.execute(
            "SELECT COUNT(*) FROM invites WHERE revoked_at IS NULL"
            " AND expires_at < ? AND used_count < max_uses", (now,)).fetchone()[0])
        if n > 20:
            s.issues.append(Issue("P2", f"过期未用尽邀请码 {n} 个（堆积）",
                                  "建议在后台清理", ""))
        else:
            s.ok_lines.append(f"过期未用尽邀请码 {n} 个（正常范围）")
    except sqlite3.Error:
        pass

    # ---- 10 分钟无活动房间 ----
    try:
        n = int(conn.execute(
            "SELECT COUNT(*) FROM rooms WHERE status != 'finished'"
            " AND updated_at < ?", (now - ROOM_STALE_SECONDS,)).fetchone()[0])
        if n > 0:
            s.issues.append(Issue(
                "P2", f"超时无活动未完结房间 {n} 个",
                "游戏侧惰性清理会在下次访问时回收；持续堆积可手动清库", ""))
        else:
            s.ok_lines.append("无超时无活动房间")
    except sqlite3.Error:
        pass

    # ---- 新闻最后同步时间 ----
    try:
        row = conn.execute(
            "SELECT value FROM news_sync_state WHERE key='last_sync_at'").fetchone()
        if row is None:
            s.issues.append(Issue(
                "P2", "新闻从未成功同步", "news_sync_state 无 last_sync_at",
                ""))
        else:
            lag_h: float = (now - float(row[0])) / 3600.0
            if lag_h > 26:
                s.issues.append(Issue(
                    "P1", "新闻同步长时间未成功", f"距上次成功 {lag_h:.1f} 小时",
                    "先跑 python connectivity_test.py 确认 GitHub 可达；"
                    "不可达时页面会自动降级为旧缓存并标注，属预期行为。"))
            else:
                s.ok_lines.append(f"新闻最后同步 {lag_h:.1f} 小时前")
    except sqlite3.Error:
        pass

    # ---- 今日/昨日 digest 是否存在（缺失说明惰性兜底失效） ----
    try:
        sys.path.insert(0, _MYSITE_DIR)
        import common  # 北京时间工具（与站点同源）
        for ntype in ("yesterday", "today"):
            date, _st, _en, due = common.digest_spec(ntype)
            row = conn.execute(
                "SELECT generator FROM news_digests WHERE ntype=? AND ndate=?",
                (ntype, date)).fetchone()
            if row:
                s.ok_lines.append(f"{ntype} 总结({date}) 已生成（{row[0]}）")
            elif now < due:
                s.ok_lines.append(f"{ntype} 总结({date}) 未到生成时刻（正常占位）")
            else:
                s.issues.append(Issue(
                    "P1", f"{ntype} 总结({date}) 已过生成时刻但缺失",
                    "惰性兜底未生效",
                    "访问一次 /digest 触发兜底；仍缺失则查 digest_service 与 news_sync 日志。"))
    except (sqlite3.Error, ImportError):
        pass

    # ---- 过期挑战未归档（惰性归档漏触发的信号） ----
    try:
        import common as _c
        today: str = _c.bj_date_str()
        n = int(conn.execute(
            "SELECT COUNT(*) FROM challenges WHERE archived=0 AND end_date < ?",
            (today,)).fetchone()[0])
        if n > 0:
            s.issues.append(Issue(
                "P2", f"已过期但未归档挑战 {n} 个",
                "访问 /checkin 或任意挑战接口会惰性归档", ""))
        else:
            s.ok_lines.append("无过期未归档挑战")
    except (sqlite3.Error, ImportError):
        pass


# ============================================================
# 四、代码静态扫描
# ============================================================
# 危险模式：字符串拼接 SQL（应为零，全部参数化）
_SQL_CONCAT_RES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\.execute\(\s*f[\"']"), "execute(f-string)"),
    (re.compile(r"\.execute\(\s*[\"'][^\"']*[\"']\s*%"), "execute(% 格式化)"),
    (re.compile(r"\.execute\(\s*[\"'][^\"']*[\"']\s*\+"), "execute(字符串拼接)"),
    (re.compile(r"\.execute\(\s*[\"'][^\"']*[\"']\s*\.format\("), "execute(.format)"),
]
# 允许例外：表名白名单拼进 COUNT 是安全的（portal_db.table_counts 等固定表名）
_SQL_WHITELIST: re.Pattern[str] = re.compile(
    r"SELECT COUNT\(\*\) FROM \{t\}|FROM \{\w+\}\"?\s*# ?固定表名")

# 已审计豁免（人工逐条确认过安全性的动态 SQL，特征匹配即放行并明示理由）：
# (文件名, 行内容子串, 豁免理由)
_SQL_AUDITED: list[tuple[str, str, str]] = [
    ("db.py", "PRAGMA busy_timeout",
     "PRAGMA 不支持参数化；拼接的是模块级 int 常量 BUSY_TIMEOUT_MS，无用户输入"),
    ("db.py", "IN ({marks})",
     "marks 由 '?'×n 拼成（纯占位符），全部值经第二参数参数化绑定"),
    ("portal_db.py", "UPDATE activities SET",
     "SET 列名经硬编码白名单 allowed 过滤（act_update），全部值参数化绑定"),
]


def _scan_py_sql(s: Section) -> None:
    hits: list[str] = []
    waived: list[str] = []
    for d in PY_DIRS:
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if not f.endswith(".py"):
                continue
            path: str = os.path.join(d, f)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    for i, line in enumerate(fh, 1):
                        for pat, label in _SQL_CONCAT_RES:
                            if not pat.search(line) or _SQL_WHITELIST.search(line):
                                continue
                            # 排除健康检查脚本自身（本文件含示例 SQL 字符串）
                            if f == "health_check.py":
                                continue
                            # 已审计豁免：特征匹配即放行并记录理由
                            audit: Optional[str] = next(
                                (reason for wf, feat, reason in _SQL_AUDITED
                                 if wf == f and feat in line), None)
                            if audit:
                                waived.append(f"{f}:{i} —— {audit}")
                            else:
                                hits.append(f"{f}:{i} [{label}] {line.strip()[:80]}")
            except OSError:
                continue
    for w in waived:
        s.ok_lines.append(f"动态 SQL 已审计豁免：{w}")
    if hits:
        s.issues.append(Issue(
            "P0", f"疑似字符串拼接 SQL {len(hits)} 处",
            "\n".join(hits[:20]),
            "全部改为参数化查询（? 占位）。逐条人工确认后立即修。"))
    else:
        s.ok_lines.append("未发现未豁免的字符串拼接 SQL（全参数化）")


def _scan_templates(s: Section) -> None:
    inline_scripts: list[str] = []
    inline_styles: list[str] = []
    safe_filters: list[str] = []
    style_attrs: int = 0
    ext_assets: list[str] = []
    for root, _dirs, files in os.walk(TEMPLATE_DIR):
        for f in sorted(files):
            if not f.endswith(".html"):
                continue
            path: str = os.path.join(root, f)
            rel: str = os.path.relpath(path, TEMPLATE_DIR)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text: str = fh.read()
            except OSError:
                continue
            for m in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>", text):
                tag: str = m.group(0)
                nonce: str = "（已有 nonce）" if "nonce=" in tag else "（缺 nonce）"
                inline_scripts.append(f"{rel} {nonce}")
            for m in re.finditer(r"<style[^>]*>", text):
                inline_styles.append(rel)
            for m in re.finditer(r"\|\s*safe\b", text):
                line: str = text[:m.start()].count("\n") + 1
                safe_filters.append(f"{rel}:{line}")
            style_attrs += len(re.findall(r"\bstyle=", text))
            # 外链资源扫描（http(s):// 引用的 js/css/字体/图标）
            for m in re.finditer(
                    r"(?:src|href)\s*=\s*[\"'](https?://[^\"']+)[\"']", text):
                ext_assets.append(f"{rel} → {m.group(1)}")
    if inline_scripts:
        no_nonce: list[str] = [x for x in inline_scripts if "缺 nonce" in x]
        sev: str = "P1" if no_nonce else "P3"
        s.issues.append(Issue(
            sev, f"内联 <script> {len(inline_scripts)} 处",
            "\n".join(inline_scripts),
            "为 CSP nonce 方案：全部加 nonce=\"{{ csp_nonce }}\"（任务三统一落地）。"))
    else:
        s.ok_lines.append("无内联 <script>")
    if inline_styles:
        s.issues.append(Issue(
            "P2", f"内联 <style> 块 {len(inline_styles)} 处",
            ", ".join(sorted(set(inline_styles))),
            ""))
    else:
        s.ok_lines.append("无内联 <style> 块")
    if safe_filters:
        s.issues.append(Issue(
            "P1", f"模板 |safe 未转义输出 {len(safe_filters)} 处（需人工确认非用户内容）",
            "\n".join(safe_filters),
            "凡输出用户内容的 |safe 一律去掉；仅保留服务端可信常量。"))
    else:
        s.ok_lines.append("无 |safe 未转义输出")
    s.ok_lines.append(f"style= 属性 {style_attrs} 处（CSP 对 style 采用 'unsafe-inline' 的依据）")
    if ext_assets:
        s.issues.append(Issue(
            "P0", f"模板外链资源 {len(ext_assets)} 处（违反白名单约束）",
            "\n".join(ext_assets),
            "全部本地化：下载到 static/ 或删除。"))
    else:
        s.ok_lines.append("模板零外链资源")


def check_code(s: Optional[Section] = None) -> None:
    sec: Section = s if s is not None else _sec("四、代码静态扫描")
    _scan_py_sql(sec)
    _scan_templates(sec)


# ============================================================
# 五、日志体检
# ============================================================
def check_logs(conn: sqlite3.Connection) -> None:
    s = _sec("五、日志")
    # ---- error.log ----
    if os.path.isfile(ERROR_LOG):
        try:
            with open(ERROR_LOG, "r", encoding="utf-8", errors="replace") as fh:
                lines: list[str] = fh.readlines()
            tail: list[str] = [ln.strip() for ln in lines[-50:] if ln.strip()]
            errs: list[str] = [ln for ln in tail if "ERROR" in ln or "Traceback" in ln]
            s.ok_lines.append(f"error.log 共 {len(lines)} 行，最近 50 行中错误 {len(errs)} 条")
            if errs:
                s.issues.append(Issue(
                    "P2", f"error.log 近期错误 {len(errs)} 条",
                    "\n".join(errs[-5:])[:600], ""))
        except OSError:
            s.ok_lines.append("error.log 不可读")
    else:
        s.issues.append(Issue(
            "P2", "error.log 不存在（日志轮转未配置）",
            "任务二将落地 RotatingFileHandler(5MB×2)", ""))

    # ---- audit_logs 行数 ----
    try:
        n: int = int(conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0])
        s.ok_lines.append(f"audit_logs 共 {n} 行")
    except sqlite3.Error:
        pass


# ============================================================
# 报告渲染
# ============================================================
SEV_ORDER: dict[str, int] = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def render_markdown() -> str:
    """把收集到的小节渲染成 Markdown 体检报告。"""
    all_issues: list[tuple[str, Issue]] = []
    for sec in SECTIONS:
        for iss in sec.issues:
            all_issues.append((sec.name, iss))
    counts: dict[str, int] = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
    for _sec_name, iss in all_issues:
        counts[iss.sev] += 1

    out: list[str] = []
    out.append("# 守夜广场 · 全站体检报告")
    out.append("")
    out.append(f"- 生成时间（北京）：{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(time.time() + 8 * 3600))}")
    out.append(f"- 数据库：`{DB_PATH}`")
    out.append(f"- 结论：**P0={counts['P0']} / P1={counts['P1']} / P2={counts['P2']} / P3={counts['P3']}**")
    verdict: str = "未通过：存在 P0/P1，必须先修复再上线" if (counts["P0"] or counts["P1"]) else "通过：P0/P1 清零"
    out.append(f"- 总评：**{verdict}**")
    out.append("")
    out.append("| 级别 | 含义 | 数量 |")
    out.append("| --- | --- | --- |")
    out.append(f"| P0 致命 | 数据损坏/注入风险/配额耗尽/外链资源 | {counts['P0']} |")
    out.append(f"| P1 严重 | 表缺失/孤儿数据/同步失效/未转义输出 | {counts['P1']} |")
    out.append(f"| P2 一般 | 索引缺失/配额偏高/日志未配置 | {counts['P2']} |")
    out.append(f"| P3 建议 | 优化项 | {counts['P3']} |")
    out.append("")

    for sec in SECTIONS:
        out.append(f"## {sec.name}")
        out.append("")
        for line in sec.ok_lines:
            out.append(f"- ✅ {line}")
        for iss in sorted(sec.issues, key=lambda x: SEV_ORDER[x.sev]):
            out.append(f"- **{iss.sev}｜{iss.title}**")
            for ln in iss.detail.splitlines():
                out.append(f"  - {ln}")
            if iss.fix:
                out.append(f"  - 🔧 修复：{iss.fix}")
        out.append("")

    # ---- P0/P1 修复清单汇总 ----
    p01: list[tuple[str, Issue]] = [x for x in all_issues if x[1].sev in ("P0", "P1")]
    out.append("## 修复清单（P0/P1）")
    out.append("")
    if not p01:
        out.append("P0/P1 清零，无需修复。")
    else:
        for i, (sec_name, iss) in enumerate(
                sorted(p01, key=lambda x: SEV_ORDER[x[1].sev]), 1):
            out.append(f"{i}. [{iss.sev}]（{sec_name}）{iss.title}")
            if iss.fix:
                out.append(f"   - {iss.fix}")
    out.append("")
    return "\n".join(out)


# ============================================================
# 入口
# ============================================================
def main() -> int:
    global DB_PATH
    parser = argparse.ArgumentParser(description="守夜广场全站体检")
    parser.add_argument("--db", default="", help="数据库路径（默认取项目 db.DB_PATH）")
    parser.add_argument("--out", default="", help="报告另存路径（默认仅 stdout）")
    args = parser.parse_args()

    DB_PATH = args.db or game_db.DB_PATH
    if not os.path.isfile(DB_PATH):
        print(f"数据库不存在: {DB_PATH}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
    try:
        check_database(conn)
        check_storage(conn)
        check_business(conn)
        check_code()
        check_logs(conn)
    finally:
        conn.close()

    report: str = render_markdown()
    print(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\n报告已写入: {args.out}", file=sys.stderr)
    # 退出码：有 P0/P1 → 1（CI 可拦截）；否则 0
    has_bad: bool = any(iss.sev in ("P0", "P1")
                        for sec in SECTIONS for iss in sec.issues)
    return 1 if has_bad else 0


if __name__ == "__main__":
    sys.exit(main())
