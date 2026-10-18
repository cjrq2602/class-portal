"""
digest_service.py —— 每日总结的"惰性兜底"编排层
==============================================
本模块落实任务二的时间逻辑（全部按北京时间；服务器在 UTC）：

  ┌ 每天两份固定产出，互不覆盖（以 (type, date) 唯一键各自独立）：
  │   • 北京 00:00 → 【昨日总结】type=yesterday，date=昨天，
  │                   窗口 = 昨天 00:00 ~ 次日 00:00（整整一天）
  │   • 北京 09:00 → 【今日总结】type=today，date=今天，
  │                   窗口 = 今天 00:00 ~ 09:00
  ├ 0~9 点之间访问：今日总结尚未到时刻 → 卡片显示"将于 09:00 更新"占位
  └ 兜底：访问 /digest 时若"已过应生成时刻但仍缺失" →
          ① 先触发一次 GitHub 同步（Worker 可能已生成好）
          ② 仍缺失则用 digest_engine 同一套规则引擎本地补生成并入库
          （幂等：重复运行只覆盖同一 (type,date) 行）
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

import common
import digest_engine
import news_sync
import portal_db


def window_label(start_ts: float, end_ts: float) -> dict[str, str]:
    """窗口的北京时间可读文案（供前端直接展示）。"""
    return {
        "start": common.fmt_beijing(start_ts),
        "end": common.fmt_beijing(end_ts),
    }


def _load_items(start_ts: float, end_ts: float) -> list[dict[str, Any]]:
    """取窗口内新闻并转换成引擎需要的结构（id 用与 Worker 一致的哈希）。"""
    rows = portal_db.news_in_window(start_ts, end_ts)
    items: list[dict[str, Any]] = []
    for r in rows:
        source: str = str(r["source"])
        url: str = str(r["url"])
        items.append({
            "id": digest_engine.news_id(source, url),
            "title": str(r["title"]),
            "source": source,
            "url": url,
            "summary": str(r["summary"] or ""),
            "published_at": float(r["published_at"]),
        })
    return items


def generate_local(ntype: str, ndate: str, start_ts: float, end_ts: float,
                   generator: str = "lazy") -> dict[str, Any]:
    """
    用规则引擎本地生成一份总结 payload（不依赖 AI、不访问白名单外网络）。
    无新闻时也会产出合法结构（news_count=0、points=[]），避免页面永久空转。
    """
    items: list[dict[str, Any]] = _load_items(start_ts, end_ts)
    payload: dict[str, Any] = digest_engine.build_digest(
        items, ntype, ndate, start_ts, end_ts, generator=generator)
    payload["window_bj"] = window_label(start_ts, end_ts)
    return payload


def _store(ntype: str, ndate: str, payload: dict[str, Any],
           generator: str, mark_edited: bool = False) -> None:
    portal_db.upsert_digest(ntype, ndate,
                            json.dumps(payload, ensure_ascii=False),
                            generator=generator, mark_edited=mark_edited)


def ensure_digests() -> dict[str, str]:
    """
    惰性兜底入口：为 today / yesterday 分别检查"该生成却没有"的情况并补齐。
    返回 {type: "synced"|"lazy"|"skip"} 便于日志观察。

    注意：本函数只在"缺失且已过时刻"时工作，因此每天每类最多重活一次；
    生成后该行即存在，后续请求直接命中跳过（幂等）。
    """
    out: dict[str, str] = {}
    for ntype in ("yesterday", "today"):
        ndate, start, end, due = common.digest_spec(ntype)
        if time.time() < due:
            out[ntype] = "skip"          # 未到应生成时刻（0~9 点的今日总结走占位分支）
            continue
        if portal_db.get_digest(ntype, ndate) is not None:
            out[ntype] = "skip"          # 已存在（无论来自 Worker 还是上次兜底）
            continue
        # ① 先试 GitHub 同步：Worker 可能已在 Actions 侧生成好，直接拉回来即可
        try:
            news_sync.sync_news(force=True)
        except Exception:  # noqa: BLE001
            pass
        if portal_db.get_digest(ntype, ndate) is not None:
            out[ntype] = "synced"
            continue
        # ② 仍缺失 → 本地同规则引擎补生成入库
        payload: dict[str, Any] = generate_local(ntype, ndate, start, end,
                                                 generator="lazy")
        _store(ntype, ndate, payload, "lazy")
        out[ntype] = "lazy"
    return out


# ============================================================
# 读取（供页面渲染）
# ============================================================
def digest_view(ntype: str) -> dict[str, Any]:
    """
    某类总结的展示视图：
      {exists: True, payload..., generator, edited, updated_at}  已生成
      {exists: False, pending: True, ready_at: "09:00"}          未到时刻（占位）
    调用前应已由 ensure_digests() 补齐"该有却缺失"的情况。
    """
    ndate, start, end, due = common.digest_spec(ntype)
    row = portal_db.get_digest(ntype, ndate)
    if row is None:
        return {"exists": False, "pending": time.time() < due,
                "ready_at": common.today_digest_ready_at() if ntype == "today" else "",
                "type": ntype, "date": ndate}
    try:
        payload: dict[str, Any] = json.loads(str(row["payload"]))
    except Exception:  # noqa: BLE001 —— payload 损坏时给出空结构而非 500
        payload = {"type": ntype, "date": ndate, "points": [], "keywords": [],
                   "news_count": 0}
    payload.setdefault("window_bj", window_label(start, end))
    payload.update({
        "exists": True,
        "generator": str(row["generator"]),
        "edited": bool(row["edited"]),
        "updated_at": float(row["updated_at"]),
    })
    return payload


def regenerate(ntype: str, ndate: Optional[str] = None,
               generator: str = "manual") -> dict[str, Any]:
    """
    手动重生成（后台用）。ndate 缺省时按当前北京时间推算该类总结的所属日期。
    会覆盖同 (type,date) 行并把 edited 置回 0（视为重新生成）。
    """
    if ndate:
        # 指定日期：today 视为该日 00:00~09:00；yesterday 视为该日全天
        if ntype == "today":
            start, _e = common.bj_day_range(ndate)
            end: float = common.bj_hour_ts(ndate, common.DIGEST_TODAY_HOUR)
        else:
            start, end = common.bj_day_range(ndate)
    else:
        ndate, start, end, _due = common.digest_spec(ntype)
    payload: dict[str, Any] = generate_local(ntype, ndate, start, end,
                                             generator=generator)
    _store(ntype, ndate, payload, generator, mark_edited=False)
    payload["exists"] = True
    return payload


def save_edited(ntype: str, ndate: str, points_text: list[str],
                keywords: list[str]) -> dict[str, Any]:
    """
    管理员手动编辑要点文案（标记 edited=1 → 后续 GitHub 同步/重生成不覆盖人工内容）。
    points_text 为逐条要点文案，与原要点一一对应；news_ids/refs 保留原值。
    """
    row = portal_db.get_digest(ntype, ndate)
    if row is None:
        raise ValueError("该总结不存在，请先生成")
    try:
        payload: dict[str, Any] = json.loads(str(row["payload"]))
    except Exception:  # noqa: BLE001
        payload = {"type": ntype, "date": ndate, "points": [], "keywords": []}
    pts: list[dict[str, Any]] = payload.get("points") or []
    for i, txt in enumerate(points_text):
        if i < len(pts):
            clean: str = digest_engine.clean_text(txt, 120)
            if clean:
                pts[i]["text"] = clean
    if keywords:
        payload["keywords"] = [digest_engine.clean_text(k, 20) for k in keywords
                               if digest_engine.clean_text(k, 20)]
    payload["points"] = pts
    payload["manual_edited_at"] = time.time()
    _store(ntype, ndate, payload, generator=str(row["generator"]), mark_edited=True)
    return payload


def archive(limit: int = 60) -> list[dict[str, Any]]:
    """历史归档列表（日期倒序，含今日/昨日两份）。"""
    rows = portal_db.list_digests(limit=limit)
    return [{
        "type": str(r["ntype"]), "date": str(r["ndate"]),
        "generator": str(r["generator"]), "edited": bool(r["edited"]),
        "updated_at": float(r["updated_at"]),
    } for r in rows]


def summary_card(ntype: str) -> Optional[dict[str, Any]]:
    """首页摘要卡数据：最近一份该类总结的标题 + 前 3 条要点 + 关键词。"""
    row = portal_db.latest_digests().get(ntype)
    if row is None:
        return None
    try:
        payload: dict[str, Any] = json.loads(str(row["payload"]))
    except Exception:  # noqa: BLE001
        return None
    return {
        "type": ntype, "date": str(row["ndate"]),
        "generator": str(row["generator"]), "edited": bool(row["edited"]),
        "news_count": int(payload.get("news_count") or 0),
        "keywords": (payload.get("keywords") or [])[:5],
        "preview": [p.get("text", "") for p in (payload.get("points") or [])[:3]],
    }
