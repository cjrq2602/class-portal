"""
game/config.py —— 全局数值配置与前 10 波平衡表
==============================================
职责：集中管理地图、炮塔、技能、波次、阶段时长、防滥用参数。
所有数值只在这里改，逻辑层/前端都以 /api/state 下发的数据为准渲染。
"""

from __future__ import annotations

from typing import Any

# ---------------- 阶段时长（秒） ----------------
PREP_TIME: int = 45          # 备战阶段时长
WAVE_BREAK: int = 8          # 波次结算/休整时长
MAX_CATCHUP_TICKS: int = 120 # 惰性结算单次补算帧数上限（防 CPU 爆炸）
TICK_SECONDS: float = 0.1    # 每逻辑帧 0.1 秒（战斗阶段 1 秒 = 10 帧）
FINAL_WAVE: int = 10         # 打完第 10 波即胜利

# ---------------- 地图 ----------------
# 网格 12x8；两条怪物行进路径（沿格子中心），单条路径、无分叉（CPU 友好）
GRID_W: int = 12
GRID_H: int = 8
CELL: int = 72               # 前端 Canvas 逻辑像素/格（前端自行缩放）
PATHS: list[list[tuple[int, int]]] = [
    # 上路：从左上进入，沿第 2 行到右上出口
    [(0, 2), (1, 2), (2, 2), (3, 2), (4, 2), (5, 2), (6, 2), (7, 2),
     (8, 2), (9, 2), (10, 2), (11, 2)],
    # 下路：从左下进入，沿第 6 行到右下出口
    [(0, 6), (1, 6), (2, 6), (3, 6), (4, 6), (5, 6), (6, 6), (7, 6),
     (8, 6), (9, 6), (10, 6), (11, 6)],
]
# 可建造格 = 全网格 - 两条路径
BUILDABLE: set[tuple[int, int]] = {
    (x, y) for x in range(GRID_W) for y in range(GRID_H)
    if (x, y) not in set(PATHS[0]) | set(PATHS[1])
}

# ---------------- 炮塔（4 种） ----------------
# 伤害/射程/射速全部服务端结算；cost 为普通造价，dual_cost 为"双人炮塔"单人出资额
TOWERS: dict[str, dict[str, Any]] = {
    "arrow": {   # 箭塔：单体速射，性价比之选
        "name": "箭塔", "emoji": "🏹", "cost": 50, "dual_cost": 45,
        "damage": 8, "range": 2.6, "cooldown": 0.5,
        "desc": "单体速射，适合补刀残血怪",
    },
    "cannon": {  # 炮塔：范围溅射，克群怪
        "name": "炮塔", "emoji": "💥", "cost": 90, "dual_cost": 70,
        "damage": 26, "range": 2.2, "cooldown": 1.4, "splash": 0.9,
        "desc": "范围溅射，怪物扎堆时收益最高",
    },
    "frost": {   # 冰塔：减速控制
        "name": "冰塔", "emoji": "❄️", "cost": 70, "dual_cost": 55,
        "damage": 4, "range": 2.4, "cooldown": 0.9, "slow": 0.5, "slow_dur": 1.5,
        "desc": "减速 50%，为输出塔争取时间",
    },
    "sniper": {  # 狙击塔：超远程高伤，打精英/BOSS
        "name": "狙击塔", "emoji": "🎯", "cost": 120, "dual_cost": 90,
        "damage": 60, "range": 4.5, "cooldown": 2.2,
        "desc": "超远程高伤，专打高血精英",
    },
}
TOWER_ORDER: list[str] = ["arrow", "cannon", "frost", "sniper"]

# 升级：每级 +35% 伤害 +8% 射程，最多 3 级（0..3）
MAX_LEVEL: int = 3
LEVEL_DMG_BONUS: float = 0.35
LEVEL_RANGE_BONUS: float = 0.08


def upgrade_cost(base_cost: int, level: int) -> int:
    """升级费用 = 基础造价 * 0.7 * (当前等级+1)。"""
    return int(base_cost * 0.7 * (level + 1))


