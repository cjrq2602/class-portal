"""
blueprints/game.py —— 游戏蓝图（塔防整体搬家：/ → /game，/api/* → /api/game/*）
=================================================================================
职责：
1. 原封不动包装 game/ 目录逻辑（逻辑目录本身零改动）；
2. 游戏页面 /game + 全部游戏 API 前缀化 /api/game/*；
3. 账号打通（关键设计）：
   - 已登录：注入派生游戏令牌（不改 players 表结构），昵称自动带账号昵称，
     对局结果经 db.RESULT_HOOK 写入门户 game_stats；
   - 未登录：完全保留旧的匿名 token 流程，行为与改造前一致（硬性要求）。
4. 响应结构保持与原接口完全一致（游戏前端按旧契约解析）。
"""

from __future__ import annotations

import time
from typing import Any, Optional

from flask import Blueprint, jsonify, render_template, request, session

import common
import db
import portal_db
from game import actions, config, entities, logic, match

bp: Blueprint = Blueprint("game", __name__)


# ============================================================
# 登录 ↔ 游戏 打通
# ============================================================
def ensure_game_player(token: str, nickname: str) -> None:
    """
    确保登录用户对应的游戏玩家行存在且昵称同步。
    幂等轻量：一次 SELECT + 极少数情况一次 INSERT/UPDATE（仅 /game 页面加载时调用）。
    """
    player = db.get_player_by_token(token)
    if player is None:
        db.create_player(token, nickname)
        return
    if player["nickname"] != nickname:
        db.set_nickname(int(player["id"]), nickname)


def on_game_result(player_id: int, won: bool) -> None:
    """
    db.RESULT_HOOK 实现：对局结算时由 db.add_game_result 调用。
    仅当该玩家行属于登录用户（令牌可反查 user_id）时写门户战绩；
    匿名玩家不受任何影响。
    """
    player = db.get_player(player_id)
    if player is None:
        return
    user_id: Optional[int] = common.parse_game_token(str(player["token"]))
    if user_id is not None:
        portal_db.bump_game_stats(user_id, won)


def _portal_ctx() -> dict[str, Any]:
    """生成注入游戏页的门户上下文（登录态/昵称/令牌）。"""
    user_id = session.get("user_id")
    if not user_id:
        return {"logged_in": False}
    user = portal_db.get_user(int(user_id))
    if user is None or bool(user["is_banned"]):
        return {"logged_in": False}
    token: str = common.make_game_token(int(user_id))
    ensure_game_player(token, str(user["nickname"]))
    return {"logged_in": True, "nickname": user["nickname"], "token": token}


# ============================================================
# 页面
# ============================================================
@bp.route("/game")
def game_page():
    """游戏页面（原 / 迁移至此）。"""
    from flask import current_app
    return render_template("game/index.html",
                           static_v=current_app.config["STATIC_V"],
                           portal_ctx=_portal_ctx())


# ============================================================
# 内部工具（与原 flask_app.py 同语义）
# ============================================================
def _get_player(token: Optional[str]):
    """token → players 行；bot 令牌禁止调用人类接口。"""
    if not token or not isinstance(token, str) or token.startswith("bot-"):
        return None
    return db.get_player_by_token(token)


def _err(msg: str, code: int = 400):
    """游戏侧错误：保留原 {ok:false, error} 契约（前端按此解析）。"""
    return jsonify({"ok": False, "code": "game_error", "msg": msg, "error": msg}), code


# ============================================================
# 游戏 API（全部 /api/game/* 前缀；逻辑与原实现一字不差）
# ============================================================
@bp.route("/api/game/nickname", methods=["POST"])
@common.api_wrap
def api_nickname():
    """设置昵称（首次调用自动创建玩家并返回 token）。"""
    body: dict = request.get_json(silent=True) or {}
    if not common.rate_check("ip:" + request.remote_addr):
        return _err("请求太频繁，请稍后再试", 429)
    nickname: str = str(body.get("nickname", "")).strip()[:config.MAX_NICK_LEN]
    if not nickname:
        return _err("昵称不能为空")

    token: str = str(body.get("token", "") or "")
    if token.startswith("bot-"):
        return _err("非法 token", 401)
    player = _get_player(token) if token else None
    if player is not None:
        db.set_nickname(int(player["id"]), nickname)
        db.touch_player(int(player["id"]))
        return jsonify({"ok": True, "token": token, "player_id": int(player["id"]),
                        "nickname": nickname})
    import secrets
    new_token: str = secrets.token_hex(16)
    pid: int = db.create_player(new_token, nickname)
    return jsonify({"ok": True, "token": new_token, "player_id": pid, "nickname": nickname})


@bp.route("/api/game/lobby", methods=["GET"])
@common.api_wrap
def api_lobby():
    """大厅信息：公开房间列表 + 在线人数（5 分钟内活跃）。"""
    if not common.rate_check("ip:" + request.remote_addr):
        return _err("请求太频繁", 429)
    rooms = [
        {
            "code": r["code"], "status": r["status"], "player_count": int(r["player_count"]),
            "max_players": int(r["max_players"]), "is_public": bool(r["is_public"]),
        }
        for r in db.list_public_rooms()
    ]
    with db.get_db() as conn:
        online = conn.execute(
            "SELECT COUNT(*) AS c FROM players WHERE last_seen > ?",
            (db.now() - 300,),
        ).fetchone()["c"]
    return jsonify({"ok": True, "rooms": rooms, "online": int(online)})


