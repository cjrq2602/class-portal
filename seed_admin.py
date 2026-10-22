"""
seed_admin.py —— 种子数据：角色 / 权限 / 7 个管理员（幂等）
===========================================================
用法（PythonAnywhere Bash，迁移之后执行一次）：
    cd ~/mysite && python3.10 seed_admin.py
行为：
1. 幂等：角色/权限/关联已存在则跳过；管理员用户名已存在则跳过（不覆盖密码）；
2. 新建管理员使用随机强密码，【仅本次打印到控制台】——请立即妥善保存；
3. 同时创建 member 普通用户角色（注册用）。
权限边界设计：
    root  = 全部权限（超管）
    news  = 公告管理 + 审计查看（后续新闻板块归其管）
    event = 活动管理 + 审计查看（活动板块第 3 期上线）
    tucao = 吐槽管理 + 审计查看（吐槽墙第 2 期上线）
    user  = 用户管理 + 邀请码管理 + 审计查看
    game  = 游戏管理 + 审计查看
    post  = 公告管理 + 审计查看（内容发布岗）
"""

from __future__ import annotations

import os
import sys

PROJECT_HOME: str = os.path.dirname(os.path.abspath(__file__))
if PROJECT_HOME not in sys.path:
    sys.path.insert(0, PROJECT_HOME)

import common       # noqa: E402  密码哈希
import db           # noqa: E402  连接管理
import portal_db    # noqa: E402  数据访问

# ---------------- 权限清单（code → 中文名） ----------------
PERMISSIONS: list[tuple[str, str]] = [
    ("admin.access", "进入管理后台"),
    ("user.manage", "用户管理"),
    ("invite.manage", "邀请码管理"),
    ("announcement.manage", "公告管理"),
    ("audit.view", "审计日志查看"),
    ("game.manage", "游戏房间管理"),
    ("attendance.manage", "考勤发起与管理"),
    ("news.manage", "新闻源与每日总结管理"),      # 第 2 期：新闻中心 + 每日总结
    ("tucao.moderate", "吐槽审核与敏感词管理"),    # 第 2 期：吐槽墙
    ("activity.manage", "活动与比赛管理"),         # 第 3 期
    ("homework.manage", "作业管理"),               # 第 3 期
    ("resource.manage", "学习资料库管理"),          # 第 3 期
    ("duty.manage", "值日表管理"),                 # 第 3 期
    ("honor.manage", "荣誉墙管理"),                # 第 3 期
]

# 已废弃的权限 code（幂等清理：删除关联与权限行本身，避免后台出现"无人可用的权限"）
#   post.manage  —— 第 1 期占位，由 tucao.moderate 取代
#   event.manage —— 第 2 期占位，由 activity.manage 取代
DEPRECATED_PERMISSIONS: list[str] = ["post.manage", "event.manage"]

# ---------------- 角色清单（name → (中文名, 权限 code 列表)） ----------------
# 第 3 期权限映射：
#   activity.manage              → admin-event（活动管理员）
#   homework.manage + honor.manage → admin-post（内容发布员）
#   resource.manage + duty.manage → admin-life（第 8 个管理员：班级生活管理员）
ROLES: dict[str, tuple[str, list[str]]] = {
    "root":  ("超级管理员", [c for c, _ in PERMISSIONS]),
    "member": ("普通成员", []),
    "news":  ("新闻管理员", ["admin.access", "news.manage", "announcement.manage", "audit.view"]),
    "event": ("活动管理员", ["admin.access", "activity.manage", "audit.view"]),
    "tucao": ("吐槽管理员", ["admin.access", "tucao.moderate", "audit.view"]),
    "user":  ("用户管理员", ["admin.access", "user.manage", "invite.manage", "audit.view"]),
    "game":  ("游戏管理员", ["admin.access", "game.manage", "audit.view"]),
    "post":  ("内容发布员", ["admin.access", "homework.manage", "honor.manage",
                             "announcement.manage", "audit.view"]),
    "life":  ("班级生活管理员", ["admin.access", "resource.manage", "duty.manage",
                                 "attendance.manage", "audit.view"]),
    "attendance": ("考勤管理员", ["admin.access", "attendance.manage", "audit.view"]),
    # 班长：考勤发起/补签 + 发布作业 + 值日表管理
    #（作业管理在 /admin 后台，故需 admin.access；后台只显示有权限的模块）
    "monitor": ("班长", ["admin.access", "attendance.manage", "homework.manage",
                         "duty.manage", "audit.view"]),
}

# ---------------- 管理员（用户名 → (角色, 默认昵称)） ----------------
ADMINS: list[tuple[str, str, str]] = [
    ("admin-root", "root", "超管"),
    ("admin-news", "news", "新闻小编"),
    ("admin-event", "event", "活动委员"),
    ("admin-tucao", "tucao", "吐槽墙管理员"),
    ("admin-user", "user", "用户管理员"),
    ("admin-game", "game", "游戏管理员"),
    ("admin-post", "post", "内容发布员"),
    ("admin-life", "life", "班级生活管理员"),
    ("admin-attendance", "attendance", "考勤管理员"),
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


def cleanup_deprecated_perms() -> int:
    """
    幂等清理已废弃权限（如 post.manage）：
    先删角色关联，再删权限行本身。返回清理条数。
    """
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


def seed_default_settings() -> None:
    """幂等写入默认站点设置（吐槽墙默认先发后审）。"""
    if not portal_db.setting_get("tucao_mode", ""):
        portal_db.setting_put("tucao_mode", "post_first")


def seed_admins(role_ids: dict[str, int]) -> None:
    """创建 7 个管理员（已存在则跳过），新密码打印到控制台。"""
    print("== 管理员账号（新创建的密码仅此一次显示） ==")
    for username, role_name, nickname in ADMINS:
        if portal_db.get_user_by_username(username) is not None:
            print(f"  [跳过] {username:<12} 已存在")
            continue
        password: str = common.gen_password(12)
        portal_db.create_user(username, nickname,
                              common.hash_password(password), role_ids[role_name])
        print(f"  [新建] {username:<12} 角色={role_name:<6} 密码={password}")


def main() -> None:
    perm_ids: dict[str, int] = seed_perms()
    role_ids: dict[str, int] = seed_roles(perm_ids)
    removed: int = cleanup_deprecated_perms()
    seed_default_settings()
    seed_admins(role_ids)
    # 审计留痕：种子操作本身也记一笔（user_id=None 表示系统脚本）
    portal_db.add_audit(None, "seed_admin", "roles+admins",
                        f"roles={len(role_ids)} perms={len(perm_ids)}"
                        f" deprecated_removed={removed}", "")
    print("== 种子数据完成 ==")
    if removed:
        print(f"   已清理废弃权限 {removed} 条（{', '.join(DEPRECATED_PERMISSIONS)}）")
    print("   权限映射（累计）：news.manage→admin-news ｜ tucao.moderate→admin-tucao")
    print("                     activity.manage→admin-event")
    print("                     homework.manage+honor.manage→admin-post")
    print("                     resource.manage+duty.manage→admin-life（第 8 个管理员）")
    print("   站点默认设置：tucao_mode=post_first（先发后审）")
    print("   注意：已存在的管理员账号【只补权限，不重置密码】")


if __name__ == "__main__":
    main()
