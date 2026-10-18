#!/usr/bin/env python3
"""
scripts/seed_superadmin.py —— 创建/重置超级管理员（密码绝不写死在代码里）
=====================================================================
用法（PythonAnywhere Bash）：
    cd ~/mysite && python3.10 scripts/seed_superadmin.py \
        --username springfox --password '你的强密码'

规则：
1. 用户名/密码全部走命令行参数传入——代码里没有任何默认口令；
2. 密码策略与注册/改密一致：≥8 位且同时含字母与数字（common.check_password_strength）；
3. 幂等：用户名已存在 → 若已是 superadmin 则仅重置密码；若是普通账号则拒绝
   （不夺舍既有账号，请换用户名或先手工处理）；
4. 跑完打印确认，并提示执行 history -c 清理 bash 历史（防密码落盘）。
"""

from __future__ import annotations

import argparse
import os
import sys

_SCRIPT_DIR: str = os.path.dirname(os.path.abspath(__file__))
_MYSITE_DIR: str = os.path.dirname(_SCRIPT_DIR)
if _MYSITE_DIR not in sys.path:
    sys.path.insert(0, _MYSITE_DIR)

import common      # noqa: E402
import db          # noqa: E402
import portal_db   # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="创建/重置超级管理员")
    parser.add_argument("--username", required=True, help="登录名（3~20 位字母数字下划线）")
    parser.add_argument("--password", required=True, help="密码（≥8 位且含字母与数字）")
    args = parser.parse_args()

    username: str = str(args.username).strip().lower()
    password: str = str(args.password)

    # ---- 校验 ----
    import re
    if not re.match(r"^[A-Za-z0-9_]{3,20}$", username):
        print("!! 用户名需为 3~20 位字母、数字或下划线")
        sys.exit(2)
    pw_err = common.check_password_strength(password)
    if pw_err:
        print(f"!! 密码不合规：{pw_err}")
        sys.exit(2)

    # ---- 确保角色存在（migrate_part5 已建；此处幂等兜底） ----
    db.init_db()
    portal_db.init_portal_db()
    portal_db.init_part5_db()
    role = portal_db.get_role_by_name(portal_db.SUPERADMIN_ROLE)
    if role is None:
        role_id: int = portal_db.upsert_role(portal_db.SUPERADMIN_ROLE, "超级管理员")
        perm_id: int = portal_db.upsert_permission(
            portal_db.ADMIN_FULL_PERM, "超级管理员全权（超越一切权限）")
        portal_db.grant(role_id, perm_id)
        role = portal_db.get_role_by_name(portal_db.SUPERADMIN_ROLE)
    assert role is not None
    role_id = int(role["id"])

    # ---- 创建或重置 ----
    existing = portal_db.get_user_by_username(username)
    if existing is None:
        uid: int = portal_db.create_user(
            username, "超级管理员", common.hash_password(password), role_id)
        portal_db.add_audit(None, "seed_superadmin", username,
                            "created", "cli")
        print(f"[OK] 超级管理员已创建：{username} (uid={uid})")
    else:
        uid = int(existing["id"])
        if str(existing["role_name"]) != portal_db.SUPERADMIN_ROLE:
            print(f"!! 用户名 {username} 已被普通账号占用，为避免夺舍请换一个用户名")
            sys.exit(3)
        portal_db.set_password(uid, common.hash_password(password))
        portal_db.set_banned(uid, False)  # 误封时顺带解封，保证超管可用
        portal_db.add_audit(uid, "seed_superadmin", username,
                            "password_reset", "cli")
        print(f"[OK] 超级管理员密码已重置：{username} (uid={uid})")

    print(f"    当前在册超管数：{portal_db.count_superadmins()}")
    print("!! 安全提醒：密码曾出现在命令行，请立即执行  history -c  清理 bash 历史")


if __name__ == "__main__":
    main()
