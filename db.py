"""
db.py —— SQLite 数据访问层（唯一模块级职责：连接管理 + 通用读写）
============================================================
设计要点（对应平台约束）：
1. 每次请求短连接：PythonAnywhere 免费版 WSGI 进程常驻，不做常驻连接/后台线程。
2. WAL 模式：读写并发友好；busy_timeout 防止偶发 "database is locked"。
3. 外键强制开启：保证 room_players / game_state 级联清理有效。
4. 写路径集中在 game_state 的单行 UPDATE（乐观锁：WHERE version = 旧版本），
   配合 PythonAnywhere 单 Web 进程（免费版默认 1 worker），实际写冲突接近零。
5. 所有函数自带异常兜底由调用方（flask_app.py 统一 try/except）包装。
"""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Optional

# 数据库文件放在项目目录（PythonAnywhere 可写）
BASE_DIR: str = os.path.dirname(os.path.abspath(__file__))
DB_PATH: str = os.environ.get("NIGHTWATCH_DB", os.path.join(BASE_DIR, "nightwatch.db"))
SCHEMA_PATH: str = os.path.join(BASE_DIR, "schema.sql")

# 写冲突最长等待 3 秒（WAL 下极少触发）
BUSY_TIMEOUT_MS: int = 3000


def _configure(conn: sqlite3.Connection) -> None:
    """连接级 PRAGMA 配置：WAL + 外键 + 忙等待。"""
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    """短生命周期连接上下文；with 块结束自动 commit/rollback + close。"""
    conn = sqlite3.connect(DB_PATH, timeout=BUSY_TIMEOUT_MS / 1000.0)
    try:
        _configure(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """首次启动执行 schema.sql 建表（幂等：全部 IF NOT EXISTS）。"""
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema: str = f.read()
    with get_db() as conn:
        conn.executescript(schema)


def now() -> float:
    """统一时间源：Unix 秒（float），便于 last_seen / 超时计算。"""
    return time.time()


# ---------------- 玩家 ----------------

def create_player(token: str, nickname: str) -> int:
    """创建玩家并返回 player_id。"""
    ts: float = now()
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO players(token, nickname, last_seen, created_at) VALUES (?,?,?,?)",
            (token, nickname, ts, ts),
        )
        return int(cur.lastrowid)


def get_player_by_token(token: str) -> Optional[sqlite3.Row]:
    """按 token 查玩家（断线重连入口）。"""
    with get_db() as conn:
        return conn.execute("SELECT * FROM players WHERE token=?", (token,)).fetchone()


def get_player(player_id: int) -> Optional[sqlite3.Row]:
    """按 id 查玩家（门户战绩钩子用）。"""
    with get_db() as conn:
        return conn.execute("SELECT * FROM players WHERE id=?", (player_id,)).fetchone()


# 门户联动钩子：对局战绩写入后被调用，签名 (player_id, won) -> None。
# 由 flask_app.create_app() 注入具体实现（登录用户同步写 game_stats）；
# 默认 None = 纯游戏模式，对匿名流程零影响。
RESULT_HOOK: Optional[Callable[[int, bool], None]] = None


def touch_player(player_id: int) -> None:
    """刷新玩家活跃时间（每次 API 调用轻量 UPDATE）。"""
    with get_db() as conn:
        conn.execute("UPDATE players SET last_seen=? WHERE id=?", (now(), player_id))


def set_nickname(player_id: int, nickname: str) -> None:
    with get_db() as conn:
        conn.execute("UPDATE players SET nickname=? WHERE id=?", (nickname, player_id))


def add_game_result(player_id: int, won: bool) -> None:
    """本地战绩：胜率统计（加分项）。结束后触发门户钩子（若已注入）。"""
    with get_db() as conn:
        conn.execute(
            "UPDATE players SET games=games+1, wins=wins+? WHERE id=?",
            (1 if won else 0, player_id),
        )
    if RESULT_HOOK is not None:
        try:
            RESULT_HOOK(player_id, won)
        except Exception:  # noqa: BLE001 —— 门户联动失败绝不影响游戏主流程
            pass


# ---------------- 房间 / 成员 ----------------

