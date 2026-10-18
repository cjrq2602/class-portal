"""
game/entities.py —— 对局状态（JSON）结构定义与初始化
===================================================
职责：
1. 定义 game_state.state_json 的完整结构（纯 dict，直接 JSON 序列化）；
2. 提供新局初始化、怪物/炮塔工厂函数；
3. 提供几何工具（路径坐标换算、距离计算）。
不做任何网络/数据库操作——纯函数，便于测试。
"""

from __future__ import annotations

import time
from typing import Any, Optional

from game import config


def new_lobby_state() -> dict[str, Any]:
    """房间等待期占位状态（结构与对局状态兼容，供 /api/state 统一读取）。"""
    return {
        "phase": "lobby",
        "wave": 0,
        "phase_end_at": 0.0,
        "battle_started_at": None,
        "last_tick_at": 0.0,
        "gold": 0,
        "lives": 0,
        "revive_used": False,
        "towers": [],
        "monsters": [],
        "spawn_queue": [],
        "next_monster_id": 1,
        "next_tower_id": 1,
        "haste": {"0": 0.0, "1": 0.0},
        "skill_cd": {"heal": 0.0, "haste": 0.0},
        "logs": ["🏠 等待玩家加入…"],
        "stats": {"kills": 0, "leaks": 0, "built": 0},
        "players": {},
    }


def new_game_state(room_id: int, players: list[dict[str, Any]], seed_wave: int = 1) -> dict[str, Any]:
    """
    创建一局初始状态（进入"备战阶段"）。
    players: [{"player_id": int, "nickname": str, "is_bot": bool}, ...]
    """
    from db import now  # 延迟导入避免循环
    ts: float = now()
    return {
        "phase": "prep",                # prep / battle / break / victory / defeat
        "wave": seed_wave,
        "phase_end_at": ts + config.PREP_TIME,  # 当前阶段截止时间戳
        "battle_started_at": None,      # 战斗阶段起点（服务端权威时间）
        "last_tick_at": ts,             # 惰性结算游标：此前的模拟已完成
        "gold": config.START_GOLD,
        "lives": config.START_LIVES,
        "revive_used": False,
        "towers": [],                   # 炮塔列表（见 make_tower）
        "monsters": [],                 # 存活怪物（见 make_monster）
        "spawn_queue": [],              # [{"type","path","at"}] at=相对战斗开始秒数
        "next_monster_id": 1,
        "next_tower_id": 1,
        "haste": {"0": 0.0, "1": 0.0},  # 各路径急速冷却到期时间戳
        "skill_cd": {"heal": 0.0, "haste": 0.0},  # 技能冷却到期时间戳（房间级）
        "logs": ["🛡️ 备战开始！建造炮塔准备迎接第 1 波"],
        "stats": {"kills": 0, "leaks": 0, "built": 0},
        "players": {
            str(p["player_id"]): {
                "nickname": p["nickname"],
                "is_bot": bool(p.get("is_bot", False)),
                "down": False,          # 失败保护演示：倒下标记（当前规则共享生命，倒下=生命归零前提示）
            }
            for p in players
        },
    }


def add_log(state: dict[str, Any], text: str, keep: int = 12) -> None:
    """追加一条事件日志（只保留最近 keep 条，控响应体）。"""
    state["logs"].append(text)
    if len(state["logs"]) > keep:
        state["logs"] = state["logs"][-keep:]


def make_monster(mtype: str, path: int, wave_hp_scale: float, monster_id: int,
                 spawn_at: float) -> dict[str, Any]:
    """构造一只怪物实体。spawn_at 为"相对战斗开始的秒数"。"""
    base: dict[str, Any] = config.MONSTERS[mtype]
    hp: int = int(base["hp"] * wave_hp_scale)
    return {
        "id": monster_id,
        "type": mtype,
        "path": path,                   # 0=上路 1=下路
        "pos": 0.0,                     # 沿路径行进距离（格），>= 路径末尾即漏怪
        "hp": hp,
        "max_hp": hp,
        "speed": base["speed"],         # 格/秒
        "slow_left": 0.0,               # 减速剩余秒数
        "slow_mult": 1.0,
        "spawn_at": spawn_at,
        "hasted_path_until": 0.0,
    }


