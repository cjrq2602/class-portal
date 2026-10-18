"""
migrate_part1.py —— 第一期迁移：创建门户新表（幂等，可重复执行）
================================================================
用法（PythonAnywhere Bash）：
    cd ~/mysite && python3.10 migrate_part1.py
承诺：
1. 全部 CREATE TABLE IF NOT EXISTS —— 重复执行零副作用；
2. 绝不触碰游戏旧表（players/match_queue/rooms/room_players/game_state/chat_messages）；
3. 跑完打印各门户表行数，便于人工核对。
"""

from __future__ import annotations

import os
import sys

# 保证从任意目录执行都能找到项目模块
PROJECT_HOME: str = os.path.dirname(os.path.abspath(__file__))
if PROJECT_HOME not in sys.path:
    sys.path.insert(0, PROJECT_HOME)

import db            # noqa: E402  游戏库连接管理（只借用连接，不动游戏表）
import portal_db     # noqa: E402  门户表结构与建表


def main() -> None:
    # 1. 游戏旧表保持存在（首次部署也顺手建，幂等）
    db.init_db()
    # 2. 创建门户新表
    portal_db.init_portal_db()
    # 3. 打印行数核对
    print("== 第一期迁移完成（幂等），各门户表行数 ==")
    for table, count in portal_db.table_counts().items():
        print(f"  {table:<18} {count}")
    print("提示：接下来执行 python3.10 seed_admin.py 生成角色/权限/7 个管理员")


if __name__ == "__main__":
    main()
