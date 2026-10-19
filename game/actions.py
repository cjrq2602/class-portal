"""
game/actions.py —— 玩家动作校验与执行（服务端权威）
=================================================
职责：处理 POST /api/action 的全部动作类型，逐项校验：
  1. 身份：token 对应玩家必须在房间内（且不是机器人）；
  2. 状态：动作只允许在对应阶段执行（建造只在备战/休整、技能战斗中也有用等）；
  3. 经济：金币足够才扣（先校验后扣，杜绝负金币）；
  4. 合法性：坐标可建造、炮塔归属、升级等级上限等。
任何校验失败返回 (False, 中文错误信息)，绝不抛裸异常。
"""

from __future__ import annotations

from typing import Any

import db
from game import config, entities, logic


def handle_action(room_id: int, player_id: int, action: dict[str, Any]) -> tuple[bool, str]:
    """动作分发入口。调用前调用方已完成 token→player 解析与速率限制。"""
    atype: str = str(action.get("type", ""))
    # 先补算时间（保证校验基于最新状态）
    state, version = logic.advance_and_save(room_id)

    seat = db.find_player_seat(room_id, player_id)
    if seat is None:
        return False, "你不在该房间中"
    if bool(seat["is_bot"]):
        return False, "机器人座位不可操作"

    handlers = {
        "build": _act_build,
        "upgrade": _act_upgrade,
        "sell": _act_sell,
        "skill": _act_skill,
        "revive": _act_revive,
        "start": _act_start,
    }
    fn = handlers.get(atype)
    if fn is None:
        return False, f"未知动作: {atype}"

    ok, msg = fn(state, room_id, player_id, action)
    if ok:
        # 备战期顺手让机器人建一座塔（合作感），并把状态写回
        logic.bot_act(state)
        # 动作结果立即落库（force_save 走缓存锁 + 最新版本 CAS，
        # 避免覆盖/被覆盖并发轮询的推进；原版这里无条件直写旧版本）
        logic.force_save(room_id, state)
    return ok, msg


# ============================================================
# 各动作实现
# ============================================================

def _act_build(state: dict[str, Any], room_id: int, player_id: int,
               action: dict[str, Any]) -> tuple[bool, str]:
    """建造炮塔：mode=normal / dual（双人炮塔需两人各出资一半）。"""
    if state["phase"] not in ("prep", "break"):
        return False, "只能在备战阶段建造"
    ttype: str = str(action.get("tower", ""))
    if ttype not in config.TOWERS:
        return False, "炮塔类型不存在"
    try:
        x, y = int(action.get("x")), int(action.get("y"))
    except (TypeError, ValueError):
        return False, "坐标非法"
    if (x, y) not in config.BUILDABLE:
        return False, "该位置不能建造"
    if any(int(t["x"]) == x and int(t["y"]) == y for t in state["towers"]):
        return False, "该位置已有炮塔"

    spec = config.TOWERS[ttype]
    mode: str = str(action.get("mode", "normal"))

    if mode == "dual":
        # 双人炮塔：需要另一名玩家确认各出资一半；威力翻倍（见 entities.tower_damage）
        partner_id = action.get("partner_id")
        if partner_id is None or int(partner_id) == player_id:
            return False, "双人炮塔需要选择另一名队友"
        partner_seat = db.find_player_seat(room_id, int(partner_id))
        if partner_seat is None or bool(partner_seat["is_bot"]):
            return False, "队友不在房间中"
        half: int = int(spec["dual_cost"])
        # 共享金币池：只需总额足够即视为双方各出资一半（合作主题共享经济）
        if state["gold"] < half * 2:
            return False, f"双人炮塔需要 {half * 2} 金币（双方各半）"
        state["gold"] -= half * 2
        tid: int = int(state["next_tower_id"])
        state["next_tower_id"] = tid + 1
        state["towers"].append(
            entities.make_tower(ttype, x, y, tid, True, [player_id, int(partner_id)]))
        state["stats"]["built"] += 1
        entities.add_log(state, f"🤝 双人{spec['name']}落成（威力翻倍）！")
        return True, ""

    cost: int = int(spec["cost"])
    if state["gold"] < cost:
        return False, f"金币不足（需要 {cost}）"
    state["gold"] -= cost
    tid = int(state["next_tower_id"])
    state["next_tower_id"] = tid + 1
    state["towers"].append(entities.make_tower(ttype, x, y, tid, False, [player_id]))
    state["stats"]["built"] += 1
    return True, ""


