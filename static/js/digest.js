/* ============================================================
   digest.js —— 每日总结页交互
   职责：分段标签图标、归档点击查看详情（Modal）、刷新按钮
   依赖：components.js（fetchJSON/Modal/Toast/fmtBeijing/icon）
   ============================================================ */
"use strict";

function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

document.querySelectorAll("[data-seg-icon]").forEach((s) => {
    s.insertAdjacentHTML("afterbegin", icon(s.dataset.segIcon, 16));
});

/* 空态小插画（服务端宏里留空，这里用内联 SVG 补上，保证零外链资源） */
document.querySelectorAll(".empty__art").forEach((el) => {
    if (!el.innerHTML.trim()) el.innerHTML = icon("inbox", 40);
});

/* 把连贯短评渲染成 HTML（历史数据也走这条路径） */
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
                <p class="digest-sec__text">${esc(p.text)}</p>${refs}
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

/* 归档点击 → 拉取详情 → Modal 展示 */
document.querySelectorAll("#archive-chips [data-type]").forEach((b) => {
    b.onclick = async () => {
        const ntype = b.dataset.type;
        const ndate = b.dataset.date;
        let r;
        try {
            r = await fetchJSON(`/api/digest/detail?type=${ntype}&date=${ndate}`);
        } catch (e) { return; }
        const d = r.digest || {};
        if (!d.exists) { Toast.show("该日总结不存在", "warning"); return; }
        const points = (d.points || []).map((p, i) => `
            <div class="digest-point">
                <span class="digest-point__idx num">${i + 1}</span>
                <div>
                    <div>${esc(p.text)}</div>
                    ${(p.refs || []).map((ref) => `
                        <div class="digest-point__refs">
                            <a class="digest-point__ref" href="${esc(ref.url)}"
                               target="_blank" rel="noopener noreferrer">
                               ${esc(ref.source)}：${esc((ref.title || "").slice(0, 22))}…</a>
                        </div>`).join("")}
                </div>
            </div>`).join("");
        const articleHtml = renderArticle(d.article);
        const body = articleHtml
            ? articleHtml + (points ? `<details class="digest-raw"><summary>查看原始要点（${(d.points || []).length} 条）</summary><div class="mt-8">${points}</div></details>` : "")
            : (points || '<div class="empty">' + icon("inbox", 36) + '<div class="empty__text">无要点</div></div>');
        Modal.open({
            title: `${ndate} · ${ntype === "today" ? "今日总结" : "昨日总结"}`,
            body: `
                <div class="text-faint" style="font-size:var(--fs-12);margin-bottom:10px">
                    覆盖：${esc((d.window_bj || {}).start || "-")} ~ ${esc((d.window_bj || {}).end || "-")}
                    · 纳入 ${d.news_count || 0} 条
                    · 来源 ${esc(d.generator || "-")}${d.edited ? "（已编辑）" : ""}
                </div>
                ${body}
                ${(d.keywords || []).length ? `<div class="kw-list mt-16">${
                    d.keywords.map((k) => `<span class="tag">${esc(k)}</span>`).join("")}</div>` : ""}`,
            foot: '<button class="btn btn--secondary" onclick="Modal.close()">关闭</button>',
        });
    };
});

/* 刷新：重新拉一次（会触发惰性兜底），然后整页刷新以复用服务端渲染 */
document.getElementById("digest-refresh").onclick = async () => {
    await fetchJSON("/api/digest/view").catch(() => {});
    location.reload();
};
