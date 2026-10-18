"""
blueprints/main.py —— 首页仪表盘 / 我的 / 互动聚合页 / 首页数据接口
==================================================================
路由：
    GET /                 首页仪表盘（Hero 问候 + 快捷宫格 + 今日总结摘要 + 最新新闻 + 热门吐槽 + 游戏横幅）
    GET /me               我的（个人信息 + 改密 + 退出）
    GET /hub              互动聚合页（自动列出已上线的互动板块，数据来自 blocks.py）
    GET /api/home/announcements   最新公告
说明：
  • 问候语/日期由服务端按北京时间渲染（common.beijing_now，UTC+8 换算）；
  • 首页会触发一次 TTL 限流的新闻同步与总结惰性兜底（幂等、失败降级）；
  • 板块清单全部取自 blocks.py，后续步骤加板块无需改本文件。
"""

from __future__ import annotations

from typing import Any, Optional

from flask import Blueprint, render_template

import blocks
import common
import digest_service
import news_sync
import portal_db
from blueprints import auth as auth_bp

bp: Blueprint = Blueprint("main", __name__)


def _bj_today() -> dict[str, str]:
    """北京时间日期与星期（Hero 展示）。"""
    bj = common.beijing_now()
    weekdays: list[str] = ["一", "二", "三", "四", "五", "六", "日"]
    return {
        "date": bj.strftime("%Y年%m月%d日"),
        "weekday": "星期" + weekdays[bj.weekday()],
        "iso": bj.strftime("%Y-%m-%d"),
    }


@common.ttl_cache(60)
def _home_public_data() -> dict[str, Any]:
    """
    首页【公共】聚合数据（60 秒进程内 TTL 缓存，降 CPU —— 任务二）。
    只放与访问者无关的内容：最新新闻 / 今日总结 / 热门吐槽 / 值日 /
    最近活动 / 荣誉精选 / 每日一句 / 进行中的挑战（公共字段）。
    用户专属数据（作业完成态、我的打卡）不进缓存，index() 实时补充。
    """
    news_rows, _news_total = portal_db.list_news(page=1, per_page=5)
    today_view: dict[str, Any] = digest_service.digest_view("today")
    # 0~9 点今日总结尚未生成时，首页回退展示昨日总结，避免整卡空白
    yesterday_view: dict[str, Any] = digest_service.digest_view("yesterday")

    duty_row = portal_db.duty_today()
    duty_item: Optional[dict[str, Any]] = None
    if duty_row is not None and (str(duty_row["group_name"]).strip()
                                or str(duty_row["members"]).strip()):
        duty_item = {"group_name": str(duty_row["group_name"]),
                     "members": str(duty_row["members"]),
                     "task": str(duty_row["task"])}

    from blueprints import quote as quote_bp
    today_quote: Optional[dict[str, Any]] = None
    try:
        today_quote = quote_bp.today_quote()
    except Exception:  # noqa: BLE001 —— 首页绝不因小功能失败而 500
        today_quote = None

    today_str: str = common.bj_date_str()
    try:
        portal_db.chal_archive_expired(today_str)
    except Exception:  # noqa: BLE001
        pass
    live_challenges = [r for r in portal_db.chal_list()
                       if str(r["start_date"]) <= today_str <= str(r["end_date"])]
    challenge_pub: Optional[dict[str, Any]] = None
    if live_challenges:
        c0 = live_challenges[0]
        challenge_pub = {"id": int(c0["id"]), "name": str(c0["name"]),
                         "icon": str(c0["icon"]), "count": len(live_challenges)}

    from blueprints import activities as act_bp
    live_acts = [r for r in portal_db.act_public_list()
                 if act_bp.act_status(r) != "ended"]
    live_acts.sort(key=lambda x: (0 if act_bp.act_status(x) == "ongoing" else 1,
                                 float(x["start_at"])))
    act_item: Optional[dict[str, Any]] = None
    if live_acts:
        a0 = live_acts[0]
        st: str = act_bp.act_status(a0)
        act_item = {"id": int(a0["id"]), "title": str(a0["title"]),
                    "status": st, "status_label": act_bp.STATUS_LABEL.get(st, st),
                    "atype_label": act_bp.TYPE_LABEL.get(str(a0["atype"]), "活动"),
                    "target_ts": float(a0["end_at"]) if st == "ongoing"
                    else float(a0["start_at"])}

    honor_row = portal_db.honor_pinned_one()
    return {
        "latest_news": [{
            "id": int(r["id"]), "source": str(r["source"]), "title": str(r["title"]),
            "url": str(r["url"]), "published_at": float(r["published_at"]),
        } for r in news_rows],
        "digest_card": today_view,
        "digest_card_yesterday": yesterday_view,
        "hot_tucao": portal_db.tucao_hot(limit=3),
        "duty_item": duty_item,
        "act_item": act_item,
        "honor": ({
            "id": int(honor_row["id"]), "title": str(honor_row["title"]),
            "owner": str(honor_row["owner"]), "date": str(honor_row["hdate"]),
            "description": str(honor_row["description"]),
            "has_image": bool(str(honor_row["image_path"])),
            "is_pinned": bool(honor_row["is_pinned"]),
        } if honor_row is not None else None),
        "today_quote": today_quote,
        "challenge_pub": challenge_pub,
        "hw_latest": portal_db.hw_latest(limit=3),
        "today": today_str,
    }


