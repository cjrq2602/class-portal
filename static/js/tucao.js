/* ============================================================
   tucao.js —— 吐槽墙交互
   职责：列表渲染（每页 20）/ 发帖 / 点赞（爱心缩放微动效）/ 举报 / 排序切换
   安全：所有用户内容一律经 esc() 转义后写入 DOM（防 XSS）
   依赖：components.js（fetchJSON/Toast/Skeleton/Pagination/fmtBeijing/avatarHtml/icon/bindCharCount）
   ============================================================ */
"use strict";

const TC = window.__TUCAO__ || { logged_in: false, mode: "post_first", max_content: 300 };
let tcPage = 1;
let tcSort = "time";

function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* 空态小插画补位（零外链） */
document.querySelectorAll(".empty__art").forEach((el) => {
    if (!el.innerHTML.trim()) el.innerHTML = icon("message", 40);
});

/* 排序切换 */
function syncSortChips() {
    document.getElementById("sort-time").classList.toggle("chip--active", tcSort === "time");
    document.getElementById("sort-hot").classList.toggle("chip--active", tcSort === "hot");
}
document.getElementById("sort-time").onclick = () => { tcSort = "time"; tcPage = 1; syncSortChips(); loadTucao(); };
document.getElementById("sort-hot").onclick = () => { tcSort = "hot"; tcPage = 1; syncSortChips(); loadTucao(); };
syncSortChips();

/* ---------- 渲染单楼 ---------- */
function postHtml(p) {
    const pending = p.status === "pending";
    const likeCls = "tucao-act" + (p.liked ? " tucao-act--liked" : "");
    return `
    <div class="tucao-post${pending ? " tucao-post--pending" : ""}" data-id="${p.id}">
        <div class="tucao-post__head">
            ${p.persona === "real" ? avatarHtml(p.display_name, "avatar--sm")
                : '<span class="tag">' + (p.persona === "anon" ? "🎭 " : "🕶️ ") + esc(p.display_name) + "</span>"}
            ${p.persona === "real" ? "<b>" + esc(p.display_name) + "</b>" : ""}
            ${p.pinned ? '<span class="tag tag--warning">置顶</span>' : ""}
            ${pending ? '<span class="tag tag--warning">审核中 · 仅自己可见</span>' : ""}
            ${p.is_mine ? '<span class="tag tag--primary">我</span>' : ""}
            <span class="text-faint" style="font-size:var(--fs-12);margin-left:auto">
                ${fmtBeijing(p.created_at)}
            </span>
        </div>
        <div class="tucao-post__content">${esc(p.content)}</div>
        <div class="tucao-post__foot">
            <button class="${likeCls}" data-like="${p.id}">
                ${icon("heart", 15)}<span class="num">${p.like_count}</span>
            </button>
            <button class="tucao-act" data-report="${p.id}">
                ${icon("flag", 15)}举报
            </button>
            ${p.report_count > 0 ? '<span class="text-faint" style="font-size:var(--fs-12)">已被举报 ' + p.report_count + " 次</span>" : ""}
        </div>
    </div>`;
}

/* 列表入场 stagger 淡入 */
function stagger(el) {
    el.classList.remove("stagger");
    void el.offsetWidth;
    el.classList.add("stagger");
}

/* ---------- 加载列表 ---------- */
function loadTucao() {
    const box = document.getElementById("tucao-list");
    if (!box.dataset.loaded) Skeleton.inject(box, 4, false);
    fetchJSON(`/api/tucao/list?page=${tcPage}&sort=${tcSort}`).then((r) => {
        box.dataset.loaded = "1";
        const list = r.list || [];
        if (!list.length) {
            box.innerHTML = '<div class="empty">' + icon("message", 42) +
                '<div class="empty__text">还没有人吐槽，来抢第一条吧</div></div>';
        } else {
            box.innerHTML = list.map(postHtml).join("");
            stagger(box);
            bindActions(box);
        }
        document.getElementById("tucao-total").textContent = `共 ${r.total} 条 · 第 ${r.page} 页`;
        Pagination.render(document.getElementById("tucao-pager"), {
            page: r.page, total: r.total, pageSize: r.per_page,
            onChange: (p) => { tcPage = p; loadTucao(); window.scrollTo({ top: 0, behavior: "smooth" }); },
        });
        const modeTag = document.getElementById("mode-tag");
        if (modeTag) modeTag.textContent = r.mode === "post_first" ? "先发后审" : "先审后发";
    }).catch(() => {
        box.dataset.loaded = "1";
        box.innerHTML = '<div class="empty">' + icon("alert", 40) +
            '<div class="empty__text">加载失败，请稍后重试</div></div>';
    });
}

/* ---------- 点赞 / 举报绑定 ---------- */
function bindActions(box) {
    box.querySelectorAll("[data-like]").forEach((btn) => {
        btn.onclick = async () => {
            if (!TC.logged_in) { Toast.show("登录后才能点赞", "warning"); return; }
            let r;
            try {
                r = await fetchJSON("/api/tucao/like", {
                    method: "POST", body: { post_id: parseInt(btn.dataset.like, 10) },
                });
            } catch (e) { return; }
            if (r.liked && window.Sfx) Sfx.play("ding");   // 点赞"叮"
            // 更新计数 + 状态样式 + 爱心缩放微动效
            btn.classList.toggle("tucao-act--liked", r.liked);
            btn.querySelector("span").textContent = r.like_count;
            btn.classList.remove("tucao-act--pop");
            void btn.offsetWidth;
            btn.classList.add("tucao-act--pop");
        };
    });
    box.querySelectorAll("[data-report]").forEach((btn) => {
        btn.onclick = async () => {
            if (!TC.logged_in) { Toast.show("登录后才能举报", "warning"); return; }
            const postId = parseInt(btn.dataset.report, 10);
            const sure = await Modal.confirm({
                title: "举报该楼",
                body: '<div class="field"><label class="field__label">举报原因（可选）</label>' +
                      '<input class="input" id="report-reason" maxlength="60" placeholder="如：人身攻击 / 广告"></div>',
                okText: "提交举报", danger: false,
            });
            if (!sure) return;
            const reasonEl = document.getElementById("report-reason");
            try {
                const r = await fetchJSON("/api/tucao/report", {
                    method: "POST",
                    body: { post_id: postId, reason: reasonEl ? reasonEl.value : "" },
                });
                Toast.show(r.first ? "已提交举报，管理员会尽快处理" : "你此前已举报过该楼",
                           r.first ? "success" : "info");
            } catch (e) { /* 已 Toast */ }
        };
    });
}

/* ---------- 发帖 ---------- */
if (TC.logged_in) {
    bindCharCount(document.getElementById("tucao-input"),
                   document.getElementById("tucao-count"));
    const input = document.getElementById("tucao-input");
    document.getElementById("tucao-submit").onclick = async () => {
        const content = input.value.trim();
        if (content.length < 2) { Toast.show("至少写两个字吧", "warning"); return; }
        const persona = (document.querySelector('input[name="persona"]:checked') || {}).value || "anon";
        const btn = document.getElementById("tucao-submit");
        btn.classList.add("btn--loading");
        try {
            const r = await fetchJSON("/api/tucao/post", {
                method: "POST", body: { content, persona },
            });
            Toast.show(r.message || "已发布", r.status === "ok" ? "success" : "info");
            input.value = "";
            document.getElementById("tucao-count").textContent = `0 / ${TC.max_content}`;
            loadTucao();
        } catch (e) { /* 包含敏感词拦截等，fetchJSON 已提示 */ }
        btn.classList.remove("btn--loading");
    };
}

loadTucao();
