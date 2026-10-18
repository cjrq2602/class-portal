"""
migrate_part5.py —— 第五期迁移：登录日志/资源导航 建表 + 补强索引 + 超管角色（幂等）
=================================================================================
用法（PythonAnywhere Bash）：
    cd ~/mysite && python3.10 migrate_part5.py

承诺：
1. 全部 CREATE TABLE/INDEX IF NOT EXISTS —— 可重复执行，零副作用；
2. 绝不改动前四期表结构，更不触碰游戏旧表；
3. 第 5 期新增 4 张表：login_logs / link_categories / friendly_links / link_submissions；
   补强 4 个索引：idx_news_created / idx_tucao_created / idx_login_logs_user / idx_links_cat；
4. 幂等清理体检发现的孤儿数据（外键悬空行）与 10 分钟无活动房间；
5. 幂等落地第 5 期权限与角色：admin.full（superadmin 专属）+ link.manage（root/admin-life）；
6. 跑完打印各表行数与索引确认。退出码 0 = 就绪。
"""

from __future__ import annotations

import os
import sys

PROJECT_HOME: str = os.path.dirname(os.path.abspath(__file__))
if PROJECT_HOME not in sys.path:
    sys.path.insert(0, PROJECT_HOME)

import db           # noqa: E402  只借用连接管理
import portal_db    # noqa: E402  门户建表与数据访问

PART5_TABLES: list[str] = ["login_logs", "link_categories",
                           "friendly_links", "link_submissions"]
PART5_INDEXES: list[str] = ["idx_news_created", "idx_tucao_created",
                            "idx_login_logs_user", "idx_login_logs_created",
                            "idx_links_cat", "idx_link_sub_status"]

# 体检发现的孤儿数据清理（外键悬空；全部幂等 DELETE ... WHERE NOT IN）
_ORPHAN_CLEANUPS: list[tuple[str, str]] = [
    ("room_players 无对应 room",
     "DELETE FROM room_players WHERE room_id NOT IN (SELECT id FROM rooms)"),
    ("game_state 无对应 room",
     "DELETE FROM game_state WHERE room_id NOT IN (SELECT id FROM rooms)"),
    ("chat_messages 无对应 room",
     "DELETE FROM chat_messages WHERE room_id NOT IN (SELECT id FROM rooms)"),
    ("homework_done 无对应 homework",
     "DELETE FROM homework_done WHERE homework_id NOT IN (SELECT id FROM homework)"),
    ("quote_likes 无对应 quote",
     "DELETE FROM quote_likes WHERE quote_id NOT IN (SELECT id FROM quotes)"),
    ("checkin_records 无对应 challenge",
     "DELETE FROM checkin_records WHERE challenge_id NOT IN (SELECT id FROM challenges)"),
    ("activity_registrations 无对应 activity",
     "DELETE FROM activity_registrations WHERE activity_id NOT IN (SELECT id FROM activities)"),
    ("tucao_likes 无对应 post",
     "DELETE FROM tucao_likes WHERE post_id NOT IN (SELECT id FROM tucao_posts)"),
    ("tucao_reports 无对应 post",
     "DELETE FROM tucao_reports WHERE post_id NOT IN (SELECT id FROM tucao_posts)"),
]


def main() -> None:
    # 1. 游戏旧表 + 前四期门户表 + 第 5 期新表（全部幂等）
    db.init_db()
    portal_db.init_portal_db()
    portal_db.init_part5_db()

    # 2. 幂等清理孤儿数据（体检报告 P1）
    print("== 孤儿数据清理（幂等） ==")
    with db.get_db() as conn:
        for desc, sql in _ORPHAN_CLEANUPS:
            try:
                cur = conn.execute(sql)
                print(f"  {desc:<28} 清理 {cur.rowcount} 行")
            except Exception as e:  # noqa: BLE001 —— 表不存在等异常不阻断迁移
                print(f"  {desc:<28} 跳过（{type(e).__name__}）")

    # 3. 清理 10 分钟无活动房间（体检报告 P2；标记 abandoned，不删数据）
    try:
        from game import match
        cleaned: int = match.cleanup_idle_rooms()
        print(f"== 超时房间清理：{cleaned} 个标记 abandoned ==")
    except Exception as e:  # noqa: BLE001
        print(f"== 超时房间清理跳过（{type(e).__name__}）==")

    # 4. 第 5 期权限与角色（幂等 upsert + grant）
    print("== 第 5 期权限与角色 ==")
    perm_full: int = portal_db.upsert_permission(
        portal_db.ADMIN_FULL_PERM, "超级管理员全权（超越一切权限）")
    perm_link: int = portal_db.upsert_permission("link.manage", "资源导航管理")
    role_sa: int = portal_db.upsert_role(portal_db.SUPERADMIN_ROLE, "超级管理员")
    portal_db.grant(role_sa, perm_full)
    print(f"  角色 superadmin(id={role_sa}) ← 权限 admin.full(id={perm_full})")
    root = portal_db.get_role_by_name("root")
    if root is not None:
        portal_db.grant(int(root["id"]), perm_link)
        print(f"  角色 root ← link.manage(id={perm_link})")
    life = portal_db.get_role_by_name("life")
    if life is not None:
        portal_db.grant(int(life["id"]), perm_link)
        print("  角色 life ← link.manage")
    # 超管角色不授予 admin.access 等具体权限——admin.full 在鉴权层展开为全部

    # 5. 行数与索引确认
    counts: dict[str, int] = portal_db.table_counts()
    print("== 第五期迁移完成（幂等） ==")
    for t in PART5_TABLES:
        print(f"  [第五期] {t:<22} {counts.get(t, 0)}")
    with db.get_db() as conn:
        idx_rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    existing: set[str] = {str(r["name"]) for r in idx_rows}
    missing: list[str] = [i for i in PART5_INDEXES if i not in existing]
    if missing:
        print(f"  !! 缺失索引：{', '.join(missing)}")
        sys.exit(1)
    print(f"  索引确认：{', '.join(PART5_INDEXES)} 全部在位")
    print(f"  门户表总数：{len(counts)} 张（含第 5 期 4 张）")
    print("提示：接下来执行")
    print("  python3.10 scripts/seed_superadmin.py --username <名> --password <密码>")
    print("  python3.10 scripts/seed_links.py")


if __name__ == "__main__":
    main()
