"""
migrate_part2.py —— 第二期迁移：新闻中心 / 每日总结 / 吐槽墙 建表（幂等）
======================================================================
用法（PythonAnywhere Bash）：
    cd ~/mysite && python3.10 migrate_part2.py

承诺：
1. 全部 CREATE TABLE IF NOT EXISTS —— 可重复执行，零副作用；
2. 绝不触碰第 1 期门户表结构，更不触碰游戏旧表
   （players/match_queue/rooms/room_players/game_state/chat_messages）；
3. 第二期新增表：news_items / news_sync_state / news_digests /
   tucao_posts / tucao_likes / tucao_reports / sensitive_words / site_settings；
4. 跑完打印各表行数 + 关键索引确认，便于人工核对；
5. 附带幂等初始化：site_settings.tucao_mode 默认 post_first（先发后审）。
"""

from __future__ import annotations

import os
import sys

PROJECT_HOME: str = os.path.dirname(os.path.abspath(__file__))
if PROJECT_HOME not in sys.path:
    sys.path.insert(0, PROJECT_HOME)

import db           # noqa: E402  只借用连接管理，不动游戏表
import portal_db    # noqa: E402  门户建表与数据访问

# 第二期新增表（用于逐表核对）
PART2_TABLES: list[str] = [
    "news_items", "news_sync_state", "news_digests",
    "tucao_posts", "tucao_likes", "tucao_reports",
    "sensitive_words", "site_settings",
]


def main() -> None:
    # 1. 游戏旧表（幂等，保证首次全新部署也能跑通）
    db.init_db()
    # 2. 门户全部表（第 1 期 + 第 2 期，内部均为 IF NOT EXISTS）
    portal_db.init_portal_db()

    counts: dict[str, int] = portal_db.table_counts()
    print("== 第二期迁移完成（幂等） ==")
    for t in PART2_TABLES:
        print(f"  [第二期] {t:<18} {counts.get(t, 0)}")
    others: list[str] = [t for t in counts if t not in PART2_TABLES]
    print("  --- 第 1 期既有表（未改动结构） ---")
    for t in others:
        print(f"  {t:<22} {counts.get(t, 0)}")

    # 3. 索引确认（唯一约束是幂等去重的基石）
    with db.get_db() as conn:
        idx_rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name IN"
            " ('uq_news_source_url','uq_digest_type_date','uq_report_once')"
        ).fetchall()
    names: list[str] = sorted(str(r["name"]) for r in idx_rows)
    print(f"  唯一索引：{', '.join(names) if names else '（缺失，请检查）'}")

    # 4. 幂等默认设置
    if not portal_db.setting_get("tucao_mode", ""):
        portal_db.setting_put("tucao_mode", "post_first")
        print("  已写入默认设置：tucao_mode=post_first（先发后审）")
    else:
        print(f"  站点设置保留原值：tucao_mode={portal_db.setting_get('tucao_mode')}")

    print("提示：接下来执行 python3.10 seed_admin.py（幂等升级权限）"
          "，再执行 python3.10 connectivity_test.py（验证 GitHub 白名单连通性）")


if __name__ == "__main__":
    main()
