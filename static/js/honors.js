/* ============================================================
   honors.js —— 荣誉墙交互
   职责：卡片流渲染（置顶在前）/ 管理端新增（图片浏览器端 Canvas 压缩后上传）/
        编辑 / 置顶 / 删除
   ★ 图片压缩同样走 upload.js（质量 0.7、最长边 1280），服务端零图像处理
   依赖：components.js + upload.js
   ============================================================ */
"use strict";

let HONOR_CAN_MANAGE = false;
let HONOR_MAX_MB = 1;
let HONOR_CACHE = [];   // 当前列表（供"编辑"回填，避免再次请求）

function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

document.querySelectorAll(".empty__art").forEach((el) => {
    if (!el.innerHTML.trim()) el.innerHTML = icon("trophy", 40);
});

function honorCard(h) {
    return `
    <div class="card card--hover honor-card${h.is_pinned ? " honor-card--pinned" : ""}">
        ${h.has_image
            ? `<img class="honor-card__img" src="/api/honors/image/${h.id}" alt="${esc(h.title)}"
                 loading="lazy">`
            : ""}
        <div class="honor-card__body">
            <div class="flex gap-8" style="align-items:flex-start">
                <div class="honor-card__title" style="flex:1">${esc(h.title)}</div>
                ${h.is_pinned ? '<span class="tag tag--warning">📌 置顶</span>' : ""}
            </div>
            ${h.owner ? `<div class="honor-card__owner">🏅 ${esc(h.owner)}</div>` : ""}
            ${h.date ? `<div class="text-faint" style="font-size:var(--fs-12);margin-top:2px">${esc(h.date)}</div>` : ""}
            ${h.description ? `<div class="honor-card__desc">${esc(h.description)}</div>` : ""}
            ${HONOR_CAN_MANAGE ? `
            <div class="flex gap-8 mt-16">
                <button class="btn btn--outline btn--sm" data-pin="${h.id}">
                    ${h.is_pinned ? "取消置顶" : "置顶"}</button>
                <button class="btn btn--outline btn--sm" data-edit="${h.id}">编辑</button>
                <button class="btn btn--danger btn--sm" data-del="${h.id}">删除</button>
            </div>` : ""}
        </div>
    </div>`;
}

function loadHonors() {
    const box = document.getElementById("honor-grid");
    if (!box.dataset.loaded) Skeleton.cards(box, 3);
    fetchJSON("/api/honors/list").then((r) => {
        box.dataset.loaded = "1";
        const list = r.list || [];
        HONOR_CACHE = list;
        if (!list.length) {
            box.className = "";
            box.innerHTML = '<div class="empty">' + icon("trophy", 46) +
                '<div class="empty__text">还没有荣誉，第一块奖牌等着你们来拿</div></div>';
            return;
        }
        // 重置 stagger 动画（列表入场淡入）
        box.className = "";
        void box.offsetWidth;
        box.className = "honor-grid stagger";
        box.innerHTML = list.map(honorCard).join("");
        bindActions(box);
    }).catch(() => {
        box.dataset.loaded = "1";
        box.innerHTML = '<div class="empty">' + icon("alert", 40) +
            '<div class="empty__text">加载失败，请稍后重试</div></div>';
    });
}