# ---------------- 支援技能（两个必须配合的机制之一） ----------------
SKILLS: dict[str, dict[str, Any]] = {
    "heal": {    # 给队友防线回血（共享生命池）
        "name": "营地修复", "emoji": "❤️", "cost": 80,
        "amount": 2, "cooldown": 20.0,
        "desc": "为营地恢复 2 点生命（全队共享生命池）",
    },
    "haste": {   # 给某条路的炮塔加速冷却
        "name": "急速冷却", "emoji": "⚡", "cost": 60,
        "amount": 5.0, "cooldown": 15.0,
        "desc": "指定一条路，其上炮塔 5 秒内射速翻倍",
    },
}

# ---------------- 经济 ----------------
START_GOLD: int = 120        # 全队共享金币池初始值
WAVE_GOLD: int = 45          # 每波结算奖励
GOLD_PER_KILL: dict[str, int] = {"grunt": 8, "runner": 10, "brute": 18, "boss": 120}
REVIVE_COST: int = 150       # 共享金币复活（每局限一次）
START_LIVES: int = 10        # 共享生命值
LEAK_LIFE_COST: int = 1      # 每只怪漏掉扣 1（BOSS 扣 3）

# ---------------- 前 10 波数值平衡表 ----------------
# 每波：n 只基础怪 grunt，附加 runner(快) / brute(肉) / boss(BOSS)
# 生成间隔随波次收紧；怪物基础属性再乘 wave_scale
WAVES: list[dict[str, Any]] = [
    {"wave": 1,  "grunt": 6,  "runner": 0, "brute": 0, "boss": 0, "hp_scale": 1.0,  "spawn_gap": 1.2},
    {"wave": 2,  "grunt": 8,  "runner": 2, "brute": 0, "boss": 0, "hp_scale": 1.1,  "spawn_gap": 1.1},
    {"wave": 3,  "grunt": 10, "runner": 3, "brute": 1, "boss": 0, "hp_scale": 1.2,  "spawn_gap": 1.0},
    {"wave": 4,  "grunt": 12, "runner": 4, "brute": 2, "boss": 0, "hp_scale": 1.35, "spawn_gap": 0.95},
    {"wave": 5,  "grunt": 12, "runner": 5, "brute": 3, "boss": 1, "hp_scale": 1.5,  "spawn_gap": 0.9},
    {"wave": 6,  "grunt": 14, "runner": 6, "brute": 4, "boss": 0, "hp_scale": 1.7,  "spawn_gap": 0.85},
    {"wave": 7,  "grunt": 16, "runner": 8, "brute": 5, "boss": 0, "hp_scale": 1.9,  "spawn_gap": 0.8},
    {"wave": 8,  "grunt": 18, "runner": 9, "brute": 6, "boss": 1, "hp_scale": 2.15, "spawn_gap": 0.75},
    {"wave": 9,  "grunt": 20, "runner": 12, "brute": 8, "boss": 0, "hp_scale": 2.45, "spawn_gap": 0.7},
    {"wave": 10, "grunt": 22, "runner": 14, "brute": 10, "boss": 2, "hp_scale": 2.8, "spawn_gap": 0.65},
]

# 怪物基础属性（战斗时乘 wave 的 hp_scale）
MONSTERS: dict[str, dict[str, Any]] = {
    "grunt":  {"name": "喽啰", "hp": 40,  "speed": 1.1, "bounty": 8,   "leak": 1},
    "runner": {"name": "疾行者", "hp": 26, "speed": 2.0, "bounty": 10,  "leak": 1},
    "brute":  {"name": "壮汉", "hp": 130, "speed": 0.7, "bounty": 18,  "leak": 1},
    "boss":   {"name": "攻城兽", "hp": 700, "speed": 0.6, "bounty": 120, "leak": 3},
}

# ---------------- 反滥用 ----------------
RATE_LIMIT_PER_SEC: int = 5      # 每玩家每秒最多 5 次请求
ROOM_IDLE_SECONDS: int = 600     # 10 分钟无活动标记废弃
MAX_CHAT_LEN: int = 80           # 聊天长度上限
MAX_NICK_LEN: int = 12           # 昵称长度上限
