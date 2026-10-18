"""
flask_app.py —— 班级门户应用工厂（唯一 Web 入口）
==================================================
职责：
1. create_app() 工厂：装配配置（会话/CSRF/安全头）、注册全部蓝图；
2. 全局 before_request：CSRF 校验（所有 POST）+ 会话时限（滑动 2h /
   管理后台 30min / 绝对 7d）+ 请求计时（超 500ms 记慢查询日志）；
3. 全局 after_request：HTTP 安全响应头（含 CSP nonce 方案）；
4. 统一错误处理：/api/* 一律 JSON {ok:false,code,msg}，页面返回友好提示，禁止裸 500；
5. 日志：RotatingFileHandler（error.log 5MB×2）；
6. 静态资源版本号（内容哈希 ?v=，防缓存）；
7. 游戏战绩钩子注册：登录用户对局结果同步写门户 game_stats。
平台约束：仅 WSGI，无 WebSocket/SSE/后台线程；wsgi.py 仍 `from flask_app import app`。
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from datetime import timedelta
from logging.handlers import RotatingFileHandler

from flask import Flask, Response, g, jsonify, redirect, render_template_string, request, session

import common
import db
import portal_db

# ---------------- 静态资源版本号（内容哈希防缓存） ----------------
def _file_hash(rel: str) -> str:
    """单个静态文件取 md5 前 8 位；文件不存在返回 dev。"""
    path: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), rel)
    try:
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()[:8]
    except OSError:
        return "dev"


def _static_v() -> str:
    """游戏 + 门户全部前端资源合并哈希（任一文件变更即整体换版本号）。"""
    files: list[str] = [
        "static/game/js/app.js", "static/game/css/style.css",
        "static/css/tokens.css", "static/css/base.css",
        "static/css/components.css", "static/css/dark.css",
        "static/js/icons.js", "static/js/components.js", "static/js/theme.js",
        "static/js/admin.js", "static/js/news.js", "static/js/digest.js",
        "static/js/tucao.js", "static/js/upload.js", "static/js/activities.js",
        "static/js/homework.js", "static/js/resources.js", "static/js/duty.js",
        "static/js/honors.js", "static/js/quote.js", "static/js/checkin.js",
        "static/js/picker.js", "static/js/me.js",
        # 第五期新增前端资源
        "static/js/fx.js", "static/js/sfx.js", "static/js/bgm.js",
        "static/js/links.js", "static/js/utils/confetti.js",
        # 第六期新增前端资源
        "static/js/updates.js", "static/js/reset_password.js",
        # 第七期：皮肤包 + 语言切换
        "static/css/skins.css", "static/js/i18n.js",
    ]
    h = hashlib.md5()
    for rel in files:
        h.update(_file_hash(rel).encode())
    return h.hexdigest()[:12]


# ---------------- 极简错误页（不新增模板文件） ----------------
_ERROR_PAGE: str = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>出错了</title>
<style>body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#0b0f19;color:#e2e8f0;font-family:-apple-system,"PingFang SC",sans-serif}
a{color:#4F6EF7}</style></head><body><div style="text-align:center">
<h1 style="font-size:48px;margin:0">{{ code }}</h1><p>{{ msg }}</p>
<a href="/">← 回首页</a></div></body></html>"""

# 慢请求阈值（毫秒）：超过即记 warning，便于按体检报告优化
SLOW_REQUEST_MS: float = 500.0


def _setup_logging(app: Flask) -> None:
    """error.log 轮转：单文件 5MB × 2 份（磁盘 512MB 约束下足够且可控）。"""
    log_path: str = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "error.log")
    handler = RotatingFileHandler(log_path, maxBytes=5 * 1024 * 1024,
                                  backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    handler.setLevel(logging.WARNING)
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.WARNING)


