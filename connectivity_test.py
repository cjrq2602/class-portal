"""
connectivity_test.py —— 白名单连通性自测（部署后先跑这个）
=======================================================
目的：确认本站能访问的两条链路真的通：
  1. https://api.github.com          列文件 / 取 commit SHA（匿名限速 60 次/小时）
  2. https://raw.githubusercontent.com 取文件内容
并顺带核对 NEWS_REPO / NEWS_BRANCH 配置是否正确、news/ 与 digests/ 目录是否存在。

用法（PythonAnywhere Bash）：
    cd ~/mysite && python3.10 connectivity_test.py
    # 可临时覆盖仓库名做验证（模拟"仓库名写错"的降级表现）：
    NEWS_REPO=wrong/repo python3.10 connectivity_test.py

输出：逐项打印 PASS/FAIL，并在最后给出结论与修复建议。
注意：本脚本只访问上述两个域名，绝不请求任何白名单外地址。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Optional

PROJECT_HOME: str = os.path.dirname(os.path.abspath(__file__))
if PROJECT_HOME not in sys.path:
    sys.path.insert(0, PROJECT_HOME)

import news_sync  # noqa: E402  复用同一套仓库配置与请求头

TIMEOUT: int = 10
results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(("  ✅ " if ok else "  ❌ ") + name + (f"  —— {detail}" if detail else ""))


def http_json(url: str) -> Any:
    """带 User-Agent/Accept 头请求 JSON（GitHub 强制要求 User-Agent）。"""
    req = urllib.request.Request(url, headers=news_sync._headers())
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def http_text(url: str) -> str:
    req = urllib.request.Request(url, headers=news_sync._headers(accept="text/plain"))
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8", "replace")


def check_api_github() -> None:
    """链路 1：api.github.com（用 /rate_limit 探测，不消耗内容请求额度）。"""
    try:
        data = http_json("https://api.github.com/rate_limit")
        core = (data or {}).get("resources", {}).get("core", {})
        remaining = core.get("remaining", "?")
        limit = core.get("limit", "?")
        record("api.github.com 可达", True, f"限速额度 {remaining}/{limit}（每小时重置）")
    except Exception as exc:  # noqa: BLE001
        record("api.github.com 可达", False, f"{type(exc).__name__}: {exc}")


def check_raw_github() -> None:
    """链路 2：raw.githubusercontent.com（取一份极小的公共文件，不依赖自建仓库）。"""
    try:
        txt: str = http_text(
            "https://raw.githubusercontent.com/github/gitignore/main/Python.gitignore")
        record("raw.githubusercontent.com 可达", len(txt) > 0,
               f"取回 {len(txt)} 字节")
    except Exception as exc:  # noqa: BLE001
        record("raw.githubusercontent.com 可达", False, f"{type(exc).__name__}: {exc}")


def check_repo() -> None:
    """核对配置的新闻仓库：仓库存在？news/ 目录有内容？digests/ 有内容？"""
    repo: str = news_sync.NEWS_REPO
    branch: str = news_sync.NEWS_BRANCH
    print(f"  当前配置：NEWS_REPO={repo}  NEWS_BRANCH={branch}")
    try:
        info = http_json(f"https://api.github.com/repos/{repo}")
        if not isinstance(info, dict) or "full_name" not in info:
            record(f"仓库存在：{repo}", False, "响应异常（可能是私有仓库或名称写错）")
            return
        record(f"仓库存在：{repo}", True,
               f"默认分支={info.get('default_branch')} 私有={info.get('private')}")
    except urllib.error.HTTPError as exc:
        hint: str = ""
        if exc.code == 404:
            hint = "仓库不存在或名称写错（改 NEWS_REPO 环境变量或 news_sync.py 常量）"
        elif exc.code == 403:
            hint = "API 限速用尽，稍后重试或配置 NEWS_GITHUB_TOKEN"
        record(f"仓库存在：{repo}", False, f"HTTP {exc.code} {hint}")
        return
    except Exception as exc:  # noqa: BLE001
        record(f"仓库存在：{repo}", False, f"{type(exc).__name__}: {exc}")
        return

    for path in ("news", "digests"):
        try:
            items = http_json(
                f"https://api.github.com/repos/{repo}/contents/{path}?ref={branch}")
            n: int = len(items) if isinstance(items, list) else 0
            record(f"目录可读：{path}/", n > 0 or isinstance(items, list),
                   f"{n} 个文件" + ("" if n else "（目录为空：Actions 还没产出数据）"))
        except Exception as exc:  # noqa: BLE001
            record(f"目录可读：{path}/", False, f"{type(exc).__name__}: {exc}")


def check_whitelist_only() -> None:
    """静态自检：确认代码里只出现白名单域名（防止误加外链域名）。"""
    files: list[str] = ["news_sync.py"]
    allowed: tuple[str, ...] = ("api.github.com", "raw.githubusercontent.com")
    bad: list[str] = []
    for fn in files:
        path: str = os.path.join(PROJECT_HOME, fn)
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                if "http://" in line or "https://" in line:
                    if not any(a in line for a in allowed):
                        bad.append(f"{fn}:{i}: {line.strip()[:80]}")
    record("同步代码仅访问白名单域名", not bad,
           "；".join(bad) if bad else "仅 api.github.com / raw.githubusercontent.com")


def main() -> None:
    print("== 白名单连通性自测 ==")
    check_api_github()
    check_raw_github()
    check_repo()
    check_whitelist_only()

    ok: int = sum(1 for _n, o, _d in results if o)
    print(f"\n== 小结：{ok}/{len(results)} 项通过 ==")
    if ok == len(results):
        print("结论：链路与仓库配置均正常，可正常同步新闻与总结。")
    else:
        print("结论：存在失败项。")
        print("  • 若 api.github.com 失败 → PythonAnywhere 白名单或网络问题，检查后重跑。")
        print("  • 若仅『仓库存在』失败 → 把 news_sync.py 的 NEWS_REPO 改成你的"
              "『用户名/仓库名』（或设环境变量 NEWS_REPO）。")
        print("  • 若 news/ 为空 → 先到 GitHub 端跑一次 Actions（workflow_dispatch）产出数据。")
        print("  • 站点在同步失败时会自动降级：继续用旧缓存并在页面标注数据更新时间，"
              "不会白屏或 500。")
    sys.exit(0 if ok == len(results) else 1)


if __name__ == "__main__":
    main()