def _act_upgrade(state: dict[str, Any], room_id: int, player_id: int,
                 action: dict[str, Any]) -> tuple[bool, str]:
    """升级炮塔（备战/休整期）。"""
    if state["phase"] not in ("prep", "break"):
        return False, "只能在备战阶段升级"
    tid = action.get("tower_id")
    tower = next((t for t in state["towers"] if int(t["id"]) == int(tid or -1)), None)
    if tower is None:
        return False, "炮塔不存在"
    if int(tower["level"]) >= config.MAX_LEVEL:
        return False, "已满级"
    cost: int = config.upgrade_cost(int(config.TOWERS[tower["type"]]["cost"]),
                                    int(tower["level"]))
    if state["gold"] < cost:
        return False, f"金币不足（升级需要 {cost}）"
    state["gold"] -= cost
    tower["level"] = int(tower["level"]) + 1
    return True, ""


def _act_sell(state: dict[str, Any], room_id: int, player_id: int,
              action: dict[str, Any]) -> tuple[bool, str]:
    """出售炮塔返还 50% 总投入（备战/休整期）。"""
    if state["phase"] not in ("prep", "break"):
        return False, "只能在备战阶段出售"
    tid = action.get("tower_id")
    tower = next((t for t in state["towers"] if int(t["id"]) == int(tid or -1)), None)
    if tower is None:
        return False, "炮塔不存在"
    base: int = int(config.TOWERS[tower["type"]]["cost"])
    invested: int = base + sum(config.upgrade_cost(base, lv)
                              for lv in range(int(tower["level"])))
    refund: int = invested // 2
    state["gold"] += refund
    state["towers"] = [t for t in state["towers"] if int(t["id"]) != int(tower["id"])]
    return True, ""


def _act_skill(state: dict[str, Any], room_id: int, player_id: int,
               action: dict[str, Any]) -> tuple[bool, str]:
    """支援技能：heal=营地回血 / haste=指定一条路炮塔加速（配合机制之二）。"""
    sk: str = str(action.get("skill", ""))
    if sk not in config.SKILLS:
        return False, "技能不存在"
    spec = config.SKILLS[sk]
    import time as _time
    now_ts: float = _time.time()
    if float(state["skill_cd"].get(sk, 0.0)) > now_ts:
        return False, "技能冷却中"
    cost: int = int(spec["cost"])
    if state["gold"] < cost:
        return False, f"金币不足（需要 {cost}）"

    if sk == "heal":
        state["lives"] = min(config.START_LIVES, state["lives"] + int(spec["amount"]))
        state["skill_cd"]["heal"] = now_ts + float(spec["cooldown"])
        entities.add_log(state, f"❤️ 队友使用了营地修复（生命 {state['lives']}）")
        return True, ""

    # haste：需要指定路径 0/1
    try:
        pidx: int = int(action.get("path", -1))
    except (TypeError, ValueError):
        return False, "路径参数非法"
    if pidx not in (0, 1):
        return False, "需要指定加速哪条路"
    state["gold"] -= cost
    state["haste"][str(pidx)] = now_ts + float(spec["amount"])
    state["skill_cd"]["haste"] = now_ts + float(spec["cooldown"])
    road: str = "上路" if pidx == 0 else "下路"
    entities.add_log(state, f"⚡ 队友为{road}开启急速冷却（5 秒）")
    return True, ""


def _act_revive(state: dict[str, Any], room_id: int, player_id: int,
                action: dict[str, Any]) -> tuple[bool, str]:
    """失败保护：全员倒下前可用共享金币复活一次（补 5 点生命）。"""
    if state["phase"] not in ("battle", "break"):
        return False, "当前阶段无法复活"
    if state["revive_used"]:
        return False, "本局已使用过复活"
    if state["gold"] < config.REVIVE_COST:
        return False, f"金币不足（复活需要 {config.REVIVE_COST}）"
    state["gold"] -= config.REVIVE_COST
    state["revive_used"] = True
    state["revive_window_until"] = 0.0  # 关闭复活窗口
    state["lives"] = max(state["lives"], 5)
    entities.add_log(state, f"💖 全队使用复活币！营地生命恢复至 {state['lives']}")
    return True, ""


def _act_start(state: dict[str, Any], room_id: int, player_id: int,
               action: dict[str, Any]) -> tuple[bool, str]:
    """提前开始战斗（备战阶段房主可跳过倒计时）。"""
    if state["phase"] != "prep":
        return False, "当前不在备战阶段"
    state["phase_end_at"] = 0.0  # 下一次 advance 即触发开战
    entities.add_log(state, "🚀 房主提前开启了战斗！")
    return True, ""


def _time_now() -> float:
    import time
    return time.time()
