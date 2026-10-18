/* ============================================================
   duty.js —— 值日表交互
   职责：周视图渲染（今天整列高亮）/ 管理端逐日编辑 /
        「一键顺延轮换」Modal 二次确认（服务端也要求 confirm=true）
   依赖：components.js（fetchJSON/Modal/Toast/icon）
   ============================================================ */
"use strict";

let DUTY_CAN_MANAGE = false;

function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

document.querySelectorAll(".empty__art").forEach((el) => {
    if (!el.innerHTML.trim()) el.innerHTML = icon("grid", 40);
});

function dutyCol(d, todayDow) {
    const isToday = d.day === todayDow;
    const body = d.has_data
        ? `<div class="duty-col__group">${esc(d.group_name || "（未命名小组）")}</div>
           <div class="duty-col__members">${esc(d.members || "—")}</div>
           ${d.task ? `<div class="duty-col__task">🧹 ${esc(d.task)}</div>` : ""}`
        : '<div class="text-faint" style="font-size:var(--fs-12)">暂无安排</div>';
    return `
    <div class="duty-col${isToday ? " duty-col--today" : ""}">
        <div class="duty-col__day">
            ${esc(d.day_name)}
            ${isToday ? '<span class="tag tag--primary">今天</span>' : ""}
        </div>
        ${body}
        ${DUTY_CAN_MANAGE ? `<button class="btn btn--ghost btn--sm mt-8"
            data-edit="${d.day}">${icon("edit", 14)} 编辑</button>` : ""}
    </div>`;
}

function renderWeek(r) {
    const grid = document.getElementById("duty-grid");
    grid.innerHTML = (r.week || []).map((d) => dutyCol(d, r.today_dow)).join("");
    // 今日提示条
    const bar = document.getElementById("duty-today-bar");
    if (bar) {
        bar.innerHTML = r.today
            ? `<div class="duty-bar">
                 <span class="duty-bar__icon">${icon("grid", 20)}</span>
                 <div><b>今天值日：${esc(r.today.group_name || "未命名小组")}</b>
                 <div class="text-muted" style="font-size:var(--fs-12)">成员：${esc(r.today.members || "—")}
                 ${r.today.task ? " · 任务：" + esc(r.today.task) : ""}</div></div>
               </div>`
            : "";
    }
    if (DUTY_CAN_MANAGE) {
        grid.querySelectorAll("[data-edit]").forEach((b) => {
            b.onclick = () => openEdit(parseInt(b.dataset.edit, 10), r);
        });
    }
}

function openEdit(day, r) {
    const cur = (r.week || [])[day] || { group_name: "", members: "", task: "", day_name: "" };
    Modal.open({
        title: "编辑值日 · " + esc(cur.day_name),
        body: `
            <div class="field"><label class="field__label">小组名</label>
                <input class="input" id="duty-group" maxlength="30" value="${esc(cur.group_name)}"
                    placeholder="如：第三小组"></div>
            <div class="field"><label class="field__label">成员（多人用、分隔）</label>
                <input class="input" id="duty-members" maxlength="200" value="${esc(cur.members)}"
                    placeholder="如：张三、李四、王五"></div>
            <div class="field"><label class="field__label">任务</label>
                <input class="input" id="duty-task" maxlength="100" value="${esc(cur.task)}"
                    placeholder="如：扫地 + 擦黑板"></div>`,
        foot: `<button class="btn btn--ghost" id="duty-clear">清空该日</button>
               <button class="btn btn--secondary" onclick="Modal.close()">取消</button>
               <button class="btn btn--primary" id="duty-save">保存</button>`,
    });
    document.getElementById("duty-save").onclick = async () => {
        try {
            await fetchJSON("/api/admin/duty/set", {
                method: "POST",
                body: {
                    day_of_week: day,
                    group_name: document.getElementById("duty-group").value.trim(),
                    members: document.getElementById("duty-members").value.trim(),
                    task: document.getElementById("duty-task").value.trim(),
                },
            });
            Toast.show("已保存", "success");
            Modal.close();
            loadDuty();
        } catch (e) { /* 已提示 */ }
    };
    document.getElementById("duty-clear").onclick = async () => {
        const sure = await Modal.confirm({
            title: "清空该日值日", body: "确定清空这一天的值日安排吗？", okText: "清空",
        });
        if (!sure) return;
        try {
            await fetchJSON("/api/admin/duty/delete", {
                method: "POST", body: { day_of_week: day },
            });
            Toast.show("已清空", "success");
            Modal.close();
            loadDuty();
        } catch (e) { /* 已提示 */ }
    };
}

function loadDuty() {
    fetchJSON("/api/duty/week").then(renderWeek).catch(() => {
        const grid = document.getElementById("duty-grid");
        if (grid) grid.innerHTML = '<div class="empty">' + icon("alert", 40) +
            '<div class="empty__text">加载失败，请稍后重试</div></div>';
    });
}

/* 一键顺延轮换：必须先二次确认，再带 confirm=true 调服务端 */
const rotateBtn = document.getElementById("duty-rotate");
if (rotateBtn) {
    rotateBtn.onclick = async () => {
        const sure = await Modal.confirm({
            title: "一键顺延轮换",
            body: `将把本周每组的值日内容<b>后挪一天</b>（周六→周日、<b>周日→周一</b>），
                   共 7 天全部平移一次。此操作会记入审计日志。确定执行吗？`,
            okText: "确认轮换",
        });
        if (!sure) return;
        try {
            const r = await fetchJSON("/api/admin/duty/rotate", {
                method: "POST", body: { confirm: true },
            });
            Toast.show(`已顺延轮换（影响 ${r.affected} 天）`, "success");
            loadDuty();
        } catch (e) { /* 已提示 */ }
    };
}

fetchJSON("/api/auth/me").then((r) => {
    DUTY_CAN_MANAGE = (r.perms || []).indexOf("duty.manage") >= 0;
}).catch(() => {});
loadDuty();
