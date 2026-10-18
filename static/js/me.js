/* ============================================================
   me.js —— 个人中心
   职责：头像 / 设置（深色模式·改昵称·改密码·退出）/ 打卡月历 /
        我的资料上传（可删）/ 我的投稿状态
   依赖：components.js（fetchJSON/Toast/Modal/Skeleton/icon/avatarHtml/fmtBeijing/bindCharCount）
   ============================================================ */
"use strict";

function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

document.querySelectorAll(".empty__art").forEach((el) => {
    if (!el.innerHTML.trim()) el.innerHTML = icon("user", 40);
});

/* ---------------- 汇总数字回填（首屏由服务端渲染，这里做一次校准） ---------------- */
fetchJSON("/api/me/summary").then((r) => {
    if (!r.logged_in) return;
    const g = r.game || {};
    const st = document.querySelectorAll(".me-stat__sub");
    if (st[0]) st[0].textContent = `${g.wins} 胜 · ${g.win_rate}% 胜率`;
}).catch(() => {});

/* ---------------- 头像 ---------------- */
(function () {
    const av = document.getElementById("me-avatar");
    const nick = document.getElementById("me-nickname");
    if (av && nick) av.innerHTML = avatarHtml(nick.textContent.trim(), "avatar--lg");
})();

/* ---------------- 设置：改昵称 ---------------- */
document.getElementById("me-nick-save").onclick = async () => {
    const input = document.getElementById("me-nick-input");
    const v = input.value.trim();
    if (!v) { Toast.show("昵称不能为空", "warning"); return; }
    try {
        const r = await fetchJSON("/api/auth/update_nickname", {
            method: "POST", body: { nickname: v },
        });
        Toast.show("昵称已更新", "success");
        const nick = document.getElementById("me-nickname");
        if (nick) {
            nick.textContent = r.nickname;
            const av = document.getElementById("me-avatar");
            if (av) av.innerHTML = avatarHtml(r.nickname, "avatar--lg");
        }
    } catch (e) { /* 已提示 */ }
};

/* ---------------- 设置：改密码 ---------------- */
document.getElementById("pwd-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const n1 = document.getElementById("pwd-new").value;
    const n2 = document.getElementById("pwd-new2").value;
    if (n1 !== n2) { Toast.show("两次输入的新密码不一致", "danger"); return; }
    try {
        await fetchJSON("/api/auth/change_password", {
            method: "POST",
            body: { old_password: document.getElementById("pwd-old").value, new_password: n1 },
        });
        Toast.show("密码已更新", "success");
        document.getElementById("pwd-form").reset();
    } catch (err) { /* 已提示 */ }
});

/* ---------------- 设置：退出 ---------------- */
document.getElementById("btn-logout").onclick = async () => {
    const sure = await Modal.confirm({
        title: "退出登录", body: "确定要退出当前账号吗？", okText: "退出", danger: false,
    });
    if (!sure) return;
    await fetchJSON("/logout", { method: "POST", body: {} });
    location.href = "/";
};

/* ---------------- 我的打卡月历（复用公共接口） ---------------- */
let meYear = 0, meMonth = 0;
function loadMeCalendar() {
    const box = document.getElementById("me-calendar");
    Skeleton.inject(box, 3, false);
    const q = meYear && meMonth ? `?year=${meYear}&month=${meMonth}` : "";
    fetchJSON("/api/checkin/calendar" + q).then((r) => {
        meYear = r.year; meMonth = r.month;
        const offset = (new Date(r.year, r.month - 1, 1).getDay() + 6) % 7;
        const cells = Array.from({ length: offset }, () =>
            '<span class="heat-cell heat-cell--blank"></span>').join("") +
            r.days.map((d) => {
                const cls = ["heat-cell"];
                if (d.checked) cls.push("heat-cell--on");
                if (d.is_today) cls.push("heat-cell--today");
                if (d.future) cls.push("heat-cell--future");
                return `<span class="${cls.join(" ")}" title="${d.date}">${parseInt(d.date.slice(-2), 10)}</span>`;
            }).join("");
        box.innerHTML = `
            <div class="flex-between mb-8">
                <b class="num">${r.year} 年 ${r.month} 月</b>
                <span class="text-faint" style="font-size:var(--fs-12)">本月打卡 ${r.active_days} 天</span>
            </div>
            <div class="heat-grid">
                ${["一", "二", "三", "四", "五", "六", "日"]
                    .map((w) => `<span class="heat-head">${w}</span>`).join("")}
                ${cells}
            </div>`;
    }).catch(() => {
        box.innerHTML = '<div class="empty">' + icon("alert", 36) +
            '<div class="empty__text">日历加载失败</div></div>';
    });
}
document.getElementById("me-ck-prev").onclick = () => {
    const d = new Date(meYear, meMonth - 2, 1);
    meYear = d.getFullYear(); meMonth = d.getMonth() + 1; loadMeCalendar();
};
document.getElementById("me-ck-next").onclick = () => {
    const d = new Date(meYear, meMonth, 1);
    meYear = d.getFullYear(); meMonth = d.getMonth() + 1; loadMeCalendar();
};

