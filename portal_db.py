"""
portal_db.py —— 门户数据访问层（用户/权限/邀请码/公告/审计/战绩）
================================================================
职责：门户新增表的全部读写；与游戏数据层 db.py 完全隔离——
游戏旧表（players/rooms/...）一字不改，本模块绝不触碰。
约定：
1. 复用 db.get_db() 的连接管理（WAL + busy_timeout=5000 + 外键开启）；
2. 时间字段统一存 Unix 秒（UTC）；展示层负责换算北京时间；
3. 所有写操作小事务；查询全部走索引。
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any, Optional

import db

# ============================================================
# 建表（migrate_part1.py 调用；幂等）
# ============================================================
PORTAL_SCHEMA: str = """
-- 角色表
CREATE TABLE IF NOT EXISTS roles (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,          -- root/member/news/event/tucao/user/game/post
    display_name TEXT NOT NULL                  -- 中文显示名
);

-- 权限表（code 为程序引用键）
CREATE TABLE IF NOT EXISTS permissions (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,                  -- admin.access / user.manage / ...
    name TEXT NOT NULL
);

-- 角色-权限关联
CREATE TABLE IF NOT EXISTS role_permissions (
    role_id       INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    permission_id INTEGER NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission_id)
);

-- 用户表（密码 pbkdf2 哈希；role_id 关联角色）
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,         -- 登录名（3~20 位字母数字下划线）
    nickname      TEXT NOT NULL,                -- 显示昵称
    password_hash TEXT NOT NULL,
    role_id       INTEGER NOT NULL REFERENCES roles(id),
    is_banned     INTEGER NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role_id);

-- 邀请码（限次数 + 有效期 + 可作废）
CREATE TABLE IF NOT EXISTS invites (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL UNIQUE,
    max_uses    INTEGER NOT NULL DEFAULT 1,
    used_count  INTEGER NOT NULL DEFAULT 0,
    expires_at  REAL NOT NULL,                  -- Unix 秒（UTC）
    created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    revoked_at  REAL,                           -- 非空=已作废
    created_at  REAL NOT NULL
);

-- 审计日志（管理员一切写操作留痕）
CREATE TABLE IF NOT EXISTS audit_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action     TEXT NOT NULL,                   -- login/ban_user/gen_invite/...
    target     TEXT NOT NULL DEFAULT '',        -- 操作对象描述
    detail     TEXT NOT NULL DEFAULT '',
    ip         TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_logs(user_id);

-- 公告（首页公告栏；后续新闻板块的前身）
CREATE TABLE IF NOT EXISTS announcements (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    content    TEXT NOT NULL DEFAULT '',
    author_id  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at REAL NOT NULL
);

-- 登录用户游戏战绩（匿名战绩仍存游戏 players 表，互不干扰）
CREATE TABLE IF NOT EXISTS game_stats (
    user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    wins       INTEGER NOT NULL DEFAULT 0,
    losses     INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
);

-- ============================================================
-- 第二期：新闻中心 / 每日总结 / 吐槽墙
-- ============================================================

-- 新闻条目（source+url 唯一去重；INSERT OR IGNORE 天然幂等）
CREATE TABLE IF NOT EXISTS news_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT NOT NULL,            -- 来源名（新华网/人民网/央视新闻/澎湃/中新网）
    title        TEXT NOT NULL,
    url          TEXT NOT NULL,
    summary      TEXT NOT NULL DEFAULT '', -- 摘要（原文 description，服务端只做截断不做图像处理）
    published_at REAL NOT NULL,            -- 发布时间 Unix 秒（UTC）
    pub_bj_date  TEXT NOT NULL,            -- 发布时间的北京日期 YYYY-MM-DD（按日归档/总结窗口用）
    created_at   REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_news_source_url ON news_items(source, url);
