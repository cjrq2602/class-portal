/* ============================================================
   resources.js —— 学习资料库页逻辑
   职责：列表（学科筛选/下载量或时间排序）/ 上传（外链或文件）/
        删除自己的资料 / 下载计数跳转
   ★ 图片上传前在本页调用 upload.js 的 compressImage（Canvas，质量 0.7、最长边 1280）
   依赖：components.js + upload.js
   ============================================================ */
"use strict";

let RES_SUBJECT = "";
let RES_SORT = "time";
let RES_LOGGED_IN = false;
let RES_MAX_MB = 5;

function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

document.querySelectorAll(".empty__art").forEach((el) => {
    if (!el.innerHTML.trim()) el.innerHTML = icon("inbox", 40);
});

function resRow(r) {
    const isFile = r.rtype === "file";
    const action = isFile
        ? `<a class="btn btn--outline btn--sm" href="/api/resources/download/${r.id}">
             ${icon("inbox", 15)} 下载</a>`
        : `<a class="btn btn--outline btn--sm" href="${esc(r.link)}" target="_blank"
             rel="noopener noreferrer">${icon("eye", 15)} 打开链接</a>`;
    return `
    <div class="res-item">
        <span class="res-item__icon">${isFile ? icon("file-text", 20) : icon("key", 20)}</span>
        <div class="res-item__body">
            <div class="res-item__title truncate">${esc(r.title)}</div>
            <div class="res-item__meta">
                <span class="tag tag--primary">${esc(r.subject)}</span>
                <span>${isFile ? esc(r.file_name) + " · " + esc(r.size_text) : "外部链接"}</span>
                <span>上传者 ${esc(r.uploader)}</span>
                <span>下载 <b class="num">${r.downloads}</b></span>
                <span>${fmtBeijing(r.created_at)}</span>
            </div>
        </div>
        ${action}
        ${r.mine ? `<button class="btn btn--ghost btn--sm" data-del="${r.id}" title="删除">${icon("trash", 15)}</button>` : ""}
    </div>`;
}

function renderFilters(subjects, counts) {
    const box = document.getElementById("res-filters");
    const chips = ['<button class="chip' + (RES_SUBJECT === "" ? " chip--active" : "") +
        '" data-sub="">全部学科</button>']
        .concat(subjects.map((s) => {
            const c = (counts || []).find((x) => x.name === s);
            return `<button class="chip${RES_SUBJECT === s ? " chip--active" : ""}" data-sub="${esc(s)}">
                ${esc(s)}${c ? " " + c.count : ""}</button>`;
        })).join("");
    box.innerHTML = chips +
        '<span class="text-faint" style="padding:0 4px">|</span>' +
        `<button class="chip${RES_SORT === "time" ? " chip--active" : ""}" data-sort="time">最新</button>` +
        `<button class="chip${RES_SORT === "hot" ? " chip--active" : ""}" data-sort="hot">下载最多</button>`;
    box.querySelectorAll("[data-sub]").forEach((b) => {
        b.onclick = () => { RES_SUBJECT = b.dataset.sub; loadRes(); };
    });
    box.querySelectorAll("[data-sort]").forEach((b) => {
        b.onclick = () => { RES_SORT = b.dataset.sort; loadRes(); };
    });
}

function loadRes() {
    const box = document.getElementById("res-list");
    if (!box.dataset.loaded) Skeleton.inject(box, 4, false);
    const q = [];
    if (RES_SUBJECT) q.push("subject=" + encodeURIComponent(RES_SUBJECT));
    q.push("sort=" + encodeURIComponent(RES_SORT));
    fetchJSON("/api/resources/list?" + q.join("&")).then((r) => {
        box.dataset.loaded = "1";
        renderFilters(r.subjects || [], r.counts || []);
        const st = r.storage || {};
        const bar = document.getElementById("res-storage");
        if (bar) {
            bar.innerHTML = `<div class="flex-between" style="font-size:var(--fs-12)">
                <span class="text-muted">全站存储用量</span>
                <span class="num">${esc(st.used_text || "")} / ${esc(st.quota_text || "")}</span></div>
                <div class="progress mt-8"><div class="progress__bar"
                    style="width:${Math.min(100, st.percent || 0)}%"></div></div>`;
        }
        const list = r.list || [];
        if (!list.length) {
            box.innerHTML = '<div class="empty">' + icon("inbox", 44) +
                '<div class="empty__text">该学科暂无资料，欢迎上传第一份</div></div>';
            return;
        }
        box.classList.remove("stagger");
        void box.offsetWidth;
        box.innerHTML = list.map(resRow).join("");
        box.classList.add("stagger");
        box.querySelectorAll("[data-del]").forEach((b) => {
            b.onclick = async () => {
                const sure = await Modal.confirm({
                    title: "删除资料",
                    body: "确定删除这份资料吗？磁盘文件会同步删除并回收配额。",
                    okText: "删除",
                });
                if (!sure) return;
                try {
                    const rr = await fetchJSON("/api/resources/delete", {
                        method: "POST", body: { id: parseInt(b.dataset.del, 10) },
                    });
                    Toast.show("已删除，释放 " + (rr.freed_text || "0 B"), "success");
                    loadRes();
                } catch (e) { /* 已提示 */ }
            };
        });
    }).catch(() => {
        box.dataset.loaded = "1";
        box.innerHTML = '<div class="empty">' + icon("alert", 40) +
            '<div class="empty__text">加载失败，请稍后重试</div></div>';
    });
}

