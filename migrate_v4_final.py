"""
migrate_v4_final.py —— 四期改造收官迁移：全表与索引完整性校验（幂等）
====================================================================
用法（PythonAnywhere Bash）：
    cd ~/mysite && python3.10 migrate_v4_final.py

职责：
1. 幂等建齐四期全部表（内部均为 CREATE TABLE IF NOT EXISTS，可反复执行）；
2. **校验**：逐表核对是否存在、逐索引核对唯一约束是否存在，缺失则报错退出；
3. 核对游戏旧表仍在（四期改造不得破坏游戏）；
4. 跑完打印汇总 + 各表行数，便于人工复核。

退出码：0 = 全部就绪；1 = 有缺漏（脚本会明确指出缺什么）。
"""

from __future__ import annotations

import os
import sys

PROJECT_HOME: str = os.path.dirname(os.path.abspath(__file__))
if PROJECT_HOME not in sys.path:
    sys.path.insert(0, PROJECT_HOME)

import db           # noqa: E402
import portal_db    # noqa: E402

# ---- 四期全部表 ----
P1_TABLES: list[str] = ["users", "roles", "permissions", "role_permissions",
                        "invites", "audit_logs", "announcements", "game_stats"]
P2_TABLES: list[str] = ["news_items", "news_sync_state", "news_digests",
                        "tucao_posts", "tucao_likes", "tucao_reports",
                        "sensitive_words", "site_settings"]
P3_TABLES: list[str] = ["activities", "activity_tags", "activity_tag_map",
                        "activity_registrations", "homework", "homework_done",
                        "resources", "duty_schedule", "honors"]
P4_TABLES: list[str] = ["quotes", "quote_likes", "challenges",
                        "checkin_records", "class_members"]

# 游戏旧表（必须仍在）
GAME_TABLES: list[str] = ["players", "match_queue", "rooms", "room_players",
                          "game_state", "chat_messages"]

# 幂等基石：必须存在的唯一索引/约束名（sqlite_autoindex_* 由 PRIMARY KEY 生成）
REQUIRED_INDEXES: list[str] = [
    "uq_news_source_url",              # 新闻去重
    "uq_digest_type_date",             # 总结 (type,date) 幂等
    "uq_report_once",                  # 举报每人每帖一次
    "sqlite_autoindex_activity_registrations_1",  # 报名幂等
    "sqlite_autoindex_homework_done_1",           # 作业完成幂等
    "sqlite_autoindex_checkin_records_1",         # 打卡每天一次
    "sqlite_autoindex_quote_likes_1",             # 每日一句点赞幂等
]


def main() -> int:
    print("== 四期收官迁移：建表（幂等） ==")
    db.init_db()
    portal_db.init_portal_db()

    with db.get_db() as conn:
        have_tables: set[str] = {str(r["name"]) for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        have_idx: set[str] = {str(r["name"]) for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}

    problems: list[str] = []
    total: int = 0
    for label, tables in (("第 1 期", P1_TABLES), ("第 2 期", P2_TABLES),
                          ("第 3 期", P3_TABLES), ("第 4 期", P4_TABLES),
                          ("游戏旧表", GAME_TABLES)):
        miss: list[str] = [t for t in tables if t not in have_tables]
        total += len(tables)
        flag = "✅" if not miss else "❌"
        print(f"  {flag} {label}：{len(tables) - len(miss)}/{len(tables)} 张表就绪"
              + (f"  缺失：{', '.join(miss)}" if miss else ""))
        if miss and label != "游戏旧表":
            problems.extend(miss)
        elif miss:
            problems.append("游戏旧表被破坏：" + ",".join(miss))

    miss_idx: list[str] = [i for i in REQUIRED_INDEXES if i not in have_idx]
    print(f"  {'✅' if not miss_idx else '❌'} 唯一约束："
          f"{len(REQUIRED_INDEXES) - len(miss_idx)}/{len(REQUIRED_INDEXES)} 个就绪"
          + (f"  缺失：{', '.join(miss_idx)}" if miss_idx else ""))
    if miss_idx:
        problems.extend(miss_idx)

    counts: dict[str, int] = portal_db.table_counts()
    print(f"== 各表行数（共 {total} 张业务表） ==")
    for t in (P4_TABLES + P3_TABLES + P2_TABLES + P1_TABLES):
        print(f"  {t:<26} {counts.get(t, 0)}")

    if problems:
        print("\n!! 校验未通过，缺失项：" + "、".join(problems))
        print("   处理：确认已上传全部迁移脚本后重跑本脚本；仍缺失请检查 schema 文件是否为最新。")
        return 1

    print("\n== 校验通过：四期全部表与唯一约束齐全，游戏旧表完整 ==")
    print("   部署流程：migrate_v4_final.py（本步）→ seed_admin_final.py → Reload")
    return 0


if __name__ == "__main__":
    sys.exit(main())
