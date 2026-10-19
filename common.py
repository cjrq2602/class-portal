"""
common.py —— 门户公共基础设施（跨蓝图共享）
============================================
职责：
1. 全局配置：SECRET_KEY（会话/CSRF/游戏令牌派生的唯一密钥，必须稳定不变）；
2. 北京时间换算：服务器在 UTC，所有"展示用"时间统一走 beijing_now()/fmt_beijing()；
3. 速率限制：内存滑动窗口（PythonAnywhere 免费版单 worker，足够且零依赖）；
4. 统一错误 JSON：{ok:false, code, msg}，任何接口不得裸 500；
5. CSRF：会话令牌 + X-CSRF-Token 头/表单字段双通道校验；
6. 密码哈希：pbkdf2-sha256 + 随机盐（标准库实现，不引第三方依赖）；
7. 登录失败锁定：同一用户名连续失败 5 次锁 10 分钟（内存计数，重启清零可接受）。
平台约束：无 WebSocket、无后台线程、低 CPU——本模块全部为纯函数/轻量结构。
"""

from __future__ import annotations

import calendar
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Any, Optional

from flask import jsonify, request, session

# ============================================================
# 全局配置
# ============================================================
# SECRET_KEY 一旦部署不可更换（更换会使所有会话与登录用户的游戏令牌失效）。
# 班级内部站点，直接写死常量；如需更高安全性可改为读取环境变量。
SECRET_KEY: str = "nightwatch-portal-2026-secret-do-not-change"

# 登录锁定策略
LOGIN_MAX_FAILS: int = 5          # 连续失败次数上限
LOGIN_LOCK_SECONDS: int = 600     # 锁定 10 分钟

# 邀请码默认值（管理后台生成时可调）
INVITE_DEFAULT_MAX_USES: int = 1      # 默认一码一人
INVITE_DEFAULT_VALID_DAYS: int = 7    # 默认有效期 7 天

# 每玩家/每 IP 每秒最多请求数（与游戏侧共用同一阈值）
RATE_LIMIT_PER_SEC: int = 5

# PBKDF2 参数（OWASP 建议下限 60 万轮针对 sha256；班级站点取 20 万轮平衡 CPU 配额）
PBKDF2_ITERATIONS: int = 200_000


# ============================================================
# 北京时间（服务器 UTC → 展示 UTC+8）
# ============================================================
def beijing_now() -> datetime:
    """当前北京时间。服务器时钟为 UTC，展示一律 +8（此处为全站唯一换算点之一）。"""
    return datetime.utcnow() + timedelta(hours=8)


def fmt_beijing(utc_unix: float, with_time: bool = True) -> str:
    """Unix 秒 → 北京时间字符串（UTC+8 换算）。用于服务端直接渲染的文案。"""
    dt: datetime = datetime.utcfromtimestamp(float(utc_unix)) + timedelta(hours=8)
    return dt.strftime("%Y-%m-%d %H:%M") if with_time else dt.strftime("%Y-%m-%d")


def greeting_by_hour() -> str:
    """按北京时间小时段返回问候语（首页 Hero 用）。"""
    h: int = beijing_now().hour
    if 5 <= h < 11:
        return "早上好"
    if 11 <= h < 14:
        return "中午好"
    if 14 <= h < 18:
        return "下午好"
    return "晚上好"


# ---------------- 北京日期 / 日窗口（新闻归档与每日总结的时间基准） ----------------
# 关键换算：北京日 D 的 00:00:00 对应 "D 的 UTC 零点 − 8 小时"。
# 例：北京 2026-10-17 00:00 == UTC 2026-10-16 16:00 == unix(2026-10-17 00:00 UTC) - 28800。
BJ_OFFSET_SECONDS: int = 8 * 3600


def bj_date_str(dt: Optional[datetime] = None, offset_days: int = 0) -> str:
    """北京日期字符串 YYYY-MM-DD（可偏移天数，昨日传 offset_days=-1）。"""
    base: datetime = dt if dt is not None else beijing_now()
    return (base + timedelta(days=offset_days)).strftime("%Y-%m-%d")


def bj_day_start_ts(date_str: str) -> float:
    """北京日期 → 该日 00:00（北京）对应的 Unix 秒（UTC 数值）。"""
    d: datetime = datetime.strptime(date_str, "%Y-%m-%d")
    return float(calendar.timegm(d.timetuple()) - BJ_OFFSET_SECONDS)


def bj_day_range(date_str: str) -> tuple[float, float]:
    """北京日期 → [当日 00:00, 次日 00:00) 的 Unix 秒区间。"""
    start: float = bj_day_start_ts(date_str)
    return start, start + 86400.0


def bj_hour_ts(date_str: str, hour: int) -> float:
    """北京日期 + 整点小时 → Unix 秒（如 09:00 截止点）。"""
    return bj_day_start_ts(date_str) + hour * 3600.0