CREATE INDEX IF NOT EXISTS idx_news_pub ON news_items(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_bjdate ON news_items(pub_bj_date);

-- 新闻同步状态（单行表 k/v：last_sha / last_sync_at / last_ok / last_error / items_total）
CREATE TABLE IF NOT EXISTS news_sync_state (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);

-- 每日总结（type+date 唯一 → 幂等，重复生成只覆盖同一条）
CREATE TABLE IF NOT EXISTS news_digests (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ntype      TEXT NOT NULL,              -- today | yesterday
    ndate      TEXT NOT NULL,              -- 所属北京日期 YYYY-MM-DD
    generator  TEXT NOT NULL DEFAULT 'lazy', -- action(GitHub定时) | lazy(站点兜底) | manual(后台重生成)
    edited     INTEGER NOT NULL DEFAULT 0, -- 管理员编辑过则置 1
    payload    TEXT NOT NULL,              -- 完整 digest JSON（画面直接渲染）
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_digest_type_date ON news_digests(ntype, ndate);
CREATE INDEX IF NOT EXISTS idx_digest_date ON news_digests(ndate DESC);

-- 吐槽帖（服务端存 user_id 便于风控，但界面只展示 persona 决定的显示名）
CREATE TABLE IF NOT EXISTS tucao_posts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content      TEXT NOT NULL,
    persona      TEXT NOT NULL DEFAULT 'anon', -- real(真实昵称) | anon(随机马甲) | fixed(固定匿名)
    anon_name    TEXT NOT NULL DEFAULT '',     -- 马甲显示名（persona=anon 时生效）
    status       TEXT NOT NULL DEFAULT 'ok',   -- ok(可见) | pending(待审) | deleted(已删)
    pinned       INTEGER NOT NULL DEFAULT 0,
    like_count   INTEGER NOT NULL DEFAULT 0,
    report_count INTEGER NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tucao_status ON tucao_posts(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tucao_hot ON tucao_posts(status, like_count DESC, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tucao_user ON tucao_posts(user_id, created_at DESC);

-- 点赞（(post_id,user_id) 主键 → 天然幂等，每人每帖一次，可取消）
CREATE TABLE IF NOT EXISTS tucao_likes (
    post_id    INTEGER NOT NULL REFERENCES tucao_posts(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    PRIMARY KEY (post_id, user_id)
);

-- 举报队列
CREATE TABLE IF NOT EXISTS tucao_reports (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id    INTEGER NOT NULL REFERENCES tucao_posts(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reason     TEXT NOT NULL DEFAULT '',
    handled    INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_report_handled ON tucao_reports(handled, id DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_report_once ON tucao_reports(post_id, user_id);

-- 敏感词库（内置词库在 sensitive.py；本表存管理员增删的词）
CREATE TABLE IF NOT EXISTS sensitive_words (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    word       TEXT NOT NULL UNIQUE,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at REAL NOT NULL
);

-- 站点设置（k/v：tucao_mode=post_first|audit_first、resource_subjects 等）
CREATE TABLE IF NOT EXISTS site_settings (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);

-- ============================================================
-- 第三期：活动/比赛 · 作业追踪 · 学习资料库 · 值日表 · 荣誉墙
-- ============================================================

-- 活动/比赛（type=activity 活动 / contest 比赛；hidden 下架不删）
CREATE TABLE IF NOT EXISTS activities (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    atype         TEXT NOT NULL DEFAULT 'activity', -- activity=活动 | contest=比赛
    start_at      REAL NOT NULL,                    -- Unix 秒（UTC），展示转北京时间
    end_at        REAL NOT NULL,
    place         TEXT NOT NULL DEFAULT '',
    detail        TEXT NOT NULL DEFAULT '',
    reg_enabled   INTEGER NOT NULL DEFAULT 0,       -- 是否开放报名
    reg_deadline  REAL NOT NULL DEFAULT 0,          -- 报名截止（Unix 秒）
    reg_limit     INTEGER NOT NULL DEFAULT 0,       -- 人数上限（0=不限）
    hidden        INTEGER NOT NULL DEFAULT 0,       -- 下架（隐藏不删）
    created_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_act_start ON activities(start_at);
CREATE INDEX IF NOT EXISTS idx_act_hidden ON activities(hidden, start_at);

-- 活动标签库（名称 + 颜色，管理员增删）
CREATE TABLE IF NOT EXISTS activity_tags (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    color      TEXT NOT NULL DEFAULT '#4F6EF7',
    created_at REAL NOT NULL
);

-- 活动 ↔ 标签（多对多）
CREATE TABLE IF NOT EXISTS activity_tag_map (
    activity_id INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
    tag_id      INTEGER NOT NULL REFERENCES activity_tags(id) ON DELETE CASCADE,
    PRIMARY KEY (activity_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_tagmap_tag ON activity_tag_map(tag_id);

-- 活动报名（UNIQUE(user,activity) → 天然幂等，重复报名不产生脏数据）
CREATE TABLE IF NOT EXISTS activity_registrations (
    activity_id INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  REAL NOT NULL,
    PRIMARY KEY (activity_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_reg_user ON activity_registrations(user_id);

-- 作业（个人完成状态单独存表，任何接口不返回他人完成情况）
CREATE TABLE IF NOT EXISTS homework (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    subject     TEXT NOT NULL,
    content     TEXT NOT NULL,
    due_at      REAL NOT NULL,                 -- 截止时间（Unix 秒）
    created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hw_due ON homework(due_at);

-- 作业完成勾选（仅本人可见：查询时必须带 user_id 过滤）
CREATE TABLE IF NOT EXISTS homework_done (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    homework_id INTEGER NOT NULL REFERENCES homework(id) ON DELETE CASCADE,
    done_at     REAL NOT NULL,
    PRIMARY KEY (user_id, homework_id)
);
CREATE INDEX IF NOT EXISTS idx_hwdone_user ON homework_done(user_id);

-- 学习资料库（link 外链 / file 上传文件；文件走 uploads/resources/）
CREATE TABLE IF NOT EXISTS resources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    subject     TEXT NOT NULL DEFAULT '其他',
    rtype       TEXT NOT NULL DEFAULT 'link',  -- link | file
    link        TEXT NOT NULL DEFAULT '',
    file_path   TEXT NOT NULL DEFAULT '',      -- 相对路径，如 resources/xxx.pdf
    file_name   TEXT NOT NULL DEFAULT '',      -- 原始文件名（下载时用）
    file_size   INTEGER NOT NULL DEFAULT 0,    -- 字节
    uploader_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    downloads   INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_res_subject ON resources(subject, created_at DESC);

-- 值日表（每周 7 行，UNIQUE(day_of_week) 保证一周一天一组）
CREATE TABLE IF NOT EXISTS duty_schedule (
    day_of_week INTEGER PRIMARY KEY,           -- 0=周一 … 6=周日
    group_name  TEXT NOT NULL DEFAULT '',
    members     TEXT NOT NULL DEFAULT '',
    task        TEXT NOT NULL DEFAULT '',
    updated_at  REAL NOT NULL
);

-- 荣誉墙
CREATE TABLE IF NOT EXISTS honors (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    owner       TEXT NOT NULL DEFAULT '',
    hdate       TEXT NOT NULL DEFAULT '',      -- 获奖日期（北京日期 YYYY-MM-DD）
    description TEXT NOT NULL DEFAULT '',
    image_path  TEXT NOT NULL DEFAULT '',      -- 相对路径 honors/xxx.jpg（可选）
    is_pinned   INTEGER NOT NULL DEFAULT 0,
    created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_honor_order ON honors(is_pinned DESC, created_at DESC);

-- ============================================================
-- 第四期：每日一句 · 打卡挑战 · 随机点名
-- ============================================================

-- 每日一句（投稿 → pending 队列 → approve/reject；按北京日期取模轮换）
CREATE TABLE IF NOT EXISTS quotes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    content     TEXT NOT NULL,                  -- ≤100 字
    author      TEXT NOT NULL DEFAULT '',       -- 可选署名
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    status      TEXT NOT NULL DEFAULT 'pending',-- pending | approved | rejected
    like_count  INTEGER NOT NULL DEFAULT 0,
    reviewed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    reviewed_at REAL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quote_status ON quotes(status, id);

-- 每日一句点赞（每人每句一次，可取消）
CREATE TABLE IF NOT EXISTS quote_likes (
    quote_id   INTEGER NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    PRIMARY KEY (quote_id, user_id)
);

-- 打卡挑战（起止日期为北京日期 YYYY-MM-DD；结束后 archived=1 自动归档）
CREATE TABLE IF NOT EXISTS challenges (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    icon       TEXT NOT NULL DEFAULT 'target',  -- icons.js 中的图标名
    start_date TEXT NOT NULL,
    end_date   TEXT NOT NULL,
    archived   INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chal_dates ON challenges(start_date, end_date);

-- 打卡记录（UNIQUE(user,challenge,date) → 每天每挑战一次，天然防刷）
CREATE TABLE IF NOT EXISTS checkin_records (
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    challenge_id INTEGER NOT NULL REFERENCES challenges(id) ON DELETE CASCADE,
    cdate        TEXT NOT NULL,                 -- 北京日期 YYYY-MM-DD
    created_at   REAL NOT NULL,
    PRIMARY KEY (user_id, challenge_id, cdate)
);
CREATE INDEX IF NOT EXISTS idx_ck_chal_date ON checkin_records(challenge_id, cdate);

-- 随机点名名单（老师视角；支持从 users 一键导入昵称并去重）
CREATE TABLE IF NOT EXISTS class_members (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    seat_no    INTEGER NOT NULL DEFAULT 0,
    is_active  INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);
"""


def init_portal_db() -> None:
    """幂等建门户表。"""
    with db.get_db() as conn:
        conn.executescript(PORTAL_SCHEMA)


def table_counts() -> dict[str, int]:
    """各门户表行数（migrate 跑完打印用）。"""
    tables: list[str] = ["users", "roles", "permissions", "role_permissions",
                         "invites", "audit_logs", "announcements", "game_stats",
                         "news_items", "news_sync_state", "news_digests",
                         "tucao_posts", "tucao_likes", "tucao_reports",
                         "sensitive_words", "site_settings",
                         "activities", "activity_tags", "activity_tag_map",
                         "activity_registrations", "homework", "homework_done",
                         "resources", "duty_schedule", "honors",
                         "quotes", "quote_likes", "challenges",
                         "checkin_records", "class_members",
                         "login_logs", "link_categories",
                         "friendly_links", "link_submissions"]
    counts: dict[str, int] = {}
    with db.get_db() as conn:
        for t in tables:
            counts[t] = int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
    return counts


# ============================================================
# 角色 / 权限
# ============================================================
def upsert_role(name: str, display_name: str) -> int:
    with db.get_db() as conn:
        conn.execute("INSERT INTO roles(name, display_name) VALUES (?,?)"
                     " ON CONFLICT(name) DO UPDATE SET display_name=excluded.display_name",
                     (name, display_name))
        return int(conn.execute("SELECT id FROM roles WHERE name=?", (name,)).fetchone()[0])


def upsert_permission(code: str, name: str) -> int:
    with db.get_db() as conn:
        conn.execute("INSERT INTO permissions(code, name) VALUES (?,?)"
                     " ON CONFLICT(code) DO UPDATE SET name=excluded.name",
                     (code, name))
        return int(conn.execute("SELECT id FROM permissions WHERE code=?", (code,)).fetchone()[0])


def grant(role_id: int, permission_id: int) -> None:
    with db.get_db() as conn:
        conn.execute("INSERT OR IGNORE INTO role_permissions(role_id, permission_id) VALUES (?,?)",
                     (role_id, permission_id))


def get_role_by_name(name: str) -> Optional[sqlite3.Row]:
    with db.get_db() as conn:
        return conn.execute("SELECT * FROM roles WHERE name=?", (name,)).fetchone()


def list_roles() -> list[sqlite3.Row]:
    with db.get_db() as conn:
        return conn.execute("SELECT * FROM roles ORDER BY id").fetchall()


def get_user_perms(user_id: int) -> list[str]:
    """用户权限 code 列表（经角色关联）。"""
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT p.code FROM permissions p"
            " JOIN role_permissions rp ON rp.permission_id=p.id"
            " JOIN users u ON u.role_id=rp.role_id WHERE u.id=?",
            (user_id,),
        ).fetchall()
    return [r["code"] for r in rows]


# ============================================================
# 用户
# ============================================================
def create_user(username: str, nickname: str, password_hash: str, role_id: int) -> int:
    with db.get_db() as conn:
        cur = conn.execute(
            "INSERT INTO users(username, nickname, password_hash, role_id, is_banned, created_at)"
            " VALUES (?,?,?,?,0,?)",
            (username, nickname, password_hash, role_id, time.time()),
        )
        return int(cur.lastrowid)


def get_user_by_username(username: str) -> Optional[sqlite3.Row]:
    with db.get_db() as conn:
        return conn.execute(
            "SELECT u.*, r.name AS role_name, r.display_name AS role_display"
            " FROM users u JOIN roles r ON r.id=u.role_id WHERE u.username=?",
            (username,),
        ).fetchone()


def get_user(user_id: int) -> Optional[sqlite3.Row]:
    with db.get_db() as conn:
        return conn.execute(
            "SELECT u.*, r.name AS role_name, r.display_name AS role_display"
            " FROM users u JOIN roles r ON r.id=u.role_id WHERE u.id=?",
            (user_id,),
        ).fetchone()


def set_password(user_id: int, password_hash: str) -> None:
    with db.get_db() as conn:
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, user_id))


def set_banned(user_id: int, banned: bool) -> None:
    with db.get_db() as conn:
        conn.execute("UPDATE users SET is_banned=? WHERE id=?", (1 if banned else 0, user_id))


def set_role(user_id: int, role_id: int) -> None:
    with db.get_db() as conn:
        conn.execute("UPDATE users SET role_id=? WHERE id=?", (role_id, user_id))


def set_nickname(user_id: int, nickname: str) -> None:
    with db.get_db() as conn:
        conn.execute("UPDATE users SET nickname=? WHERE id=?", (nickname, user_id))


def list_users(limit: int = 100, offset: int = 0) -> list[sqlite3.Row]:
    with db.get_db() as conn:
        return conn.execute(
            "SELECT u.id, u.username, u.nickname, u.is_banned, u.created_at,"
            " r.name AS role_name, r.display_name AS role_display, r.id AS role_id"
            " FROM users u JOIN roles r ON r.id=u.role_id"
            " ORDER BY u.id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()


def count_users() -> int:
    with db.get_db() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])


# ============================================================
# 邀请码
# ============================================================
def create_invite(code: str, max_uses: int, expires_at: float,
                  created_by: Optional[int]) -> int:
    with db.get_db() as conn:
        cur = conn.execute(
            "INSERT INTO invites(code, max_uses, used_count, expires_at, created_by, created_at)"
            " VALUES (?,?,0,?,?,?)",
            (code, max_uses, expires_at, created_by, time.time()),
        )
        return int(cur.lastrowid)


def get_invite_by_code(code: str) -> Optional[sqlite3.Row]:
    with db.get_db() as conn:
        return conn.execute("SELECT * FROM invites WHERE code=?", (code,)).fetchone()


def consume_invite(invite_id: int) -> None:
    with db.get_db() as conn:
        conn.execute("UPDATE invites SET used_count=used_count+1 WHERE id=?", (invite_id,))


def revoke_invite(invite_id: int) -> None:
    with db.get_db() as conn:
        conn.execute("UPDATE invites SET revoked_at=? WHERE id=?", (time.time(), invite_id))


def list_invites(limit: int = 100) -> list[sqlite3.Row]:
    with db.get_db() as conn:
        return conn.execute(
            "SELECT i.*, u.username AS creator FROM invites i"
            " LEFT JOIN users u ON u.id=i.created_by ORDER BY i.id DESC LIMIT ?",
            (limit,),
        ).fetchall()


# ============================================================
# 审计日志
# ============================================================
def add_audit(user_id: Optional[int], action: str, target: str = "",
              detail: str = "", ip: str = "") -> None:
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO audit_logs(user_id, action, target, detail, ip, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (user_id, action, target, detail, ip, time.time()),
        )


def list_audit(limit: int = 50, offset: int = 0,
               user_id: Optional[int] = None) -> list[sqlite3.Row]:
    sql: str = ("SELECT a.*, u.username FROM audit_logs a"
                " LEFT JOIN users u ON u.id=a.user_id")
    args: list[Any] = []
    if user_id is not None:
        sql += " WHERE a.user_id=?"
        args.append(user_id)
    sql += " ORDER BY a.id DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    with db.get_db() as conn:
        return conn.execute(sql, args).fetchall()


def count_audit() -> int:
    with db.get_db() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0])


# ============================================================
# 公告
# ============================================================
def add_announcement(title: str, content: str, author_id: Optional[int]) -> int:
    with db.get_db() as conn:
        cur = conn.execute(
            "INSERT INTO announcements(title, content, author_id, created_at) VALUES (?,?,?,?)",
            (title, content, author_id, time.time()),
        )
        return int(cur.lastrowid)


def delete_announcement(ann_id: int) -> None:
    with db.get_db() as conn:
        conn.execute("DELETE FROM announcements WHERE id=?", (ann_id,))


def list_announcements(limit: int = 3) -> list[sqlite3.Row]:
    with db.get_db() as conn:
        return conn.execute(
            "SELECT a.*, u.nickname AS author FROM announcements a"
            " LEFT JOIN users u ON u.id=a.author_id ORDER BY a.id DESC LIMIT ?",
            (limit,),
        ).fetchall()


# ============================================================
# 游戏战绩（登录用户）
# ============================================================
def bump_game_stats(user_id: int, won: bool) -> None:
    """对局结束累加登录用户战绩（幂等由调用方保证：每局每人只调一次）。"""
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO game_stats(user_id, wins, losses, updated_at) VALUES (?,?,?,?)"
            " ON CONFLICT(user_id) DO UPDATE SET"
            " wins=wins+excluded.wins, losses=losses+excluded.losses,"
            " updated_at=excluded.updated_at",
            (user_id, 1 if won else 0, 0 if won else 1, time.time()),
        )


def get_game_stats(user_id: int) -> Optional[sqlite3.Row]:
    with db.get_db() as conn:
        return conn.execute("SELECT * FROM game_stats WHERE user_id=?", (user_id,)).fetchone()


# ============================================================
# 第二期：新闻条目
# ============================================================
def insert_news_many(items: list[dict[str, Any]]) -> int:
    """
    批量写入新闻（source+url 唯一 → INSERT OR IGNORE 天然幂等去重）。
    返回实际新增条数（被忽略的重复项不计）。
    """
    if not items:
        return 0
    ts: float = time.time()
    added: int = 0
    with db.get_db() as conn:
        for it in items:
            cur = conn.execute(
                "INSERT OR IGNORE INTO news_items"
                "(source, title, url, summary, published_at, pub_bj_date, created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (str(it["source"]), str(it["title"]), str(it["url"]),
                 str(it.get("summary", ""))[:400], float(it["published_at"]),
                 str(it["pub_bj_date"]), ts),
            )
            added += cur.rowcount
    return added


def list_news(page: int = 1, per_page: int = 30,
              source: Optional[str] = None) -> tuple[list[sqlite3.Row], int]:
    """分页查询新闻（时间倒序）；可按来源筛选。返回 (行列表, 总数)。"""
    where: str = ""
    args: list[Any] = []
    if source:
        where = " WHERE source=?"
        args.append(source)
    offset: int = max(0, (page - 1) * per_page)
    with db.get_db() as conn:
        total: int = int(conn.execute(
            f"SELECT COUNT(*) FROM news_items{where}", args).fetchone()[0])
        rows = conn.execute(
            f"SELECT * FROM news_items{where} ORDER BY published_at DESC, id DESC"
            f" LIMIT ? OFFSET ?", args + [per_page, offset]).fetchall()
    return rows, total


def news_sources() -> list[tuple[str, int]]:
    """全部来源及条数（筛选下拉用）。"""
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT source, COUNT(*) AS c FROM news_items GROUP BY source"
            " ORDER BY c DESC").fetchall()
    return [(str(r["source"]), int(r["c"])) for r in rows]


def news_in_window(start_ts: float, end_ts: float) -> list[sqlite3.Row]:
    """按时间窗口取新闻（总结生成用：北京日窗口换算后的 Unix 秒区间）。"""
    with db.get_db() as conn:
        return conn.execute(
            "SELECT * FROM news_items WHERE published_at >= ? AND published_at < ?"
            " ORDER BY published_at DESC",
            (float(start_ts), float(end_ts))).fetchall()


def count_news() -> int:
    with db.get_db() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM news_items").fetchone()[0])


# ---------------- 同步状态（k/v 单行表） ----------------
def sync_state_get(key: str, default: str = "") -> str:
    with db.get_db() as conn:
        row = conn.execute("SELECT v FROM news_sync_state WHERE k=?", (key,)).fetchone()
    return str(row["v"]) if row else default


def sync_state_put(key: str, value: str) -> None:
    with db.get_db() as conn:
        conn.execute("INSERT INTO news_sync_state(k, v) VALUES (?,?)"
                     " ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, str(value)))


def sync_state_all() -> dict[str, str]:
    """全部同步状态（前端/后台展示"数据更新于 xx:xx"）。"""
    with db.get_db() as conn:
        rows = conn.execute("SELECT k, v FROM news_sync_state").fetchall()
    return {str(r["k"]): str(r["v"]) for r in rows}


# ============================================================
# 第二期：每日总结
# ============================================================
def get_digest(ntype: str, ndate: str) -> Optional[sqlite3.Row]:
    """按 (type, date) 唯一键取总结。"""
    with db.get_db() as conn:
        return conn.execute(
            "SELECT * FROM news_digests WHERE ntype=? AND ndate=?",
            (ntype, ndate)).fetchone()


def upsert_digest(ntype: str, ndate: str, payload: str,
                  generator: str = "lazy", mark_edited: bool = False) -> None:
    """
    写入/覆盖总结（(type,date) 唯一 → 重复运行幂等覆盖同一条）。
    mark_edited 用于管理员手动编辑（置 edited=1，避免被重新生成覆盖时丢失人工内容）。
    """
    ts: float = time.time()
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO news_digests(ntype, ndate, generator, edited, payload,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(ntype, ndate) DO UPDATE SET"
            " generator=excluded.generator, payload=excluded.payload,"
            " edited=excluded.edited, updated_at=excluded.updated_at",
            (ntype, ndate, generator, 1 if mark_edited else 0, payload, ts, ts),
        )


def list_digests(limit: int = 60) -> list[sqlite3.Row]:
    """历史归档（按日期倒序；只回摘要字段，payload 由详情接口按需取）。"""
    with db.get_db() as conn:
        return conn.execute(
            "SELECT ntype, ndate, generator, edited, updated_at FROM news_digests"
            " ORDER BY ndate DESC, ntype DESC LIMIT ?", (limit,)).fetchall()


def latest_digests() -> dict[str, Optional[sqlite3.Row]]:
    """取"最近一份 today"与"最近一份 yesterday"（首页摘要卡用）。"""
    out: dict[str, Optional[sqlite3.Row]] = {"today": None, "yesterday": None}
    with db.get_db() as conn:
        for t in ("today", "yesterday"):
            out[t] = conn.execute(
                "SELECT * FROM news_digests WHERE ntype=? ORDER BY ndate DESC LIMIT 1",
                (t,)).fetchone()
    return out


# ============================================================
# 第二期：吐槽墙
# ============================================================
def tucao_insert(user_id: int, content: str, persona: str,
                 anon_name: str, status: str) -> int:
    ts: float = time.time()
    with db.get_db() as conn:
        cur = conn.execute(
            "INSERT INTO tucao_posts(user_id, content, persona, anon_name, status,"
            " pinned, like_count, report_count, created_at, updated_at)"
            " VALUES (?,?,?,?,?,0,0,0,?,?)",
            (user_id, content, persona, anon_name, status, ts, ts))
        return int(cur.lastrowid)


def tucao_get(post_id: int) -> Optional[sqlite3.Row]:
    with db.get_db() as conn:
        return conn.execute("SELECT * FROM tucao_posts WHERE id=?", (post_id,)).fetchone()


def tucao_list(page: int = 1, per_page: int = 20, sort: str = "time",
               viewer_id: Optional[int] = None) -> tuple[list[dict[str, Any]], int]:
    """
    分页查询吐槽（仅 status IN ('ok') 且"先审后发"下 pending 仅作者可见，
    故此处额外允许 viewer 看到自己的 pending 帖）。
    排序：time=时间倒序；hot=点赞数优先再按时间。置顶帖永远排最前。
    返回 (含 liked/display_name 的字典列表, 总数)。
    """
    order: str = ("pinned DESC, like_count DESC, created_at DESC"
                  if sort == "hot" else "pinned DESC, created_at DESC")
    args: list[Any] = []
    if viewer_id is not None:
        vis = "(status='ok' OR (status='pending' AND user_id=?))"
        args.append(viewer_id)
    else:
        vis = "status='ok'"
    offset: int = max(0, (page - 1) * per_page)
    with db.get_db() as conn:
        total: int = int(conn.execute(
            f"SELECT COUNT(*) FROM tucao_posts WHERE {vis}", args).fetchone()[0])
        rows = conn.execute(
            f"SELECT * FROM tucao_posts WHERE {vis} ORDER BY {order} LIMIT ? OFFSET ?",
            args + [per_page, offset]).fetchall()
        liked_ids: set[int] = set()
        if viewer_id is not None and rows:
            ids: list[int] = [int(r["id"]) for r in rows]
            marks: str = ",".join("?" * len(ids))
            liked_rows = conn.execute(
                f"SELECT post_id FROM tucao_likes WHERE user_id=? AND post_id IN ({marks})",
                [viewer_id] + ids).fetchall()
            liked_ids = {int(x["post_id"]) for x in liked_rows}
        nicknames: dict[int, str] = {}
        uids = list({int(r["user_id"]) for r in rows})
        if uids:
            marks2: str = ",".join("?" * len(uids))
            for u in conn.execute(
                    f"SELECT id, nickname FROM users WHERE id IN ({marks2})", uids).fetchall():
                nicknames[int(u["id"])] = str(u["nickname"])
    out: list[dict[str, Any]] = []
    for r in rows:
        pid: int = int(r["id"])
        persona: str = str(r["persona"])
        if persona == "real":
            disp: str = nicknames.get(int(r["user_id"]), "同学")
        elif persona == "anon":
            disp = str(r["anon_name"]) or "匿名同学"
        else:
            disp = "匿名同学"
        out.append({
            "id": pid, "content": r["content"], "persona": persona,
            "display_name": disp, "status": r["status"],
            "pinned": bool(r["pinned"]), "like_count": int(r["like_count"]),
            "report_count": int(r["report_count"]),
            "created_at": float(r["created_at"]),
            "liked": pid in liked_ids,
            "is_mine": viewer_id is not None and int(r["user_id"]) == int(viewer_id),
        })
    return out, total


def tucao_set_status(post_id: int, status: str) -> None:
    with db.get_db() as conn:
        conn.execute("UPDATE tucao_posts SET status=?, updated_at=? WHERE id=?",
                     (status, time.time(), post_id))


def tucao_toggle_pin(post_id: int) -> bool:
    """置顶/取消置顶，返回操作后的置顶状态。"""
    with db.get_db() as conn:
        row = conn.execute("SELECT pinned FROM tucao_posts WHERE id=?", (post_id,)).fetchone()
        if row is None:
            return False
        new_val: int = 0 if int(row["pinned"]) else 1
        conn.execute("UPDATE tucao_posts SET pinned=?, updated_at=? WHERE id=?",
                     (new_val, time.time(), post_id))
        return bool(new_val)


def tucao_like(post_id: int, user_id: int) -> tuple[bool, int]:
    """
    点赞/取消点赞（幂等）：INSERT OR IGNORE 插入 likes，
    以"是否真的插入了行"判断本次是点赞还是取消；like_count 用子查询重算防漂移。
    返回 (liked_now, like_count)。
    """
    with db.get_db() as conn:
        # 先尝试插入（已存在则 rowcount=0 → 本次为取消）
        cur = conn.execute(
            "INSERT OR IGNORE INTO tucao_likes(post_id, user_id, created_at) VALUES (?,?,?)",
            (post_id, user_id, time.time()))
        if cur.rowcount == 1:
            liked: bool = True
        else:
            conn.execute("DELETE FROM tucao_likes WHERE post_id=? AND user_id=?",
                         (post_id, user_id))
            liked = False
        # 用真实计数回写（防并发/取消导致计数漂移）
        real: int = int(conn.execute(
            "SELECT COUNT(*) FROM tucao_likes WHERE post_id=?", (post_id,)).fetchone()[0])
        conn.execute("UPDATE tucao_posts SET like_count=?, updated_at=? WHERE id=?",
                     (real, time.time(), post_id))
    return liked, real


def tucao_report(post_id: int, user_id: int, reason: str) -> bool:
    """举报（每人每帖一次，uq_report_once 保证幂等）。返回是否首次举报。"""
    with db.get_db() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO tucao_reports(post_id, user_id, reason, handled, created_at)"
            " VALUES (?,?,?,0,?)", (post_id, user_id, reason[:200], time.time()))
        if cur.rowcount == 1:
            conn.execute("UPDATE tucao_posts SET report_count=report_count+1 WHERE id=?",
                         (post_id,))
            return True
    return False


def tucao_reports_list(limit: int = 100) -> list[sqlite3.Row]:
    """举报队列（未处理优先）。"""
    with db.get_db() as conn:
        return conn.execute(
            "SELECT r.*, p.content AS post_content, p.status AS post_status"
            " FROM tucao_reports r JOIN tucao_posts p ON p.id=r.post_id"
            " ORDER BY r.handled ASC, r.id DESC LIMIT ?", (limit,)).fetchall()


def tucao_report_handle(report_id: int) -> None:
    with db.get_db() as conn:
        conn.execute("UPDATE tucao_reports SET handled=1 WHERE id=?", (report_id,))


def tucao_user_recent_count(user_id: int, since_ts: float) -> int:
    """某用户自 since_ts 起的发帖数（防刷限频用）。"""
    with db.get_db() as conn:
        return int(conn.execute(
            "SELECT COUNT(*) FROM tucao_posts WHERE user_id=? AND created_at>=?",
            (user_id, float(since_ts))).fetchone()[0])


def tucao_hot(limit: int = 3) -> list[dict[str, Any]]:
    """首页"热门吐槽"：按点赞数取前 N 条可见帖。"""
    rows, _ = tucao_list(page=1, per_page=limit, sort="hot", viewer_id=None)
    return rows


def tucao_admin_list(status: str = "all", limit: int = 200) -> list[sqlite3.Row]:
    """后台审核队列原始行（含马甲名，但不对前台暴露 user_id）。"""
    with db.get_db() as conn:
        if status and status != "all":
            return conn.execute(
                "SELECT id, content, persona, anon_name, status, pinned, like_count,"
                " report_count, created_at FROM tucao_posts WHERE status=?"
                " ORDER BY id DESC LIMIT ?", (status, limit)).fetchall()
        return conn.execute(
            "SELECT id, content, persona, anon_name, status, pinned, like_count,"
            " report_count, created_at FROM tucao_posts"
            " ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


# ============================================================
# 第二期：敏感词 / 站点设置
# ============================================================
def sensitive_list() -> list[sqlite3.Row]:
    with db.get_db() as conn:
        return conn.execute(
            "SELECT w.*, u.username AS creator FROM sensitive_words w"
            " LEFT JOIN users u ON u.id=w.created_by ORDER BY w.id DESC").fetchall()


def sensitive_add(word: str, created_by: Optional[int]) -> bool:
    """新增敏感词，已存在返回 False。"""
    with db.get_db() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO sensitive_words(word, created_by, created_at) VALUES (?,?,?)",
            (word.strip(), created_by, time.time()))
        return cur.rowcount == 1


def sensitive_delete(word_id: int) -> None:
    with db.get_db() as conn:
        conn.execute("DELETE FROM sensitive_words WHERE id=?", (word_id,))


def sensitive_all_words() -> list[str]:
    """管理员词库全部词（与内置词库合并后用于过滤）。"""
    with db.get_db() as conn:
        rows = conn.execute("SELECT word FROM sensitive_words").fetchall()
    return [str(r["word"]) for r in rows]


def setting_get(key: str, default: str = "") -> str:
    with db.get_db() as conn:
        row = conn.execute("SELECT v FROM site_settings WHERE k=?", (key,)).fetchone()
    return str(row["v"]) if row else default


def setting_put(key: str, value: str) -> None:
    with db.get_db() as conn:
        conn.execute("INSERT INTO site_settings(k, v) VALUES (?,?)"
                     " ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, str(value)))


# ============================================================
# 第三期：活动 / 比赛
# ============================================================
def act_create(title: str, atype: str, start_at: float, end_at: float, place: str,
               detail: str, reg_enabled: bool, reg_deadline: float, reg_limit: int,
               created_by: Optional[int]) -> int:
    ts: float = time.time()
    with db.get_db() as conn:
        cur = conn.execute(
            "INSERT INTO activities(title, atype, start_at, end_at, place, detail,"
            " reg_enabled, reg_deadline, reg_limit, hidden, created_by, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,0,?,?,?)",
            (title, atype, start_at, end_at, place, detail,
             1 if reg_enabled else 0, reg_deadline, reg_limit, created_by, ts, ts))
        return int(cur.lastrowid)


def act_update(act_id: int, **fields: Any) -> None:
    """局部更新活动字段（只允许白名单列，防注入）。"""
    allowed: set[str] = {"title", "atype", "start_at", "end_at", "place", "detail",
                         "reg_enabled", "reg_deadline", "reg_limit", "hidden"}
    sets: list[str] = []
    args: list[Any] = []
    for k, v in fields.items():
        if k not in allowed:
            continue
        sets.append(f"{k}=?")
        args.append(v)
    if not sets:
        return
    sets.append("updated_at=?")
    args.append(time.time())
    args.append(act_id)
    with db.get_db() as conn:
        conn.execute(f"UPDATE activities SET {', '.join(sets)} WHERE id=?", args)


def act_get(act_id: int) -> Optional[sqlite3.Row]:
    with db.get_db() as conn:
        return conn.execute("SELECT * FROM activities WHERE id=?", (act_id,)).fetchone()


def act_tag_ids(act_id: int) -> list[int]:
    with db.get_db() as conn:
        rows = conn.execute("SELECT tag_id FROM activity_tag_map WHERE activity_id=?",
                            (act_id,)).fetchall()
    return [int(r["tag_id"]) for r in rows]


def act_set_tags(act_id: int, tag_ids: list[int]) -> None:
    """整表重设某活动的标签（先清后插，幂等）。"""
    with db.get_db() as conn:
        conn.execute("DELETE FROM activity_tag_map WHERE activity_id=?", (act_id,))
        for tid in tag_ids:
            conn.execute("INSERT OR IGNORE INTO activity_tag_map(activity_id, tag_id)"
                         " VALUES (?,?)", (act_id, int(tid)))


def act_public_list(atype: Optional[str] = None,
                    tag_id: Optional[int] = None) -> list[dict[str, Any]]:
    """
    用户端活动列表（不含 hidden；LEFT JOIN 取标签与已报人数）。
    状态由调用方按北京时间/当前时间计算（保持 SQL 简单、逻辑集中）。
    """
    sql: str = ("SELECT a.*,"
                " (SELECT COUNT(*) FROM activity_registrations r WHERE r.activity_id=a.id)"
                "   AS reg_count"
                " FROM activities a WHERE a.hidden=0")
    args: list[Any] = []
    if atype:
        sql += " AND a.atype=?"
        args.append(atype)
    if tag_id:
        sql += (" AND EXISTS (SELECT 1 FROM activity_tag_map m WHERE m.activity_id=a.id"
                " AND m.tag_id=?)")
        args.append(int(tag_id))
    sql += " ORDER BY a.start_at DESC"
    with db.get_db() as conn:
        rows = conn.execute(sql, args).fetchall()
        # 一次性取回全部标签，避免 N+1
        tag_rows = conn.execute(
            "SELECT m.activity_id, t.id, t.name, t.color FROM activity_tag_map m"
            " JOIN activity_tags t ON t.id=m.tag_id").fetchall()
    tags_by_act: dict[int, list[dict[str, Any]]] = {}
    for t in tag_rows:
        tags_by_act.setdefault(int(t["activity_id"]), []).append(
            {"id": int(t["id"]), "name": str(t["name"]), "color": str(t["color"])})
    out: list[dict[str, Any]] = []
    for r in rows:
        item = dict(r)
        item["tags"] = tags_by_act.get(int(r["id"]), [])
        out.append(item)
    return out


def act_admin_list(limit: int = 200) -> list[sqlite3.Row]:
    """后台列表（含已下架）。"""
    with db.get_db() as conn:
        return conn.execute(
            "SELECT a.*, (SELECT COUNT(*) FROM activity_registrations r"
            " WHERE r.activity_id=a.id) AS reg_count FROM activities a"
            " ORDER BY a.id DESC LIMIT ?", (limit,)).fetchall()


def act_my_reg_ids(user_id: int) -> set[int]:
    """当前用户已报名的活动 id 集合（只查自己）。"""
    with db.get_db() as conn:
        rows = conn.execute("SELECT activity_id FROM activity_registrations WHERE user_id=?",
                            (user_id,)).fetchall()
    return {int(r["activity_id"]) for r in rows}


def act_register(act_id: int, user_id: int) -> bool:
    """报名（幂等：已报名返回 False）。"""
    with db.get_db() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO activity_registrations(activity_id, user_id, created_at)"
            " VALUES (?,?,?)", (act_id, user_id, time.time()))
        return cur.rowcount == 1


def act_unregister(act_id: int, user_id: int) -> bool:
    """取消报名（幂等：未报名返回 False）。"""
    with db.get_db() as conn:
        cur = conn.execute(
            "DELETE FROM activity_registrations WHERE activity_id=? AND user_id=?",
            (act_id, user_id))
        return cur.rowcount == 1


def act_reg_count(act_id: int) -> int:
    with db.get_db() as conn:
        return int(conn.execute(
            "SELECT COUNT(*) FROM activity_registrations WHERE activity_id=?",
            (act_id,)).fetchone()[0])


def act_registrants(act_id: int) -> list[sqlite3.Row]:
    """报名名单（后台查看；仅暴露昵称/用户名，用于点名）。"""
    with db.get_db() as conn:
        return conn.execute(
            "SELECT u.id, u.username, u.nickname, r.created_at FROM activity_registrations r"
            " JOIN users u ON u.id=r.user_id WHERE r.activity_id=? ORDER BY r.created_at",
            (act_id,)).fetchall()


# ---------------- 活动标签库 ----------------
def tag_create(name: str, color: str) -> int:
    with db.get_db() as conn:
        cur = conn.execute("INSERT INTO activity_tags(name, color, created_at) VALUES (?,?,?)",
                           (name, color, time.time()))
        return int(cur.lastrowid)


def tag_list() -> list[sqlite3.Row]:
    with db.get_db() as conn:
        return conn.execute("SELECT * FROM activity_tags ORDER BY id").fetchall()


def tag_delete(tag_id: int) -> None:
    with db.get_db() as conn:
        conn.execute("DELETE FROM activity_tags WHERE id=?", (tag_id,))


# ============================================================
# 第三期：作业追踪
# ============================================================
def hw_create(subject: str, content: str, due_at: float,
              created_by: Optional[int]) -> int:
    ts: float = time.time()
    with db.get_db() as conn:
        cur = conn.execute(
            "INSERT INTO homework(subject, content, due_at, created_by, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?)", (subject, content, due_at, created_by, ts, ts))
        return int(cur.lastrowid)


def hw_update(hw_id: int, subject: str, content: str, due_at: float) -> None:
    with db.get_db() as conn:
        conn.execute("UPDATE homework SET subject=?, content=?, due_at=?, updated_at=?"
                     " WHERE id=?", (subject, content, due_at, time.time(), hw_id))


def hw_delete(hw_id: int) -> None:
    with db.get_db() as conn:
        conn.execute("DELETE FROM homework WHERE id=?", (hw_id,))


def hw_get(hw_id: int) -> Optional[sqlite3.Row]:
    """单条作业（存在性校验用；不含任何完成状态）。"""
    with db.get_db() as conn:
        return conn.execute("SELECT * FROM homework WHERE id=?", (hw_id,)).fetchone()


def hw_list(subject: Optional[str] = None, state: Optional[str] = None,
            limit: int = 200) -> list[dict[str, Any]]:
    """
    作业列表（全员登录可见的内容）。
    ⚠️ 隐私硬性要求：本函数【只返回作业本身】，绝不返回任何完成状态；
    完成状态由 hw_my_done_ids() 单独按当前用户取，前端自行合并。
    """
    sql: str = "SELECT * FROM homework WHERE 1=1"
    args: list[Any] = []
    if subject:
        sql += " AND subject=?"
        args.append(subject)
    if state == "expired":
        sql += " AND due_at < ?"
        args.append(time.time())
    elif state == "active":
        sql += " AND due_at >= ?"
        args.append(time.time())
    sql += " ORDER BY due_at ASC LIMIT ?"
    args.append(limit)
    with db.get_db() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def hw_my_done_ids(user_id: int) -> set[int]:
    """当前用户已完成的作业 id（只查自己 → 不可能泄露他人状态）。"""
    with db.get_db() as conn:
        rows = conn.execute("SELECT homework_id FROM homework_done WHERE user_id=?",
                            (user_id,)).fetchall()
    return {int(r["homework_id"]) for r in rows}


def hw_set_done(user_id: int, hw_id: int, done: bool) -> None:
    """勾选/取消（幂等）。"""
    with db.get_db() as conn:
        if done:
            conn.execute("INSERT OR IGNORE INTO homework_done(user_id, homework_id, done_at)"
                         " VALUES (?,?,?)", (user_id, hw_id, time.time()))
        else:
            conn.execute("DELETE FROM homework_done WHERE user_id=? AND homework_id=?",
                         (user_id, hw_id))


def hw_subjects() -> list[str]:
    """出现过的科目（筛选下拉用）。"""
    with db.get_db() as conn:
        rows = conn.execute("SELECT DISTINCT subject FROM homework ORDER BY subject").fetchall()
    return [str(r["subject"]) for r in rows]


def hw_latest(limit: int = 3) -> list[dict[str, Any]]:
    """最新未过期作业（首页卡片用）。"""
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM homework WHERE due_at >= ? ORDER BY due_at ASC LIMIT ?",
            (time.time(), limit)).fetchall()
    return [dict(r) for r in rows]


# ============================================================
# 第三期：学习资料库
# ============================================================
def res_create(title: str, subject: str, rtype: str, link: str, file_path: str,
               file_name: str, file_size: int, uploader_id: Optional[int]) -> int:
    with db.get_db() as conn:
        cur = conn.execute(
            "INSERT INTO resources(title, subject, rtype, link, file_path, file_name,"
            " file_size, uploader_id, downloads, created_at) VALUES (?,?,?,?,?,?,?,?,0,?)",
            (title, subject, rtype, link, file_path, file_name, file_size,
             uploader_id, time.time()))
        return int(cur.lastrowid)


def res_get(res_id: int) -> Optional[sqlite3.Row]:
    with db.get_db() as conn:
        return conn.execute("SELECT * FROM resources WHERE id=?", (res_id,)).fetchone()


def res_delete(res_id: int) -> None:
    with db.get_db() as conn:
        conn.execute("DELETE FROM resources WHERE id=?", (res_id,))


def res_list(subject: Optional[str] = None, sort: str = "time",
             limit: int = 200) -> list[sqlite3.Row]:
    """资料列表；sort=time 按时间倒序，sort=hot 按下载量倒序。"""
    sql: str = ("SELECT r.*, u.nickname AS uploader FROM resources r"
                " LEFT JOIN users u ON u.id=r.uploader_id")
    args: list[Any] = []
    if subject:
        sql += " WHERE r.subject=?"
        args.append(subject)
    sql += (" ORDER BY r.downloads DESC, r.created_at DESC" if sort == "hot"
            else " ORDER BY r.created_at DESC")
    sql += " LIMIT ?"
    args.append(limit)
    with db.get_db() as conn:
        return conn.execute(sql, args).fetchall()


def res_subjects() -> list[tuple[str, int]]:
    with db.get_db() as conn:
        rows = conn.execute("SELECT subject, COUNT(*) AS c FROM resources"
                            " GROUP BY subject ORDER BY c DESC").fetchall()
    return [(str(r["subject"]), int(r["c"])) for r in rows]


def res_bump_download(res_id: int) -> None:
    with db.get_db() as conn:
        conn.execute("UPDATE resources SET downloads=downloads+1 WHERE id=?", (res_id,))


def storage_used_bytes() -> int:
    """已上传文件占用字节（配额统计：仅统计 file 类型记录）。"""
    with db.get_db() as conn:
        row = conn.execute("SELECT COALESCE(SUM(file_size),0) AS s FROM resources"
                           " WHERE rtype='file'").fetchone()
    return int(row["s"] if row else 0)


# ============================================================
# 第三期：值日表
# ============================================================
def duty_week() -> dict[int, sqlite3.Row]:
    """一周 7 行（缺的日期返回空，由调用方补默认行）。"""
    with db.get_db() as conn:
        rows = conn.execute("SELECT * FROM duty_schedule ORDER BY day_of_week").fetchall()
    return {int(r["day_of_week"]): r for r in rows}


def duty_set(day_of_week: int, group_name: str, members: str, task: str) -> None:
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO duty_schedule(day_of_week, group_name, members, task, updated_at)"
            " VALUES (?,?,?,?,?) ON CONFLICT(day_of_week) DO UPDATE SET"
            " group_name=excluded.group_name, members=excluded.members,"
            " task=excluded.task, updated_at=excluded.updated_at",
            (int(day_of_week), group_name, members, task, time.time()))


def duty_delete(day_of_week: int) -> None:
    with db.get_db() as conn:
        conn.execute("DELETE FROM duty_schedule WHERE day_of_week=?", (int(day_of_week),))


def duty_rotate() -> int:
    """
    一键顺延轮换：每组后挪一天，周日(6) 的内容移到周一(0)。
    规则：new[d] = old[(d-1) mod 7]。返回受影响的日期数。
    """
    with db.get_db() as conn:
        rows = conn.execute("SELECT * FROM duty_schedule ORDER BY day_of_week").fetchall()
        old: dict[int, tuple[str, str, str]] = {
            int(r["day_of_week"]): (str(r["group_name"]), str(r["members"]), str(r["task"]))
            for r in rows
        }
        if not old:
            return 0
        ts: float = time.time()
        for d in range(7):
            src = (d - 1) % 7
            g, m, t = old.get(src, ("", "", ""))
            conn.execute(
                "INSERT INTO duty_schedule(day_of_week, group_name, members, task, updated_at)"
                " VALUES (?,?,?,?,?) ON CONFLICT(day_of_week) DO UPDATE SET"
                " group_name=excluded.group_name, members=excluded.members,"
                " task=excluded.task, updated_at=excluded.updated_at",
                (d, g, m, t, ts))
        return len(old)


def duty_today() -> Optional[sqlite3.Row]:
    """今天（北京时间的星期）的值日行；无数据返回 None。"""
    import common
    dow: int = common.beijing_now().weekday()   # Monday=0 … Sunday=6
    with db.get_db() as conn:
        return conn.execute("SELECT * FROM duty_schedule WHERE day_of_week=?",
                            (dow,)).fetchone()


# ============================================================
# 第三期：荣誉墙
# ============================================================
def honor_create(title: str, owner: str, hdate: str, description: str,
                 image_path: str, created_by: Optional[int]) -> int:
    with db.get_db() as conn:
        cur = conn.execute(
            "INSERT INTO honors(title, owner, hdate, description, image_path,"
            " is_pinned, created_by, created_at) VALUES (?,?,?,?,?,0,?,?)",
            (title, owner, hdate, description, image_path, created_by, time.time()))
        return int(cur.lastrowid)


def honor_get(honor_id: int) -> Optional[sqlite3.Row]:
    with db.get_db() as conn:
        return conn.execute("SELECT * FROM honors WHERE id=?", (honor_id,)).fetchone()


def honor_update(honor_id: int, title: str, owner: str, hdate: str,
                 description: str) -> None:
    with db.get_db() as conn:
        conn.execute("UPDATE honors SET title=?, owner=?, hdate=?, description=?"
                     " WHERE id=?", (title, owner, hdate, description, honor_id))


def honor_delete(honor_id: int) -> None:
    with db.get_db() as conn:
        conn.execute("DELETE FROM honors WHERE id=?", (honor_id,))


def honor_toggle_pin(honor_id: int) -> bool:
    with db.get_db() as conn:
        row = conn.execute("SELECT is_pinned FROM honors WHERE id=?", (honor_id,)).fetchone()
        if row is None:
            return False
        v: int = 0 if int(row["is_pinned"]) else 1
        conn.execute("UPDATE honors SET is_pinned=? WHERE id=?", (v, honor_id))
        return bool(v)


def honor_list(limit: int = 100) -> list[sqlite3.Row]:
    """置顶在前 + 时间倒序。"""
    with db.get_db() as conn:
        return conn.execute(
            "SELECT * FROM honors ORDER BY is_pinned DESC, created_at DESC LIMIT ?",
            (limit,)).fetchall()


def honor_pinned_one() -> Optional[sqlite3.Row]:
    """首页荣誉精选：取置顶的一条；没有置顶则取最新一条。"""
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM honors WHERE is_pinned=1"
                           " ORDER BY created_at DESC LIMIT 1").fetchone()
        if row is None:
            row = conn.execute("SELECT * FROM honors ORDER BY created_at DESC LIMIT 1"
                               ).fetchone()
    return row


# ============================================================
# 第四期：每日一句
# ============================================================
def quote_create(content: str, author: str, user_id: Optional[int]) -> int:
    with db.get_db() as conn:
        cur = conn.execute(
            "INSERT INTO quotes(content, author, user_id, status, like_count, created_at)"
            " VALUES (?,?,?,'pending',0,?)",
            (content[:100], author[:20], user_id, time.time()))
        return int(cur.lastrowid)


def quote_get(quote_id: int) -> Optional[sqlite3.Row]:
    with db.get_db() as conn:
        return conn.execute("SELECT * FROM quotes WHERE id=?", (quote_id,)).fetchone()


def quote_approved_count() -> int:
    with db.get_db() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM quotes WHERE status='approved'"
                                ).fetchone()[0])


def quote_by_offset(offset: int) -> Optional[sqlite3.Row]:
    """按已审核句子的稳定顺序（id 升序）取第 offset 条 —— 每日轮换用。"""
    with db.get_db() as conn:
        return conn.execute(
            "SELECT * FROM quotes WHERE status='approved' ORDER BY id LIMIT 1 OFFSET ?",
            (max(0, int(offset)),)).fetchone()


def quote_pending_count() -> int:
    with db.get_db() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM quotes WHERE status='pending'"
                                ).fetchone()[0])


def quote_list(status: Optional[str] = None, limit: int = 200,
               viewer_id: Optional[int] = None,
               user_id: Optional[int] = None) -> list[dict[str, Any]]:
    """
    句子列表。status=None 时只返回已审核（前台）；后台可传具体状态。
    viewer_id 用于标记 liked（只查当前用户）；user_id 用于"我的投稿"。
    """
    sql: str = "SELECT * FROM quotes WHERE 1=1"
    args: list[Any] = []
    if status:
        sql += " AND status=?"
        args.append(status)
    else:
        sql += " AND status='approved'"
    if user_id is not None:
        sql += " AND user_id=?"
        args.append(user_id)
    sql += " ORDER BY like_count DESC, id DESC LIMIT ?"
    args.append(limit)
    with db.get_db() as conn:
        rows = conn.execute(sql, args).fetchall()
        liked: set[int] = set()
        if viewer_id is not None and rows:
            ids: list[int] = [int(r["id"]) for r in rows]
            marks: str = ",".join("?" * len(ids))
            for x in conn.execute(
                    f"SELECT quote_id FROM quote_likes WHERE user_id=? AND quote_id IN ({marks})",
                    [viewer_id] + ids).fetchall():
                liked.add(int(x["quote_id"]))
    return [{
        "id": int(r["id"]), "content": str(r["content"]),
        "author": str(r["author"]) or "匿名同学",
        "status": str(r["status"]), "like_count": int(r["like_count"]),
        "created_at": float(r["created_at"]),
        "liked": int(r["id"]) in liked,
        "mine": viewer_id is not None and int(r["user_id"] or -1) == viewer_id,
    } for r in rows]


def quote_review(quote_id: int, status: str, reviewer_id: Optional[int]) -> None:
    with db.get_db() as conn:
        conn.execute("UPDATE quotes SET status=?, reviewed_by=?, reviewed_at=?"
                     " WHERE id=?", (status, reviewer_id, time.time(), quote_id))


def quote_delete(quote_id: int) -> None:
    with db.get_db() as conn:
        conn.execute("DELETE FROM quotes WHERE id=?", (quote_id,))


def quote_like(quote_id: int, user_id: int) -> tuple[bool, int]:
    """点赞/取消（幂等，模式与吐槽墙一致；计数用真实条数回写防漂移）。"""
    with db.get_db() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO quote_likes(quote_id, user_id, created_at) VALUES (?,?,?)",
            (quote_id, user_id, time.time()))
        liked: bool = cur.rowcount == 1
        if not liked:
            conn.execute("DELETE FROM quote_likes WHERE quote_id=? AND user_id=?",
                         (quote_id, user_id))
        real: int = int(conn.execute("SELECT COUNT(*) FROM quote_likes WHERE quote_id=?",
                                     (quote_id,)).fetchone()[0])
        conn.execute("UPDATE quotes SET like_count=? WHERE id=?", (real, quote_id))
    return liked, real


# ============================================================
# 第四期：打卡挑战
# ============================================================
def chal_create(name: str, icon: str, start_date: str, end_date: str,
                created_by: Optional[int]) -> int:
    with db.get_db() as conn:
        cur = conn.execute(
            "INSERT INTO challenges(name, icon, start_date, end_date, archived,"
            " created_by, created_at) VALUES (?,?,?,?,0,?,?)",
            (name[:30], icon[:24], start_date, end_date, created_by, time.time()))
        return int(cur.lastrowid)


def chal_get(chal_id: int) -> Optional[sqlite3.Row]:
    with db.get_db() as conn:
        return conn.execute("SELECT * FROM challenges WHERE id=?", (chal_id,)).fetchone()


def chal_list(include_archived: bool = False) -> list[sqlite3.Row]:
    with db.get_db() as conn:
        if include_archived:
            return conn.execute("SELECT * FROM challenges"
                                " ORDER BY archived ASC, start_date DESC").fetchall()
        return conn.execute("SELECT * FROM challenges WHERE archived=0"
                            " ORDER BY start_date DESC").fetchall()


def chal_delete(chal_id: int) -> None:
    with db.get_db() as conn:
        conn.execute("DELETE FROM challenges WHERE id=?", (chal_id,))


def chal_archive_expired(today: str) -> int:
    """
    自动归档：end_date < today（北京日期）的挑战置 archived=1。
    幂等；由 /checkin 访问时惰性触发（免费版无可靠定时任务）。
    """
    with db.get_db() as conn:
        cur = conn.execute("UPDATE challenges SET archived=1"
                           " WHERE archived=0 AND end_date < ?", (today,))
        return int(cur.rowcount)


def ck_add(user_id: int, chal_id: int, cdate: str) -> bool:
    """打卡（UNIQUE 兜底幂等）。返回本次是否真的新增。"""
    with db.get_db() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO checkin_records(user_id, challenge_id, cdate, created_at)"
            " VALUES (?,?,?,?)", (user_id, chal_id, cdate, time.time()))
        return cur.rowcount == 1


def ck_has(user_id: int, chal_id: int, cdate: str) -> bool:
    with db.get_db() as conn:
        return conn.execute(
            "SELECT 1 FROM checkin_records WHERE user_id=? AND challenge_id=? AND cdate=?",
            (user_id, chal_id, cdate)).fetchone() is not None


def ck_dates(user_id: int, chal_id: int) -> set[str]:
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT cdate FROM checkin_records WHERE user_id=? AND challenge_id=?",
            (user_id, chal_id)).fetchall()
    return {str(r["cdate"]) for r in rows}


def ck_my_all_dates(user_id: int) -> set[str]:
    """我在所有挑战里打过卡的日期集合（个人中心热力图用）。"""
    with db.get_db() as conn:
        rows = conn.execute("SELECT DISTINCT cdate FROM checkin_records WHERE user_id=?",
                            (user_id,)).fetchall()
    return {str(r["cdate"]) for r in rows}


def ck_month_count(user_id: int, year: int, month: int) -> dict[str, int]:
    """某月每天的打卡次数（值 >0 表示当天有打卡）——月历热力图。"""
    prefix: str = f"{year:04d}-{month:02d}-"
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT cdate, COUNT(*) AS c FROM checkin_records WHERE user_id=?"
            " AND cdate LIKE ? GROUP BY cdate", (user_id, prefix + "%")).fetchall()
    return {str(r["cdate"]): int(r["c"]) for r in rows}


def calc_streak(dates: set[str], today: str) -> int:
    """
    连续打卡天数。规则：
      · 今天已打卡 → 从今天往前连续计数
      · 今天未打卡 → 从昨天往前连续计数（今天还没开始补，不算断签）
      · 昨天也没打卡 → 0（断签清零）
    参数 dates 为该用户在某挑战下的打卡日期集合；today 为北京日期字符串。
    """
    from datetime import datetime, timedelta
    try:
        cur = datetime.strptime(today, "%Y-%m-%d").date()
    except ValueError:
        return 0
    if today not in dates:
        cur = cur - timedelta(days=1)
        if cur.strftime("%Y-%m-%d") not in dates:
            return 0
    n: int = 0
    while cur.strftime("%Y-%m-%d") in dates:
        n += 1
        cur = cur - timedelta(days=1)
    return n


def ck_streak(user_id: int, chal_id: int, today: str) -> int:
    return calc_streak(ck_dates(user_id, chal_id), today)


def ck_leaderboard(chal_id: int, today: str,
                   limit: int = 10) -> list[dict[str, Any]]:
    """
    班级排行榜：该挑战下所有参与者的连续天数降序取前 N。
    数据量小（班级规模），在 Python 侧统一算 streak，避免复杂 SQL。
    """
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT r.user_id, u.nickname, r.cdate FROM checkin_records r"
            " JOIN users u ON u.id=r.user_id WHERE r.challenge_id=?", (chal_id,)).fetchall()
    per_user: dict[int, dict[str, Any]] = {}
    for r in rows:
        uid: int = int(r["user_id"])
        d: dict[str, Any] = per_user.setdefault(uid, {"nickname": str(r["nickname"]),
                                                      "dates": set()})
        d["dates"].add(str(r["cdate"]))
    board: list[dict[str, Any]] = []
    for uid, d in per_user.items():
        board.append({"user_id": uid, "nickname": d["nickname"],
                      "streak": calc_streak(d["dates"], today),
                      "total": len(d["dates"])})
    board.sort(key=lambda x: (-x["streak"], -x["total"], x["user_id"]))
    for i, b in enumerate(board):
        b["rank"] = i + 1
    return board[:limit]


def ck_my_rank(user_id: int, chal_id: int, today: str) -> Optional[dict[str, Any]]:
    """我的名次（排行榜固定前排显示"我的名次"，即使不在前 10）。"""
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT r.user_id, u.nickname, r.cdate FROM checkin_records r"
            " JOIN users u ON u.id=r.user_id WHERE r.challenge_id=?", (chal_id,)).fetchall()
    per_user: dict[int, set[str]] = {}
    names: dict[int, str] = {}
    for r in rows:
        uid: int = int(r["user_id"])
        per_user.setdefault(uid, set()).add(str(r["cdate"]))
        names[uid] = str(r["nickname"])
    if user_id not in per_user:
        return None
    scored: list[tuple[int, int, int]] = []
    for uid, ds in per_user.items():
        scored.append((calc_streak(ds, today), len(ds), uid))
    scored.sort(key=lambda x: (-x[0], -x[1], x[2]))
    for i, (streak, total, uid) in enumerate(scored):
        if uid == user_id:
            return {"rank": i + 1, "streak": streak, "total": total,
                    "name": names.get(uid, "我"), "of": len(scored)}
    return None


# ============================================================
# 第四期：随机点名名单
# ============================================================
def member_list(active_only: bool = False) -> list[sqlite3.Row]:
    with db.get_db() as conn:
        if active_only:
            return conn.execute("SELECT * FROM class_members WHERE is_active=1"
                                " ORDER BY seat_no, id").fetchall()
        return conn.execute("SELECT * FROM class_members ORDER BY seat_no, id").fetchall()


def member_add(name: str, seat_no: int = 0) -> bool:
    """新增（name 唯一；重复返回 False）。"""
    with db.get_db() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO class_members(name, seat_no, is_active, created_at)"
            " VALUES (?,?,1,?)", (name[:20], int(seat_no), time.time()))
        return cur.rowcount == 1


def member_update(member_id: int, name: str, seat_no: int, is_active: bool) -> None:
    with db.get_db() as conn:
        conn.execute("UPDATE class_members SET name=?, seat_no=?, is_active=? WHERE id=?",
                     (name[:20], int(seat_no), 1 if is_active else 0, member_id))


def member_delete(member_id: int) -> None:
    with db.get_db() as conn:
        conn.execute("DELETE FROM class_members WHERE id=?", (member_id,))


def member_import_from_users() -> int:
    """从 users 一键导入昵称并去重（INSERT OR IGNORE 靠 name 唯一约束）。返回新增数。"""
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT nickname FROM users WHERE is_banned=0 AND nickname<>''").fetchall()
        added: int = 0
        for r in rows:
            cur = conn.execute(
                "INSERT OR IGNORE INTO class_members(name, seat_no, is_active, created_at)"
                " VALUES (?,0,1,?)", (str(r["nickname"]).strip()[:20], time.time()))
            added += int(cur.rowcount)
    return added


def member_count() -> int:
    with db.get_db() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM class_members WHERE is_active=1"
                                ).fetchone()[0])


# ============================================================
# 第五期：登录日志 / 资源导航（友情链接）/ 超级管理员
# ============================================================
PART5_SCHEMA: str = """
-- 登录日志（成功/失败均记录；后台仅 root 或 admin.full 可查）
CREATE TABLE IF NOT EXISTS login_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL, -- 失败时可能为空
    username   TEXT NOT NULL DEFAULT '',                        -- 冗余记录尝试的用户名
    success    INTEGER NOT NULL DEFAULT 0,
    ip         TEXT NOT NULL DEFAULT '',
    ua         TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_logs_user ON login_logs(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_login_logs_created ON login_logs(created_at);

-- 友链分类（color 为首字母方块图标底色）
CREATE TABLE IF NOT EXISTS link_categories (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL UNIQUE,
    color TEXT NOT NULL DEFAULT '#4F6EF7',
    sort  INTEGER NOT NULL DEFAULT 0
);

-- 友情链接（禁外链 favicon：前端一律彩色首字母方块）
CREATE TABLE IF NOT EXISTS friendly_links (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    url         TEXT NOT NULL,
    category_id INTEGER NOT NULL REFERENCES link_categories(id) ON DELETE CASCADE,
    description TEXT NOT NULL DEFAULT '',
    sort        INTEGER NOT NULL DEFAULT 0,
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_links_cat ON friendly_links(category_id, is_active, sort);

-- 学生推荐链接（进审核队列，审批通过后转入 friendly_links）
CREATE TABLE IF NOT EXISTS link_submissions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    url         TEXT NOT NULL,
    category_id INTEGER REFERENCES link_categories(id) ON DELETE SET NULL,
    description TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending/approved/rejected
    submitted_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    reviewer_id  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    reviewed_at  REAL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_link_sub_status ON link_submissions(status, created_at);

-- 第五期补强索引：首页聚合与列表按时间扫描提速
CREATE INDEX IF NOT EXISTS idx_news_created ON news_items(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tucao_created ON tucao_posts(created_at DESC);
"""


def init_part5_db() -> None:
    """幂等建第 5 期新表与补强索引（migrate_part5.py 调用）。"""
    with db.get_db() as conn:
        conn.executescript(PART5_SCHEMA)


# ---------------- 登录日志 ----------------
def login_log_add(user_id: Optional[int], username: str, success: bool,
                  ip: str, ua: str) -> None:
    """登录成功/失败均落一条日志（ua 截断 200 字符防爆行）。"""
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO login_logs(user_id, username, success, ip, ua, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (user_id, username[:20], 1 if success else 0,
             ip[:64], ua[:200], time.time()))


def login_logs_list(limit: int = 50, offset: int = 0,
                    only_fail: bool = False) -> list[sqlite3.Row]:
    """后台分页查询（按时间倒序）。"""
    sql: str = ("SELECT l.*, u.nickname FROM login_logs l"
                " LEFT JOIN users u ON u.id=l.user_id")
    if only_fail:
        sql += " WHERE l.success=0"
    sql += " ORDER BY l.id DESC LIMIT ? OFFSET ?"
    with db.get_db() as conn:
        return conn.execute(sql, (limit, offset)).fetchall()


def login_logs_count(only_fail: bool = False) -> int:
    with db.get_db() as conn:
        if only_fail:
            return int(conn.execute(
                "SELECT COUNT(*) FROM login_logs WHERE success=0").fetchone()[0])
        return int(conn.execute("SELECT COUNT(*) FROM login_logs").fetchone()[0])


# ---------------- 超级管理员 ----------------
SUPERADMIN_ROLE: str = "superadmin"   # 角色名常量（全站唯一超管角色）
ADMIN_FULL_PERM: str = "admin.full"   # 超管权限 code（拥有它 = 拥有全部权限）


def is_superadmin(user_id: int) -> bool:
    """该用户是否为超级管理员。"""
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM users u JOIN roles r ON r.id=u.role_id"
            " WHERE u.id=? AND r.name=?", (user_id, SUPERADMIN_ROLE)).fetchone()
    return row is not None


def count_superadmins() -> int:
    """当前超管人数（系统必须保证 ≥1）。"""
    with db.get_db() as conn:
        return int(conn.execute(
            "SELECT COUNT(*) FROM users u JOIN roles r ON r.id=u.role_id"
            " WHERE r.name=? AND u.is_banned=0", (SUPERADMIN_ROLE,)).fetchone()[0])


def all_perm_codes() -> list[str]:
    """全部权限 code（admin.full 展开用）。"""
    with db.get_db() as conn:
        return [str(r["code"]) for r in conn.execute(
            "SELECT code FROM permissions ORDER BY id").fetchall()]


# ---------------- 友链分类 ----------------
def link_cat_list() -> list[sqlite3.Row]:
    with db.get_db() as conn:
        return conn.execute(
            "SELECT * FROM link_categories ORDER BY sort, id").fetchall()


def link_cat_add(name: str, color: str, sort: int) -> int:
    with db.get_db() as conn:
        cur = conn.execute(
            "INSERT INTO link_categories(name, color, sort) VALUES (?,?,?)",
            (name[:20], color[:20], int(sort)))
        return int(cur.lastrowid)


def link_cat_update(cat_id: int, name: str, color: str, sort: int) -> None:
    with db.get_db() as conn:
        conn.execute("UPDATE link_categories SET name=?, color=?, sort=? WHERE id=?",
                     (name[:20], color[:20], int(sort), cat_id))


def link_cat_delete(cat_id: int) -> int:
    """删除分类（级联删其下链接，返回受影响链接数）。"""
    with db.get_db() as conn:
        n: int = int(conn.execute(
            "SELECT COUNT(*) FROM friendly_links WHERE category_id=?",
            (cat_id,)).fetchone()[0])
        conn.execute("DELETE FROM link_submissions WHERE category_id=?", (cat_id,))
        conn.execute("DELETE FROM link_categories WHERE id=?", (cat_id,))
        return n


# ---------------- 友情链接 ----------------
def link_list_active() -> list[sqlite3.Row]:
    """前台展示：启用中的链接（带分类名与颜色）。"""
    with db.get_db() as conn:
        return conn.execute(
            "SELECT l.*, c.name AS cat_name, c.color AS cat_color, c.sort AS cat_sort"
            " FROM friendly_links l JOIN link_categories c ON c.id=l.category_id"
            " WHERE l.is_active=1 ORDER BY c.sort, c.id, l.sort, l.id").fetchall()


def link_list_all() -> list[sqlite3.Row]:
    """后台管理：全部链接。"""
    with db.get_db() as conn:
        return conn.execute(
            "SELECT l.*, c.name AS cat_name FROM friendly_links l"
            " JOIN link_categories c ON c.id=l.category_id"
            " ORDER BY c.sort, c.id, l.sort, l.id").fetchall()


def link_get(link_id: int) -> Optional[sqlite3.Row]:
    with db.get_db() as conn:
        return conn.execute("SELECT * FROM friendly_links WHERE id=?",
                            (link_id,)).fetchone()


def link_add(name: str, url: str, category_id: int, description: str,
             sort: int, created_by: Optional[int]) -> int:
    with db.get_db() as conn:
        cur = conn.execute(
            "INSERT INTO friendly_links(name, url, category_id, description, sort,"
            " is_active, created_by, created_at) VALUES (?,?,?,?,?,1,?,?)",
            (name[:30], url[:200], category_id, description[:80],
             int(sort), created_by, time.time()))
        return int(cur.lastrowid)


def link_update(link_id: int, name: str, url: str, category_id: int,
                description: str, sort: int, is_active: bool) -> None:
    with db.get_db() as conn:
        conn.execute(
            "UPDATE friendly_links SET name=?, url=?, category_id=?, description=?,"
            " sort=?, is_active=? WHERE id=?",
            (name[:30], url[:200], category_id, description[:80], int(sort),
             1 if is_active else 0, link_id))


def link_delete(link_id: int) -> None:
    with db.get_db() as conn:
        conn.execute("DELETE FROM friendly_links WHERE id=?", (link_id,))


# ---------------- 学生推荐（审核队列） ----------------
def link_sub_add(name: str, url: str, category_id: Optional[int],
                 description: str, submitted_by: Optional[int]) -> int:
    with db.get_db() as conn:
        cur = conn.execute(
            "INSERT INTO link_submissions(name, url, category_id, description,"
            " submitted_by, created_at) VALUES (?,?,?,?,?,?)",
            (name[:30], url[:200], category_id, description[:80],
             submitted_by, time.time()))
        return int(cur.lastrowid)


def link_sub_list(status: str = "pending", limit: int = 100) -> list[sqlite3.Row]:
    with db.get_db() as conn:
        return conn.execute(
            "SELECT s.*, u.nickname AS submitter, c.name AS cat_name"
            " FROM link_submissions s"
            " LEFT JOIN users u ON u.id=s.submitted_by"
            " LEFT JOIN link_categories c ON c.id=s.category_id"
            " WHERE s.status=? ORDER BY s.id DESC LIMIT ?",
            (status, limit)).fetchall()


def link_sub_get(sub_id: int) -> Optional[sqlite3.Row]:
    with db.get_db() as conn:
        return conn.execute("SELECT * FROM link_submissions WHERE id=?",
                            (sub_id,)).fetchone()


def link_sub_approve(sub_id: int, reviewer_id: int) -> Optional[int]:
    """
    审批通过：把推荐转入 friendly_links（幂等——仅 pending 态可审）。
    返回新链接 id；状态不是 pending 返回 None。
    """
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM link_submissions WHERE id=? AND status='pending'",
                           (sub_id,)).fetchone()
        if row is None:
            return None
        cat_id: int = int(row["category_id"]) if row["category_id"] else 0
        if cat_id <= 0:
            # 未选分类 → 兜底进 sort 最小的第一个分类
            first = conn.execute(
                "SELECT id FROM link_categories ORDER BY sort, id LIMIT 1").fetchone()
            if first is None:
                return None
            cat_id = int(first["id"])
        cur = conn.execute(
            "INSERT INTO friendly_links(name, url, category_id, description, sort,"
            " is_active, created_by, created_at) VALUES (?,?,?,?,0,1,?,?)",
            (str(row["name"]), str(row["url"]), cat_id,
             str(row["description"]), reviewer_id, time.time()))
        conn.execute(
            "UPDATE link_submissions SET status='approved', reviewer_id=?,"
            " reviewed_at=? WHERE id=?", (reviewer_id, time.time(), sub_id))
        return int(cur.lastrowid)


def link_sub_reject(sub_id: int, reviewer_id: int) -> bool:
    with db.get_db() as conn:
        cur = conn.execute(
            "UPDATE link_submissions SET status='rejected', reviewer_id=?,"
            " reviewed_at=? WHERE id=? AND status='pending'",
            (reviewer_id, time.time(), sub_id))
        return cur.rowcount == 1


# ============================================================
# 第六期：网站更新公告 / 已读记录 / 密码重置码
# ============================================================
PART6_SCHEMA: str = """
-- 网站更新公告（preview=预告 | notice=通知 | release=更新；release 必填 version_tag）
-- content 为 Markdown-lite（仅换行/加粗/列表），存储前服务端已做 HTML 转义
CREATE TABLE IF NOT EXISTS site_updates (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    update_type  TEXT NOT NULL DEFAULT 'notice',
    title        TEXT NOT NULL,
    content      TEXT NOT NULL DEFAULT '',
    version_tag  TEXT NOT NULL DEFAULT '',
    publish_at   REAL NOT NULL DEFAULT 0,
    is_published INTEGER NOT NULL DEFAULT 0,
    is_pinned    INTEGER NOT NULL DEFAULT 0,
    created_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_updates_pub
    ON site_updates(is_published, is_pinned DESC, publish_at DESC);

-- 已读记录（复合主键 → 幂等，重复标记无副作用）
CREATE TABLE IF NOT EXISTS site_update_reads (
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    update_id INTEGER NOT NULL REFERENCES site_updates(id) ON DELETE CASCADE,
    read_at   REAL NOT NULL,
    PRIMARY KEY (user_id, update_id)
);

-- 密码重置码（只存 HMAC 哈希，绝不存明文；15 分钟有效；一码一次）
CREATE TABLE IF NOT EXISTS password_reset_codes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    target_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash      TEXT NOT NULL,
    created_by     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    expires_at     REAL NOT NULL,
    used_at        REAL,                       -- 非空=已用（含被新码作废）
    created_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reset_codes_target
    ON password_reset_codes(target_user_id);
"""


def init_part6_db() -> None:
    """幂等建第 6 期新表与索引（migrate_part6.py 调用）。"""
    with db.get_db() as conn:
        conn.executescript(PART6_SCHEMA)


def ensure_pwd_changed_column() -> bool:
    """
    users 表补 password_changed_at 列（改密时间戳，用于"改密后全部会话失效"）。
    SQLite ALTER TABLE 无 IF NOT EXISTS，先查 PRAGMA 再决定是否执行；返回是否新增。
    """
    with db.get_db() as conn:
        cols = {str(r["name"]) for r in conn.execute("PRAGMA table_info(users)")}
        if "password_changed_at" in cols:
            return False
        conn.execute(
            "ALTER TABLE users ADD COLUMN password_changed_at REAL NOT NULL DEFAULT 0")
        return True


# ---------------- 网站更新公告：写 ----------------
UPDATE_TYPES: tuple[str, ...] = ("preview", "notice", "release")


def update_add(update_type: str, title: str, content: str, version_tag: str,
               is_pinned: bool, created_by: Optional[int]) -> int:
    """新建更新公告（默认草稿 is_published=0，发布走 update_set_published）。"""
    with db.get_db() as conn:
        cur = conn.execute(
            "INSERT INTO site_updates(update_type, title, content, version_tag,"
            " publish_at, is_published, is_pinned, created_by, created_at)"
            " VALUES (?,?,?,?,0,0,?,?,?)",
            (update_type, title[:60], content, version_tag[:20],
             1 if is_pinned else 0, created_by, time.time()))
        return int(cur.lastrowid)


def update_edit(update_id: int, update_type: str, title: str, content: str,
                version_tag: str, is_pinned: bool) -> bool:
    with db.get_db() as conn:
        cur = conn.execute(
            "UPDATE site_updates SET update_type=?, title=?, content=?,"
            " version_tag=?, is_pinned=? WHERE id=?",
            (update_type, title[:60], content, version_tag[:20],
             1 if is_pinned else 0, update_id))
        return cur.rowcount == 1


def update_set_published(update_id: int, published: bool) -> bool:
    """发布/下架。首次发布时把 publish_at 落成当前时间（列表按它排序）。"""
    with db.get_db() as conn:
        if published:
            cur = conn.execute(
                "UPDATE site_updates SET is_published=1,"
                " publish_at=CASE WHEN publish_at=0 THEN ? ELSE publish_at END"
                " WHERE id=?", (time.time(), update_id))
        else:
            cur = conn.execute(
                "UPDATE site_updates SET is_published=0 WHERE id=?", (update_id,))
        return cur.rowcount == 1


def update_set_pinned(update_id: int, pinned: bool) -> bool:
    with db.get_db() as conn:
        cur = conn.execute("UPDATE site_updates SET is_pinned=? WHERE id=?",
                           (1 if pinned else 0, update_id))
        return cur.rowcount == 1


# ---------------- 网站更新公告：读 ----------------
def update_get(update_id: int) -> Optional[sqlite3.Row]:
    with db.get_db() as conn:
        return conn.execute(
            "SELECT s.*, u.nickname AS author FROM site_updates s"
            " LEFT JOIN users u ON u.id=s.created_by WHERE s.id=?",
            (update_id,)).fetchone()


def update_list_public(update_type: str = "", limit: int = 20,
                       offset: int = 0) -> list[sqlite3.Row]:
    """前台列表：仅已发布；置顶在前，其余按发布时间倒序。"""
    sql: str = ("SELECT s.*, u.nickname AS author FROM site_updates s"
                " LEFT JOIN users u ON u.id=s.created_by"
                " WHERE s.is_published=1")
    args: list[Any] = []
    if update_type in UPDATE_TYPES:
        sql += " AND s.update_type=?"
        args.append(update_type)
    sql += " ORDER BY s.is_pinned DESC, s.publish_at DESC, s.id DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    with db.get_db() as conn:
        return conn.execute(sql, args).fetchall()


def update_list_admin(limit: int = 100) -> list[sqlite3.Row]:
    """管理端列表：含草稿，按创建时间倒序。"""
    with db.get_db() as conn:
        return conn.execute(
            "SELECT s.*, u.nickname AS author FROM site_updates s"
            " LEFT JOIN users u ON u.id=s.created_by"
            " ORDER BY s.id DESC LIMIT ?", (limit,)).fetchall()


def update_latest_release() -> Optional[sqlite3.Row]:
    """最新一条已发布的 release（首页"网站更新"摘要卡用）。"""
    with db.get_db() as conn:
        return conn.execute(
            "SELECT * FROM site_updates"
            " WHERE is_published=1 AND update_type='release'"
            " ORDER BY publish_at DESC, id DESC LIMIT 1").fetchone()


# ---------------- 已读 / 未读（小红点） ----------------
def update_mark_read(user_id: int, update_id: int) -> None:
    """标记已读（INSERT OR IGNORE 天然幂等）。"""
    with db.get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO site_update_reads(user_id, update_id, read_at)"
            " VALUES (?,?,?)", (user_id, update_id, time.time()))


def update_unread_count(user_id: int) -> int:
    """未读的已发布条数（导航/首页红点；一次索引查询，无轮询）。"""
    with db.get_db() as conn:
        return int(conn.execute(
            "SELECT COUNT(*) FROM site_updates s"
            " WHERE s.is_published=1 AND NOT EXISTS ("
            "   SELECT 1 FROM site_update_reads r"
            "   WHERE r.update_id=s.id AND r.user_id=?)",
            (user_id,)).fetchone()[0])


def update_unread_latest(user_id: int) -> Optional[sqlite3.Row]:
    """最新一条未读（首页横幅文案"网站有新动态 v1.x"用）。"""
    with db.get_db() as conn:
        return conn.execute(
            "SELECT s.* FROM site_updates s"
            " WHERE s.is_published=1 AND NOT EXISTS ("
            "   SELECT 1 FROM site_update_reads r"
            "   WHERE r.update_id=s.id AND r.user_id=?)"
            " ORDER BY s.is_pinned DESC, s.publish_at DESC, s.id DESC LIMIT 1",
            (user_id,)).fetchone()


def update_my_reads(user_id: int, limit: int = 50) -> list[sqlite3.Row]:
    """我的已读更新记录（/me 普通用户区）。"""
    with db.get_db() as conn:
        return conn.execute(
            "SELECT r.update_id, s.id, s.update_type, s.title, s.version_tag,"
            " s.publish_at, r.read_at FROM site_update_reads r"
            " JOIN site_updates s ON s.id=r.update_id"
            " WHERE r.user_id=? ORDER BY r.read_at DESC LIMIT ?",
            (user_id, limit)).fetchall()


# ---------------- 密码重置码 ----------------
def reset_code_create(target_user_id: int, code_hash: str, created_by: int,
                      expires_at: float) -> int:
    """
    生成新码：同一事务内先作废旧码（used_at=now），保证同一用户同时
    只有 1 个未过期未使用的码。
    """
    now: float = time.time()
    with db.get_db() as conn:
        conn.execute(
            "UPDATE password_reset_codes SET used_at=?"
            " WHERE target_user_id=? AND used_at IS NULL", (now, target_user_id))
        cur = conn.execute(
            "INSERT INTO password_reset_codes(target_user_id, code_hash,"
            " created_by, expires_at, created_at) VALUES (?,?,?,?,?)",
            (target_user_id, code_hash, created_by, expires_at, now))
        return int(cur.lastrowid)


def reset_code_active(target_user_id: int) -> Optional[sqlite3.Row]:
    """该用户当前唯一活跃码（未使用且未过期）；无则 None。"""
    with db.get_db() as conn:
        return conn.execute(
            "SELECT * FROM password_reset_codes"
            " WHERE target_user_id=? AND used_at IS NULL AND expires_at>?"
            " ORDER BY id DESC LIMIT 1",
            (target_user_id, time.time())).fetchone()


def reset_code_latest(target_user_id: int) -> Optional[sqlite3.Row]:
    """该用户最近一条码（管理面板状态展示用；绝不返回明文——库里也只有哈希）。"""
    with db.get_db() as conn:
        return conn.execute(
            "SELECT c.*, u.nickname AS creator FROM password_reset_codes c"
            " LEFT JOIN users u ON u.id=c.created_by"
            " WHERE c.target_user_id=? ORDER BY c.id DESC LIMIT 1",
            (target_user_id,)).fetchone()


def reset_code_mark_used(code_id: int) -> None:
    with db.get_db() as conn:
        conn.execute("UPDATE password_reset_codes SET used_at=? WHERE id=?",
                     (time.time(), code_id))


def bump_password_changed_at(user_id: int) -> None:
    """改密时间戳：会话 born_at 早于它的会话全部被判定失效（强制重登）。"""
    with db.get_db() as conn:
        conn.execute("UPDATE users SET password_changed_at=? WHERE id=?",
                     (time.time(), user_id))