function bindActions(box) {
    box.querySelectorAll("[data-pin]").forEach((b) => {
        b.onclick = async () => {
            try {
                const r = await fetchJSON("/api/admin/honors/pin", {
                    method: "POST", body: { id: parseInt(b.dataset.pin, 10) },
                });
                Toast.show(r.is_pinned ? "已置顶" : "已取消置顶", "success");
                loadHonors();
            } catch (e) { /* 已提示 */ }
        };
    });
    box.querySelectorAll("[data-del]").forEach((b) => {
        b.onclick = async () => {
            const sure = await Modal.confirm({
                title: "删除荣誉",
                body: "确定删除这条荣誉吗？关联图片会同步删除并回收配额。",
                okText: "删除",
            });
            if (!sure) return;
            try {
                await fetchJSON("/api/admin/honors/delete", {
                    method: "POST", body: { id: parseInt(b.dataset.del, 10) },
                });
                Toast.show("已删除", "success");
                loadHonors();
            } catch (e) { /* 已提示 */ }
        };
    });
    box.querySelectorAll("[data-edit]").forEach((b) => {
        b.onclick = () => {
            const id = parseInt(b.dataset.edit, 10);
            const cur = HONOR_CACHE.find((x) => x.id === id) || {};
            Modal.open({
                title: "编辑荣誉",
                body: `
                    <div class="field"><label class="field__label">奖项名称</label>
                        <input class="input" id="h-title" maxlength="60" value="${esc(cur.title || "")}"></div>
                    <div class="field"><label class="field__label">获奖人 / 集体</label>
                        <input class="input" id="h-owner" maxlength="60" value="${esc(cur.owner || "")}"></div>
                    <div class="field"><label class="field__label">获奖日期（YYYY-MM-DD）</label>
                        <input class="input" id="h-date" maxlength="10" value="${esc(cur.date || "")}"></div>
                    <div class="field"><label class="field__label">描述</label>
                        <textarea class="input" id="h-desc" maxlength="500">${esc(cur.description || "")}</textarea></div>`,
                foot: `<button class="btn btn--secondary" onclick="Modal.close()">取消</button>
                       <button class="btn btn--primary" id="h-save">保存</button>`,
            });
            document.getElementById("h-save").onclick = async () => {
                try {
                    await fetchJSON("/api/admin/honors/update", {
                        method: "POST",
                        body: {
                            id,
                            title: document.getElementById("h-title").value.trim(),
                            owner: document.getElementById("h-owner").value.trim(),
                            hdate: document.getElementById("h-date").value.trim(),
                            description: document.getElementById("h-desc").value.trim(),
                        },
                    });
                    Toast.show("已保存", "success");
                    Modal.close();
                    loadHonors();
                } catch (e) { /* 已提示 */ }
            };
        };
    });
}

/* ---------------- 后台新增（含图片压缩上传） ---------------- */
function initCreate() {
    const form = document.getElementById("honor-form");
    if (!form) return;
    HONOR_MAX_MB = parseFloat(form.dataset.maxMb || "1");
    const imgInput = document.getElementById("honor-image");
    const hint = document.getElementById("honor-image-hint");
    imgInput.onchange = () => {
        const f = imgInput.files && imgInput.files[0];
        if (!f) { hint.textContent = ""; return; }
        hint.textContent = `已选：${f.name}（${(f.size / 1024).toFixed(0)} KB）` +
            " · 上传前会自动压缩到最长边 1280 / 质量 0.7";
    };
    form.onsubmit = async (e) => {
        e.preventDefault();
        const title = document.getElementById("honor-title").value.trim();
        if (!title) { Toast.show("请填写奖项名称", "warning"); return; }
        const btn = document.getElementById("honor-submit");
        btn.classList.add("btn--loading");
        try {
            const fd = new FormData();
            fd.append("title", title);
            fd.append("owner", document.getElementById("honor-owner").value.trim());
            fd.append("hdate", document.getElementById("honor-date").value.trim());
            fd.append("description", document.getElementById("honor-desc").value.trim());
            const f = imgInput.files && imgInput.files[0];
            if (f) {
                if (f.size > HONOR_MAX_MB * 1024 * 1024) {
                    Toast.show(`图片超过 ${HONOR_MAX_MB}MB 上限`, "danger");
                    btn.classList.remove("btn--loading");
                    return;
                }
                let out = f;
                try {
                    // 浏览器端 Canvas 压缩（质量 0.7、最长边 1280）
                    out = await compressImage(f, { maxEdge: 1280, quality: 0.7 });
                    hint.textContent = `已压缩至 ${(out.size / 1024).toFixed(0)} KB`;
                } catch (err) { out = f; }
                fd.append("image", out, out.name);
            }
            await uploadForm("/api/admin/honors/create", fd);
            Toast.show("已添加荣誉", "success");
            form.reset();
            hint.textContent = "";
            loadHonors();
        } catch (err) { /* 已提示 */ }
        btn.classList.remove("btn--loading");
    };
}

fetchJSON("/api/auth/me").then((r) => {
    HONOR_CAN_MANAGE = (r.perms || []).indexOf("honor.manage") >= 0;
    const box = document.getElementById("honor-compose");
    if (box) box.classList.toggle("hidden", !HONOR_CAN_MANAGE);
}).catch(() => {});
initCreate();
loadHonors();