@bp.route("/")
def index():
    """
    首页仪表盘。
    part2 槽位：左列 = 今日总结摘要卡 + 最新新闻；右列 = 进行中活动 + 热门吐槽。
    part3 槽位：顶部值日提示条 / Hero 活动倒计时 / 左列最新作业卡 / 右列荣誉精选。
    公共数据走 60 秒 TTL 缓存（_home_public_data）；用户专属字段实时计算。
    """
    user = auth_bp.current_user()
    logged_in: bool = user is not None and not bool(user["is_banned"])
    stats = portal_db.get_game_stats(int(user["id"])) if logged_in else None
    uid: Optional[int] = int(user["id"]) if logged_in else None

    # 新闻：TTL 内直接读缓存；过期则尝试同步（内部有锁与异常兜底）
    try:
        news_sync.sync_news()
    except Exception:  # noqa: BLE001 —— 首页绝不因同步失败而 500
        pass
    # 总结：惰性兜底（只在"已过时刻却缺失"时活）
    try:
        digest_service.ensure_digests()
    except Exception:  # noqa: BLE001
        pass

    pub: dict[str, Any] = _home_public_data()
    today_str: str = str(pub["today"])

    # ---- 第六期：网站更新（未读横幅 + 最新 release 摘要卡） ----
    unread_latest = portal_db.update_unread_latest(uid) if uid else None
    update_banner: Optional[dict[str, Any]] = None
    if unread_latest is not None:
        update_banner = {
            "id": int(unread_latest["id"]),
            "title": str(unread_latest["title"]),
            "update_type": str(unread_latest["update_type"]),
            "version_tag": str(unread_latest["version_tag"]),
        }
    latest_release = portal_db.update_latest_release()
    release_card: Optional[dict[str, Any]] = None
    if latest_release is not None:
        release_card = {
            "id": int(latest_release["id"]),
            "title": str(latest_release["title"]),
            "version_tag": str(latest_release["version_tag"]),
            "publish_at": float(latest_release["publish_at"]),
        }

    # ---- 用户专属：作业完成态 / 我的打卡（实时，不进缓存） ----
    hw_done: Optional[set[int]] = portal_db.hw_my_done_ids(uid) if uid else None
    challenge_pub = pub["challenge_pub"]
    checkin_item: Optional[dict[str, Any]] = None
    if challenge_pub is not None:
        cid0: int = int(challenge_pub["id"])
        checkin_item = {
            "id": cid0, "name": challenge_pub["name"], "icon": challenge_pub["icon"],
            "checked_today": portal_db.ck_has(uid, cid0, today_str) if uid else False,
            "my_streak": portal_db.ck_streak(uid, cid0, today_str) if uid else 0,
            "count": int(challenge_pub["count"]),
        }

    homework_list: list[dict[str, Any]] = [
        {"id": int(r["id"]), "subject": str(r["subject"]), "content": str(r["content"]),
         "due_at": float(r["due_at"]),
         "done": bool(hw_done is not None and int(r["id"]) in hw_done)}
        for r in pub["hw_latest"]]
    hw_pending: int = sum(1 for h in homework_list if not h["done"])

    # ---- 班级动态：把「活动 / 荣誉 / 吐槽 / 打卡」合并成一条时间流 ----
    # 首页不再摆四张并列卡片，减少视觉噪音；每项仍可点进对应板块。
    feed: list[dict[str, Any]] = []
    if pub["act_item"]:
        a = pub["act_item"]
        feed.append({
            "kind": "activity", "icon": "calendar", "color": "#F59E0B",
            "title": a["title"],
            "meta": f"{a['atype_label']} · {a['status_label']}",
            "href": "/activities",
        })
    if pub["honor"]:
        h = pub["honor"]
        feed.append({
            "kind": "honor", "icon": "trophy", "color": "#EAB308",
            "title": h["title"],
            "meta": (h["owner"] or "班级荣誉"),
            "href": "/honors",
        })
    for t in (pub["hot_tucao"] or [])[:2]:
        feed.append({
            "kind": "tucao", "icon": "message", "color": "#EC4899",
            "title": str(t["content"])[:42],
            "meta": f"{t['display_name']} · ❤️ {t['like_count']}",
            "href": "/tucao",
        })
    if checkin_item is not None:
        feed.append({
            "kind": "checkin", "icon": "target", "color": "#10B981",
            "title": checkin_item["name"],
            "meta": ("今天已打卡 ✓" if checkin_item["checked_today"]
                     else "今天还没打卡") if uid else
                    f"{checkin_item['count']} 个挑战进行中",
            "href": "/checkin",
        })

    # 首页总结卡：优先今日；0~9 点今日未生成时回退昨日，标题同步切换
    digest_card: dict[str, Any] = pub["digest_card"]
    digest_title: str = "今日总结"
    if not digest_card.get("exists"):
        y = pub.get("digest_card_yesterday") or {}
        if y.get("exists"):
            digest_card = y
            digest_title = "昨日总结"

    return render_template(
        "index.html",
        greeting=common.greeting_by_hour(),
        bj=_bj_today(),
        me=user if logged_in else None,
        game_wins=int(stats["wins"]) if stats else 0,
        game_losses=int(stats["losses"]) if stats else 0,
        home_primary=blocks.home_primary(),
        home_more=blocks.home_more(),
        latest_news=pub["latest_news"],
        digest_card=digest_card,
        digest_title=digest_title,
        ready_at=common.today_digest_ready_at(),
        hot_tucao=pub["hot_tucao"],
        duty_item=pub["duty_item"],
        act_item=pub["act_item"],
        homework=homework_list,
        hw_pending=hw_pending,
        feed=feed,
        honor=pub["honor"],
        today_quote=pub["today_quote"],
        checkin_item=checkin_item,
        today=today_str,
        update_banner=update_banner,
        release_card=release_card,
    )