/* ---------------- 上传 ---------------- */
function initUpload() {
    const form = document.getElementById("res-form");
    if (!form) return;
    const modeSel = document.getElementById("res-mode");
    const linkField = document.getElementById("res-link-field");
    const fileField = document.getElementById("res-file-field");
    const fileInput = document.getElementById("res-file");
    const hint = document.getElementById("res-file-hint");

    RES_MAX_MB = parseFloat(form.dataset.maxMb || "5");
    modeSel.onchange = () => {
        const isFile = modeSel.value === "file";
        linkField.classList.toggle("hidden", isFile);
        fileField.classList.toggle("hidden", !isFile);
    };
    fileInput.onchange = () => {
        const f = fileInput.files && fileInput.files[0];
        if (!f) { hint.textContent = ""; return; }
        const isImg = /^image\//.test(f.type || "");
        hint.textContent = `已选：${f.name}（${(f.size / 1024 / 1024).toFixed(2)} MB）` +
            (isImg ? " · 图片将在上传前自动压缩（最长边 1280 / 质量 0.7）" : "");
    };

    form.onsubmit = async (e) => {
        e.preventDefault();
        const title = document.getElementById("res-title").value.trim();
        const subject = document.getElementById("res-subject").value;
        const mode = modeSel.value;
        if (!title) { Toast.show("请填写标题", "warning"); return; }
        const btn = document.getElementById("res-submit");
        btn.classList.add("btn--loading");
        try {
            const formData = new FormData();
            formData.append("title", title);
            formData.append("subject", subject);
            formData.append("mode", mode);
            if (mode === "link") {
                formData.append("link", document.getElementById("res-link").value.trim());
            } else {
                const f = fileInput.files && fileInput.files[0];
                if (!f) { Toast.show("请选择文件", "warning"); btn.classList.remove("btn--loading"); return; }
                if (f.size > RES_MAX_MB * 1024 * 1024 && !/^image\//.test(f.type || "")) {
                    Toast.show(`文件超过 ${RES_MAX_MB}MB 上限`, "danger");
                    btn.classList.remove("btn--loading");
                    return;
                }
                // 图片先压缩（Canvas，质量 0.7、最长边 1280）——服务端零图像处理
                let out = f;
                if (/^image\//.test(f.type || "")) {
                    try {
                        out = await compressImage(f, { maxEdge: 1280, quality: 0.7 });
                        hint.textContent = `已压缩至 ${(out.size / 1024).toFixed(0)} KB`;
                    } catch (err) { out = f; }
                }
                formData.append("file", out, out.name);
            }
            const r = await uploadForm("/api/resources/upload", formData);
            Toast.show("上传成功" + (r.size_text ? "（" + r.size_text + "）" : ""), "success");
            form.reset();
            hint.textContent = "";
            modeSel.onchange();
            loadRes();
        } catch (err) { /* uploadForm 已提示 */ }
        btn.classList.remove("btn--loading");
    };
}

fetchJSON("/api/auth/me").then((r) => {
    RES_LOGGED_IN = !!r.logged_in;
    const box = document.getElementById("res-compose");
    if (box && !RES_LOGGED_IN) {
        box.innerHTML = '<div class="empty">' + icon("lock", 36) +
            '<div class="empty__text">登录后可上传资料</div>' +
            '<a class="btn btn--primary" href="/login">登录</a></div>';
    }
}).catch(() => {});
initUpload();
loadRes();