/* ---------------- 我的资料上传（可删） ---------------- */
function loadMyResources() {
    const box = document.getElementById("me-resources");
    Skeleton.inject(box, 3, false);
    fetchJSON("/api/resources/mine").then((r) => {
        const c = document.getElementById("me-res-count");
        const s = document.getElementById("me-res-size");
        if (c) c.textContent = r.count;
        if (s) s.textContent = r.total_size_text || "0 B";
        const list = r.list || [];
        if (!list.length) {
            box.innerHTML = '<div class="empty">' + icon("inbox", 36) +
                '<div class="empty__text">你还没有上传资料</div></div>';
            return;
        }
        box.innerHTML = list.map((x) => `
            <div class="res-item">
                <span class="res-item__icon">${x.rtype === "file" ? icon("file-text", 20) : icon("key", 20)}</span>
                <div class="res-item__body">
                    <div class="res-item__title truncate">${esc(x.title)}</div>
                    <div class="res-item__meta">
                        <span class="tag tag--primary">${esc(x.subject)}</span>
                        <span>${x.rtype === "file" ? esc(x.file_name) + " · " + esc(x.size_text) : "外部链接"}</span>
                        <span>下载 <b class="num">${x.downloads}</b></span>
                        <span>${fmtBeijing(x.created_at)}</span>
                    </div>
                </div>
                ${x.rtype === "file"
                    ? `<a class="btn btn--outline btn--sm" href="/api/resources/download/${x.id}">下载</a>`
                    : `<a class="btn btn--outline btn--sm" href="${esc(x.link)}" target="_blank"
                          rel="noopener noreferrer">打开</a>`}
                <button class="btn btn--ghost btn--sm" data-me-del="${x.id}">${icon("trash", 15)}</button>
            </div>`).join("");
        box.querySelectorAll("[data-me-del]").forEach((b) => {
            b.onclick = async () => {
                const sure = await Modal.confirm({
                    title: "删除资料", body: "确定删除？磁盘文件会同步删除并回收配额。", okText: "删除",
                });
                if (!sure) return;
                try {
                    const rr = await fetchJSON("/api/resources/delete", {
                        method: "POST", body: { id: parseInt(b.dataset.meDel, 10) },
                    });
                    Toast.show("已删除，释放 " + (rr.freed_text || "0 B"), "success");
                    loadMyResources();
                } catch (e) { /* 已提示 */ }
            };
        });
    }).catch(() => {
        box.innerHTML = '<div class="empty">' + icon("alert", 36) +
            '<div class="empty__text">加载失败</div></div>';
    });
}

/* ---------------- 我的投稿状态 ---------------- */
function loadMyQuotes() {
    const box = document.getElementById("me-quotes");
    Skeleton.inject(box, 3, false);
    fetchJSON("/api/quote/mine").then((r) => {
        const list = r.list || [];
        if (!list.length) {
            box.innerHTML = '<div class="empty">' + icon("heart", 36) +
                '<div class="empty__text">你还没有投过句子</div></div>';
            return;
        }
        const badge = (st) => ({
            pending: '<span class="tag tag--warning">审核中</span>',
            approved: '<span class="tag tag--success">已通过</span>',
            rejected: '<span class="tag tag--danger">未通过</span>',
        }[st] || `<span class="tag">${esc(st)}</span>`);
        box.innerHTML = list.map((q) => `
            <div class="res-item">
                <span class="res-item__icon" style="color:var(--c-primary)">${icon("heart", 20)}</span>
                <div class="res-item__body">
                    <div class="res-item__title">${esc(q.content)}</div>
                    <div class="res-item__meta">
                        ${badge(q.status)}
                        <span>${fmtBeijing(q.created_at)}</span>
                        ${q.status === "approved" ? `<span>❤️ <b class="num">${q.like_count}</b></span>` : ""}
                    </div>
                </div>
            </div>`).join("");
    }).catch(() => {
        box.innerHTML = '<div class="empty">' + icon("alert", 36) +
            '<div class="empty__text">加载失败</div></div>';
    });
}