def create_app() -> Flask:
    """应用工厂：装配并返回 Flask 实例。"""
    app = Flask(__name__)

    # ---- 会话与安全配置 ----
    app.config["SECRET_KEY"] = common.SECRET_KEY
    app.config["SESSION_COOKIE_HTTPONLY"] = True       # JS 不可读
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"      # 防 CSRF 辅助
    app.config["SESSION_COOKIE_NAME"] = "portal_session"
    # 线上默认 Secure（仅 HTTPS 下发）；本地 http 开发用环境变量关闭
    app.config["SESSION_COOKIE_SECURE"] = (
        os.environ.get("PORTAL_INSECURE_COOKIE", "") != "1")
    # 绝对过期 7 天（配合 session["born_at"] 双保险）；不在每次请求时刷新 cookie
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
        days=common.SESSION_ABSOLUTE_DAYS)
    app.config["SESSION_REFRESH_EACH_REQUEST"] = False
    app.config["STATIC_V"] = _static_v()

    _setup_logging(app)

    # ---- HSTS：告诉浏览器以后都用 HTTPS 访问（由 PA 的 SSL 证书保证） ----
    # 不再做应用层重定向，避免循环重定向问题。PA 本身已支持强制 HTTPS。
    # 本地开发（localhost/127.0.0.1）或显式关闭时不加 HSTS。
    @app.after_request
    def _hsts_header(resp: Response) -> Response:
        if os.environ.get("PORTAL_INSECURE_COOKIE") == "1":
            return resp
        host = request.host.split(":")[0]
        if host in ("localhost", "127.0.0.1", "0.0.0.0"):
            return resp
        # max-age=31536000 = 1 年；includeSubDomains 对所有子域生效
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return resp

    # ---- 请求计时 + 会话时限（CSRF 之前执行，保证被锁会话尽早登出） ----
    @app.before_request
    def _request_timer_and_session_ttl():
        g._req_start = time.time()
        # 静态资源/登录页不做会话时限检查（未登录无所谓，避免登录页死循环）
        if request.path.startswith("/static/") or request.endpoint == "static":
            return None
        if "user_id" not in session:
            return None
        now: float = time.time()
        # 绝对过期：会话建立超过 7 天 → 强制登出
        born: float = float(session.get("born_at", 0.0))
        if born and now - born > common.SESSION_ABSOLUTE_DAYS * 86400:
            session.clear()
            return None
        # 滑动过期：2 小时无任何操作 → 强制登出
        last: float = float(session.get("last_active", 0.0))
        if last and now - last > common.SESSION_IDLE_SECONDS:
            session.clear()
            return None
        # 管理后台：30 分钟无操作 → 收回后台标记（普通页面不受影响）
        if request.path.startswith("/admin") or request.path.startswith("/api/admin"):
            admin_last: float = float(session.get("admin_active", 0.0))
            if admin_last and now - admin_last > common.ADMIN_IDLE_SECONDS:
                session.pop("admin_active", None)
                if request.path.startswith("/api/"):
                    return common.err("后台会话已超时，请重新登录", "admin_timeout", 401)
            else:
                session["admin_active"] = now
        session["last_active"] = now
        # 改密后会话失效：password_changed_at 晚于会话建立时间 → 全部旧会话强制下线
        # （凭重置码改密/本人改密都会刷新该时间戳；主键查询，开销极小）
        if "user_id" in session:
            _u = portal_db.get_user(int(session["user_id"]))
            if _u is None:
                session.clear()
                return None
            _pwd_ts: float = float(_u["password_changed_at"]) if "password_changed_at" in _u.keys() else 0.0
            if _pwd_ts and _pwd_ts > born:
                session.clear()
                return None
        return None

    # ---- CSRF：所有 POST 必带令牌（头 X-CSRF-Token 或表单 csrf_token） ----
    app.before_request(common.csrf_protect)

    # ---- 模板全局注入：csrf 令牌 + 导航用当前用户 + 板块清单 + CSP nonce ----
    @app.context_processor
    def inject_globals() -> dict:
        import blocks
        from blueprints import auth as auth_bp
        # 每请求生成一次性 CSP nonce（内联 script 必须带它才能执行）
        nonce: str = getattr(g, "_csp_nonce", "") or common.make_csp_nonce()
        g._csp_nonce = nonce
        user = auth_bp.current_user()
        nav_user = None
        nav_unread_updates: int = 0
        if user is not None and not bool(user["is_banned"]):
            nav_user = {"id": int(user["id"]), "nickname": user["nickname"],
                        "username": user["username"]}
            # 第六期小红点：未读更新数（一次索引查询，模板注入，无额外轮询）
            try:
                nav_unread_updates = portal_db.update_unread_count(int(user["id"]))
            except Exception:  # noqa: BLE001 —— 表未迁移时导航照常渲染
                nav_unread_updates = 0
        # 导航/宫格/互动页共用 blocks.py 的已上线板块清单
        return {"csrf_token": common.get_csrf_token(),
                "csp_nonce": nonce,
                "nav_user": nav_user,
                "nav_perms": auth_bp.user_perms(),
                "nav_unread_updates": nav_unread_updates,
                "nav_top": blocks.top_items(),
                "nav_groups": blocks.dropdowns(),
                "nav_tabs": blocks.tab_items(),
                "static_v": app.config["STATIC_V"]}

    # ---- 全局安全响应头 + 慢请求日志 ----
    @app.after_request
    def _security_headers(resp: Response) -> Response:
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
        # CSP：script 走 nonce（模板内联配置脚本必须执行）；
        # style 允许 unsafe-inline —— 模板存在大量动态 style= 属性（颜色/进度），
        # 样式注入风险低且不可执行脚本，属可接受残余风险（体检报告已注明依据）。
        nonce: str = getattr(g, "_csp_nonce", "")
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "font-src 'self'; "
            "connect-src 'self'; "
            "media-src 'self'; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'")
        # 慢请求记日志（静态资源不算）
        start: float = getattr(g, "_req_start", 0.0)
        if start and not request.path.startswith("/static/"):
            cost_ms: float = (time.time() - start) * 1000.0
            if cost_ms > SLOW_REQUEST_MS:
                app.logger.warning("SLOW %.0fms %s %s", cost_ms,
                                   request.method, request.path)
        return resp

    # ---- robots.txt（全站禁止收录；后台页面另有 noindex meta） ----
    @app.route("/robots.txt")
    def robots_txt() -> Response:
        return Response("User-agent: *\nDisallow: /\n",
                        mimetype="text/plain")

    # ---- 注册蓝图 ----
    from blueprints import activities as activities_bp
    from blueprints import admin as admin_bp
    from blueprints import auth as auth_bp
    from blueprints import checkin as checkin_bp
    from blueprints import digest as digest_bp
    from blueprints import duty as duty_bp
    from blueprints import game as game_bp
    from blueprints import homework as homework_bp
    from blueprints import honors as honors_bp
    from blueprints import links as links_bp
    from blueprints import main as main_bp
    from blueprints import news as news_bp
    from blueprints import picker as picker_bp
    from blueprints import quote as quote_bp
    from blueprints import resetcode as resetcode_bp
    from blueprints import resources as resources_bp
    from blueprints import tucao as tucao_bp
    from blueprints import updates as updates_bp
    app.register_blueprint(game_bp.bp)
    app.register_blueprint(auth_bp.bp)
    app.register_blueprint(main_bp.bp)
    app.register_blueprint(news_bp.bp)
    app.register_blueprint(digest_bp.bp)
    app.register_blueprint(tucao_bp.bp)
    app.register_blueprint(activities_bp.bp)
    app.register_blueprint(homework_bp.bp)
    app.register_blueprint(resources_bp.bp)
    app.register_blueprint(duty_bp.bp)
    app.register_blueprint(honors_bp.bp)
    app.register_blueprint(quote_bp.bp)
    app.register_blueprint(checkin_bp.bp)
    app.register_blueprint(picker_bp.bp)
    app.register_blueprint(links_bp.bp)
    app.register_blueprint(updates_bp.bp)
    app.register_blueprint(resetcode_bp.bp)
    app.register_blueprint(admin_bp.bp)

    # ---- 游戏战绩钩子：登录用户结果写门户 game_stats（匿名不受影响） ----
    db.RESULT_HOOK = game_bp.on_game_result

    # ---- 统一错误处理（禁止裸 500） ----
    @app.errorhandler(404)
    def not_found(_e):
        if request.path.startswith("/api/"):
            return common.err("接口不存在", "not_found", 404)
        return render_template_string(_ERROR_PAGE, code=404, msg="页面走丢了"), 404

    @app.errorhandler(500)
    def internal(_e):
        app.logger.exception("500 on %s %s", request.method, request.path)
        if request.path.startswith("/api/"):
            return common.err("服务器内部错误", "internal", 500)
        return render_template_string(_ERROR_PAGE, code=500, msg="服务器开小差了"), 500

    return app


# PythonAnywhere WSGI 入口（wsgi.py 引用）与本地直跑共用同一实例
app: Flask = create_app()

if __name__ == "__main__":
    db.init_db()               # 游戏旧表（幂等）
    portal_db.init_portal_db()  # 门户新表（幂等；线上按部署步骤用 migrate_part1.py）
    portal_db.init_part5_db()   # 第 5 期新表（幂等；线上用 migrate_part5.py）
    portal_db.init_part6_db()   # 第 6 期新表（幂等；线上用 migrate_part6.py）
    portal_db.ensure_pwd_changed_column()  # 第 6 期 users 补列（幂等）
    # 本地 http 开发：Secure cookie 会发不下去，这里关掉（线上 WSGI 不受影响）
    app.config["SESSION_COOKIE_SECURE"] = False
    # 监听所有网卡；debug=False 避免 reloader fork 子进程导致浏览器连接不稳定
    app.run(host="0.0.0.0", port=8050, debug=False, use_reloader=False)
