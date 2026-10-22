#!/usr/bin/env python3
"""
migrate_roster_groups.py —— 点名名单分组迁移（幂等）
====================================================
用法（PythonAnywhere Bash）：
    cd ~/mysite && python3.10 migrate_roster_groups.py

行为：
1. 建 roster_groups 分组表，并给 class_members 加 group_id 列（已存在则跳过）；
2. 保证默认分组（id=1「默认名单」）存在；
3. 创建「燃气2602」分组并导入 50 人名单（按姓名去重，重跑不重复建行）；
4. 不触碰任何已有成员数据，可安全反复执行。
"""

from __future__ import annotations

import portal_db

# ============================================================
# 燃气2602 名单（来自「学生账号清单.xlsx」，共 50 人）
# ============================================================
GROUP_NAME: str = "燃气2602"
NAMES_2602: list[str] = [
    "李文乐", "胡惠贤", "潘晓月", "胡畅", "钟智",
    "袁浩哲", "李杰", "张玉辉", "吴函逾", "张粤光",
    "孙岩", "王宇翔", "陈铭浩", "张先恒", "姜观星",
    "王诗文", "辛宇航", "陈鸿明", "鲁强语", "袁杰",
    "朱颢宇", "王艺霖", "吴张迪", "徐秋月", "严宇辰",
    "贾兴甜", "张恒轩", "吴翔", "朱子豪", "梁卫涛",
    "董文蓉", "柏耀扬", "吴嘉妮", "朱择航", "衡毅宸",
    "张雨乐", "韩瑞泽", "张翊乔", "周子禾", "江思涵",
    "刘铭涛", "陈浩然", "赵乐然", "凌梦溪", "谢睿萱",
    "赵伟智", "刘桐炜", "许曼", "崔浩然", "魏来",
]


def main() -> None:
    print("== 点名名单分组迁移 ==")

    # 1. 建表 + 加列 + 默认分组（幂等）
    portal_db.init_roster_groups()
    print("  [ok] roster_groups 表 / class_members.group_id 列 / 默认分组")

    # 2. 燃气2602 分组
    gid: int | None = None
    for g in portal_db.roster_groups():
        if g["name"] == GROUP_NAME:
            gid = int(g["id"])
            break
    if gid is None:
        gid = portal_db.roster_group_add(GROUP_NAME)
        print(f"  [新建分组] {GROUP_NAME} (id={gid})")
    else:
        print(f"  [分组已存在] {GROUP_NAME} (id={gid})")
    assert gid is not None

    # 3. 导入 50 人（姓名全局唯一 → 重跑全部记为重复，不会产生重复行）
    result = portal_db.member_batch_add(NAMES_2602, gid)
    print(f"  [导入] 新增 {result['added']} 人，"
          f"跳过重复 {result['skipped_dup']} 人，无效 {result['invalid']} 人")

    # 4. 汇总
    print("== 当前分组 ==")
    for g in portal_db.roster_groups():
        print(f"  · {g['name']}: 共 {g['count']} 人（在册 {g['active_count']}）")
    print("完成。")


if __name__ == "__main__":
    main()
