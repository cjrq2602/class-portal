/* ============================================================
   updates.js —— 网站更新公告页
   职责：Tab 切换 / 卡片列表 / 展开收起详情（Markdown-lite 渲染）/
        点开即记已读（服务端 detail 接口自动标记）→ 未读红点消失
   依赖：components.js（fetchJSON/Toast/Skeleton/fmtBeijing/icon）
   注意：content 服务端已转义，前端只做 **加粗** 与列表/前缀识别，零注入面
   ============================================================ */
"use strict";

function updEsc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---------------- Markdown-lite 渲染（输入已是转义文本） ---------------- */
function mdLite(content) {
    const lines = String(content || "").split("\n");
    const out = [];
    let inList = false;
    const bold = (t) => t.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
    /* 条目前缀自动识别：新增/修复/优化 → ✨/🐛/⚡ 类别徽标 */
    const itemCls = (t) => {
        if (/新增|✨|新功能|添加/.test(t)) return ["✨", "新增", "upd-cat--new"];
        if (/修复|🐛|fix/i.test(t)) return ["🐛", "修复", "upd-cat--fix"];
        if (/优化|⚡|改进|提升/.test(t)) return ["⚡", "优化", "upd-cat--opt"];
        return null;
    };
    for (const raw of lines) {
        const line = raw.trimEnd();
        const m = line.match(/^\s*[-•]\s+(.*)$/);
        if (m) {
            if (!inList) { out.push('<ul class="upd-items">'); inList = true; }
            const cat = itemCls(m[1]);
            out.push(cat
                ? `<li><span class="upd-cat ${cat[2]}">${cat[0]} ${cat[1]}</span>${bold(m[1])}</li>`
                : `<li>${bold(m[1])}</li>`);
        } else {
            if (inList) { out.push("</ul>"); inList = false; }
            if (line.trim()) out.push(`<p class="upd-p">${bold(line)}</p>`);
        }
    }
    if (inList) out.push("</ul>");
    return out.join("") || '<p class="upd-p text-faint">暂无详细内容</p>';
}

/* ---------------- 卡片 ---------------- */
let curType = "all";
let curPage = 1;
const detailCache = {};   /* id → 已拉取的详情（避免重复请求） */

function badgeOf(item) {
    if (item.update_type === "preview") return '<span class="upd-badge upd-badge--preview">预告</span>';
    if (item.update_type === "release")
        return `<span class="upd-badge upd-badge--release num">${updEsc(item.version_tag || "更新")}</span>`;
    return '<span class="upd-badge upd-badge--notice">通知</span>';
}

function cardHtml(item) {
    const cls = ["upd-card", "card"];
    if (item.update_type === "preview") cls.push("upd-card--preview");
    return `
    <div class="${cls.join(" ")}" data-upd-id="${item.id}">
        <div class="upd-card__head" data-upd-toggle="${item.id}" role="button" tabindex="0">
            <div class="flex gap-8" style="align-items:center;flex-wrap:wrap">
                ${badgeOf(item)}
                ${item.is_pinned ? '<span class="tag tag--warning">📌 置顶</span>' : ""}
                ${item.unread ? '<span class="upd-dot" title="未读"></span>' : ""}
            </div>
            <div class="upd-card__title">${updEsc(item.title)}</div>
            <div class="upd-card__meta">
                <span>${updEsc(item.author)}</span>
                <span>${fmtBeijing(item.publish_at)}</span>
                <span class="upd-card__chevron" data-chevron>${icon("chevron-down", 15)}</span>
            </div>
        </div>
        <div class="upd-card__body" id="upd-body-${item.id}" hidden></div>
    </div>`;
}

/* ---------------- 列表加载 ---------------- */
function loadList(reset) {
    const box = document.getElementById("upd-list");
    if (reset) { curPage = 1; Skeleton.inject(box, 3, true); }
    fetchJSON(`/api/updates/list?type=${curType}&page=${curPage}`).then((r) => {
        const html = (r.list || []).map(cardHtml).join("");
        if (reset) box.innerHTML = html;
        else box.insertAdjacentHTML("beforeend", html);
        if (reset && !(r.list || []).length) {
            box.innerHTML = '<div class="card"><div class="empty">' + icon("megaphone", 40) +
                '<div class="empty__text">暂无内容，敬请期待</div></div></div>';
        }
        document.getElementById("upd-more-box").style.display = r.has_more ? "" : "none";
    }).catch(() => {
        if (reset) box.innerHTML = '<div class="card"><div class="empty">' +
            icon("alert", 36) + '<div class="empty__text">加载失败，请稍后再试</div></div></div>';
    });
}

/* ---------------- 展开 / 收起（点开即已读） ---------------- */
document.getElementById("upd-list").addEventListener("click", async (e) => {
    const head = e.target.closest("[data-upd-toggle]");
    if (!head) return;
    const id = parseInt(head.dataset.updToggle, 10);
    const body = document.getElementById("upd-body-" + id);
    if (!body) return;
    const opening = body.hidden;
    body.hidden = !opening;
    const chev = head.querySelector("[data-chevron]");
    if (chev) chev.innerHTML = icon(opening ? "chevron-down" : "chevron-right", 15);
    if (!opening) return;
    if (detailCache[id]) { body.innerHTML = detailCache[id]; return; }
    body.innerHTML = '<div class="skeleton" style="height:48px"></div>';
    try {
        const r = await fetchJSON("/api/updates/detail?id=" + id);
        const html = mdLite(r.item.content);
        detailCache[id] = html;
        body.innerHTML = html;
        /* 服务端已记已读 → 本卡片红点消失，同步导航未读数（下页刷新生效） */
        const card = head.closest(".upd-card");
        const dot = card && card.querySelector(".upd-dot");
        if (dot) dot.remove();
    } catch (err) {
        body.innerHTML = '<div class="upd-p text-faint">详情加载失败</div>';
    }
});

/* 键盘可达：回车展开（电脑端无障碍） */
document.getElementById("upd-list").addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const head = e.target.closest("[data-upd-toggle]");
    if (head) head.click();
});

/* ---------------- Tab 与分页 ---------------- */
document.querySelectorAll("#upd-tabs .chip").forEach((c) => {
    c.onclick = () => {
        document.querySelectorAll("#upd-tabs .chip").forEach((x) =>
            x.classList.toggle("chip--active", x === c));
        curType = c.dataset.utype;
        loadList(true);
    };
});
document.getElementById("upd-more").onclick = () => { curPage += 1; loadList(false); };

loadList(true);