@bp.route("/me")
def me_page():
    """
    个人中心：我的打卡日历 / 我的资料上传 / 我的游戏战绩 / 我的投稿状态 + 设置。
    服务端先渲染首屏要点数字，明细列表由 me.js 拉取（避免首屏过多查询）。
    """
    user = auth_bp.current_user()
    if user is None or bool(user["is_banned"]):
        return render_template("me.html", me=None)
    uid: int = int(user["id"])
    stats = portal_db.get_game_stats(uid)
    games: int = (int(stats["wins"]) + int(stats["losses"])) if stats else 0
    wins: int = int(stats["wins"]) if stats else 0
    pending_q: int = len(portal_db.quote_list(status="pending", user_id=uid))
    approved_q: int = len(portal_db.quote_list(status="approved", user_id=uid))
    my_all_dates: set[str] = portal_db.ck_my_all_dates(uid)
    this_month: str = common.bj_date_str()[:7]
    # 第六期：面板按权限渲染（admin.full 已展开为全部 code）
    perms: list[str] = auth_bp.user_perms()
    is_admin: bool = bool(perms)  # 有任何权限即为管理员/superadmin
    return render_template(
        "me.html",
        me=user,
        created_str=common.fmt_beijing(float(user["created_at"]), with_time=False),
        game_wins=wins, game_losses=games - wins, game_games=games,
        win_rate=(round(wins * 100.0 / games, 1) if games else 0.0),
        quote_pending=pending_q, quote_approved=approved_q,
        checkin_days=len([d for d in my_all_dates if d.startswith(this_month)]),
        checkin_total=len(my_all_dates),
        today=common.bj_date_str(),
        me_perms=perms,
        is_admin=is_admin,
        unread_updates=portal_db.update_unread_count(uid),
    )