def create_room(code: str, host_id: int, is_public: bool,
                max_players: int, allow_join: bool) -> int:
    """建房并让房主占 0 号座位，返回 room_id。"""
    ts: float = now()
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO rooms(code,status,host_id,is_public,max_players,allow_join,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (code, "waiting", host_id, 1 if is_public else 0,
             max_players, 1 if allow_join else 0, ts, ts),
        )
        room_id: int = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO room_players(room_id,player_id,seat,is_bot,is_ready,last_action_at)"
            " VALUES (?,?,?,?,0,?)",
            (room_id, host_id, 0, 0, ts),
        )
        return room_id


def get_room_by_code(code: str) -> Optional[sqlite3.Row]:
    with get_db() as conn:
        return conn.execute("SELECT * FROM rooms WHERE code=?", (code,)).fetchone()


def get_room(room_id: int) -> Optional[sqlite3.Row]:
    with get_db() as conn:
        return conn.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()


def get_room_players(room_id: int) -> list[sqlite3.Row]:
    """按座位号返回成员（含昵称/战绩，供房间与游戏界面渲染）。"""
    with get_db() as conn:
        return conn.execute(
            "SELECT rp.*, p.nickname, p.wins, p.games FROM room_players rp"
            " JOIN players p ON p.id = rp.player_id"
            " WHERE rp.room_id=? ORDER BY rp.seat",
            (room_id,),
        ).fetchall()


def find_player_seat(room_id: int, player_id: int) -> Optional[sqlite3.Row]:
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM room_players WHERE room_id=? AND player_id=?",
            (room_id, player_id),
        ).fetchone()


def add_room_player(room_id: int, player_id: int, seat: int, is_bot: bool = False) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO room_players(room_id,player_id,seat,is_bot,is_ready,last_action_at)"
            " VALUES (?,?,?,?,0,?)",
            (room_id, player_id, seat, 1 if is_bot else 0, now()),
        )


def remove_room_player(room_id: int, player_id: int) -> None:
    with get_db() as conn:
        conn.execute(
            "DELETE FROM room_players WHERE room_id=? AND player_id=?",
            (room_id, player_id),
        )


def set_ready(room_id: int, player_id: int, ready: bool) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE room_players SET is_ready=?, last_action_at=? WHERE room_id=? AND player_id=?",
            (1 if ready else 0, now(), room_id, player_id),
        )


def set_all_ready(room_id: int) -> None:
    """开局时统一置准备（防止残留状态）。"""
    with get_db() as conn:
        conn.execute(
            "UPDATE room_players SET is_ready=1 WHERE room_id=?", (room_id,))


def update_room_status(room_id: int, status: str) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE rooms SET status=?, updated_at=? WHERE id=?",
            (status, now(), room_id))


def touch_room(room_id: int) -> None:
    with get_db() as conn:
        conn.execute("UPDATE rooms SET updated_at=? WHERE id=?", (now(), room_id))


def set_host(room_id: int, host_id: int) -> None:
    with get_db() as conn:
        conn.execute("UPDATE rooms SET host_id=?, updated_at=? WHERE id=?",
                     (host_id, now(), room_id))


def list_public_rooms() -> list[sqlite3.Row]:
    """大厅公开房间列表（只取 waiting/playing，限量 20 条防大响应）。"""
    with get_db() as conn:
        return conn.execute(
            "SELECT r.*, (SELECT COUNT(*) FROM room_players WHERE room_id=r.id) AS player_count"
            " FROM rooms r WHERE r.is_public=1 AND r.status IN ('waiting','playing')"
            " ORDER BY r.updated_at DESC LIMIT 20",
        ).fetchall()


# ---------------- 快速匹配队列 ----------------

def queue_join(player_id: int, mode: str = "coop") -> None:
    """入队（幂等）：已在队列中则保留原 joined_at，确保 30 秒机器人补位可触发。"""
    with get_db() as conn:
        conn.execute(
            "INSERT INTO match_queue(player_id, mode, joined_at) VALUES (?,?,?)"
            " ON CONFLICT(player_id) DO NOTHING",
            (player_id, mode, now()),
        )


def queue_leave(player_id: int) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM match_queue WHERE player_id=?", (player_id,))


def queue_leave_many(player_ids: list[int]) -> None:
    if not player_ids:
        return
    marks = ",".join("?" * len(player_ids))
    with get_db() as conn:
        conn.execute(f"DELETE FROM match_queue WHERE player_id IN ({marks})", player_ids)


