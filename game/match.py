"""
game/match.py —— 快速匹配、房间生命周期、机器人补位、清理
=======================================================
职责：
1. 快速匹配队列：凑满 2 人立即建房；等待超 30 秒用机器人补位开局。
2. 房间：建房/加入/准备/离开/房主转移/中途补员/观战。
3. 开局：全员准备 → 初始化 game_state → status=playing。
4. 空房清理：10 分钟无活动标记 abandoned（cleanup.py 定时调用）。
"""

from __future__ import annotations

import json
import secrets
import string
from typing import Any, Optional

import db
from game import config, entities

# ============================================================
# 工具
# ============================================================

def gen_room_code() -> str:
    """生成 4 位房间码（大写字母数字，排除易混淆字符）。"""
    alphabet: str = "".join(c for c in string.ascii_uppercase + string.digits if c not in "O0I1")
    while True:
        code: str = "".join(secrets.choice(alphabet) for _ in range(4))
        if db.get_room_by_code(code) is None:
            return code


def make_bot_player(nickname: str) -> int:
    """
    创建一个机器人玩家记录并返回其 id。
    机器人 token 带 "bot-" 前缀且不可猜，用 room_players.is_bot=1 标记身份；
    API 层会拒绝 bot token 的一切人类操作。
    """
    token: str = "bot-" + secrets.token_hex(16)
    return db.create_player(token, nickname)


# ============================================================
# 房间操作
# ============================================================

def create_room(host_id: int, is_public: bool = True,
                max_players: int = 4, allow_join: bool = True) -> dict[str, Any]:
    """创建房间，房主自动入座 0 号位。返回房间信息。"""
    max_players = max(2, min(4, int(max_players)))
    code: str = gen_room_code()
    room_id: int = db.create_room(code, host_id, is_public, max_players, allow_join)
    # 初始化等待期占位状态（结构完整，供 /api/state 统一读取）
    db.insert_game_state(room_id, 1, json.dumps(entities.new_lobby_state(), ensure_ascii=False))
    return {"room_id": room_id, "code": code}


def join_room(code: str, player_id: int) -> tuple[bool, str]:
    """加入房间。返回 (成功, 错误信息)。"""
    room = db.get_room_by_code(code.upper().strip())
    if room is None:
        return False, "房间不存在"
    if room["status"] in ("finished", "abandoned"):
        return False, "房间已结束"
    if room["status"] == "playing" and not room["allow_join"]:
        return False, "该房间不允许中途加入"
    members = db.get_room_players(room["id"])
    # 已在房间里（重连场景）直接放行
    if any(m["player_id"] == player_id for m in members):
        return True, ""
    if len(members) >= int(room["max_players"]):
        return False, "房间已满"
    used_seats = {int(m["seat"]) for m in members}
    free_seat: int = next((s for s in range(int(room["max_players"])) if s not in used_seats), -1)
    if free_seat < 0:
        return False, "房间已满"
    db.add_room_player(room["id"], player_id, free_seat, is_bot=False)
    return True, ""


def leave_room(room_id: int, player_id: int) -> None:
    """
    离开房间：移除座位；若房主离开则转移房主；若房间空了标记 abandoned。
    游戏中离开：座位由机器人接管（补员/防挂机）。
    """
    room = db.get_room(room_id)
    if room is None:
        return
    seat_row = db.find_player_seat(room_id, player_id)
    if seat_row is None:
        return

    members = [m for m in db.get_room_players(room_id) if m["player_id"] != player_id]
    if room["status"] == "playing" and not bool(seat_row["is_bot"]):
        # 游戏中：不删座位（保持玩家数据供重连），只做活跃标记由 last_seen 判定
        db.touch_room(room_id)
        return

    db.remove_room_player(room_id, player_id)
    if not members:
        db.update_room_status(room_id, "abandoned")
        return
    # 房主转移给最早加入的真人玩家
    if room["host_id"] == player_id:
        humans = [m for m in members if not bool(m["is_bot"])]
        new_host: int = int((humans or members)[0]["player_id"])
        db.set_host(room_id, new_host)
    db.touch_room(room_id)


def invite_bot(room_id: int) -> tuple[bool, str]:
    """邀请机器人补位（房主可用）。"""
    room = db.get_room(room_id)
    if room is None:
        return False, "房间不存在"
    if room["status"] != "waiting":
        return False, "游戏已开始，无法邀请机器人"
    members = db.get_room_players(room_id)
    if len(members) >= int(room["max_players"]):
        return False, "房间已满"
    used = {int(m["seat"]) for m in members}
    free: int = next((s for s in range(int(room["max_players"])) if s not in used), -1)
    bot_id: int = make_bot_player(f"机器人{free + 1}")
    db.add_room_player(room_id, bot_id, free, is_bot=True)
    db.set_ready(room_id, bot_id, True)  # 机器人默认就绪
    return True, ""


