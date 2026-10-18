/* ============================================================
   quote.js —— 每日一句页
   职责：今日一句横幅 / 全班句子列表（点赞，先审后显）/ 投稿
   依赖：components.js（fetchJSON/Toast/Skeleton/icon/bindCharCount）
   ============================================================ */
"use strict";

const QT = window.__QUOTE__ || { logged_in: false, max_content: 100 };

function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

document.querySelectorAll(".empty__art").forEach((el) => {
    if (!el.innerHTML.trim()) el.innerHTML = icon("heart", 40);
});

/** 引用样式横幅（今日一句） */
function renderToday(item) {
    const box = document.getElementById("quote-today");
    if (!item) {
        box.innerHTML = '<div class="empty">' + icon("heart", 36) +
            '<div class="empty__text">还没有已审核的句子，欢迎投出第一句</div></div>';
        return;
    }
    box.innerHTML = `
        <blockquote>
            <div class="quote-banner__mark">❝</div>
            <div class="quote-banner__text">${esc(item.content)}</div>
            <div class="quote-banner__meta">— ${esc(item.author)}
                <span class="quote-like" data-today-like="${item.id}">
                    ${icon("heart", 14)} <b class="num">${item.like_count}</b></span>
            </div>
        </blockquote>`;
    const btn = box.querySelector("[data-today-like]");
    if (btn) {
        if (item.liked) btn.classList.add("quote-like--on");
        btn.onclick = () => likeQuote(item.id, btn);
    }
}

async function likeQuote(id, el) {
    if (!QT.logged_in) { Toast.show("登录后才能点赞", "warning"); return; }
    try {
        const r = await fetchJSON("/api/quote/like", { method: "POST", body: { id } });
        if (r.liked && window.Sfx) Sfx.play("ding");   // 点赞"叮"
        el.classList.toggle("quote-like--on", r.liked);
        const b = el.querySelector("b");
        if (b) b.textContent = r.like_count;
        el.classList.remove("quote-like--pop");
        void el.offsetWidth;
        el.classList.add("quote-like--pop");
    } catch (e) { /* 已提示 */ }
}

function quoteCard(q) {
    return `
    <div class="res-item">
        <span class="res-item__icon" style="color:var(--c-primary)">${icon("heart", 20)}</span>
        <div class="res-item__body">
            <div class="res-item__title">${esc(q.content)}</div>
            <div class="res-item__meta">
                <span>— ${esc(q.author)}</span>
                <span>${fmtBeijing(q.created_at)}</span>
                ${q.mine ? '<span class="tag tag--primary">我投稿的</span>' : ""}
            </div>
        </div>
        <button class="tucao-act${q.liked ? " tucao-act--liked" : ""}" data-like="${q.id}">
            ${icon("heart", 15)}<span class="num">${q.like_count}</span>
        </button>
    </div>`;
}

function loadList() {
    const box = document.getElementById("quote-list");
    if (!box.dataset.loaded) Skeleton.inject(box, 4, false);
    fetchJSON("/api/quote/list").then((r) => {
        box.dataset.loaded = "1";
        const list = r.list || [];
        document.getElementById("quote-total").textContent = `已审核 ${r.total} 句`;
        if (!list.length) {
            box.innerHTML = '<div class="empty">' + icon("heart", 42) +
                '<div class="empty__text">还没有句子，来投第一句吧</div></div>';
            return;
        }
        box.classList.remove("stagger");
        void box.offsetWidth;
        box.innerHTML = list.map(quoteCard).join("");
        box.classList.add("stagger");
        box.querySelectorAll("[data-like]").forEach((b) => {
            b.onclick = () => likeQuote(parseInt(b.dataset.like, 10), b);
        });
    }).catch(() => {
        box.dataset.loaded = "1";
        box.innerHTML = '<div class="empty">' + icon("alert", 40) +
            '<div class="empty__text">加载失败，请稍后重试</div></div>';
    });
}

/* 投稿 */
if (QT.logged_in) {
    bindCharCount(document.getElementById("quote-input"),
                  document.getElementById("quote-count"));
    document.getElementById("quote-submit").onclick = async () => {
        const input = document.getElementById("quote-input");
        const content = input.value.trim();
        if (content.length < 2) { Toast.show("至少写两个字吧", "warning"); return; }
        const btn = document.getElementById("quote-submit");
        btn.classList.add("btn--loading");
        try {
            const r = await fetchJSON("/api/quote/submit", {
                method: "POST",
                body: { content, author: document.getElementById("quote-author").value.trim() },
            });
            Toast.show(r.message || "已提交审核", "info");
            input.value = "";
            document.getElementById("quote-count").textContent = `0 / ${QT.max_content}`;
        } catch (e) { /* 已提示 */ }
        btn.classList.remove("btn--loading");
    };
}

fetchJSON("/api/quote/today").then((r) => renderToday(r.item)).catch(() => renderToday(null));
loadList();