loadMeCalendar();
loadMyResources();
loadMyQuotes();

/* ============================================================
   第六期：网站动态 / 管理快捷区 / 更新管理面板 / 重置码面板
   ============================================================ */
const ME_PERMS = (window.__ME__ && window.__ME__.perms) || [];

/* ---------------- 快捷区图标注入 ---------------- */
document.querySelectorAll("[data-mq-icon]").forEach((el) => {
    el.innerHTML = icon(el.dataset.mqIcon, 20);
});

/* ---------------- 我的已读更新记录（details 展开时惰性加载） ---------------- */
const readsBox = document.getElementById("me-reads-box");
if (readsBox) {
    let readsLoaded = false;
    readsBox.addEventListener("toggle", () => {
        if (!readsBox.open || readsLoaded) return;
        readsLoaded = true;
        const box = document.getElementById("me-reads");
        Skeleton.inject(box, 2, false);
        fetchJSON("/api/updates/my_reads").then((r) => {
            const list = r.list || [];
            if (!list.length) {
                box.innerHTML = '<div class="text-faint" style="font-size:var(--fs-13)">' +
                    '还没有读过任何更新</div>';
                return;
            }
            box.innerHTML = list.map((x) => `
                <div class="res-item">
                    <div class="res-item__body">
                        <div class="res-item__title truncate">${esc(x.title)}</div>
                        <div class="res-item__meta">
                            <span class="tag tag--primary">${esc(x.type_label)}</span>
                            ${x.version_tag ? `<span class="tag num">${esc(x.version_tag)}</span>` : ""}
                            <span>读于 ${fmtBeijing(x.read_at)}</span>
                        </div>
                    </div>
                </div>`).join("");
        }).catch(() => {
            box.innerHTML = '<div class="text-faint">加载失败</div>';
        });
    });
}

/* ---------------- 网站更新管理面板（update.manage） ---------------- */
const updAdminBox = document.getElementById("upd-admin-list");

function updTypeBadge(t) {
    return { preview: '<span class="upd-badge upd-badge--preview">预告</span>',
             notice: '<span class="upd-badge upd-badge--notice">通知</span>',
             release: '<span class="upd-badge upd-badge--release">更新</span>' }[t] || "";
}

function loadUpdAdmin() {
    if (!updAdminBox) return;
    Skeleton.inject(updAdminBox, 3, false);
    fetchJSON("/api/admin-panel/updates/list").then((r) => {
        const list = r.list || [];
        if (!list.length) {
            updAdminBox.innerHTML = '<div class="empty">' + icon("megaphone", 36) +
                '<div class="empty__text">还没有更新公告，点右上角「新建」发第一条</div></div>';
            return;
        }
        updAdminBox.innerHTML = list.map((x) => `
            <div class="res-item">
                <div class="res-item__body">
                    <div class="res-item__title truncate">
                        ${x.is_pinned ? "📌 " : ""}${esc(x.title)}</div>
                    <div class="res-item__meta">
                        ${updTypeBadge(x.update_type)}
                        ${x.version_tag ? `<span class="tag num">${esc(x.version_tag)}</span>` : ""}
                        ${x.is_published
                            ? '<span class="tag tag--success">已发布</span>'
                            : '<span class="tag">草稿</span>'}
                        <span>${x.is_published ? fmtBeijing(x.publish_at) : "创建于 " + fmtBeijing(x.created_at)}</span>
                    </div>
                </div>
                <div class="flex gap-8" style="flex-shrink:0;flex-wrap:wrap">
                    <button class="btn btn--ghost btn--sm" data-upd-edit="${x.id}">${icon("edit", 14)}</button>
                    ${x.is_published
                        ? `<button class="btn btn--outline btn--sm" data-upd-unpub="${x.id}">下架</button>`
                        : `<button class="btn btn--primary btn--sm" data-upd-pub="${x.id}">发布</button>`}
                    <button class="btn btn--ghost btn--sm" data-upd-pin="${x.id}" data-pinned="${x.is_pinned ? 1 : 0}">
                        ${x.is_pinned ? "取消置顶" : "置顶"}</button>
                </div>
            </div>`).join("");
        bindUpdAdminActions(list);
    }).catch(() => {
        updAdminBox.innerHTML = '<div class="empty">' + icon("alert", 36) +
            '<div class="empty__text">加载失败</div></div>';
    });
}