@bp.route("/api/me/summary")
@common.api_wrap
def api_me_summary():
    """
    个人中心汇总数据（供前端刷新/骨架屏后回填）。
    返回：{ok, logged_in, user?, game:{...}, quote:{...}, checkin:{...}, resource:{...}}
    权限：需登录。
    """
    user = auth_bp.current_user()
    if user is None or bool(user["is_banned"]):
        return common.ok({"logged_in": False})
    uid: int = int(user["id"])
    stats = portal_db.get_game_stats(uid)
    games: int = (int(stats["wins"]) + int(stats["losses"])) if stats else 0
    wins: int = int(stats["wins"]) if stats else 0
    my_all_dates: set[str] = portal_db.ck_my_all_dates(uid)
    this_month: str = common.bj_date_str()[:7]
    res_rows = [r for r in portal_db.res_list(limit=1000)
                if int(r["uploader_id"] or -1) == uid]
    # 第六期：红点状态并入本接口一次返回（导航/首页不加额外轮询）
    latest_rel = portal_db.update_latest_release()
    return common.ok({
        "logged_in": True,
        "user": {"id": uid, "username": user["username"], "nickname": user["nickname"],
                 "role_display": user["role_display"],
                 "perms": auth_bp.user_perms()},
        "game": {"wins": wins, "losses": games - wins, "games": games,
                 "win_rate": round(wins * 100.0 / games, 1) if games else 0.0},
        "quote": {"pending": len(portal_db.quote_list(status="pending", user_id=uid)),
                  "approved": len(portal_db.quote_list(status="approved", user_id=uid)),
                  "rejected": len(portal_db.quote_list(status="rejected", user_id=uid))},
        "checkin": {"days_this_month": len([d for d in my_all_dates
                                            if d.startswith(this_month)]),
                    "total_days": len(my_all_dates)},
        "resource": {"count": len(res_rows)},
        "unread_updates": {
            "count": portal_db.update_unread_count(uid),
            "latest_version": str(latest_rel["version_tag"]) if latest_rel else "",
        },
    })


@bp.route("/hub")
def hub_page():
    """
    互动聚合页（手机底部 Tab「互动」）。
    自动收录 blocks.py 中 hub=True 且已上线的板块。
    """
    return render_template("hub.html", items=blocks.hub_items())


@bp.route("/api/home/announcements")
@common.api_wrap
def api_announcements():
    """
    最新 3 条公告。返回：{ok, list:[{id,title,content,author,created_at}]}
    权限：公开。时间返回 Unix 秒（UTC），前端按 Asia/Shanghai 格式化展示。
    """
    rows = portal_db.list_announcements(limit=3)
    return common.ok({"list": [
        {"id": int(r["id"]), "title": r["title"], "content": r["content"],
         "author": r["author"] or "管理员", "created_at": float(r["created_at"])}
        for r in rows
    ]})