@bp.route("/api/game/quick_match", methods=["POST"])
@common.api_wrap
def api_quick_match():
    """快速匹配：入队 → 撮合推进 → 返回匹配状态/房间码。"""
    body: dict = request.get_json(silent=True) or {}
    player = _get_player(str(body.get("token", "")))
    if player is None:
        return _err("无效 token，请重新设置昵称", 401)
    pid: int = int(player["id"])
    if not common.rate_check(f"p:{pid}"):
        return _err("请求太频繁", 429)
    db.touch_player(pid)

    existing = match.poll_match(pid)
    if existing:
        return jsonify({"ok": True, "status": "matched", "code": existing})

    db.queue_join(pid)
    created = match.quick_match_tick()
    mine: Optional[str] = None
    for c in created:
        if pid in c["players"]:
            mine = c["code"]
    if mine:
        return jsonify({"ok": True, "status": "matched", "code": mine})
    with db.get_db() as conn:
        row = conn.execute("SELECT joined_at FROM match_queue WHERE player_id=?",
                           (pid,)).fetchone()
    waited: float = (db.now() - float(row["joined_at"])) if row else 0.0
    return jsonify({"ok": True, "status": "queueing",
                    "queue_size": len(db.queue_members()), "waited": waited})


@bp.route("/api/game/create_room", methods=["POST"])
@common.api_wrap
def api_create_room():
    """创建房间（房主即建即入座）。"""
    body: dict = request.get_json(silent=True) or {}
    player = _get_player(str(body.get("token", "")))
    if player is None:
        return _err("无效 token", 401)
    pid: int = int(player["id"])
    if not common.rate_check(f"p:{pid}"):
        return _err("请求太频繁", 429)
    room = match.create_room(
        pid,
        is_public=bool(body.get("is_public", True)),
        max_players=int(body.get("max_players", 4)),
        allow_join=bool(body.get("allow_join", True)),
    )
    db.touch_player(pid)
    return jsonify({"ok": True, "code": room["code"], "room_id": room["room_id"]})


@bp.route("/api/game/join", methods=["POST"])
@common.api_wrap
def api_join():
    """按房间码加入房间（支持观战：spectate=true 只看不入座）。"""
    body: dict = request.get_json(silent=True) or {}
    player = _get_player(str(body.get("token", "")))
    if player is None:
        return _err("无效 token", 401)
    pid: int = int(player["id"])
    if not common.rate_check(f"p:{pid}"):
        return _err("请求太频繁", 429)
    code: str = str(body.get("code", "")).strip().upper()
    if not code:
        return _err("请输入房间码")

    room = db.get_room_by_code(code)
    if room is None:
        return _err("房间不存在")
    if bool(body.get("spectate")):
        return jsonify({"ok": True, "code": code, "spectating": True})
    ok, msg = match.join_room(code, pid)
    if not ok:
        return _err(msg)
    db.touch_player(pid)
    return jsonify({"ok": True, "code": code, "spectating": False})


@bp.route("/api/game/ready", methods=["POST"])
@common.api_wrap
def api_ready():
    """准备 / 取消准备；全员就绪自动开局。"""
    body: dict = request.get_json(silent=True) or {}
    player = _get_player(str(body.get("token", "")))
    if player is None:
        return _err("无效 token", 401)
    pid: int = int(player["id"])
    if not common.rate_check(f"p:{pid}"):
        return _err("请求太频繁", 429)
    room = db.get_room_by_code(str(body.get("code", "")).strip().upper())
    if room is None:
        return _err("房间不存在")
    room_id: int = int(room["id"])
    if db.find_player_seat(room_id, pid) is None:
        return _err("你不在该房间中")
    db.set_ready(room_id, pid, bool(body.get("ready", True)))
    started: bool = match.try_start_game(room_id)
    return jsonify({"ok": True, "started": started})