/* 新建 / 编辑表单弹窗（textarea 带字数统计；content 服务端转义后存储） */
function updFormModal(item) {
    const isEdit = !!item;
    const overlay = Modal.open({
        title: isEdit ? "编辑更新" : "新建更新",
        body: `
        <div class="field"><label class="field__label">类型（三选一）</label>
            <div class="chip-row" id="uf-type">
                <button type="button" class="chip" data-t="preview">预告</button>
                <button type="button" class="chip" data-t="notice">通知</button>
                <button type="button" class="chip" data-t="release">更新</button>
            </div>
        </div>
        <div class="field"><label class="field__label">标题</label>
            <input class="input" id="uf-title" maxlength="60"
                   value="${isEdit ? esc(item.title) : ""}"></div>
        <div class="field"><label class="field__label">内容（支持换行 / **加粗** / - 列表）</label>
            <textarea class="input" id="uf-content" rows="6" data-max="2000"
                placeholder="- 新增 xxx&#10;- 修复 yyy&#10;- 优化 zzz">${isEdit ? esc(item.content) : ""}</textarea>
            <div class="text-faint mt-8" style="font-size:var(--fs-12)" id="uf-count"></div>
        </div>
        <div class="field" id="uf-ver-box"><label class="field__label">版本号（更新类型必填，如 v1.5.0）</label>
            <input class="input num" id="uf-version" maxlength="20" placeholder="v1.5.0"
                   value="${isEdit ? esc(item.version_tag) : ""}"></div>
        <label class="flex gap-8" style="align-items:center;font-size:var(--fs-13)">
            <input type="checkbox" id="uf-pinned" ${isEdit && item.is_pinned ? "checked" : ""}> 置顶显示
        </label>`,
        foot: `<button class="btn btn--secondary" data-uf="cancel">取消</button>
               <button class="btn btn--outline" data-uf="draft">${isEdit ? "保存" : "存为草稿"}</button>
               <button class="btn btn--primary" data-uf="publish">${isEdit ? "保存" : "保存并发布"}</button>`,
    });
    let curType = isEdit ? item.update_type : "notice";
    const typeBox = overlay.querySelector("#uf-type");
    const verBox = overlay.querySelector("#uf-ver-box");
    const syncType = () => {
        typeBox.querySelectorAll(".chip").forEach((c) =>
            c.classList.toggle("chip--active", c.dataset.t === curType));
        verBox.style.display = curType === "release" ? "" : "none";
    };
    typeBox.querySelectorAll(".chip").forEach((c) => {
        c.onclick = () => { curType = c.dataset.t; syncType(); };
    });
    syncType();
    bindCharCount(overlay.querySelector("#uf-content"), overlay.querySelector("#uf-count"));

    const payload = () => ({
        update_type: curType,
        title: overlay.querySelector("#uf-title").value.trim(),
        content: overlay.querySelector("#uf-content").value,
        version_tag: overlay.querySelector("#uf-version").value.trim(),
        is_pinned: overlay.querySelector("#uf-pinned").checked,
    });
    overlay.querySelector('[data-uf="cancel"]').onclick = () => Modal.close(overlay);
    const submit = async (publish) => {
        const p = payload();
        if (!p.title) { Toast.show("标题不能为空", "warning"); return; }
        if (!p.content.trim()) { Toast.show("内容不能为空", "warning"); return; }
        if (curType === "release" && !p.version_tag) {
            Toast.show("更新类型必须填写版本号", "warning"); return;
        }
        if (publish && !isEdit) {
            const sure = await Modal.confirm({
                title: "确认发布", body: "发布后全体同学可见，且会产生未读红点。确定发布？",
                okText: "发布", danger: false,
            });
            if (!sure) return;
        }
        try {
            if (isEdit) {
                await fetchJSON("/api/admin-panel/updates/update", {
                    method: "POST", body: Object.assign({ id: item.id }, p),
                });
                Toast.show("已保存", "success");
            } else {
                await fetchJSON("/api/admin-panel/updates/create", {
                    method: "POST", body: Object.assign({ publish }, p),
                });
                Toast.show(publish ? "已发布" : "草稿已保存", "success");
            }
            Modal.close(overlay);
            loadUpdAdmin();
        } catch (e) { /* 已提示 */ }
    };
    overlay.querySelector('[data-uf="draft"]').onclick = () => submit(false);
    overlay.querySelector('[data-uf="publish"]').onclick = () => submit(true);
}

