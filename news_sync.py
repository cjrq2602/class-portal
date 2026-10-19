"""
news_sync.py —— 新闻/总结的 GitHub 中转同步
==========================================
背景（关键平台约束）：
  PythonAnywhere 免费版白名单只能访问 PyPI / GitHub，
  因此本站【只允许】请求两个域名：
      https://api.github.com            （列文件、取 commit SHA，匿名 60 次/小时限速）
      https://raw.githubusercontent.com （取文件内容）
  新闻正文由 GitHub Actions 子项目（github_news_worker/）抓取后写入仓库，
  本站只做"读取仓库文件 → 入库缓存 → 页面展示"。

同步策略：
1. 本地缓存 TTL 300 秒：过期后第一个请求触发同步，其余请求直接用旧缓存；
2. 进程级锁：并发请求同时触发时只有一个真正去拉，避免重复消耗限速额度；
3. SHA 比对：先取 news 目录最新 commit SHA，与上次相同则跳过文件下载（省额度）；
4. 全 try/except：任何网络失败都不抛异常，保留旧缓存，页面照常展示并标注数据时间。
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Optional

import portal_db

# ============================================================
# 配置（部署时按需修改或通过环境变量覆盖）
# ============================================================
# 新闻仓库：形如 "用户名/仓库名"；改错可用来验证"GitHub 不可达"降级表现
NEWS_REPO: str = os.environ.get("NEWS_REPO", "cjrq2602/class-portal-news")
NEWS_BRANCH: str = os.environ.get("NEWS_BRANCH", "main")
# 可选：配了 token 可把 api.github.com 限速从 60 次/小时提升到 5000
NEWS_GITHUB_TOKEN: str = os.environ.get("NEWS_GITHUB_TOKEN", "")

NEWS_TTL_SECONDS: int = 300          # 本地缓存有效期（秒）
NEWS_HTTP_TIMEOUT: int = 10          # 单次请求超时
NEWS_MAX_DAY_FILES: int = 3          # 每次最多拉取最近 N 天的新闻文件
NEWS_MAX_DIGEST_FILES: int = 12      # 每次最多拉取最近 N 份总结文件

# 进程级锁：非阻塞获取，拿不到说明已有请求在同步中
_SYNC_LOCK: threading.Lock = threading.Lock()


# ============================================================
# HTTP 基元（仅允许白名单内的两个域名）
# ============================================================
def _headers(accept: str = "application/vnd.github+json") -> dict[str, str]:
    h: dict[str, str] = {"Accept": accept, "User-Agent": "class-portal-news-sync"}
    if NEWS_GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {NEWS_GITHUB_TOKEN}"
    return h


def _get_json(url: str) -> Any:
    """GET 并解析 JSON；任何异常向上抛给调用方统一兜底。"""
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req, timeout=NEWS_HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _get_text(url: str) -> str:
    """GET 纯文本（raw.githubusercontent.com 用）。"""
    req = urllib.request.Request(url, headers=_headers(accept="text/plain"))
    with urllib.request.urlopen(req, timeout=NEWS_HTTP_TIMEOUT) as resp:
        return resp.read().decode("utf-8", "replace")


# ============================================================
# 仓库文件访问
# ============================================================
def _repo_sha(path: str) -> Optional[str]:
    """取仓库中某目录/文件最新 commit SHA（路径不存在返回 None）。"""
    url: str = (f"https://api.github.com/repos/{NEWS_REPO}/commits"
                f"?path={path}&sha={NEWS_BRANCH}&per_page=1")
    data = _get_json(url)
    if isinstance(data, list) and data:
        return str(data[0].get("sha") or "")
    return None


def _list_dir(path: str, ref: str) -> list[str]:
    """
    列出仓库目录下的文件名（api.github.com/contents）。
    目录不存在（Worker 还没产出 digests/ 等）时返回空列表，而不是让整个同步失败。
    """
    url: str = f"https://api.github.com/repos/{NEWS_REPO}/contents/{path}?ref={ref}"
    try:
        data = _get_json(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return []
        raise
    if not isinstance(data, list):
        return []
    return [str(x.get("name") or "") for x in data if x.get("type") == "file"]


def _raw(path: str, ref: str) -> str:
    """
    取文件原始内容。
    优先 raw.githubusercontent.com；该域名在部分网络下会被重置，
    失败时回退到 api.github.com/contents（base64 内联），两个域名都在白名单内。
    """
    try:
        return _get_text(f"https://raw.githubusercontent.com/{NEWS_REPO}/{ref}/{path}")
    except Exception:  # noqa: BLE001 —— 网络重置/超时 → 走 API 回退
        url: str = f"https://api.github.com/repos/{NEWS_REPO}/contents/{path}?ref={ref}"
        data = _get_json(url)
        content: str = str((data or {}).get("content") or "")
        if not content:
            raise
        return base64.b64decode(content).decode("utf-8")


def _day_files(names: list[str]) -> list[str]:
    """从文件名中挑出日期文件（YYYY-MM-DD.json），按日期倒序取最近 N 个。"""
    ok: list[str] = [n for n in names
                     if len(n) == 15 and n.endswith(".json") and n[4] == "-" and n[7] == "-"]
    ok.sort(reverse=True)
    return ok[:NEWS_MAX_DAY_FILES]


def _digest_files(names: list[str]) -> list[str]:
    """挑出总结文件（YYYY-MM-DD_today.json / _yesterday.json），日期倒序取最近 N 个。"""
    ok: list[str] = [n for n in names
                     if n.endswith("_today.json") or n.endswith("_yesterday.json")]
    ok.sort(reverse=True)
    return ok[:NEWS_MAX_DIGEST_FILES]


# ============================================================
# 同步主流程
# ============================================================
def _do_sync(force: bool = False) -> dict[str, Any]:
    """
    实际执行同步（调用方保证已持锁）。
    返回 {ok, changed, added, digests_added, sha, error}
    """
    result: dict[str, Any] = {"ok": False, "changed": False, "added": 0,
                              "digests_added": 0, "sha": "", "error": ""}
    now: float = time.time()
    last_sha: str = portal_db.sync_state_get("last_sha", "")
    try:
        # ---- 1. 新闻目录 SHA 比对（省限速额度） ----
        sha: Optional[str] = _repo_sha("news")
        result["sha"] = sha or ""
        if sha and sha == last_sha and not force:
            result["ok"] = True
            portal_db.sync_state_put("last_sync_at", str(now))
            portal_db.sync_state_put("last_ok", "1")
            portal_db.sync_state_put("last_error", "")
            return result

        # ---- 2. 新闻文件 → 入库 ----
        if sha:
            names: list[str] = _list_dir("news", sha)
            added: int = 0
            for name in _day_files(names):
                try:
                    payload = json.loads(_raw(f"news/{name}", sha))
                except Exception:  # noqa: BLE001 单个文件坏不影响整体
                    continue
                items: list[dict[str, Any]] = []
                for it in (payload.get("items") or []):
                    try:
                        items.append({
                            "source": str(it.get("source") or ""),
                            "title": str(it.get("title") or ""),
                            "url": str(it.get("url") or ""),
                            "summary": str(it.get("summary") or ""),
                            "published_at": float(it.get("published_at") or 0.0),
                            "pub_bj_date": str(payload.get("date") or ""),
                        })
                    except Exception:  # noqa: BLE001
                        continue
                items = [x for x in items if x["source"] and x["title"] and x["url"]]
                added += portal_db.insert_news_many(items)
            result["added"] = added

            # ---- 3. 总结文件 → 入库（管理员手改过的 edited=1 不覆盖） ----
            d_names: list[str] = _list_dir("digests", sha)
            d_added: int = 0
            for name in _digest_files(d_names):
                try:
                    payload = json.loads(_raw(f"digests/{name}", sha))
                except Exception:  # noqa: BLE001
                    continue
                ntype: str = str(payload.get("type") or "")
                ndate: str = str(payload.get("date") or "")
                if ntype not in ("today", "yesterday") or not ndate:
                    continue
                exist = portal_db.get_digest(ntype, ndate)
                if exist is not None and int(exist["edited"]) == 1:
                    continue  # 管理员手工编辑过 → 保留人工内容
                portal_db.upsert_digest(ntype, ndate,
                                        json.dumps(payload, ensure_ascii=False),
                                        generator=str(payload.get("generator") or "action"))
                d_added += 1
            result["digests_added"] = d_added
            result["changed"] = bool(added or d_added)
            portal_db.sync_state_put("last_sha", sha)

        # ---- 4. 成功记账 ----
        portal_db.sync_state_put("last_sync_at", str(now))
        portal_db.sync_state_put("last_ok", "1")
        portal_db.sync_state_put("last_error", "")
        result["ok"] = True
    except Exception as exc:  # noqa: BLE001 —— 网络/解析失败一律降级
        result["error"] = f"{type(exc).__name__}: {exc}"[:200]
        portal_db.sync_state_put("last_sync_at", str(now))  # 仍记账，防限速风暴
        portal_db.sync_state_put("last_ok", "0")
        portal_db.sync_state_put("last_error", result["error"])
    return result


def sync_news(force: bool = False) -> dict[str, Any]:
    """
    对外入口：TTL 内直接返回缓存状态；过期则（尝试）同步。
    force=True 用于后台"手动同步"按钮（忽略 TTL，但仍受锁保护）。
    """
    now: float = time.time()
    last_at: float = float(portal_db.sync_state_get("last_sync_at", "0") or 0)
    if not force and last_at and (now - last_at) < NEWS_TTL_SECONDS:
        return {"ok": True, "skipped": True, "reason": "ttl",
                "last_sync_at": last_at}
    if not _SYNC_LOCK.acquire(blocking=False):
        # 已有请求在同步中：直接用旧缓存，避免重复拉取
        return {"ok": True, "skipped": True, "reason": "locked",
                "last_sync_at": last_at}
    try:
        res = _do_sync(force=force)
        res["skipped"] = False
        res["last_sync_at"] = time.time()
        return res
    finally:
        _SYNC_LOCK.release()


# ============================================================
# 展示用状态（页面标注"数据更新于 xx:xx"）
# ============================================================
def sync_status() -> dict[str, Any]:
    """
    返回同步状态（供新闻页/后台展示）：
      last_sync_at  上次同步时间（Unix 秒，UTC；前端按北京时间格式化）
      ok            上次是否成功
      error         上次失败原因
      item_count    本地已入库新闻总数
      ttl           缓存 TTL（秒）
      repo          当前配置的仓库名（后台可见，便于发现配置错）
    """
    st: dict[str, str] = portal_db.sync_state_all()
    return {
        "last_sync_at": float(st.get("last_sync_at", "0") or 0),
        "ok": st.get("last_ok", "") == "1",
        "error": st.get("last_error", ""),
        "item_count": portal_db.count_news(),
        "ttl": NEWS_TTL_SECONDS,
        "repo": NEWS_REPO,
        "branch": NEWS_BRANCH,
    }