@bp.route("/api/game/state", methods=["GET"])
@common.api_wrap
def api_state():
    """
    核心轮询端点：版本没变只返回 {"changed": false} 轻响应（省 CPU / 流量）。
    参数：room=房间码, token=玩家凭证（可空=观战）, v=客户端已知版本, chat=已知聊天 id
    """
    code: str = request.args.get("room", "").strip().upper()
    token: str = request.args.get("token", "") or ""
    player = _get_player(token)
    rate_key: str = f"p:{int(player['id'])}" if player is not None else "ip:" + request.remote_addr
    if not common.rate_check(rate_key):
        return _err("请求太频繁", 429)
    room = db.get_room_by_code(code)
    if room is None:
        return _err("房间不存在", 404)
    room_id: int = int(room["id"])

    is_member: bool = False
    if player is not None:
        is_member = db.find_player_seat(room_id, int(player["id"])) is not None
        db.touch_player(int(player["id"]))

    # 任何请求都会驱动惰性结算（游戏公平推进的关键）
    state, version = logic.advance_and_save(room_id)

    client_v: int = int(request.args.get("v", "0") or 0)
    force_full: bool = room["status"] == "waiting"
    if client_v == version and not force_full:
        chat_rows = db.get_chat_since(room_id, int(request.args.get("chat", "0") or 0))
        return jsonify({
            "changed": False, "version": version, "server_ts": time.time(),
            "chat": [{"id": int(c["id"]), "nickname": c["nickname"] or "??",
                      "text": c["text"]} for c in chat_rows],
        })

    view = entities.state_public_view(state, room_id, version)
    members = db.get_room_players(room_id)
    view["room"] = {
        "code": code,
        "status": room["status"],
        "host_id": room["host_id"],
        "allow_join": bool(room["allow_join"]),
        "max_players": int(room["max_players"]),
    }
    view["members"] = [
        {"player_id": int(m["player_id"]), "nickname": m["nickname"],
         "seat": int(m["seat"]), "is_bot": bool(m["is_bot"]),
         "is_ready": bool(m["is_ready"]),
         "is_host": int(m["player_id"]) == int(room["host_id"] or -1),
         "is_me": player is not None and int(m["player_id"]) == int(player["id"]),
         "wins": int(m["wins"]), "games": int(m["games"]),
         }
        for m in members
    ]
    view["is_member"] = is_member
    chat_rows = db.get_chat_since(room_id, int(request.args.get("chat", "0") or 0))
    view["chat"] = [{"id": int(c["id"]), "nickname": c["nickname"] or "??",
                     "text": c["text"]} for c in chat_rows]
    view["changed"] = True
    return jsonify(view)


@bp.route("/api/game/action", methods=["POST"])
@common.api_wrap
def api_action():
    """所有游戏操作：body = {token, code, action:{type,...}}，服务端完整校验。"""
    body: dict = request.get_json(silent=True) or {}
    player = _get_player(str(body.get("token", "")))
    if player is None:
        return _err("无效 token", 401)
    pid: int = int(player["id"])
    if not common.rate_check(f"p:{pid}"):
        return _err("请求太频繁", 429)
    room = db.get_room_by_code(str(body.get("code", "")).strip().upper())
    if room is None:
        return _err("房间不存在", 404)
    if room["status"] not in ("playing",):
        return _err("对局未在进行中")
    action_obj: Any = body.get("action")
    if not isinstance(action_obj, dict):
        return _err("动作参数非法")
    ok, msg = actions.handle_action(int(room["id"]), pid, action_obj)
    if not ok:
        return _err(msg)
    return jsonify({"ok": True})


@bp.route("/api/game/chat", methods=["POST"])
@common.api_wrap
def api_chat():
    """发送聊天（只保留最近 50 条，随轮询增量返回）。"""
    body: dict = request.get_json(silent=True) or {}
    player = _get_player(str(body.get("token", "")))
    if player is None:
        return _err("无效 token", 401)
    pid: int = int(player["id"])
    if not common.rate_check(f"p:{pid}"):
        return _err("请求太频繁", 429)
    room = db.get_room_by_code(str(body.get("code", "")).strip().upper())
    if room is None:
        return _err("房间不存在", 404)
    text: str = str(body.get("text", "")).strip()[:config.MAX_CHAT_LEN]
    if not text:
        return _err("消息不能为空")
    db.add_chat(int(room["id"]), pid, text)
    return jsonify({"ok": True})


@bp.route("/api/game/invite_bot", methods=["POST"])
@common.api_wrap
def api_invite_bot():
    """房主一键邀请机器人补位。"""
    body: dict = request.get_json(silent=True) or {}
    player = _get_player(str(body.get("token", "")))
    if player is None:
        return _err("无效 token", 401)
    pid: int = int(player["id"])
    if not common.rate_check(f"p:{pid}"):
        return _err("请求太频繁", 429)
    room = db.get_room_by_code(str(body.get("code", "")).strip().upper())
    if room is None:
        return _err("房间不存在", 404)
    if int(room["host_id"] or -1) != pid:
        return _err("只有房主可以邀请机器人", 403)
    ok, msg = match.invite_bot(int(room["id"]))
    return (jsonify({"ok": True}) if ok else _err(msg))


@bp.route("/api/game/leave", methods=["POST"])
@common.api_wrap
def api_leave():
    """离开房间 / 退出匹配队列；房主自动转移。"""
    body: dict = request.get_json(silent=True) or {}
    player = _get_player(str(body.get("token", "")))
    if player is None:
        return _err("无效 token", 401)
    pid: int = int(player["id"])
    if not common.rate_check(f"p:{pid}"):
        return _err("请求太频繁", 429)
    db.queue_leave(pid)
    code: str = str(body.get("code", "") or "").strip().upper()
    if code:
        room = db.get_room_by_code(code)
        if room is not None:
            match.leave_room(int(room["id"]), pid)
    return jsonify({"ok": True})
