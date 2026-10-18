"""
blocks.py —— 板块注册表（导航 / 下拉分组 / 手机 Tab / 首页宫格 / 互动聚合页 的唯一数据源）
=========================================================================================
设计意图：站点地图里每个板块在此登记一次（online=已上线），
所有页面都从这里取数 —— 新增板块只需改本文件，模板无需改动。
四期改造已全部完成，本文件即最终形态（无遗留 TODO）。

字段：
  key      唯一键（同时作为模板 active_page 的值，用于高亮当前项）
  name     展示名
  desc     一句话说明（宫格/聚合页用）
  href     链接
  icon     icons.js 中的图标名
  color    宫格图标底色
  menu     所在菜单位置：top(顶栏一级) / study(学习▾) / life(生活▾) /
           interactive(互动▾) / me(右侧个人入口)
  tab      是否出现在手机底部 Tab 栏
  hub      是否被「互动聚合页」收录
  online   是否已上线（false 的条目只作记录，任何界面都不出现）
  todo     是否仍为未来期次的占位（当前全部已上线，无占位）
"""

from __future__ import annotations
from typing import Any

BLOCKS: list[dict[str, Any]] = [
    # ---------------- 顶栏一级 ----------------
    {"key": "home", "name": "首页", "desc": "总览 · 倒计时 · 值日提示", "href": "/",
     "icon": "home", "color": "#4F6EF7", "menu": "top", "tab": True,
     "hub": False, "online": True},
    {"key": "news", "name": "新闻", "desc": "多源热点聚合", "href": "/news",
     "icon": "file-text", "color": "#3B82F6", "menu": "top", "tab": False,
     "hub": False, "online": True},
    {"key": "digest", "name": "总结", "desc": "每日 0 点 / 9 点更新", "href": "/digest",
     "icon": "clock", "color": "#6366F1", "menu": "top", "tab": False,
     "hub": False, "online": True},
    {"key": "activities", "name": "活动", "desc": "活动与比赛 · 在线报名", "href": "/activities",
     "icon": "calendar", "color": "#F59E0B", "menu": "top", "tab": False,
     "hub": False, "online": True},
    {"key": "game", "name": "游戏", "desc": "守夜小队 · 合作塔防", "href": "/game",
     "icon": "gamepad", "color": "#8B5CF6", "menu": "top", "tab": True,
     "hub": False, "online": True},
    {"key": "links", "name": "资源导航", "desc": "常用网站 · 学习就业工具直达", "href": "/links",
     "icon": "link", "color": "#0EA5E9", "menu": "top", "tab": False,
     "hub": True, "online": True},

    # ---------------- 学习▾ ----------------
    {"key": "homework", "name": "作业追踪", "desc": "作业清单与个人完成勾选", "href": "/homework",
     "icon": "file-text", "color": "#0EA5E9", "menu": "study", "tab": False,
     "hub": False, "online": True},
    {"key": "resources", "name": "学习资料库", "desc": "共享资料 · 学科分类", "href": "/resources",
     "icon": "inbox", "color": "#14B8A6", "menu": "study", "tab": False,
     "hub": False, "online": True},

    # ---------------- 生活▾ ----------------
    {"key": "duty", "name": "值日表", "desc": "本周值日分工与轮换", "href": "/duty",
     "icon": "grid", "color": "#F97316", "menu": "life", "tab": False,
     "hub": False, "online": True},
    {"key": "honors", "name": "荣誉墙", "desc": "班级荣誉与获奖时刻", "href": "/honors",
     "icon": "trophy", "color": "#EAB308", "menu": "life", "tab": False,
     "hub": False, "online": True},

    # ---------------- 互动▾（同时被聚合页收录） ----------------
    {"key": "hub", "name": "互动总览", "desc": "所有互动板块入口", "href": "/hub",
     "icon": "target", "color": "#EC4899", "menu": "interactive", "tab": True,
     "hub": False, "online": True},
    {"key": "tucao", "name": "吐槽墙", "desc": "匿名说点想说的，点赞最热神评", "href": "/tucao",
     "icon": "message", "color": "#EC4899", "menu": "interactive", "tab": False,
     "hub": True, "online": True},
    {"key": "checkin", "name": "打卡挑战", "desc": "连续打卡 · 班级排行榜 · 月历热力图",
     "href": "/checkin", "icon": "target", "color": "#10B981", "menu": "interactive",
     "tab": False, "hub": True, "online": True},
    {"key": "picker", "name": "随机点名", "desc": "课堂抽人 · 翻滚动画 · 鼓点音效",
     "href": "/picker", "icon": "zap", "color": "#F59E0B", "menu": "interactive",
     "tab": False, "hub": True, "online": True},
    {"key": "quote", "name": "每日一句", "desc": "一天一句 · 全班共创", "href": "/quote",
     "icon": "heart", "color": "#8B5CF6", "menu": "interactive", "tab": False,
     "hub": True, "online": True},

    # ---------------- 右侧个人入口 ----------------
    {"key": "me", "name": "我的", "desc": "个人中心 · 战绩 · 打卡日历", "href": "/me",
     "icon": "user", "color": "#10B981", "menu": "me", "tab": True,
     "hub": False, "online": True},
]


