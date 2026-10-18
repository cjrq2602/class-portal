/* ============================================================
   news.js —— 新闻中心页逻辑
   职责：拉取 /api/news/list（每页 30）→ 渲染列表 / 来源筛选 / 分页 /
        同步状态标注（含 GitHub 不可达时的降级提示）
   依赖：components.js（fetchJSON/Toast/Skeleton/Pagination/fmtBeijing/icon）
   ============================================================ */
"use strict";

let newsPage = 1;
let newsSource = "";

function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/** 渲染同步状态提示：正常显示"数据更新于 xx:xx"；失败则给出降级警告 */
function renderSyncNote(status) {
    const el = document.getElementById("sync-note");
    if (!el || !status) return;
    const parts = [];
    if (status.last_sync_at > 0) {
        parts.push("数据更新于 " + fmtBeijing(status.last_sync_at) + "（北京时间）");
    } else {
        parts.push("尚无同步记录");
    }
    parts.push(`本地缓存 ${status.item_count} 条`);
    if (!status.ok && status.error) {
        parts.push(`⚠️ 同步异常，正在展示旧数据（${esc(status.error)}）`);
        el.innerHTML = '<span class="text-danger">' + parts.join(" · ") + "</span>";
    } else {
        el.textContent = parts.join(" · ");
    }
}

function renderChips(sources) {
    const box = document.getElementById("source-chips");
    const all = [{ name: "", count: null }].concat(sources || []);
    box.innerHTML = all.map((s) => {
        const label = s.name ? `${esc(s.name)}${s.count != null ? " " + s.count : ""}` : "全部";
        const active = (newsSource === s.name) ? " chip--active" : "";
        return `<button class="chip${active}" data-src="${esc(s.name)}">${label}</button>`;
    }).join("");
    box.querySelectorAll("[data-src]").forEach((b) => {
        b.onclick = () => {
            newsSource = b.dataset.src;
            newsPage = 1;
            loadNews();
        };
    });
}

function renderList(list) {
    const box = document.getElementById("news-list");
    if (!list.length) {
        box.innerHTML = '<div class="empty">' +
            '<span class="empty__art">' + icon("inbox", 44) + "</span>" +
            '<div class="empty__text">' +
            (newsSource ? "该来源暂无新闻，试试切换来源" : "暂无新闻，抓取任务可能尚未产出数据") +
            "</div></div>";
        return;
    }
    box.innerHTML = list.map((n) => `
        <a class="news-item" href="${esc(n.url)}" target="_blank" rel="noopener noreferrer">
            <div class="news-item__title">${esc(n.title)}</div>
            <div class="news-item__meta">
                <span class="tag tag--primary">${esc(n.source)}</span>
                <span>${fmtBeijing(n.published_at)}</span>
                <span class="text-faint">· 原文 ↗</span>
            </div>
            ${n.summary ? `<div class="news-item__summary">${esc(n.summary)}</div>` : ""}
        </a>`).join("");
}

function loadNews() {
    const box = document.getElementById("news-list");
    if (!box.dataset.loaded) Skeleton.inject(box, 6, false);
    const url = `/api/news/list?page=${newsPage}` +
        (newsSource ? `&source=${encodeURIComponent(newsSource)}` : "");
    fetchJSON(url).then((r) => {
        box.dataset.loaded = "1";
        // 渲染时重置 stagger 动画（列表入场淡入）
        box.classList.remove("stagger");
        void box.offsetWidth;
        box.classList.add("stagger");
        renderList(r.list || []);
        renderChips(r.sources || []);
        renderSyncNote(r.status);
        document.getElementById("news-total").textContent =
            `共 ${r.total} 条 · 第 ${r.page} 页`;
        Pagination.render(document.getElementById("news-pager"), {
            page: r.page, total: r.total, pageSize: r.per_page,
            onChange: (p) => { newsPage = p; loadNews(); window.scrollTo({ top: 0, behavior: "smooth" }); },
        });
    }).catch(() => {
        box.dataset.loaded = "1";
        box.innerHTML = '<div class="empty">' +
            '<span class="empty__art">' + icon("alert", 40) + "</span>" +
            '<div class="empty__text">加载失败，请稍后重试</div>' +
            '<button class="btn btn--outline btn--sm" id="news-retry">重试</button></div>';
        const rt = document.getElementById("news-retry");
        if (rt) rt.onclick = loadNews;
    });
}

// 分段标签图标
document.querySelectorAll("[data-seg-icon]").forEach((s) => {
    s.insertAdjacentHTML("afterbegin", icon(s.dataset.segIcon, 16));
});
loadNews();
