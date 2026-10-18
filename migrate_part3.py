"""
migrate_part3.py —— 第三期迁移：活动/作业/资料库/值日/荣誉 建表（幂等）
=====================================================================
用法（PythonAnywhere Bash）：
    cd ~/mysite && python3.10 migrate_part3.py

承诺：
1. 全部 CREATE TABLE IF NOT EXISTS —— 可重复执行，零副作用；
2. 绝不触碰第 1/2 期门户表结构，更不触碰游戏旧表
   （players/match_queue/rooms/room_players/game_state/chat_messages）；
3. 第三期新增 9 张表：activities / activity_tags / activity_tag_map /
   activity_registrations / homework / homework_done / resources /
   duty_schedule / honors；
4. 跑完打印各表行数 + 唯一约束确认；
5. 幂等初始化：resource_subjects（学科标签）默认值。
"""

from __future__ import annotations

import json
import os
import sys

PROJECT_HOME: str = os.path.dirname(os.path.abspath(__file__))
if PROJECT_HOME not in sys.path:
    sys.path.insert(0, PROJECT_HOME)

import db           # noqa: E402  只借用连接管理
import portal_db    # noqa: E402  门户建表与数据访问

PART3_TABLES: list[str] = [
    "activities", "activity_tags", "activity_tag_map", "activity_registrations",
    "homework", "homework_done", "resources", "duty_schedule", "honors",
]

DEFAULT_SUBJECTS: list[str] = ["语文", "数学", "英语", "物理", "化学", "生物", "其他"]


def main() -> None:
    # 1. 游戏旧表（幂等，全新部署也能跑通）
    db.init_db()
    # 2. 门户全部表（第 1/2/3 期，内部均为 IF NOT EXISTS）
    portal_db.init_portal_db()

    counts: dict[str, int] = portal_db.table_counts()
    print("== 第三期迁移完成（幂等） ==")
    for t in PART3_TABLES:
        print(f"  [第三期] {t:<24} {counts.get(t, 0)}")
    others: list[str] = [t for t in counts if t not in PART3_TABLES]
    print(f"  --- 第 1/2 期既有表 {len(others)} 张（未改动结构）---")
    for t in others:
        print(f"  {t:<24} {counts.get(t, 0)}")

    # 3. 关键唯一约束确认（幂等报名/完成/标签映射的基石）
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT name, tbl_name FROM sqlite_master WHERE type='index' AND name IN"
            " ('sqlite_autoindex_activity_registrations_1',"
            "  'sqlite_autoindex_homework_done_1',"
            "  'sqlite_autoindex_activity_tag_map_1')"
        ).fetchall()
    names: list[str] = [str(r["tbl_name"]) for r in rows]
    print(f"  唯一约束（复合主键）：{', '.join(names) if names else '（缺失，请检查）'}")

    # 4. 幂等初始化学科标签
    if not portal_db.setting_get("resource_subjects", ""):
        portal_db.setting_put("resource_subjects",
                              json.dumps(DEFAULT_SUBJECTS, ensure_ascii=False))
        print("  已写入默认学科标签：" + "、".join(DEFAULT_SUBJECTS))
    else:
        print("  学科标签保留原值：%s" % portal_db.setting_get("resource_subjects"))

    print("提示：接下来执行 python3.10 seed_admin.py（幂等补齐第 3 期权限 + 新建 admin-life）")


if __name__ == "__main__":
    main()
