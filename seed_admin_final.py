"""
seed_admin_final.py —— 四期改造收官：权限矩阵定稿（幂等）
==========================================================
用法（PythonAnywhere Bash）：
    cd ~/mysite && python3.10 seed_admin_final.py

行为：
1. 幂等：权限/角色/关联已存在则更新或跳过；
   **已存在的管理员账号只补权限，绝不重置密码**；
2. 新建尚未存在的管理员时才生成随机强密码并打印（仅本次可见）；
3. 跑完打印完整权限矩阵表（权限 code × 8 角色）；
4. 幂等清理历史废弃权限（post.manage / event.manage）。

权限矩阵（★=拥有）：
    code                 root  news  event tucao user  game  post  life  member
    admin.access          ★     ★     ★     ★    ★     ★     ★     ★
    audit.view            ★     ★     ★     ★    ★     ★     ★     ★
    news.manage           ★     ★
    activity.manage       ★                ★(event)
    tucao.moderate        ★                       ★(tucao)
    user.manage           ★                                  ★(user)
    roster.manage         ★                                  ★(user)
    game.manage           ★                                        ★(game)
    announcement.manage   ★                                              ★(post)
    homework.manage       ★                                              ★(post)
    honor.manage          ★                                              ★(post)
    quote.moderate        ★                                              ★(post)
    resource.manage       ★                                                    ★(life)
    duty.manage           ★                                                    ★(life)
    checkin.manage        ★                                                    ★(life)
"""

from __future__ import annotations

import os
import sys

PROJECT_HOME: str = os.path.dirname(os.path.abspath(__file__))
if PROJECT_HOME not in sys.path:
    sys.path.insert(0, PROJECT_HOME)

import common       # noqa: E402  密码哈希 / 随机密码
import db           # noqa: E402  连接管理
import portal_db    # noqa: E402  数据访问

# ---------------- 权限清单（code → 中文名） ----------------
PERMISSIONS: list[tuple[str, str]] = [
    ("admin.access", "进入管理后台"),
    ("audit.view", "审计日志查看"),
    ("user.manage", "用户管理"),
    ("invite.manage", "邀请码管理"),
    ("roster.manage", "点名名单管理"),
    ("news.manage", "新闻源与每日总结管理"),
    ("announcement.manage", "公告管理"),
    ("tucao.moderate", "吐槽审核与敏感词"),
    ("quote.moderate", "每日一句审核"),
    ("activity.manage", "活动与比赛管理"),
    ("homework.manage", "作业管理"),
    ("honor.manage", "荣誉墙管理"),
    ("game.manage", "游戏房间管理"),
    ("resource.manage", "学习资料库管理"),
    ("duty.manage", "值日表管理"),
    ("checkin.manage", "打卡挑战管理"),
    ("update.manage", "网站更新管理"),   # 第六期
]

# 历史废弃权限（幂等清理：先删关联再删权限行）
DEPRECATED_PERMISSIONS: list[str] = ["post.manage", "event.manage"]

# ---------------- 角色矩阵（name → (中文名, 权限 codes)） ----------------
ROLES: dict[str, tuple[str, list[str]]] = {
    "root": ("超级管理员", [c for c, _ in PERMISSIONS]),
    "member": ("普通成员", []),
    "news": ("新闻管理员", ["admin.access", "audit.view", "news.manage"]),
    "event": ("活动管理员", ["admin.access", "audit.view", "activity.manage"]),
    "tucao": ("吐槽管理员", ["admin.access", "audit.view", "tucao.moderate"]),
    "user": ("用户管理员", ["admin.access", "audit.view", "user.manage",
                             "invite.manage", "roster.manage"]),
    "game": ("游戏管理员", ["admin.access", "audit.view", "game.manage"]),
    "post": ("内容发布员", ["admin.access", "audit.view", "announcement.manage",
                             "homework.manage", "honor.manage", "quote.moderate",
                             "update.manage"]),
    "life": ("班级生活管理员", ["admin.access", "audit.view", "resource.manage",
                                 "duty.manage", "checkin.manage"]),
}