def online_blocks(menu: str | None = None) -> list[dict[str, Any]]:
    """已上线板块（可按菜单位置过滤）。未上线条目永不返回。"""
    return [b for b in BLOCKS if b["online"] and (menu is None or b["menu"] == menu)]


def top_items() -> list[dict[str, Any]]:
    """顶栏一级入口（不含「我的」，它渲染在右侧操作区）。"""
    return online_blocks("top")


def dropdowns() -> list[dict[str, Any]]:
    """
    三个下拉分组（学习▾ / 生活▾ / 互动▾）。
    返回 [{key, name, icon, links:[...]}]，空组不返回。
    注意：键名用 links 而非 items —— Jinja 里 `g.items` 会命中 dict 的
    items() 方法（'builtin_function_or_method' object is not iterable）。
    """
    groups: list[tuple[str, str, str]] = [
        ("study", "学习", "file-text"),
        ("life", "生活", "star"),
        ("interactive", "互动", "message"),
    ]
    out: list[dict[str, Any]] = []
    for key, name, icon_name in groups:
        links: list[dict[str, Any]] = online_blocks(key)
        if links:
            out.append({"key": key, "name": name, "icon": icon_name, "links": links})
    return out


def tab_items() -> list[dict[str, Any]]:
    """手机底部 Tab 栏条目（首页/互动/游戏/我的）。"""
    return [b for b in online_blocks() if b["tab"]]


def hub_items() -> list[dict[str, Any]]:
    """「互动聚合页」收录的板块（吐槽墙 / 打卡挑战 / 随机点名 / 每日一句）。"""
    return [b for b in online_blocks() if b["hub"]]


def quick_items() -> list[dict[str, Any]]:
    """首页快捷宫格：全部已上线板块（首页自身除外）。"""
    return [b for b in online_blocks() if b["key"] != "home"]


# 首页「常用」入口：只放真正高频的 6 个，其余收进「全部功能」抽屉
HOME_PRIMARY_KEYS: tuple[str, ...] = (
    "homework", "checkin", "activities", "news", "game", "resources",
)


def home_primary() -> list[dict[str, Any]]:
    """首页常用入口（6 个，按固定顺序，缺失则跳过）。"""
    by_key: dict[str, dict[str, Any]] = {
        b["key"]: b for b in online_blocks() if b["key"] != "home"}
    return [by_key[k] for k in HOME_PRIMARY_KEYS if k in by_key]


def home_more() -> list[dict[str, Any]]:
    """首页「全部功能」抽屉：常用之外的其余板块。"""
    primary: set[str] = set(HOME_PRIMARY_KEYS)
    return [b for b in online_blocks()
            if b["key"] != "home" and b["key"] not in primary]
