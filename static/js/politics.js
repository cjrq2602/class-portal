/* ============================================================
   politics.js —— 时政学习页（新闻 / 每日解读 / 时政词典 三选项卡）
   依赖：components.js（fetchJSON/Toast/Skeleton/Pagination/fmtBeijing/icon）
   数据：/api/politics/{list,digest,terms}（公开，游客可读）
   ============================================================ */
"use strict";

function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---------------- 选项卡切换（支持 #news/#digest/#dict 直达） ---------------- */
const TABS = ["news", "digest", "dict"];
let polTab = TABS.indexOf(location.hash.slice(1)) >= 0 ? location.hash.slice(1) : "news";
const tabLoaded = { news: false, digest: false, dict: false };

function switchTab(name) {
    if (TABS.indexOf(name) < 0) name = "news";
    polTab = name;
    document.querySelectorAll("#pol-tabs [data-tab]").forEach((b) => {
        b.classList.toggle("chip--active", b.dataset.tab === name);
    });
    TABS.forEach((t) => {
        document.getElementById("tab-" + t).hidden = (t !== name);
    });
    history.replaceState(null, "", "#" + name);
    if (!tabLoaded[name]) {
        tabLoaded[name] = true;
        if (name === "news") loadList();
        if (name === "digest") loadDigest();
        if (name === "dict") loadTerms();
    }
}
document.querySelectorAll("#pol-tabs [data-tab]").forEach((b) => {
    b.onclick = () => switchTab(b.dataset.tab);
});

/* 分段标签图标 */
document.querySelectorAll("[data-seg-icon]").forEach((s) => {
    s.insertAdjacentHTML("afterbegin", icon(s.dataset.segIcon, 16));
});

/* ============================================================
   选项卡 1：时政新闻（含词典词条高亮）
   ============================================================ */
let polPage = 1;
let polSource = "";
let termNames = [];   // 词典词条名（长度倒序），用于标题/摘要高亮

fetchJSON("/api/politics/terms?names_only=1").then((r) => {
    termNames = r.names || [];
    if (tabLoaded.news) loadList();   // 词表晚到则重绘一次以补高亮
}).catch(() => { /* 高亮是增强体验，失败不影响列表 */ });

/** 标题/摘要里的词条高亮（只处理文本节点语义：先转义再包 span，词条本身无 HTML） */
function highlightTerms(text) {
    let html = esc(text);
    if (!termNames.length) return html;
    for (const t of termNames) {
        if (t.length < 2) continue;
        const et = esc(t);
        if (html.indexOf(et) < 0) continue;
        html = html.split(et).join(
            `<button type="button" class="pol-term" data-term="${et}">${et}</button>`);
    }
    return html;
}

/* 词条点击委托：跳到词典选项卡并搜索该词 */
document.addEventListener("click", (e) => {
    const btn = e.target.closest && e.target.closest(".pol-term");
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    const q = btn.dataset.term || "";
    switchTab("dict");
    const input = document.getElementById("dict-q");
    input.value = q;
    dictQ = q;
    if (tabLoaded.dict) loadTerms();
});

function renderSyncNote(status) {
    const el = document.getElementById("pol-sync-note");
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
    const box = document.getElementById("pol-source-chips");
    const all = [{ name: "", count: null }].concat(sources || []);
    box.innerHTML = all.map((s) => {
        const label = s.name ? `${esc(s.name)}${s.count != null ? " " + s.count : ""}` : "全部";
        const active = (polSource === s.name) ? " chip--active" : "";
        return `<button class="chip${active}" data-src="${esc(s.name)}">${label}</button>`;
    }).join("");
    box.querySelectorAll("[data-src]").forEach((b) => {
        b.onclick = () => { polSource = b.dataset.src; polPage = 1; loadList(); };
    });
}