def ts_to_bj_str(unix_ts: float, with_time: bool = True) -> str:
    """Unix 秒 → 北京时间字符串（与 fmt_beijing 同源，命名更语义化）。"""
    return fmt_beijing(unix_ts, with_time)


def parse_bj_local(value: str) -> float:
    """
    <input type="datetime-local"> 的值（形如 "2026-10-17T18:30"，语义是【北京时间】）
    → Unix 秒。两种写法都接受（带/不带秒）。
    用户填的永远是北京时间，这里做 UTC+8 反算，避免"差 8 小时"的经典错误。
    """
    s: str = str(value or "").strip().replace(" ", "T")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            dt: datetime = datetime.strptime(s, fmt)
            return float(calendar.timegm(dt.timetuple()) - BJ_OFFSET_SECONDS)
        except ValueError:
            continue
    raise ValueError("时间格式应为 YYYY-MM-DDTHH:MM")


def bj_local_value(unix_ts: float) -> str:
    """Unix 秒 → datetime-local 输入框可用的北京时间字符串（后台编辑回填用）。"""
    return (datetime.utcfromtimestamp(float(unix_ts)) +
            timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M")


# ============================================================
# 每日总结的应生成时刻与惰性兜底判定
# ============================================================
# 规则（全部按北京时间）：
#   00:00 → 生成【昨日总结】type=yesterday，窗口 = 昨天 00:00 ~ 次日 00:00
#   09:00 → 生成【今日总结】type=today，    窗口 = 今天 00:00 ~ 09:00
#   (type, date) 唯一 → 重复生成幂等
DIGEST_TODAY_HOUR: int = 9      # 今日总结的截止/应生成时刻（北京 09:00）
DIGEST_YESTERDAY_HOUR: int = 0  # 昨日总结的应生成时刻（北京 00:00）


def digest_spec(ntype: str, now_bj: Optional[datetime] = None
                ) -> tuple[str, float, float, float]:
    """
    计算某类总结的 (所属北京日期, 窗口起, 窗口止, 应生成时刻)。
    - yesterday：date=昨天，窗口=昨天全天，应生成时刻=今天 00:00
    - today    ：date=今天，窗口=今天 00:00~09:00，应生成时刻=今天 09:00
    """
    now: datetime = now_bj if now_bj is not None else beijing_now()
    today: str = now.strftime("%Y-%m-%d")
    if ntype == "yesterday":
        ydate: str = bj_date_str(now, offset_days=-1)
        start, end = bj_day_range(ydate)
        due: float = bj_hour_ts(today, DIGEST_YESTERDAY_HOUR)
        return ydate, start, end, due
    start, _end = bj_day_range(today)
    due = bj_hour_ts(today, DIGEST_TODAY_HOUR)
    return today, start, bj_hour_ts(today, DIGEST_TODAY_HOUR), due


def digest_pending(ntype: str, now_bj: Optional[datetime] = None) -> bool:
    """
    该总结是否"已过应生成时刻但尚未生成"（惰性兜底触发条件）。
    未到时刻 → False（不生成，前端显示"将于 09:00 更新"占位）。
    """
    now: datetime = now_bj if now_bj is not None else beijing_now()
    date, _start, _end, due = digest_spec(ntype, now)
    if time.time() < due:
        return False
    import portal_db  # 局部导入：避免 common ↔ portal_db 循环依赖
    return portal_db.get_digest(ntype, date) is None


def today_digest_ready_at() -> str:
    """今日总结的展示占位文案（未到 09:00 时前端显示）。"""
    return f"{DIGEST_TODAY_HOUR:02d}:00"


# ============================================================
# 速率限制（内存滑动窗口；PythonAnywhere 免费版单 worker 下全局有效）
# ============================================================
_rate_store: dict[str, deque] = defaultdict(deque)


def client_ip() -> str:
    """
    取真实访客 IP：PythonAnywhere 反向代理后 remote_addr 是 LB 内网地址
    （全站所有访客共享同一个），必须读 X-Forwarded-For 首跳。
    限流/审计都应使用本函数，否则全班共用同一个限流桶会集体 429。
    """
    xff: str = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "unknown"


def rate_check(key: str, limit: int = RATE_LIMIT_PER_SEC) -> bool:
    """每 key 每秒最多 limit 次。返回是否放行。"""
    q = _rate_store[key]
    now = time.time()
    while q and now - q[0] > 1.0:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True


def rate_check_window(key: str, limit: int, window_seconds: float) -> bool:
    """
    自定义窗口限流（同一内存滑动窗口结构，窗口可配）。
    用于"每用户每分钟 N 次"这类需求（如上传接口 5 次/分钟）。
    """
    q = _rate_store[key]
    now = time.time()
    while q and now - q[0] > window_seconds:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True


# ============================================================
# 统一错误 JSON（禁止裸 500）
# ============================================================
def err(msg: str, code: str = "bad_request", http: int = 400):
    """统一错误响应：{ok:false, code, msg}。"""
    return jsonify({"ok": False, "code": code, "msg": msg}), http


def ok(data: Optional[dict[str, Any]] = None):
    """统一成功响应：{ok:true, ...}。"""
    payload: dict[str, Any] = {"ok": True}
    if data:
        payload.update(data)
    return jsonify(payload)


def api_wrap(fn):
    """端点装饰器：任何异常都包装成统一 JSON，绝不 500 裸崩。"""
    from functools import wraps

    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except ValueError as e:
            return err(str(e))
        except Exception as e:  # noqa: BLE001 —— 边界兜底
            return err(f"服务器内部错误: {type(e).__name__}", "internal", 500)

    return wrapper


# ============================================================
# CSRF（会话令牌制：所有 POST 必带，头或表单字段二选一）
# ============================================================
def get_csrf_token() -> str:
    """取当前会话 CSRF 令牌（不存在则生成）。模板经 context_processor 注入。"""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(16)
    return session["csrf_token"]


def csrf_protect() -> Optional[Any]:
    """
    before_request 钩子：校验所有 POST 的 CSRF 令牌。
    通过返回 None；不通过直接返回统一错误 JSON（403）。
    """
    if request.method != "POST":
        return None
    sent: str = (request.headers.get("X-CSRF-Token", "")
                 or (request.form.get("csrf_token", "") if request.form else ""))
    expected: str = session.get("csrf_token", "")
    if not expected or not hmac.compare_digest(sent, expected):
        return err("CSRF 校验失败，请刷新页面重试", "csrf", 403)
    return None


# ============================================================
# 密码哈希（pbkdf2-sha256 + 随机盐，纯标准库）
# ============================================================
def hash_password(password: str) -> str:
    """生成 'pbkdf2$迭代数$盐hex$哈希hex' 格式字符串。"""
    salt: bytes = secrets.token_bytes(16)
    dk: bytes = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                    salt, PBKDF2_ITERATIONS)
    return f"pbkdf2${PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """恒定时间比较密码。格式非法一律返回 False。"""
    try:
        _, iters_s, salt_hex, hash_hex = stored.split("$")
        iters: int = int(iters_s)
        dk: bytes = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                        bytes.fromhex(salt_hex), iters)
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:  # noqa: BLE001
        return False


def gen_password(length: int = 12) -> str:
    """生成随机强密码（去易混淆字符），seed_admin / 重置密码用。"""
    alphabet: str = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


# ============================================================
# 登录失败锁定（内存计数；免费版单 worker 下有效）
# ============================================================
_login_fails: dict[str, dict[str, float]] = {}


def login_locked(username: str) -> bool:
    """该用户名是否处于锁定期。"""
    rec = _login_fails.get(username)
    if not rec:
        return False
    locked_until: float = rec.get("until", 0.0)
    if locked_until and time.time() < locked_until:
        return True
    if locked_until and time.time() >= locked_until:
        _login_fails.pop(username, None)  # 锁定期过，清零
    return False


def login_fail(username: str) -> None:
    """记录一次失败；达到阈值上锁。"""
    rec = _login_fails.setdefault(username, {"count": 0.0, "until": 0.0})
    rec["count"] += 1
    if rec["count"] >= LOGIN_MAX_FAILS:
        rec["until"] = time.time() + LOGIN_LOCK_SECONDS
        rec["count"] = 0.0


def login_success(username: str) -> None:
    """登录成功清零计数。"""
    _login_fails.pop(username, None)


# ============================================================
# 登录用户的游戏令牌（派生制：不改游戏旧表结构）
# ============================================================
# 设计：登录用户的游戏 token = "lg2-{user_id}-{签名}"，由 SECRET_KEY 派生。
# 游戏侧 players 表结构一字不改；服务端凭签名反查 user_id，
# 从而把对局战绩写入门户 game_stats、把账号昵称带入游戏。
GAME_TOKEN_PREFIX: str = "lg2-"


def make_game_token(user_id: int) -> str:
    """为登录用户派生稳定游戏令牌（同一用户永远相同）。"""
    sig: str = hmac.new(SECRET_KEY.encode("utf-8"),
                        f"game-token:{user_id}".encode("utf-8"),
                        hashlib.sha256).hexdigest()[:24]
    return f"{GAME_TOKEN_PREFIX}{user_id}-{sig}"


def parse_game_token(token: str) -> Optional[int]:
    """从游戏令牌反查 user_id；签名不符或非登录令牌返回 None。"""
    if not token or not token.startswith(GAME_TOKEN_PREFIX):
        return None
    try:
        body: str = token[len(GAME_TOKEN_PREFIX):]
        uid_s, _sep, _sig = body.partition("-")
        uid: int = int(uid_s)
    except Exception:  # noqa: BLE001
        return None
    # 恒定时间比对签名，防伪造
    expected: str = make_game_token(uid)
    if hmac.compare_digest(expected, token):
        return uid
    return None


# ============================================================
# 第五期：密码强度策略 / CSP nonce / 进程内 TTL 缓存 / 会话时限
# ============================================================
PASSWORD_MIN_LEN: int = 8   # 密码最短长度（且须同时含字母与数字）


def check_password_strength(password: str) -> Optional[str]:
    """
    密码策略统一校验（注册/改密/seed 全走这里）。
    规则：≥8 位 且 同时含字母与数字。通过返回 None，不通过返回中文原因。
    """
    if len(password or "") < PASSWORD_MIN_LEN:
        return f"密码至少 {PASSWORD_MIN_LEN} 位"
    has_alpha: bool = any(c.isalpha() for c in password)
    has_digit: bool = any(c.isdigit() for c in password)
    if not (has_alpha and has_digit):
        return "密码需同时包含字母与数字"
    return None


def make_csp_nonce() -> str:
    """每次请求生成一次性 CSP nonce（URL 安全字符）。"""
    return secrets.token_urlsafe(16)


# 会话时限策略（秒）
SESSION_ABSOLUTE_DAYS: int = 7          # 绝对过期 7 天
SESSION_IDLE_SECONDS: int = 2 * 3600    # 滑动过期：2 小时无操作失效
ADMIN_IDLE_SECONDS: int = 30 * 60       # 管理后台：30 分钟无操作失效


def ttl_cache(seconds: float):
    """
    进程内 TTL 缓存装饰器（首页聚合数据用，降 CPU）。
    仅缓存无参函数的返回值；PythonAnywhere 免费版单 worker 下全局有效。
    """
    def deco(fn):
        box: dict[str, Any] = {"ts": 0.0, "val": None}

        def wrapper():
            now: float = time.time()
            if now - box["ts"] < seconds and box["val"] is not None:
                return box["val"]
            val = fn()
            box["ts"], box["val"] = now, val
            return val

        wrapper.__name__ = getattr(fn, "__name__", "cached")
        return wrapper
    return deco


# ============================================================
# 第六期：密码重置码（类智慧团建：管理员发码，用户凭码改密）
# ============================================================
RESET_CODE_TTL_SECONDS: int = 15 * 60   # 码 15 分钟有效
RESET_MAX_FAILS: int = 5                # 同一目标用户输错 5 次
RESET_LOCK_SECONDS: int = 600           # 锁定 10 分钟
# 8 位一次性码字符集：数字+大写字母，剔除易混淆 0/O/1/L/I
_RESET_ALPHABET: str = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def make_reset_code(length: int = 8) -> str:
    """生成明文重置码（仅生成接口的本次响应可见，服务端绝不落库/落日志）。"""
    return "".join(secrets.choice(_RESET_ALPHABET) for _ in range(length))


def hash_reset_code(code: str) -> str:
    """
    重置码哈希：HMAC-SHA256(SECRET_KEY)。不用 pbkdf2 —— 码本身 32^8 熵足够，
    且校验需要快速（登录页每次提交都要比对），HMAC 一次搞定。
    """
    return hmac.new(SECRET_KEY.encode("utf-8"),
                    f"reset-code:{(code or '').strip().upper()}".encode("utf-8"),
                    hashlib.sha256).hexdigest()


def verify_reset_code(code: str, stored_hash: str) -> bool:
    """恒定时间比对重置码（防时序侧信道）。"""
    if not code or not stored_hash:
        return False
    return hmac.compare_digest(hash_reset_code(code), stored_hash)


# ---- 输错锁定（内存计数，与登录锁定同构；免费版单 worker 下全局有效） ----
_reset_fails: dict[int, dict[str, float]] = {}


def reset_locked(user_id: int) -> bool:
    """该目标用户的重置尝试是否处于锁定期。"""
    rec = _reset_fails.get(int(user_id))
    if not rec:
        return False
    until: float = rec.get("until", 0.0)
    if until and time.time() < until:
        return True
    if until and time.time() >= until:
        _reset_fails.pop(int(user_id), None)  # 锁定期过，清零
    return False


def reset_fail(user_id: int) -> None:
    """记录一次输错；满 5 次锁 10 分钟。"""
    rec = _reset_fails.setdefault(int(user_id), {"count": 0.0, "until": 0.0})
    rec["count"] += 1
    if rec["count"] >= RESET_MAX_FAILS:
        rec["until"] = time.time() + RESET_LOCK_SECONDS
        rec["count"] = 0.0


def reset_fail_clear(user_id: int) -> None:
    """改密成功清零计数。"""
    _reset_fails.pop(int(user_id), None)
