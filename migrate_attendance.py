"""
migrate_attendance.py —— 线上迁移：考勤模块（日常考勤 / 定位签到）
================================================================
用法（PythonAnywhere Bash，git pull 之后、Reload 之前执行一次）：
    cd ~/mysite && python3.10 migrate_attendance.py

行为（全部幂等，可重复执行）：
1. 建考勤表 attendance_sessions / attendance_records（IF NOT EXISTS）；
   老库自动补列：sessions.group_id（关联名单分组）、records.manual（补签标记）；
2. 复用 seed_admin 的幂等逻辑：写入 attendance.manage 权限、
   attendance 角色（考勤管理员）、monitor 角色（班长）、life 角色追加考勤权限、
   新建 admin-attendance 账号（密码仅本次打印）；
3. 写审计留痕。
备注：名单分组与成员由 migrate_roster_groups.py / 随机点名页维护，
考勤模块直接复用（按「账号昵称 = 名单姓名」对账）。
"""

from __future__ import annotations

import os
import sys

PROJECT_HOME: str = os.path.dirname(os.path.abspath(__file__))
if PROJECT_HOME not in sys.path:
    sys.path.insert(0, PROJECT_HOME)

import portal_db      # noqa: E402
import seed_admin     # noqa: E402  复用幂等种子逻辑


def main() -> None:
    portal_db.init_attendance_db()
    print("== 考勤表就绪 ==")
    perm_ids: dict[str, int] = seed_admin.seed_perms()
    role_ids: dict[str, int] = seed_admin.seed_roles(perm_ids)
    seed_admin.seed_admins(role_ids)
    portal_db.add_audit(None, "migrate_attendance", "attendance", "tables+perm+role", "")
    print("== 考勤模块迁移完成 ==")


if __name__ == "__main__":
    main()