function renderList(list) {
    const box = document.getElementById("pol-list");
    if (!list.length) {
        box.innerHTML = '<div class="empty">' +
            '<span class="empty__art">' + icon("inbox", 44) + "</span>" +
            '<div class="empty__text">' +
            (polSource ? "该来源暂无新闻，试试切换来源"
                       : "暂无时政新闻，采集任务首次产出后即可看到") +
            "</div></div>";
        return;
    }
    box.innerHTML = list.map((n) => `
        <a class="news-item" href="${esc(n.url)}" target="_blank" rel="noopener noreferrer">
            <div class="news-item__title">${highlightTerms(n.title)}</div>
            <div class="news-item__meta">
                <span class="tag tag--primary">${esc(n.source)}</span>
                <span>${fmtBeijing(n.published_at)}</span>
                <span class="text-faint">· 原文 ↗</span>
            </div>
            ${n.summary ? `<div class="news-item__summary">${highlightTerms(n.summary)}</div>` : ""}
        </a>`).join("");
}

function loadList() {
    const box = document.getElementById("pol-list");
    if (!box.dataset.loaded) Skeleton.inject(box, 6, false);
    const url = `/api/politics/list?page=${polPage}` +
        (polSource ? `&source=${encodeURIComponent(polSource)}` : "");
    fetchJSON(url).then((r) => {
        box.dataset.loaded = "1";
        box.classList.remove("stagger");
        void box.offsetWidth;
        box.classList.add("stagger");
        renderList(r.list || []);
        renderChips(r.sources || []);
        renderSyncNote(r.status);
        document.getElementById("pol-total").textContent =
            `共 ${r.total} 条 · 第 ${r.page} 页`;
        Pagination.render(document.getElementById("pol-pager"), {
            page: r.page, total: r.total, pageSize: r.per_page,
            onChange: (p) => { polPage = p; loadList(); window.scrollTo({ top: 0, behavior: "smooth" }); },
        });
    }).catch(() => {
        box.dataset.loaded = "1";
        box.innerHTML = '<div class="empty">' +
            '<span class="empty__art">' + icon("alert", 40) + "</span>" +
            '<div class="empty__text">加载失败，请稍后重试</div>' +
            '<button class="btn btn--outline btn--sm" id="pol-retry">重试</button></div>';
        const rt = document.getElementById("pol-retry");
        if (rt) rt.onclick = loadList;
    });
}

/* ============================================================
   选项卡 2：每日解读（复用每日总结的文章渲染结构）
   ============================================================ */
let digestType = "today";

document.querySelectorAll("[data-dtype]").forEach((b) => {
    b.onclick = () => {
        digestType = b.dataset.dtype;
        document.querySelectorAll("[data-dtype]").forEach((x) =>
            x.classList.toggle("chip--active", x === b));
        loadDigest();
    };
});

function renderArticle(article) {
    if (!article || !(article.paragraphs || []).length) return "";
    return `<div class="digest-article">${article.paragraphs.map((p) => {
        if (p.kind === "lead") {
            return `<p class="digest-lead">${esc(p.text)}</p>`;
        }
        if (p.kind === "section") {
            const refs = (p.refs || []).map((r) => `
                <div class="digest-point__refs">
                    <a class="digest-point__ref" href="${esc(r.url)}"
                       target="_blank" rel="noopener noreferrer">
                       ${esc(r.source)}：${esc((r.title || "").slice(0, 22))}…</a>
                </div>`).join("");
            return `<section class="digest-sec">
                <h3 class="digest-sec__title">${esc(p.heading || "")}</h3>
                <p class="digest-sec__text">${highlightTerms(p.text)}</p>${refs}
            </section>`;
        }
        if (p.kind === "glossary") {
            const items = (p.items || []).map((g) =>
                `<dt>${esc(g.term)}</dt><dd>${esc(g.desc)}</dd>`).join("");
            return `<section class="digest-glossary">
                <div class="digest-glossary__title">📖 名词解释</div>
                <dl class="digest-glossary__list">${items}</dl>
            </section>`;
        }
        if (p.kind === "outlook") {
            return `<section class="digest-outlook">
                <div class="digest-outlook__label">💭 一点看法</div>
                <p>${esc(p.text)}</p>
            </section>`;
        }
        return "";
    }).join("")}</div>`;
}