# ---------------- 管理员账号（用户名 → (角色, 默认昵称)） ----------------
ADMINS: list[tuple[str, str, str]] = [
    ("admin-root", "root", "超管"),
    ("admin-news", "news", "新闻小编"),
    ("admin-event", "event", "活动委员"),
    ("admin-tucao", "tucao", "吐槽墙管理员"),
    ("admin-user", "user", "用户管理员"),
    ("admin-game", "game", "游戏管理员"),
    ("admin-post", "post", "内容发布员"),
    ("admin-life", "life", "班级生活管理员"),
]


def seed_perms() -> dict[str, int]:
    """幂等写入权限，返回 code→id。"""
    return {code: portal_db.upsert_permission(code, name) for code, name in PERMISSIONS}


def seed_roles(perm_ids: dict[str, int]) -> dict[str, int]:
    """幂等写入角色与授权，返回 name→id。"""
    role_ids: dict[str, int] = {}
    for name, (display, codes) in ROLES.items():
        rid: int = portal_db.upsert_role(name, display)
        role_ids[name] = rid
        for code in codes:
            portal_db.grant(rid, perm_ids[code])
    return role_ids


def cleanup_deprecated() -> int:
    """幂等清理废弃权限。返回清理条数。"""
    removed: int = 0
    with db.get_db() as conn:
        for code in DEPRECATED_PERMISSIONS:
            row = conn.execute("SELECT id FROM permissions WHERE code=?", (code,)).fetchone()
            if row is None:
                continue
            pid: int = int(row["id"])
            conn.execute("DELETE FROM role_permissions WHERE permission_id=?", (pid,))
            conn.execute("DELETE FROM permissions WHERE id=?", (pid,))
            removed += 1
    return removed


def seed_admins(role_ids: dict[str, int]) -> None:
    """创建缺失的管理员（已存在只跳过 —— 不重置密码）。"""
    print("== 管理员账号 ==")
    for username, role_name, nickname in ADMINS:
        if portal_db.get_user_by_username(username) is not None:
            print(f"  [仅补权限] {username:<12} 已存在（密码保持不变）")
            continue
        password: str = common.gen_password(12)
        portal_db.create_user(username, nickname,
                              common.hash_password(password), role_ids[role_name])
        print(f"  [新建]     {username:<12} 角色={role_name:<6} 密码={password}")


def print_matrix() -> None:
    """打印完整权限矩阵（★=拥有）。"""
    role_names: list[str] = list(ROLES.keys())
    short: dict[str, str] = {"root": "root", "member": "memb", "news": "news",
                             "event": "evnt", "tucao": "tuca", "user": "user",
                             "game": "game", "post": "post", "life": "life"}
    head: str = "  {:<22}".format("权限 code") + "".join(
        "{:>6}".format(short[r]) for r in role_names)
    print("== 权限矩阵（★ = 拥有） ==")
    print(head)
    print("  " + "-" * (len(head) - 2))
    for code, cname in PERMISSIONS:
        row: str = "  {:<22}".format(code)
        for r in role_names:
            row += "{:>6}".format("★" if code in ROLES[r][1] else "·")
        print(row + f"   {cname}")


def main() -> None:
    perm_ids: dict[str, int] = seed_perms()
    role_ids: dict[str, int] = seed_roles(perm_ids)
    removed: int = cleanup_deprecated()
    if not portal_db.setting_get("tucao_mode", ""):
        portal_db.setting_put("tucao_mode", "post_first")

    print("== 权限与角色写入完成 ==")
    print(f"   权限 {len(perm_ids)} 个 ｜ 角色 {len(role_ids)} 个"
          + (f" ｜ 清理废弃权限 {removed} 条" if removed else ""))
    seed_admins(role_ids)
    print()
    print_matrix()

    portal_db.add_audit(None, "seed_admin_final", "matrix",
                        f"perms={len(perm_ids)} roles={len(role_ids)}"
                        f" deprecated={removed}", "")
    print()
    print("== 完成 ==")
    print("   下一步：python3.10 migrate_v4_final.py（校验四期表与索引齐全）→ Reload")


if __name__ == "__main__":
    main()