function bindUpdAdminActions(list) {
    updAdminBox.querySelectorAll("[data-upd-edit]").forEach((b) => {
        b.onclick = () => {
            const item = list.find((x) => x.id === parseInt(b.dataset.updEdit, 10));
            if (item) updFormModal(item);
        };
    });
    updAdminBox.querySelectorAll("[data-upd-pub]").forEach((b) => {
        b.onclick = async () => {
            const sure = await Modal.confirm({
                title: "确认发布", body: "发布后全体同学可见，且会产生未读红点。确定发布？",
                okText: "发布", danger: false,
            });
            if (!sure) return;
            try {
                await fetchJSON("/api/admin-panel/updates/publish", {
                    method: "POST", body: { id: parseInt(b.dataset.updPub, 10) },
                });
                Toast.show("已发布", "success");
                loadUpdAdmin();
            } catch (e) { /* 已提示 */ }
        };
    });
    updAdminBox.querySelectorAll("[data-upd-unpub]").forEach((b) => {
        b.onclick = async () => {
            const sure = await Modal.confirm({
                title: "确认下架", body: "下架后前台不可见（数据保留，可重新发布）。",
                okText: "下架",
            });
            if (!sure) return;
            try {
                await fetchJSON("/api/admin-panel/updates/unpublish", {
                    method: "POST", body: { id: parseInt(b.dataset.updUnpub, 10) },
                });
                Toast.show("已下架", "success");
                loadUpdAdmin();
            } catch (e) { /* 已提示 */ }
        };
    });
    updAdminBox.querySelectorAll("[data-upd-pin]").forEach((b) => {
        b.onclick = async () => {
            try {
                await fetchJSON("/api/admin-panel/updates/pin", {
                    method: "POST",
                    body: { id: parseInt(b.dataset.updPin, 10), pinned: b.dataset.pinned !== "1" },
                });
                loadUpdAdmin();
            } catch (e) { /* 已提示 */ }
        };
    });
}

const updNewBtn = document.getElementById("upd-new");
if (updNewBtn) updNewBtn.onclick = () => updFormModal(null);
if (updAdminBox) loadUpdAdmin();

/* ---------------- 账号管理 · 重置码面板（user.manage） ---------------- */
const rcBox = document.getElementById("rc-results");
const RC_STATUS = { active: ["生效中", "tag--success"], used: ["已使用/已作废", ""],
                    expired: ["已过期", "tag--warning"], none: ["未生成", ""] };

/* 单个用户的码状态行（含剩余有效期倒计时，不显示码本身） */
function rcCodeLine(u) {
    const c = u.code || {};
    const [label, cls] = RC_STATUS[c.status || "none"] || RC_STATUS.none;
    const cd = c.status === "active"
        ? ` · 剩余 <b class="num" data-rc-cd="${u.id}">${Math.floor(c.remaining_seconds / 60)}分${c.remaining_seconds % 60}秒</b>` : "";
    return `<span class="tag ${cls}">${label}</span>${cd}`;
}