function loadDigest() {
    const box = document.getElementById("pol-digest-body");
    Skeleton.inject(box, 4, false);
    fetchJSON(`/api/politics/digest?type=${digestType}`).then((r) => {
        const d = r.digest || {};
        if (!d.exists) {
            box.innerHTML = '<div class="empty">' +
                '<span class="empty__art">' + icon("clock", 40) + "</span>" +
                '<div class="empty__text">' +
                (d.pending
                    ? `将于 ${esc(d.ready_at || "下一时刻")} 更新，稍后再来看看`
                    : "本期解读尚未生成，等时政新闻攒够一批后自动产出") +
                "</div></div>";
            return;
        }
        const win = d.window_bj
            ? `<p class="text-faint" style="font-size:var(--fs-12)">
                 解读窗口：${esc(d.window_bj.start)} ~ ${esc(d.window_bj.end)}（北京时间）
                 · 收录 ${d.news_count || 0} 条 · ${d.generator === "action" ? "定时生成" : "本站补生成"}</p>`
            : "";
        const kws = (d.keywords || []).slice(0, 8)
            .map((k) => `<span class="tag tag--primary">${esc(k)}</span>`).join(" ");
        box.innerHTML = win +
            (kws ? `<div class="mt-8">${kws}</div>` : "") +
            renderArticle(d.article);
    }).catch(() => {
        box.innerHTML = '<div class="empty">' +
            '<span class="empty__art">' + icon("alert", 40) + "</span>" +
            '<div class="empty__text">加载失败，请稍后重试</div></div>';
    });
}

/* ============================================================
   选项卡 3：时政词典（搜索 + 分类筛选）
   ============================================================ */
let dictQ = "";
let dictCat = "";
let dictTimer = null;

const dictInput = document.getElementById("dict-q");
dictInput.addEventListener("input", () => {
    clearTimeout(dictTimer);
    dictTimer = setTimeout(() => {
        dictQ = dictInput.value.trim();
        loadTerms();
    }, 250);   // 输入防抖
});

function renderCatChips(categories) {
    const box = document.getElementById("dict-cat-chips");
    const all = [{ name: "", count: null }].concat(categories || []);
    box.innerHTML = all.map((c) => {
        const label = c.name ? `${esc(c.name)} ${c.count}` : "全部";
        const active = (dictCat === c.name) ? " chip--active" : "";
        return `<button class="chip${active}" data-cat="${esc(c.name)}">${label}</button>`;
    }).join("");
    box.querySelectorAll("[data-cat]").forEach((b) => {
        b.onclick = () => { dictCat = b.dataset.cat; loadTerms(); };
    });
}

function loadTerms() {
    const box = document.getElementById("dict-list");
    if (!box.dataset.loaded) Skeleton.inject(box, 5, false);
    const url = "/api/politics/terms?" +
        (dictQ ? `q=${encodeURIComponent(dictQ)}&` : "") +
        (dictCat ? `category=${encodeURIComponent(dictCat)}` : "");
    fetchJSON(url).then((r) => {
        box.dataset.loaded = "1";
        box.classList.remove("stagger");
        void box.offsetWidth;
        box.classList.add("stagger");
        renderCatChips(r.categories || []);
        document.getElementById("dict-count").textContent =
            `共收录 ${r.total_terms} 条`;
        const list = r.list || [];
        if (!list.length) {
            box.innerHTML = '<div class="empty">' +
                '<span class="empty__art">' + icon("search", 40) + "</span>" +
                '<div class="empty__text">没有匹配的词条，换个关键词试试</div></div>';
            return;
        }
        box.innerHTML = list.map((t) => `
            <div class="pol-term-card">
                <div class="pol-term-card__head">
                    <b class="pol-term-card__name">${esc(t.term)}</b>
                    <span class="tag tag--primary">${esc(t.category)}</span>
                </div>
                <p class="pol-term-card__def">${esc(t.definition)}</p>
                ${t.example ? `<p class="pol-term-card__eg">📌 ${esc(t.example)}</p>` : ""}
            </div>`).join("");
    }).catch(() => {
        box.dataset.loaded = "1";
        box.innerHTML = '<div class="empty">' +
            '<span class="empty__art">' + icon("alert", 40) + "</span>" +
            '<div class="empty__text">词典加载失败，请稍后重试</div></div>';
    });
}

/* ---------------- 启动 ---------------- */
switchTab(polTab);
