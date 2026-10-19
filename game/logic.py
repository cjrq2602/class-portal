"""
game/logic.py —— 服务端权威游戏逻辑 + 惰性结算（核心）
====================================================
职责：
1. 【关键】惰性结算 advance_state()：不跑后台线程/定时器，任何请求进来时
   根据 now - last_tick_at 补算遗漏的逻辑帧（0.1s/帧），单次最多补
   MAX_CATCHUP_TICKS 帧。游戏公平性由服务端时间戳保证——没人请求也会"补课"。
2. 战斗阶段：怪物沿路径行进、炮塔自动开火、减速/溅射/急速、漏怪扣血、
   波次结算转备战，全部服务端结算，客户端只渲染。
3. 与 db 层配合：advance_and_save() 用乐观锁写回 game_state 单行。
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Optional

from db import (get_game_state, get_room_players, insert_game_state,
                put_game_state, update_room_status, add_game_result, now)
from game import config, entities


# ============================================================
# 进程内状态缓存 + 落库节流（热路径优化）
# ============================================================
# 背景：原版每次轮询都 json.loads + 模拟 + json.dumps + 写库，
# 战斗期 changed 恒为 True → 4 人房 8 次/秒全量写 SQLite。
# 在慢盘/限 CPU 的 PythonAnywhere 上写事务互相排队 = 全员卡顿。
# 现在：对局状态驻留进程内存，同一房间最多每 _SAVE_MIN_INTERVAL
# 秒落库一次；响应版本 = 已落库版本，未落库的推进只体现在下一次
# 全量响应内容里（前端插值不受影响）。多 worker 安全：落库仍走
# 乐观锁，冲突时重读合并，时间轴模拟按时间戳重算可自愈。
_SAVE_MIN_INTERVAL: float = 0.9   # 同一房间两次落库最小间隔（秒）
_REVALIDATE_SECONDS: float = 2.0  # 干净缓存超过该秒数回库校验（多 worker 安全网）
_CACHE_MAX: int = 128             # 缓存房间数上限，超出逐出最久未读

_CACHE: dict[int, dict[str, Any]] = {}
_CACHE_LOCK: threading.Lock = threading.Lock()


def _evict_cache() -> None:
    """缓存超限时逐出"干净且最久未读"的条目（脏条目保留等落库）。"""
    if len(_CACHE) <= _CACHE_MAX:
        return
    for rid, ent in sorted(_CACHE.items(), key=lambda kv: kv[1]["db_read_at"]):
        if len(_CACHE) <= _CACHE_MAX:
            break
        if not ent["dirty"]:
            _CACHE.pop(rid, None)


def _persist_locked(room_id: int, entry: dict[str, Any], ts: float) -> None:
    """乐观锁落库；被其他 worker 抢先则以库为准重载（本地推进丢弃）。"""
    if put_game_state(room_id, entry["db_version"] + 1,
                      json.dumps(entry["state"], ensure_ascii=False)):
        entry["db_version"] += 1
        entry["dirty"] = False
        entry["saved_at"] = ts
        entry["db_read_at"] = ts
        return
    row = get_game_state(room_id)
    if row is not None:
        entry["state"] = json.loads(row["state_json"])
        entry["db_version"] = int(row["version"])
    entry["dirty"] = False
    entry["saved_at"] = ts
    entry["db_read_at"] = ts


# ============================================================
# 惰性结算主入口
# ============================================================

def advance_and_save(room_id: int) -> tuple[dict[str, Any], int]:
    """
    把房间对局状态推进到当前时刻。
    返回 (state, version)：state 为进程内权威对象（动作层可直接改后
    调 force_save），version 为已落库版本（前端据此判断 changed）。
    """
    with _CACHE_LOCK:
        ts: float = time.time()
        entry: Optional[dict[str, Any]] = _CACHE.get(room_id)
        # 无缓存，或干净缓存到期（多 worker 安全网）→ 回库重读
        if entry is None or (
                not entry["dirty"] and ts - float(entry["db_read_at"]) > _REVALIDATE_SECONDS):
            row = get_game_state(room_id)
            if row is None:
                _CACHE.pop(room_id, None)
                raise ValueError("对局状态不存在")
            entry = {"state": json.loads(row["state_json"]),
                     "db_version": int(row["version"]), "dirty": False,
                     "saved_at": 0.0, "db_read_at": ts}
            _CACHE[room_id] = entry
            _evict_cache()

        state: dict[str, Any] = entry["state"]
        if advance_state(state, ts):
            entry["dirty"] = True

        # 节流落库：脏且距上次落库够久才写
        if entry["dirty"] and ts - float(entry["saved_at"]) >= _SAVE_MIN_INTERVAL:
            _persist_locked(room_id, entry, ts)
            state = entry["state"]  # 冲突重载后对象可能已换

        # 终局收尾（战绩统计 / 房间状态），finalized 标志保证只做一次，立即落库
        if state.get("phase") in ("victory", "defeat") and not state.get("finalized"):
            try:
                finish_game(room_id, state)
                state["finalized"] = True
                entry["dirty"] = True
                _persist_locked(room_id, entry, time.time())
                state = entry["state"]
            except Exception:
                pass
        return state, int(entry["db_version"])


def force_save(room_id: int, state: dict[str, Any]) -> None:
    """
    玩家动作后的立即落库：动作无法由时间轴重算（不同于模拟推进），
    必须马上持久化。读最新库版本 CAS 写入，微冲突重试一次；
    成功后同步进程缓存，避免缓存里的旧版本回写覆盖动作结果。
    """
    with _CACHE_LOCK:
        ts: float = time.time()
        payload: str = json.dumps(state, ensure_ascii=False)
        for _ in range(2):
            row = get_game_state(room_id)
            if row is None:
                return
            ver: int = int(row["version"])
            if put_game_state(room_id, ver + 1, payload):
                _CACHE[room_id] = {"state": state, "db_version": ver + 1,
                                   "dirty": False, "saved_at": ts, "db_read_at": ts}
                return
        # 极端并发下放弃本次写入，下次轮询以库为准（前端会再拉全量）


def advance_state(state: dict[str, Any], ts: float) -> bool:
    """
    将状态从 last_tick_at 推进到 ts（含阶段切换 + 战斗帧模拟）。
    返回状态是否发生变化（无变化不写库、version 不涨，前端收到 changed:false）。

    【关键时序】先补算战斗帧，再切阶段：
    若先切阶段（旧实现），长时间无人轮询后一个请求会把整波怪按"强制漏怪"
    结算掉，导致玩家回来时直接跳到下一波。现在必须先模拟到阶段结束才允许切。
    """
    if state["phase"] in ("victory", "defeat"):
        return False  # 终局不再模拟

    changed: bool = False
    last: float = float(state["last_tick_at"])

    # ---- 闲置暂停：超过阈值无任何请求 → 视为全员离开，整体顺延时间轴 ----
    # 这样"关掉页面再回来"不会发现游戏已经跑完好几波。
    idle_gap: float = ts - last
    if idle_gap > config.IDLE_PAUSE_SECONDS:
        state["phase_end_at"] = float(state["phase_end_at"]) + idle_gap
        if state.get("battle_started_at"):
            state["battle_started_at"] = float(state["battle_started_at"]) + idle_gap
        if state.get("revive_window_until"):
            state["revive_window_until"] = float(state["revive_window_until"]) + idle_gap
        if state.get("next_bot_act_at"):
            state["next_bot_act_at"] = float(state["next_bot_act_at"]) + idle_gap
        state["last_tick_at"] = ts
        return True

    # ---- 战斗帧模拟（必须先补算，再切阶段）----
    if state["phase"] == "battle":
        target: float = min(ts, float(state["phase_end_at"]))
        elapsed: float = max(0.0, target - last)
        ticks: int = min(int(elapsed / config.TICK_SECONDS), config.MAX_CATCHUP_TICKS)
        for _ in range(ticks):
            _simulate_tick(state, config.TICK_SECONDS)
            changed = True
        state["last_tick_at"] = max(last, last + ticks * config.TICK_SECONDS)
        # 还没模拟到阶段结束（补算被上限截断）→ 本次不切阶段，留给下次请求
        if state["last_tick_at"] < float(state["phase_end_at"]) - config.TICK_SECONDS:
            return changed

    # ---- 阶段推进（此时战斗已模拟到边界，可安全结算）----
    changed = _advance_phase(state, ts) or changed

    # ---- 机器人自主建塔：备战期每 5 秒最多建 1 座（限频防金币秒空）----
    if state["phase"] in ("prep", "break") and ts >= float(state.get("next_bot_act_at", 0.0)):
        state["next_bot_act_at"] = ts + 5.0
        changed = bot_act(state) or changed

    # 游标推进到"已模拟到的时刻"，防止重复补算
    if state["phase"] == "battle":
        state["last_tick_at"] = max(
            float(state["last_tick_at"]),
            min(ts, float(state["phase_end_at"])))
    else:
        state["last_tick_at"] = ts
    return changed


def _advance_phase(state: dict[str, Any], ts: float) -> bool:
    """阶段状态机：prep → battle → break → battle … → victory/defeat。"""
    changed: bool = False
    # 单次请求最多推进一个阶段转换，防止长时间离开后一次跳多波
    max_transitions = 1
    transitions = 0
    while ts >= float(state["phase_end_at"]) and transitions < max_transitions:
        phase: str = state["phase"]
        if phase == "prep":
            _start_battle(state, ts)
            changed = True
            transitions += 1
        elif phase == "battle":
            # 战斗时间到但怪没清完：剩余怪直接算漏掉（波次强制收束，防拖死 CPU）
            _resolve_battle_end(state, forced=True)
            changed = True
            transitions += 1
        elif phase == "break":
            _start_prep(state, ts)
            changed = True
            transitions += 1
        else:
            break
        if state["phase"] in ("victory", "defeat"):
            break
        # 复活窗口内 _resolve_battle_end 不切阶段：必须跳出防止死循环
        if state["phase"] == phase and phase == "battle":
            break
    return changed


# ============================================================
# 阶段转换
# ============================================================

def _start_prep(state: dict[str, Any], ts: float) -> None:
    """进入下一波备战。"""
    state["phase"] = "prep"
    state["phase_end_at"] = ts + config.PREP_TIME
    entities.add_log(state, f"🛠️ 第 {state['wave']} 波备战开始（{config.PREP_TIME} 秒）")


def _start_battle(state: dict[str, Any], ts: float) -> None:
    """备战结束 → 生成刷怪计划，进入战斗。"""
    wave: int = int(state["wave"])
    cfg: dict[str, Any] = config.WAVES[min(wave, len(config.WAVES)) - 1]
    gap: float = float(cfg["spawn_gap"])
    queue: list[dict[str, Any]] = []
    t: float = 0.0
    nid: int = int(state["next_monster_id"])

    def push(mtype: str, path: int) -> None:
        nonlocal t, nid
        queue.append({"type": mtype, "path": path, "at": t, "id": nid})
        nid += 1
        t += gap

    # 交替两条路径投放：grunt 循环铺底，runner 快速穿插，brute/boss 压轴
    for i in range(int(cfg["grunt"])):
        push("grunt", i % 2)
    for i in range(int(cfg["runner"])):
        push("runner", (i + 1) % 2)
    for i in range(int(cfg["brute"])):
        push("brute", i % 2)
    for i in range(int(cfg["boss"])):
        push("boss", i % 2)

    state["spawn_queue"] = queue
    state["next_monster_id"] = nid
    state["phase"] = "battle"
    state["battle_started_at"] = ts
    # 战斗时长 = 刷怪总时长 + 30 秒清场缓冲
    state["phase_end_at"] = ts + t + 30.0
    state["last_tick_at"] = ts
    entities.add_log(state, f"⚔️ 第 {wave} 波来袭！共 {len(queue)} 只怪物")


def _resolve_battle_end(state: dict[str, Any], forced: bool = False) -> None:
    """战斗结束：清场结算 → 发放奖励 → 胜利/下一波。"""
    if forced and state["monsters"]:
        # 未处理的怪按漏怪处理（保底扣血，防止拖时间白嫖）
        for m in state["monsters"]:
            state["lives"] -= config.MONSTERS[m["type"]]["leak"]
            state["stats"]["leaks"] += 1
        state["monsters"] = []
    state["spawn_queue"] = []

    if state["lives"] <= 0:
        state["lives"] = 0
        if not state.get("revive_used") and not state.get("revive_window_until"):
            # 失败保护：给 15 秒共享金币复活窗口，倒计时结束才真正判负
            state["revive_window_until"] = time.time() + 15.0
            entities.add_log(state, "⚠️ 营地生命归零！15 秒内花 150💰复活可挽救（每局限一次）")
            return
        if state.get("revive_window_until") and time.time() < float(state["revive_window_until"]):
            return  # 复活窗口内冻结，等玩家操作
        state["phase"] = "defeat"
        entities.add_log(state, "💀 营地陷落……挑战失败")
        return

    wave: int = int(state["wave"])
    if wave >= config.FINAL_WAVE:
        state["phase"] = "victory"
        entities.add_log(state, "🎉 守住了！全队胜利！")
        return

    # 波次结算：发金币、进休整；wave+1（备战日志显示下一波编号）
    state["gold"] += config.WAVE_GOLD
    state["wave"] = wave + 1
    state["phase"] = "break"
    state["phase_end_at"] = time.time() + config.WAVE_BREAK
    entities.add_log(state, f"✅ 第 {wave} 波守住了！奖励 +{config.WAVE_GOLD} 金币")


# ============================================================
# 战斗帧模拟（每帧 0.1 秒，纯 CPU 小运算）
# ============================================================

def _simulate_tick(state: dict[str, Any], dt: float) -> None:
    """单帧：刷怪 → 移动 → 炮塔开火 → 漏怪/清场判定 → 波次切换。"""
    ts_offset_base: float = float(state["battle_started_at"] or 0.0)
    battle_elapsed: float = (float(state["last_tick_at"]) + dt) - ts_offset_base

    # 1) 按计划刷怪（spawn_queue 按 at 升序）
    q: list[dict[str, Any]] = state["spawn_queue"]
    while q and q[0]["at"] <= battle_elapsed:
        item: dict[str, Any] = q.pop(0)
        cfg_wave: dict[str, Any] = config.WAVES[min(int(state["wave"]), len(config.WAVES)) - 1]
        state["monsters"].append(
            entities.make_monster(item["type"], item["path"],
                                  float(cfg_wave["hp_scale"]), item["id"], item["at"])
        )

    monsters: list[dict[str, Any]] = state["monsters"]
    path_len: float = float(len(config.PATHS[0]) - 1)

    # 2) 怪物移动（含减速）
    for m in monsters:
        if m["slow_left"] > 0:
            m["slow_left"] = max(0.0, m["slow_left"] - dt)
            if m["slow_left"] <= 0:
                m["slow_mult"] = 1.0
        m["pos"] += m["speed"] * m["slow_mult"] * dt

    # 3) 漏怪判定
    leaked: list[dict[str, Any]] = [m for m in monsters if m["pos"] >= path_len]
    for m in leaked:
        cost: int = int(config.MONSTERS[m["type"]]["leak"])
        state["lives"] -= cost
        state["stats"]["leaks"] += 1
    if leaked:
        state["monsters"] = [m for m in monsters if m["pos"] < path_len]

    # 4) 炮塔开火：冷却归零 → 找射程内最靠前（pos 最大）的怪
    for tw in state["towers"]:
        tw["cd_left"] = max(0.0, float(tw["cd_left"]) - dt)
        if tw["cd_left"] > 0:
            continue
        spec: dict[str, Any] = config.TOWERS[tw["type"]]
        rng: float = entities.tower_range(tw)
        tx, ty = float(tw["x"]) + 0.5, float(tw["y"]) + 0.5

        # 急速冷却：该塔纵坐标邻近的路径被加速时射速减半（与 actions.haste 语义一致）
        hasted: bool = False
        for pidx in (0, 1):
            path_y: int = config.PATHS[pidx][0][1]
            if abs(int(tw["y"]) - path_y) <= 1 and \
                    float(state["haste"].get(str(pidx), 0.0)) > time.time():
                hasted = True
        cooldown: float = float(spec["cooldown"]) * (0.5 if hasted else 1.0)

        best: Optional[dict[str, Any]] = None
        best_pos: float = -1.0
        for m in state["monsters"]:
            mx, my = entities.path_pos_to_xy(config.PATHS[m["path"]], m["pos"])
            if entities.dist(tx, ty, mx, my) <= rng and m["pos"] > best_pos:
                best, best_pos = m, m["pos"]
        if best is None:
            tw["cd_left"] = 0.05  # 没目标短冷却再找，避免每帧全量扫描
            continue

        # 命中：单体 / 溅射 / 减速
        dmg: float = entities.tower_damage(tw)
        _apply_damage(state, best, dmg)
        if "slow" in spec:
            best["slow_mult"] = float(spec["slow"])
            best["slow_left"] = float(spec["slow_dur"])
        if "splash" in spec:
            bx, by = entities.path_pos_to_xy(config.PATHS[best["path"]], best["pos"])
            for m in list(state["monsters"]):
                if m is best:
                    continue
                mx, my = entities.path_pos_to_xy(config.PATHS[m["path"]], m["pos"])
                if entities.dist(bx, by, mx, my) <= float(spec["splash"]):
                    _apply_damage(state, m, dmg * 0.6)
        tw["cd_left"] = cooldown

    # 5) 死亡清理 + 金币/击杀统计（_apply_damage 内已发赏金）
    state["monsters"] = [m for m in state["monsters"] if m["hp"] > 0]

    # 6) 生命归零 → 失败（或触发复活保护前的判定由 actions 处理）
    if state["lives"] <= 0:
        state["lives"] = 0
        if not state.get("revive_used") and not state.get("revive_window_until"):
            # 失败保护：给 15 秒共享金币复活窗口，倒计时结束才真正判负
            state["revive_window_until"] = time.time() + 15.0
            entities.add_log(state, "⚠️ 营地生命归零！15 秒内花 150💰复活可挽救（每局限一次）")
            return
        if state.get("revive_window_until") and time.time() < float(state["revive_window_until"]):
            return  # 复活窗口内冻结，等玩家操作
        state["phase"] = "defeat"
        entities.add_log(state, "💀 营地陷落……挑战失败")
        return

    # 7) 战斗收束：场上无怪且刷怪计划耗尽 → 提前进入波次结算
    if not state["monsters"] and not state["spawn_queue"]:
        _resolve_battle_end(state, forced=False)
        return


def _apply_damage(state: dict[str, Any], monster: dict[str, Any], dmg: float) -> None:
    """扣血并结算击杀赏金。"""
    monster["hp"] -= dmg
    if monster["hp"] <= 0:
        bounty: int = int(config.GOLD_PER_KILL.get(monster["type"], 5))
        state["gold"] += bounty
        state["stats"]["kills"] += 1


# ============================================================
# 机器人（简单策略 AI）——在备战阶段被调用
# ============================================================

def bot_act(state: dict[str, Any]) -> bool:
    """
    机器人自动建造：优先造最便宜的箭塔，金币富余造炮塔。
    只在备战阶段执行；每次动作只造 1 座，防止 CPU 尖峰。
    返回是否有动作发生（发生则需要写库）。
    """
    if state["phase"] != "prep":
        return False
    # 共享金币池保护：金币充足（≥300）时才替队伍补塔，避免花光玩家的建造预算
    if state["gold"] < 300:
        return False

    # 找一个可建造空位（按固定顺序扫一遍，无随机 → 可预测省 CPU）
    occupied: set[tuple[int, int]] = {(int(t["x"]), int(t["y"])) for t in state["towers"]}
    target_type: str = "arrow"
    if state["gold"] >= 600:
        target_type = "cannon"
    cost: int = int(config.TOWERS[target_type]["cost"])
    if state["gold"] < cost:
        return False

    for cell in sorted(config.BUILDABLE):
        if cell in occupied:
            continue
        # 机器人把塔建在路径旁（离路径最近的建造格优先）
        near_path: bool = any(
            abs(cell[0] - px) + abs(cell[1] - py) <= 1
            for p in config.PATHS for (px, py) in p
        )
        if not near_path:
            continue
        tid: int = int(state["next_tower_id"])
        state["next_tower_id"] = tid + 1
        state["towers"].append(entities.make_tower(target_type, cell[0], cell[1], tid, False, [-1]))
        state["gold"] -= cost
        state["stats"]["built"] += 1
        entities.add_log(state, f"🤖 机器人建造了{config.TOWERS[target_type]['name']}")
        return True
    return False


# ============================================================
# 对外辅助
# ============================================================

def finish_game(room_id: int, state: dict[str, Any]) -> None:
    """终局后收尾：房间状态更新 + 人类玩家战绩统计。"""
    won: bool = state["phase"] == "victory"
    update_room_status(room_id, "finished")
    for pid_str, info in state["players"].items():
        if not info.get("is_bot"):
            try:
                add_game_result(int(pid_str), won)
            except Exception:
                pass  # 战绩失败不影响主流程
