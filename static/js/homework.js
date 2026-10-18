/* ============================================================
   homework.js —— 作业追踪页逻辑
   ★ 隐私约束：页面只渲染"我"的完成状态（my_done 来自服务端按当前用户查询），
     不请求也不展示任何他人的完成情况。
   依赖：components.js（fetchJSON/Toast/Skeleton/icon/fmtBeijing）
   ============================================================ */
"use strict";

let HW_SUBJECT = "";
let HW_STATE = "";
let HW_LOGGED_IN = false;

function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

document.querySelectorAll(".empty__art").forEach((el) => {
    if (!el.innerHTML.trim()) el.innerHTML = icon("file-text", 40);
});

/** 剩余时间文案（前端按 due_at 计算；<24h 视为"即将到期"） */
function dueText(h) {
    const ms = h.due_at * 1000 - Date.now();
    if (ms <= 0) return "已过期";
    const hours = ms / 3600000;
    if (hours < 1) return "不足 1 小时到期";
    if (hours < 24) return "剩余 " + Math.floor(hours) + " 小时";
    return "剩余 " + Math.floor(hours / 24) + " 天";
}

function hwItem(h) {
    const soon = !h.expired && (h.due_at * 1000 - Date.now()) < 86400000;
    const cls = ["hw-item"];
    if (h.expired) cls.push("hw-item--expired");
    else if (soon) cls.push("hw-item--soon");
    if (h.done) cls.push("hw-item--done");
    const check = HW_LOGGED_IN
        ? `<input type="checkbox" class="hw-item__check" data-hw="${h.id}"
             ${h.done ? "checked" : ""} title="我已完成（仅自己可见）">`
        : `<span class="text-faint" title="登录后可勾选" style="font-size:18px">□</span>`;
    return `
    <div class="${cls.join(" ")}">
        ${check}
        <div class="hw-item__body">
            <div class="hw-item__content">${esc(h.content)}</div>
            <div class="hw-item__meta">
                <span class="tag tag--primary">${esc(h.subject)}</span>
                <span>截止 ${fmtBeijing(h.due_at)}</span>
                <span class="${h.expired ? "text-danger" : soon ? "text-danger" : ""}">${dueText(h)}</span>
                ${h.expired ? '<span class="tag">已过期</span>' : ""}
                ${h.done ? '<span class="tag tag--success">我已完成</span>' : ""}
            </div>
        </div>
    </div>`;
}

function renderFilters(subjects) {
    const box = document.getElementById("hw-filters");
    const subChips = ['<button class="chip' + (HW_SUBJECT === "" ? " chip--active" : "") +
        '" data-sub="">全部科目</button>']
        .concat(subjects.map((s) => `<button class="chip${HW_SUBJECT === s ? " chip--active" : ""}"
            data-sub="${esc(s)}">${esc(s)}</button>`)).join("");
    const stChips = [["", "全部状态"], ["active", "未过期"], ["expired", "已过期"]]
        .map(([v, n]) => `<button class="chip${HW_STATE === v ? " chip--active" : ""}"
            data-state="${v}">${n}</button>`).join("");
    box.innerHTML = subChips + '<span class="text-faint" style="padding:0 4px">|</span>' + stChips;
    box.querySelectorAll("[data-sub]").forEach((b) => {
        b.onclick = () => { HW_SUBJECT = b.dataset.sub; loadHw(); };
    });
    box.querySelectorAll("[data-state]").forEach((b) => {
        b.onclick = () => { HW_STATE = b.dataset.state; loadHw(); };
    });
}

function loadHw() {
    const box = document.getElementById("hw-list");
    if (!box.dataset.loaded) Skeleton.inject(box, 4, false);
    const q = [];
    if (HW_SUBJECT) q.push("subject=" + encodeURIComponent(HW_SUBJECT));
    if (HW_STATE) q.push("state=" + encodeURIComponent(HW_STATE));
    fetchJSON("/api/homework/list" + (q.length ? "?" + q.join("&") : "")).then((r) => {
        box.dataset.loaded = "1";
        HW_LOGGED_IN = !!r.logged_in;
        renderFilters(r.subjects || []);
        const list = r.list || [];
        if (!list.length) {
            box.innerHTML = '<div class="empty">' + icon("file-text", 44) +
                '<div class="empty__text">暂无作业，好好休息</div></div>';
            return;
        }
        box.classList.remove("stagger");
        void box.offsetWidth;
        box.innerHTML = list.map(hwItem).join("");
        box.classList.add("stagger");
        bindChecks(box);
    }).catch(() => {
        box.dataset.loaded = "1";
        box.innerHTML = '<div class="empty">' + icon("alert", 40) +
            '<div class="empty__text">加载失败，请稍后重试</div></div>';
    });
}

function bindChecks(box) {
    box.querySelectorAll("[data-hw]").forEach((cb) => {
        cb.onchange = async () => {
            const id = parseInt(cb.dataset.hw, 10);
            const done = cb.checked;
            try {
                await fetchJSON("/api/homework/toggle_done", {
                    method: "POST", body: { id, done },
                });
                Toast.show(done ? "已标记完成（仅自己可见）" : "已取消完成标记", "success", 1400);
                loadHw();
            } catch (e) {
                cb.checked = !done;   // 失败回滚勾选状态
            }
        };
    });
}

loadHw();