def queue_members() -> list[sqlite3.Row]:
    with get_db() as conn:
        return conn.execute(
            "SELECT q.*, p.nickname FROM match_queue q JOIN players p ON p.id=q.player_id"
            " ORDER BY q.joined_at",
        ).fetchall()


# ---------------- 对局状态（乐观锁核心） ----------------

def get_game_state(room_id: int) -> Optional[sqlite3.Row]:
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM game_state WHERE room_id=?", (room_id,)).fetchone()


def put_game_state(room_id: int, version: int, state_json: str) -> bool:
    """
    乐观锁写入：只有当存储版本仍等于调用方读到的 version 才写入成功。
    返回 True=写入成功；False=被并发写抢先（调用方应放弃本次结算，下次轮询再补）。
    """
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE game_state SET version=?, state_json=?, updated_at=?"
            " WHERE room_id=? AND version=?",
            (version, state_json, now(), room_id, version - 1),
        )
        return cur.rowcount == 1


def insert_game_state(room_id: int, version: int, state_json: str) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO game_state(room_id, version, state_json, updated_at) VALUES (?,?,?,?)",
            (room_id, version, state_json, now()),
        )


# ---------------- 聊天 ----------------

def add_chat(room_id: int, player_id: Optional[int], text: str) -> None:
    """写入聊天并裁剪，只保留最近 50 条（低频操作，不做触发器省 CPU）。"""
    with get_db() as conn:
        conn.execute(
            "INSERT INTO chat_messages(room_id, player_id, text, created_at) VALUES (?,?,?,?)",
            (room_id, player_id, text, now()),
        )
        conn.execute(
            "DELETE FROM chat_messages WHERE room_id=? AND id NOT IN"
            " (SELECT id FROM chat_messages WHERE room_id=? ORDER BY id DESC LIMIT 50)",
            (room_id, room_id),
        )


def get_chat_since(room_id: int, since_id: int) -> list[sqlite3.Row]:
    """增量拉取聊天（前端带 last_chat_id，降低响应体）。"""
    with get_db() as conn:
        return conn.execute(
            "SELECT c.id, c.text, c.created_at, p.nickname FROM chat_messages c"
            " LEFT JOIN players p ON p.id=c.player_id"
            " WHERE c.room_id=? AND c.id>? ORDER BY c.id LIMIT 50",
            (room_id, since_id),
        ).fetchall()


# ---------------- 轮询热路径合并查询（一次连接取多件，省连接开关销） ----------------

def state_poll_prefetch(code: str, token: str) -> tuple[
        Optional[sqlite3.Row], Optional[sqlite3.Row], bool]:
    """
    /api/game/state 合并首查：token→玩家、code→房间、是否成员。
    替代原先 get_player_by_token + get_room_by_code + find_player_seat
    三次独立连接（连接开关是慢盘部署上的主要开销）。
    """
    with get_db() as conn:
        player: Optional[sqlite3.Row] = None
        if token:
            player = conn.execute(
                "SELECT * FROM players WHERE token=?", (token,)).fetchone()
        room: Optional[sqlite3.Row] = conn.execute(
            "SELECT * FROM rooms WHERE code=?", (code,)).fetchone()
        is_member: bool = False
        if room is not None and player is not None:
            r = conn.execute(
                "SELECT 1 AS x FROM room_players WHERE room_id=? AND player_id=?",
                (int(room["id"]), int(player["id"]))).fetchone()
            is_member = r is not None
        return player, room, is_member


def state_poll_extras(room_id: int, chat_since: int) -> tuple[
        list[sqlite3.Row], list[sqlite3.Row]]:
    """一次连接：房间成员（含昵称/战绩） + 聊天增量（原两次独立连接）。"""
    with get_db() as conn:
        members = conn.execute(
            "SELECT rp.*, p.nickname, p.wins, p.games FROM room_players rp"
            " JOIN players p ON p.id = rp.player_id"
            " WHERE rp.room_id=? ORDER BY rp.seat",
            (room_id,)).fetchall()
        chat = conn.execute(
            "SELECT c.id, c.text, c.created_at, p.nickname FROM chat_messages c"
            " LEFT JOIN players p ON p.id=c.player_id"
            " WHERE c.room_id=? AND c.id>? ORDER BY c.id LIMIT 50",
            (room_id, chat_since)).fetchall()
        return members, chat