function rcSearch() {
    const kw = document.getElementById("rc-kw").value.trim();
    if (!kw) { Toast.show("请输入搜索关键词", "warning"); return; }
    Skeleton.inject(rcBox, 2, false);
    fetchJSON("/api/reset-code/search_users?kw=" + encodeURIComponent(kw)).then((r) => {
        const list = r.list || [];
        if (!list.length) {
            rcBox.innerHTML = '<div class="empty">' + icon("users", 36) +
                '<div class="empty__text">没有匹配的用户</div></div>';
            return;
        }
        rcBox.innerHTML = list.map((u) => `
            <div class="res-item">
                <span class="res-item__icon">${avatarHtml(u.nickname, "avatar--sm")}</span>
                <div class="res-item__body">
                    <div class="res-item__title">${esc(u.nickname)}
                        <span class="text-faint" style="font-size:var(--fs-12)">@${esc(u.username)}</span>
                        ${u.is_superadmin ? '<span class="tag tag--danger">超管</span>' : ""}
                        ${u.is_banned ? '<span class="tag tag--warning">已封禁</span>' : ""}</div>
                    <div class="res-item__meta">${rcCodeLine(u)}</div>
                </div>
                <button class="btn btn--primary btn--sm" data-rc-gen="${u.id}"
                        data-nick="${esc(u.nickname)}"
                        ${u.is_superadmin ? "disabled" : ""} style="flex-shrink:0">生成重置码</button>
            </div>`).join("");
        rcBox.querySelectorAll("[data-rc-gen]").forEach((b) => {
            b.onclick = () => rcGenerate(parseInt(b.dataset.rcGen, 10), b.dataset.nick);
        });
    }).catch(() => {
        rcBox.innerHTML = '<div class="empty">' + icon("alert", 36) +
            '<div class="empty__text">搜索失败</div></div>';
    });
}

/* 生成：Modal 显示 8 位明文码（只此一次）+ 复制按钮 + 关闭后轮询状态 */
async function rcGenerate(uid, nick) {
    const sure = await Modal.confirm({
        title: "生成重置码",
        body: `将为 <b>${esc(nick)}</b> 生成新的 8 位重置码，其旧码（如有）立即作废。确定？`,
        okText: "生成", danger: false,
    });
    if (!sure) return;
    try {
        const r = await fetchJSON("/api/reset-code/generate", {
            method: "POST", body: { user_id: uid },
        });
        const overlay = Modal.open({
            title: "重置码已生成（只显示这一次）",
            body: `
            <p class="text-muted" style="font-size:var(--fs-13)">
                请立即把下面的码转达给 <b>${esc(r.target_nickname)}</b>（@${esc(r.target_username)}）：
            </p>
            <div class="rc-code num" id="rc-plain">${esc(r.code)}</div>
            <p class="text-faint mt-8" style="font-size:var(--fs-12)">
                15 分钟内有效 · 仅可使用一次 · 关闭本窗口后将无法再次查看</p>`,
            foot: `<button class="btn btn--outline" data-rc="copy">复制</button>
                   <button class="btn btn--primary" data-rc="done">我已转达</button>`,
        });
        overlay.querySelector('[data-rc="copy"]').onclick = async () => {
            try {
                await navigator.clipboard.writeText(r.code);
                Toast.show("已复制", "success", 1200);
            } catch (e) {
                /* 剪贴板不可用时退化为全选提示 */
                Toast.show("请手动长按复制：" + r.code, "warning", 3000);
            }
        };
        overlay.querySelector('[data-rc="done"]').onclick = () => {
            Modal.close(overlay);
            rcSearch();  /* 刷新状态行（倒计时） */
        };
    } catch (e) { /* 已提示（含禁止给超管/本人生成的明确错误） */ }
}

const rcSearchBtn = document.getElementById("rc-search");
if (rcSearchBtn) {
    rcSearchBtn.onclick = rcSearch;
    document.getElementById("rc-kw").addEventListener("keydown", (e) => {
        if (e.key === "Enter") rcSearch();
    });
    /* 状态行倒计时：每秒本地递减（服务端 status 接口按需在搜索时取，零轮询） */
    setInterval(() => {
        document.querySelectorAll("[data-rc-cd]").forEach((el) => {
            const m = el.textContent.match(/(\d+)分(\d+)秒/);
            if (!m) return;
            let s = parseInt(m[1], 10) * 60 + parseInt(m[2], 10) - 1;
            if (s <= 0) { el.textContent = "已过期"; return; }
            el.textContent = Math.floor(s / 60) + "分" + (s % 60) + "秒";
        });
    }, 1000);
}
