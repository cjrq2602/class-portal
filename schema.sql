-- ============================================================
-- schema.sql —— 守夜小队 全部建表 SQL（SQLite）
-- 约定：主键 INTEGER 自增；外键需在连接上 PRAGMA foreign_keys=ON；
--       时间字段统一用 Unix 秒（float/int），便于服务端数值比较。
-- ============================================================

-- 玩家表：token 为断线重连凭证（localStorage 持久化）
CREATE TABLE IF NOT EXISTS players (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    token      TEXT    NOT NULL UNIQUE,   -- 随机 32 hex，重连凭证
    nickname   TEXT    NOT NULL,          -- 显示昵称
    last_seen  REAL    NOT NULL,          -- 最后活跃 Unix 秒
    created_at REAL    NOT NULL,
    wins       INTEGER NOT NULL DEFAULT 0, -- 本地战绩：胜场
    games      INTEGER NOT NULL DEFAULT 0  -- 本地战绩：总场次
);
CREATE INDEX IF NOT EXISTS idx_players_last_seen ON players(last_seen);
CREATE INDEX IF NOT EXISTS idx_players_token    ON players(token);

-- 快速匹配队列：一个玩家一行，离开时删除
CREATE TABLE IF NOT EXISTS match_queue (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL UNIQUE REFERENCES players(id) ON DELETE CASCADE,
    mode      TEXT    NOT NULL DEFAULT 'coop', -- 匹配模式（当前仅 coop）
    joined_at REAL    NOT NULL                 -- 入队 Unix 秒（30 秒凑不齐自动开）
);
CREATE INDEX IF NOT EXISTS idx_queue_joined ON match_queue(joined_at);

-- 房间表
CREATE TABLE IF NOT EXISTS rooms (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT    NOT NULL UNIQUE,      -- 4~6 位房间码（大写字母数字）
    status      TEXT    NOT NULL DEFAULT 'waiting',
                  -- waiting=等待 / playing=对局中 / finished=已结束 / abandoned=已废弃
    host_id     INTEGER REFERENCES players(id) ON DELETE SET NULL, -- 房主，退出自动转移
    is_public   INTEGER NOT NULL DEFAULT 1,   -- 是否公开（大厅可见）
    max_players INTEGER NOT NULL DEFAULT 4,   -- 2~4
    allow_join  INTEGER NOT NULL DEFAULT 1,   -- 是否允许中途补员
    created_at  REAL    NOT NULL,
    updated_at  REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rooms_status ON rooms(status);
CREATE INDEX IF NOT EXISTS idx_rooms_updated ON rooms(updated_at);

-- 房间成员表：seat 为座位号（0..max_players-1），机器人 is_bot=1
CREATE TABLE IF NOT EXISTS room_players (
    room_id       INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    player_id     INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    seat          INTEGER NOT NULL,
    is_bot        INTEGER NOT NULL DEFAULT 0,
    is_ready      INTEGER NOT NULL DEFAULT 0,
    last_action_at REAL   NOT NULL DEFAULT 0, -- 最后动作时间（掉线判定辅助）
    PRIMARY KEY (room_id, player_id)
);
CREATE INDEX IF NOT EXISTS idx_rp_room ON room_players(room_id);
CREATE INDEX IF NOT EXISTS idx_rp_player ON room_players(player_id);

-- 对局状态表：整局状态 JSON 存单行，version 全局递增做乐观锁 / 轮询变更检测
CREATE TABLE IF NOT EXISTS game_state (
    room_id    INTEGER PRIMARY KEY REFERENCES rooms(id) ON DELETE CASCADE,
    version    INTEGER NOT NULL DEFAULT 1,   -- 每次状态变更 +1，前端带 v 轮询
    state_json TEXT    NOT NULL,             -- game/logic.py 序列化的完整状态
    updated_at REAL    NOT NULL
);

-- 聊天表：只保留最近 50 条（写入时顺手裁剪，省 CPU）
CREATE TABLE IF NOT EXISTS chat_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id    INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    player_id  INTEGER REFERENCES players(id) ON DELETE SET NULL,
    text       TEXT    NOT NULL,
    created_at REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_room ON chat_messages(room_id, id);