def try_start_game(room_id: int) -> bool:
    """
    检查是否满足开局条件（≥2 人且全部就绪），满足则初始化对局状态并开局。
    返回是否成功开局。
    """
    room = db.get_room(room_id)
    if room is None or room["status"] != "waiting":
        return False
    members = db.get_room_players(room_id)
    if len(members) < 2:
        return False
    if not all(bool(m["is_ready"]) for m in members):
        return False

    players = [
        {"player_id": int(m["player_id"]), "nickname": m["nickname"],
         "is_bot": bool(m["is_bot"])}
        for m in members
    ]
    state = entities.new_game_state(room_id, players)
    row = db.get_game_state(room_id)
    version: int = int(row["version"]) if row else 0
    if row is None:
        db.insert_game_state(room_id, 1, json.dumps(state, ensure_ascii=False))
    else:
        db.put_game_state(room_id, version + 1, json.dumps(state, ensure_ascii=False))
    db.set_all_ready(room_id)
    db.update_room_status(room_id, "playing")
    return True


# ============================================================
# 快速匹配
# ============================================================

def quick_match_tick() -> list[dict[str, Any]]:
    """
    匹配推进（每次 quick_match 请求或轮询触发）：
    - 队列 ≥2 人：取最早的 2 人建房开局（其余继续排队，下次再撮合）；
    - 队列 1 人等待超 30 秒：建房 + 1 个机器人补位开局。
    返回本次创建的房间列表（便于前端直接跳转）。
    """
    created: list[dict[str, Any]] = []
    members = db.queue_members()
    if not members:
        return created

    # 多人撮合：每 2 人一组
    idx: int = 0
    while idx + 1 < len(members):
        a, b = members[idx], members[idx + 1]
        room = create_room(int(a["player_id"]), is_public=False, max_players=4)
        join_ok, _ = join_room(room["code"], int(b["player_id"]))
        if join_ok:
            db.queue_leave_many([int(a["player_id"]), int(b["player_id"])])
            # 双方自动准备并开局（再补 2 个机器人撑起 4 人节奏）
            db.set_ready(room["room_id"], int(a["player_id"]), True)
            db.set_ready(room["room_id"], int(b["player_id"]), True)
            for i in range(2):
                invite_bot(room["room_id"])
            try_start_game(room["room_id"])
            created.append({"code": room["code"],
                            "players": [int(a["player_id"]), int(b["player_id"])]})
        idx += 2

    # 单人等待超时 → 机器人补位
    if len(created) == 0 and len(members) % 2 == 1:
        last = members[-1]
        waited: float = db.now() - float(last["joined_at"])
        if waited >= 30.0:
            room = create_room(int(last["player_id"]), is_public=False, max_players=4)
            db.queue_leave(int(last["player_id"]))
            db.set_ready(room["room_id"], int(last["player_id"]), True)
            for i in range(3):
                invite_bot(room["room_id"])
            try_start_game(room["room_id"])
            created.append({"code": room["code"], "players": [int(last["player_id"])]})
    return created


def poll_match(player_id: int) -> Optional[str]:
    """
    查询玩家是否已被撮合出房（前端轮询用）。
    返回房间码或 None。
    """
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT r.code FROM rooms r JOIN room_players rp ON rp.room_id=r.id"
            " WHERE rp.player_id=? AND rp.is_bot=0 AND r.status IN ('waiting','playing')"
            " ORDER BY r.updated_at DESC LIMIT 1",
            (player_id,),
        ).fetchall()
        return str(rows[0]["code"]) if rows else None


# ============================================================
# 清理
# ============================================================

def cleanup_idle_rooms() -> int:
    """10 分钟无活动的 waiting/playing 房间标记 abandoned。返回处理数量。"""
    cutoff: float = db.now() - config.ROOM_IDLE_SECONDS
    count: int = 0
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT id FROM rooms WHERE status IN ('waiting','playing') AND updated_at < ?",
            (cutoff,),
        ).fetchall()
        for r in rows:
            conn.execute("UPDATE rooms SET status='abandoned' WHERE id=?", (int(r["id"]),))
            count += 1
    return count