def make_tower(ttype: str, x: int, y: int, tower_id: int, dual: bool,
               owner_ids: list[int]) -> dict[str, Any]:
    """构造一座炮塔实体。dual=True 时威力翻倍（双人炮塔机制）。"""
    return {
        "id": tower_id,
        "type": ttype,
        "x": x, "y": y,
        "level": 0,
        "dual": dual,
        "owners": owner_ids,            # 普通塔 1 人，双人塔 2 人
        "cd_left": 0.0,                 # 距下次开火的冷却秒数（惰性推进）
    }


def path_pos_to_xy(path: list[tuple[int, int]], pos: float) -> tuple[float, float]:
    """
    将路径行进距离（格单位）换算为网格浮点坐标（格中心）。
    pos ∈ [0, len(path)-1]；越界时截到两端（渲染安全）。
    """
    if pos <= 0:
        x, y = path[0]
        return x + 0.5, y + 0.5
    max_pos: float = float(len(path) - 1)
    if pos >= max_pos:
        x, y = path[-1]
        return x + 0.5, y + 0.5
    idx: int = int(pos)
    frac: float = pos - idx
    x0, y0 = path[idx]
    x1, y1 = path[min(idx + 1, len(path) - 1)]
    return x0 + 0.5 + (x1 - x0) * frac, y0 + 0.5 + (y1 - y0) * frac


def dist(ax: float, ay: float, bx: float, by: float) -> float:
    """欧氏距离（格单位）。"""
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def tower_damage(tower: dict[str, Any]) -> float:
    """炮塔单发伤害 = 基础 * (1 + 等级加成) * (双人翻倍)。"""
    base: float = float(config.TOWERS[tower["type"]]["damage"])
    dmg: float = base * (1.0 + config.LEVEL_DMG_BONUS * tower["level"])
    return dmg * 2.0 if tower["dual"] else dmg


def tower_range(tower: dict[str, Any]) -> float:
    """炮塔射程 = 基础 * (1 + 等级射程加成)。"""
    base: float = float(config.TOWERS[tower["type"]]["range"])
    return base * (1.0 + config.LEVEL_RANGE_BONUS * tower["level"])


def path_of_cell(x: int, y: int) -> Optional[int]:
    """给定格子，返回它属于哪条路径（0/1），不在路径上返回 None。"""
    for i, p in enumerate(config.PATHS):
        if (x, y) in p:
            return i
    return None


def state_public_view(state: dict[str, Any], room_id: int, version: int) -> dict[str, Any]:
    """
    生成发给客户端的公开视图（含客户端渲染需要的一切）。
    额外附带路径/建造格配置，客户端零硬编码、以服务端为准。
    """
    return {
        "room_id": room_id,
        "version": version,
        "phase": state["phase"],
        "wave": state["wave"],
        "phase_end_at": state["phase_end_at"],
        "now_ts": __import__("time").time(),
        "gold": state["gold"],
        "lives": state["lives"],
        "revive_used": state["revive_used"],
        "revive_window_until": state.get("revive_window_until", 0.0),
        "towers": state["towers"],
        "monsters": [
            {
                "id": m["id"], "type": m["type"], "path": m["path"],
                "hp": m["hp"], "max_hp": m["max_hp"],
                "x": path_pos_to_xy(config.PATHS[m["path"]], m["pos"])[0],
                "y": path_pos_to_xy(config.PATHS[m["path"]], m["pos"])[1],
                "slowed": m["slow_left"] > 0,
            }
            for m in state["monsters"]
        ],
        "spawn_pending": len(state["spawn_queue"]),
        "haste": state["haste"],
        "skill_cd": state["skill_cd"],
        "logs": state["logs"],
        "stats": state["stats"],
        "players": state["players"],
        "paths": config.PATHS,
        "grid": [config.GRID_W, config.GRID_H],
        "towers_config": {
            k: {"name": v["name"], "emoji": v["emoji"], "cost": v["cost"],
                "dual_cost": v["dual_cost"], "desc": v["desc"]}
            for k, v in config.TOWERS.items()
        },
        "tower_order": config.TOWER_ORDER,
        "skills_config": {
            k: {"name": v["name"], "emoji": v["emoji"], "cost": v["cost"], "desc": v["desc"]}
            for k, v in config.SKILLS.items()
        },
        "upgrade_max": config.MAX_LEVEL,
        "revive_cost": config.REVIVE_COST,
    }
