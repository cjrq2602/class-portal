"""
blueprints/duty.py —— 值日表（/duty + /api/duty/* + 后台管理）
============================================================
要点：
1. 周视图：周一~周日 7 行，每行 = 小组名 + 成员 + 任务；今天那列整列高亮（前端按北京时间判断星期）。
2. duty.manage 可增删改；「一键顺延轮换」把每组内容后挪一天（周日→周一），
   由前端 Modal 二次确认后调用，服务端写审计日志。
3. 首页顶部提示条"今天值日：XX组·成员名"（北京时间星期查表；无数据则不显示）。
"""

from __future__ import annotations

from typing import Any, Optional

from flask import Blueprint, render_template, request

import common
import portal_db
from blueprints import auth as auth_bp
from blueprints.admin import require_perm

bp: Blueprint = Blueprint("duty", __name__)

DOW_NAMES: list[str] = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def week_view() -> list[dict[str, Any]]:
    """一周 7 行（缺失的日期补空行，保证前端永远渲染 7 列）。"""
    rows = portal_db.duty_week()
    out: list[dict[str, Any]] = []
    for d in range(7):
        r = rows.get(d)
        out.append({
            "day": d, "day_name": DOW_NAMES[d],
            "group_name": str(r["group_name"]) if r else "",
            "members": str(r["members"]) if r else "",
            "task": str(r["task"]) if r else "",
            "has_data": bool(r and (str(r["group_name"]).strip()
                                    or str(r["members"]).strip())),
        })
    return out


# ============================================================
# 页面
# ============================================================
@bp.route("/duty")
def duty_page():
    """值日表页（服务端渲染 7 列骨架，前端负责高亮今天）。"""
    bj = common.beijing_now()
    return render_template("duty.html", week=week_view(),
                           today_dow=bj.weekday(),
                           can_manage="duty.manage" in auth_bp.user_perms())


# ============================================================
# API
# ============================================================
@bp.route("/api/duty/week")
@common.api_wrap
def api_week():
    """
    一周值日表。返回：{ok, week:[7 行], today_dow, today}
    权限：公开。today_dow 按北京时间星期（Monday=0）。
    """
    bj = common.beijing_now()
    dow: int = bj.weekday()
    wk = week_view()
    return common.ok({"week": wk, "today_dow": dow,
                      "today": wk[dow] if wk[dow]["has_data"] else None})


@bp.route("/api/duty/today")
@common.api_wrap
def api_today():
    """
    首页提示条用：今天值日的小组与成员。
    返回：{ok, item: {group_name, members, task, day_name} | null}
    权限：公开。无数据时 item=null（前端不显示提示条）。
    """
    row = portal_db.duty_today()
    if row is None or not (str(row["group_name"]).strip() or str(row["members"]).strip()):
        return common.ok({"item": None})
    bj = common.beijing_now()
    return common.ok({"item": {
        "group_name": str(row["group_name"]), "members": str(row["members"]),
        "task": str(row["task"]), "day_name": DOW_NAMES[bj.weekday()],
    }})


# ============================================================
# 后台（duty.manage）
# ============================================================
@bp.route("/api/admin/duty/set", methods=["POST"])
@require_perm("duty.manage")
@common.api_wrap
def api_set():
    """
    设置某天的值日。参数：{day_of_week, group_name, members, task}
    权限：duty.manage。写审计。
    """
    body: dict = request.get_json(silent=True) or {}
    try:
        d: int = int(body.get("day_of_week", -1))
    except (TypeError, ValueError):
        return common.err("星期不合法")
    if d < 0 or d > 6:
        return common.err("星期必须是 0（周一）~ 6（周日）")
    group_name: str = str(body.get("group_name", "")).strip()[:30]
    members: str = str(body.get("members", "")).strip()[:200]
    task: str = str(body.get("task", "")).strip()[:100]
    portal_db.duty_set(d, group_name, members, task)
    portal_db.add_audit(auth_bp.current_user_id(), "duty_set", DOW_NAMES[d],
                        f"{group_name}/{members}"[:100], common.client_ip())
    return common.ok()


@bp.route("/api/admin/duty/delete", methods=["POST"])
@require_perm("duty.manage")
@common.api_wrap
def api_delete():
    """清空某天的值日。参数：{day_of_week}。权限：duty.manage"""
    body: dict = request.get_json(silent=True) or {}
    try:
        d: int = int(body.get("day_of_week", -1))
    except (TypeError, ValueError):
        return common.err("星期不合法")
    if d < 0 or d > 6:
        return common.err("星期必须是 0~6")
    portal_db.duty_delete(d)
    portal_db.add_audit(auth_bp.current_user_id(), "duty_clear", DOW_NAMES[d], "",
                        common.client_ip())
    return common.ok()


@bp.route("/api/admin/duty/rotate", methods=["POST"])
@require_perm("duty.manage")
@common.api_wrap
def api_rotate():
    """
    一键顺延轮换：每组后挪一天，周日→周一。
    权限：duty.manage。**危险操作**：前端必须先弹 Modal 二次确认；
    服务端同样要求 confirm=true 字段，双重保险。写审计。
    """
    body: dict = request.get_json(silent=True) or {}
    if not bool(body.get("confirm")):
        return common.err("该操作需要二次确认（confirm=true）", "need_confirm")
    affected: int = portal_db.duty_rotate()
    portal_db.add_audit(auth_bp.current_user_id(), "duty_rotate", "week",
                        f"affected_days={affected}", common.client_ip())
    return common.ok({"affected": affected})
