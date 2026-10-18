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
        Modal.open({
            title: `${ndate} · ${ntype === "today" ? "今日总结" : "昨日总结"}`,
            body: `
                <div class="text-faint" style="font-size:var(--fs-12);margin-bottom:10px">
                    覆盖：${esc((d.window_bj || {}).start || "-")} ~ ${esc((d.window_bj || {}).end || "-")}
                    · 纳入 ${d.news_count || 0} 条
                    · 来源 ${esc(d.generator || "-")}${d.edited ? "（已编辑）" : ""}
                </div>
                ${points || '<div class="empty">' + icon("inbox", 36) + '<div class="empty__text">无要点</div></div>'}
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
