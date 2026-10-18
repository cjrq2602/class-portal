/* ============================================================
   activities.js —— 活动/比赛页逻辑
   职责：列表渲染（状态徽标/标签/已报人数）/ 类型与标签筛选 /
        一键报名与取消 / 首页倒计时由 index.html 单独处理
   依赖：components.js（fetchJSON/Toast/Skeleton/icon/fmtBeijing）
   ============================================================ */
"use strict";

let actType = "";
let actTag = "";
let ACT_LOGGED_IN = false;

function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

document.querySelectorAll(".empty__art").forEach((el) => {
    if (!el.innerHTML.trim()) el.innerHTML = icon("calendar", 40);
});

function tagHtml(t) {
    return `<span class="tag" style="background:${esc(t.color)}22;color:${esc(t.color)}">
        <i class="tag-dot" style="background:${esc(t.color)}"></i>${esc(t.name)}</span>`;
}

function actCard(a) {
    const full = a.reg_full;
    const btn = !ACT_LOGGED_IN
        ? `<a class="btn btn--outline btn--sm" href="/login">登录后报名</a>`
        : a.registered
            ? `<button class="btn btn--secondary btn--sm" data-cancel="${a.id}">已报名 · 取消</button>`
            : a.can_register
                ? `<button class="btn btn--primary btn--sm" data-reg="${a.id}">一键报名</button>`
                : `<button class="btn btn--outline btn--sm" disabled>${esc(a.status_label)}</button>`;
    return `
    <div class="card card--hover act-card${a.status === "ended" ? " act-card--ended" : ""}">
        <div class="act-card__head">
            <span class="tag ${"st-" + a.status}">${esc(a.status_label)}</span>
            <span class="tag tag--primary">${esc(a.atype_label)}</span>
            ${(a.tags || []).map(tagHtml).join("")}
            <span class="act-card__title" style="flex-basis:100%">${esc(a.title)}</span>
        </div>
        <div class="act-card__meta">
            <span data-act-icon="clock"></span>
            <span>${fmtBeijing(a.start_at)} ~ ${fmtBeijing(a.end_at)}</span>
            ${a.place ? `<span>📍 ${esc(a.place)}</span>` : ""}
        </div>
        ${a.detail ? `<div class="text-muted" style="font-size:var(--fs-13);line-height:1.7">${esc(a.detail)}</div>` : ""}
        <div class="act-card__foot">
            <span class="tag">已报 <b class="num">${a.reg_count}</b>${a.reg_limit ? " / " + a.reg_limit : ""} 人</span>
            ${full ? '<span class="tag tag--danger">名额已满</span>' : ""}
            ${a.reg_enabled && a.reg_deadline ? `<span class="text-faint" style="font-size:var(--fs-12)">报名截止 ${fmtBeijing(a.reg_deadline)}</span>` : ""}
            <span style="margin-left:auto">${btn}</span>
        </div>
    </div>`;
}

function renderFilters(tags) {
    const box = document.getElementById("act-filters");
    const typeChips = [["", "全部类型"], ["activity", "活动"], ["contest", "比赛"]]
        .map(([v, n]) => `<button class="chip${actType === v ? " chip--active" : ""}"
            data-type="${v}">${n}</button>`).join("");
    const tagChips = tags.map((t) => `<button class="chip${String(actTag) === String(t.id) ? " chip--active" : ""}"
        data-tag="${t.id}">${esc(t.name)}</button>`).join("");
    box.innerHTML = typeChips + (tags.length ? '<span class="text-faint" style="padding:0 4px">|</span>' : "") + tagChips;
    box.querySelectorAll("[data-type]").forEach((b) => {
        b.onclick = () => { actType = b.dataset.type; loadActs(); };
    });
    box.querySelectorAll("[data-tag]").forEach((b) => {
        b.onclick = () => { actTag = actTag === b.dataset.tag ? "" : b.dataset.tag; loadActs(); };
    });
}

function loadActs() {
    const box = document.getElementById("act-list");
    if (!box.dataset.loaded) Skeleton.cards(box, 3);
    const q = [];
    if (actType) q.push("atype=" + encodeURIComponent(actType));
    if (actTag) q.push("tag=" + encodeURIComponent(actTag));
    fetchJSON("/api/activities/list" + (q.length ? "?" + q.join("&") : "")).then((r) => {
        box.dataset.loaded = "1";
        renderFilters(r.tags || []);
        const list = r.list || [];
        if (!list.length) {
            box.innerHTML = '<div class="card card--hover"><div class="empty">' +
                icon("calendar", 44) + '<div class="empty__text">暂无活动，敬请期待</div></div></div>';
            return;
        }
        box.className = "";
        box.innerHTML = '<div class="stagger" style="display:grid;gap:var(--sp-12)">' +
            list.map(actCard).join("") + "</div>";
        document.querySelectorAll("[data-act-icon]").forEach((s) => {
            s.outerHTML = icon("clock", 14);
        });
        bindActions(box);
    }).catch(() => {
        box.dataset.loaded = "1";
        box.innerHTML = '<div class="empty">' + icon("alert", 40) +
            '<div class="empty__text">加载失败，请稍后重试</div></div>';
    });
}

function bindActions(box) {
    box.querySelectorAll("[data-reg]").forEach((b) => {
        b.onclick = async () => {
            try {
                const r = await fetchJSON("/api/activities/register", {
                    method: "POST", body: { id: parseInt(b.dataset.reg, 10) },
                });
                Toast.show(r.already ? "你已报名过该活动" : "报名成功", "success");
                loadActs();
            } catch (e) { /* 已提示 */ }
        };
    });
    box.querySelectorAll("[data-cancel]").forEach((b) => {
        b.onclick = async () => {
            const sure = await Modal.confirm({
                title: "取消报名",
                body: "确定取消该活动的报名吗？取消后可再次报名（若名额未满）。",
                okText: "取消报名", danger: false,
            });
            if (!sure) return;
            try {
                await fetchJSON("/api/activities/unregister", {
                    method: "POST", body: { id: parseInt(b.dataset.cancel, 10) },
                });
                Toast.show("已取消报名", "success");
                loadActs();
            } catch (e) { /* 已提示 */ }
        };
    });
}

fetchJSON("/api/auth/me").then((r) => { ACT_LOGGED_IN = !!r.logged_in; }).catch(() => {});
loadActs();
