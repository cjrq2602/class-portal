"""
migrate_part6.py —— 第六期迁移：网站更新公告 + 密码重置码（幂等）
====================================================================
用法（PythonAnywhere Bash）：
    cd ~/mysite && python3.10 migrate_part6.py

承诺：
1. 全部 CREATE TABLE/INDEX IF NOT EXISTS —— 可重复执行，零副作用；
2. 绝不改动前五期表结构与游戏旧表；users 仅幂等追加 password_changed_at 列
   （PRAGMA 探测后 ALTER TABLE，重复执行自动跳过）；
3. 第 6 期新增 3 张表：site_updates / site_update_reads / password_reset_codes；
4. 幂等落地权限：update.manage → root + post（admin-post）；superadmin 凭
   admin.full 自动拥有，无需显式授权；不重置任何既有密码；
5. 跑完打印表行数与索引确认。退出码 0 = 就绪。
"""

from __future__ import annotations

import os
import sys

PROJECT_HOME: str = os.path.dirname(os.path.abspath(__file__))
if PROJECT_HOME not in sys.path:
    sys.path.insert(0, PROJECT_HOME)

import db           # noqa: E402  只借用连接管理
import portal_db    # noqa: E402  门户建表与数据访问

PART6_TABLES: list[str] = ["site_updates", "site_update_reads",
                           "password_reset_codes"]
PART6_INDEXES: list[str] = ["idx_updates_pub", "idx_reset_codes_target"]


def main() -> None:
    # 1. 前五期 + 第 6 期新表（全部幂等）
    db.init_db()
    portal_db.init_portal_db()
    portal_db.init_part5_db()
    portal_db.init_part6_db()

    # 2. users 幂等补列（改密后强制全端下线的时间戳）
    added: bool = portal_db.ensure_pwd_changed_column()
    print(f"== users.password_changed_at 列：{'新增' if added else '已存在（跳过）'} ==")

    # 3. 第 6 期权限（幂等 upsert + grant；superadmin 由 admin.full 自动覆盖）
    print("== 第 6 期权限与角色 ==")
    perm_update: int = portal_db.upsert_permission("update.manage", "网站更新管理")
    root = portal_db.get_role_by_name("root")
    if root is not None:
        portal_db.grant(int(root["id"]), perm_update)
        print(f"  角色 root ← update.manage(id={perm_update})")
    post = portal_db.get_role_by_name("post")
    if post is not None:
        portal_db.grant(int(post["id"]), perm_update)
        print("  角色 post(admin-post) ← update.manage")
    sa = portal_db.get_role_by_name(portal_db.SUPERADMIN_ROLE)
    if sa is not None:
        print("  角色 superadmin 凭 admin.full 自动拥有全部新权限（无需授权）")

    # 4. 行数与索引确认
    with db.get_db() as conn:
        idx_rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    existing: set[str] = {str(r["name"]) for r in idx_rows}
    missing: list[str] = [i for i in PART6_INDEXES if i not in existing]
    print("== 第六期迁移完成（幂等） ==")
    counts: dict[str, int] = portal_db.table_counts()
    for t in PART6_TABLES:
        print(f"  [第六期] {t:<22} {counts.get(t, 0)}")
    if missing:
        print(f"  !! 缺失索引：{', '.join(missing)}")
        sys.exit(1)
    print(f"  索引确认：{', '.join(PART6_INDEXES)} 全部在位")
    print("提示：接下来 Reload Web 应用即可（seed_admin_final.py 已同步升级，可选跑）")


if __name__ == "__main__":
    main()
