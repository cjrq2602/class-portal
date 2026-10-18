"""
sensitive.py —— 吐槽内容敏感词过滤（内置词库 + 管理员词库）
=========================================================
职责：
1. 内置词库 BUILTIN_WORDS：开箱即用的基础拦截集（违法/色情/赌博/诈骗/广告/辱骂）；
2. 与 sensitive_words 表（管理员增删）合并后统一过滤；
3. 归一化匹配：去掉空白与常见分隔符干扰（如"傻 逼""傻*逼"），提高绕过成本；
4. 命中即拦截，返回命中的词与位置，便于前端提示与后台审计。

平台约束：纯字符串处理，零依赖、零 CPU 压力（每次发帖仅一次全表遍历）。
"""

from __future__ import annotations

import re
from typing import Optional

import portal_db

# ============================================================
# 内置词库（管理员可在后台增补；此处为兜底基线）
# ============================================================
BUILTIN_WORDS: tuple[str, ...] = (
    # 违法/危险
    "赌博", "博彩", "六合彩", "枪支", "弹药", "毒品", "冰毒", "大麻", "吸毒",
    "代考", "代写作业", "枪手代考", "黑客", "木马", "钓鱼网站",
    # 色情/低俗
    "色情", "裸聊", "约炮", "一夜情", "卖淫", "嫖娼",
    # 诈骗/广告
    "刷单", "代刷", "兼职刷", "网络兼职", "日结兼职", "加微信", "加qq", "加QQ",
    "出售账号", "出售外挂", "代充", "代练", "扫码领", "返利", "贷款秒批",
    # 辱骂/攻击（班级场景常见）
    "傻逼", "傻B", "煞笔", "沙比", "脑残", "智障", "废物", "垃圾人",
    "滚蛋", "去死", "死全家", "全家si", "草泥马", "尼玛", "妈的",
    # 人身攻击/霸凌
    "丑八怪", "恶心死了", "蠢猪", "贱人", "贱货",
)

# 归一化：统一小写、去掉空白/常见分隔符号（用于对抗"隔字符"绕过）
_NORMALIZE_RE = re.compile(r"[\s\-_*·．.、,，/~|\\+]+")


def normalize(text: str) -> str:
    """压缩文本：去空白与常见分隔符，英文统一小写，用于匹配。"""
    return _NORMALIZE_RE.sub("", str(text or "")).lower()


def all_words() -> list[str]:
    """
    合并词库：内置 + 管理员词库（去重，保留内置顺序）。
    管理员词库查询失败时退化为仅内置词库（保证发帖不因 DB 抖动而失败）。
    """
    words: list[str] = list(BUILTIN_WORDS)
    try:
        words.extend(portal_db.sensitive_all_words())
    except Exception:  # noqa: BLE001
        pass
    # 去重且保持稳定顺序
    seen: set[str] = set()
    out: list[str] = []
    for w in words:
        key: str = w.strip()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def check(text: str) -> Optional[str]:
    """
    检查文本是否命中敏感词。
    返回命中的词（已归一化前的原文写法）；未命中返回 None。
    同时检测原文与归一化文本，任何一侧命中即拦截。
    """
    raw: str = str(text or "")
    if not raw.strip():
        return None
    norm: str = normalize(raw)
    for w in all_words():
        if w in raw or normalize(w) in norm:
            return w
    return None


def mask(text: str, word: str) -> str:
    """把命中的词在文本中打码（仅用于后台展示审计，前台直接拦截不展示）。"""
    if not word:
        return text
    return str(text).replace(word, "＊" * len(word))
