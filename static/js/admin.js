/* ============================================================
   admin.js —— 管理后台单页逻辑
   菜单按 /api/auth/me 返回的权限动态渲染（无权限模块不显示）；
   危险操作（封禁/重置密码/作废/删除/清理）一律 Modal 二次确认；
   所有写操作服务端自动写审计日志。
   依赖：components.js（Toast/Modal/fetchJSON/fmtBeijing）+ icons.js
   ============================================================ */
"use strict";

/* ---------------- 模块注册表（perm 为菜单可见所需权限） ---------------- */
const ADMIN_MODULES = [
    { key: "overview", name: "数据总览", icon: "grid", perm: "admin.access" },
    { key: "users", name: "用户管理", icon: "users", perm: "user.manage" },
    { key: "invites", name: "邀请码", icon: "key", perm: "invite.manage" },
    { key: "announcements", name: "公告管理", icon: "megaphone", perm: "announcement.manage" },
    { key: "news", name: "新闻源与同步", icon: "file-text", perm: "news.manage" },
    { key: "digest", name: "每日总结", icon: "clock", perm: "news.manage" },
    { key: "tucao", name: "吐槽审核", icon: "message", perm: "tucao.moderate" },
    { key: "sensitive", name: "敏感词库", icon: "lock", perm: "tucao.moderate" },
    { key: "activities", name: "活动与比赛", icon: "calendar", perm: "activity.manage" },
    { key: "homework", name: "作业管理", icon: "file-text", perm: "homework.manage" },
    { key: "resources", name: "资料库", icon: "inbox", perm: "resource.manage" },
    { key: "duty", name: "值日管理", icon: "grid", perm: "duty.manage" },
    { key: "honors", name: "荣誉管理", icon: "trophy", perm: "honor.manage" },
    { key: "quote", name: "每日一句审核", icon: "heart", perm: "quote.moderate" },
    { key: "checkin", name: "打卡挑战", icon: "target", perm: "checkin.manage" },
    { key: "roster", name: "点名名单", icon: "zap", perm: "roster.manage" },
    { key: "links", name: "资源导航", icon: "link", perm: "link.manage" },
    { key: "settings", name: "站点设置", icon: "settings", perm: "admin.access" },
    /* 登录日志仅 root 角色或 admin.full 可见（init 里做自定义判断） */
    { key: "loginlogs", name: "登录日志", icon: "shield", perm: "admin.full" },
    { key: "audit", name: "审计日志", icon: "file-text", perm: "audit.view" },
    { key: "game", name: "游戏房间", icon: "gamepad", perm: "game.manage" },
];

let ADMIN_PERMS = [];
let ADMIN_ROLE = "";
let curModule = "";

/* ---------------- 入口 ---------------- */
(async function init() {
    let me;
    try {
        me = await fetchJSON("/api/auth/me");
    } catch (e) { return; }
    ADMIN_PERMS = me.perms || [];
    ADMIN_ROLE = (me.user && me.user.role_name) || "";
    const visible = ADMIN_MODULES.filter((m) => {
        /* 登录日志：root 角色或超管（admin.full）专属 */
        if (m.key === "loginlogs") {
            return ADMIN_PERMS.includes("admin.full") || ADMIN_ROLE === "root";
        }
        return ADMIN_PERMS.includes(m.perm);
    });
    if (!visible.length) return;

    // 渲染侧边菜单
    const menu = document.getElementById("admin-menu");
    menu.innerHTML = visible.map((m) =>
        `<button class="admin-menu__item" data-mod="${m.key}">${icon(m.icon, 17)} ${m.name}</button>`
    ).join("");
    menu.querySelectorAll("[data-mod]").forEach((b) => {
        b.onclick = () => switchModule(b.dataset.mod);
    });
    /* 第六期：hash 路由——/admin#announcements 等直达链接（/me 管理快捷区用） */
    const fromHash = location.hash.replace(/^#/, "");
    const first = visible.some((m) => m.key === fromHash)
        ? fromHash : visible[0].key;
    switchModule(first);
})();

function switchModule(key) {
    curModule = key;
    if (("#" + key) !== location.hash) {
        history.replaceState(null, "", "#" + key);  /* 可分享的直达链接 */
    }
    document.querySelectorAll(".admin-menu__item").forEach((b) =>
        b.classList.toggle("admin-menu__item--active", b.dataset.mod === key));
    const c = document.getElementById("admin-content");
    ({
        overview: renderOverview, users: renderUsers, invites: renderInvites,
        announcements: renderAnnouncements, audit: renderAudit, game: renderGame,
        news: renderNewsAdmin, digest: renderDigestAdmin,
        tucao: renderTucaoAdmin, sensitive: renderSensitiveAdmin,
        activities: renderActAdmin, homework: renderHwAdmin,
        resources: renderResAdmin, duty: renderDutyAdmin, honors: renderHonorAdmin,
        quote: renderQuoteAdmin, checkin: renderCheckinAdmin, roster: renderRosterAdmin,
        links: renderLinksAdmin, settings: renderSiteSettings, loginlogs: renderLoginLogs,
    }[key])(c);
}

function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ================= 数据总览 ================= */
async function renderOverview(c) {
    Skeleton.inject(c, 3);
    const r = await fetchJSON("/api/admin/overview");
    c.innerHTML = `<div class="card">
        <div class="card__title">${icon("grid", 18)} 数据总览</div>
        <div class="admin-stat">
            <div class="admin-stat__item"><div class="admin-stat__num num">${r.user_count}</div>
                <div class="admin-stat__label">注册用户</div></div>
            <div class="admin-stat__item"><div class="admin-stat__num num">${r.active_rooms}</div>
                <div class="admin-stat__label">进行中房间</div></div>
            <div class="admin-stat__item"><div class="admin-stat__num num">${r.audit_count}</div>
                <div class="admin-stat__label">审计记录</div></div>
            <div class="admin-stat__item"><div class="admin-stat__num num">${r.invite_count}</div>
                <div class="admin-stat__label">有效邀请码</div></div>
        </div>
    </div>`;
}

/* ================= 用户管理 ================= */
let roleCache = [];
async function renderUsers(c) {
    Skeleton.inject(c, 5);
    const [r, roles] = await Promise.all([
        fetchJSON("/api/admin/users"),
        fetchJSON("/api/admin/roles"),
    ]);
    roleCache = roles.list || [];
    const roleOpts = roleCache.map((ro) =>
        `<option value="${ro.id}">${esc(ro.display_name)}</option>`).join("");
    c.innerHTML = `<div class="card">
        <div class="card__title">${icon("users", 18)} 用户管理（${r.list.length}）</div>
        <div style="overflow-x:auto"><table class="admin-table">
        <thead><tr><th>ID</th><th>用户</th><th>角色</th><th>状态</th><th>注册时间(北京)</th><th>操作</th></tr></thead>
        <tbody>${r.list.map((u) => `<tr>
            <td class="num">${u.id}</td>
            <td>${avatarHtml(u.nickname, "avatar--sm")} <b>${esc(u.nickname)}</b>
                <span class="text-faint" style="font-size:12px">@${esc(u.username)}</span></td>
            <td><select class="input" style="min-height:34px;width:auto" data-role-uid="${u.id}">
                ${roleCache.map((ro) =>
                    `<option value="${ro.id}" ${ro.id === u.role_id ? "selected" : ""}>${esc(ro.display_name)}</option>`).join("")}
                </select></td>
            <td>${u.is_banned ? '<span class="tag tag--danger">已封禁</span>' : '<span class="tag tag--success">正常</span>'}</td>
            <td class="num">${fmtBeijing(u.created_at)}</td>
            <td style="white-space:nowrap">
                <button class="btn btn--sm ${u.is_banned ? "btn--secondary" : "btn--danger"}"
                    data-ban="${u.id}" data-banned="${u.is_banned ? 1 : 0}" data-nick="${esc(u.nickname)}">
                    ${u.is_banned ? "解封" : "封禁"}</button>
                <button class="btn btn--sm btn--outline" data-reset="${u.id}" data-nick="${esc(u.nickname)}">重置密码</button>
            </td></tr>`).join("")}
        </tbody></table></div></div>`;

    // 封禁/解封（二次确认）
    c.querySelectorAll("[data-ban]").forEach((b) => {
        b.onclick = async () => {
            const banned = b.dataset.banned !== "1";
            const sure = await Modal.confirm({
                title: banned ? "封禁用户" : "解封用户",
                body: `确定${banned ? "封禁" : "解封"}用户 <b>${b.dataset.nick}</b> 吗？`,
                okText: banned ? "封禁" : "解封", danger: banned,
            });
            if (!sure) return;
            await fetchJSON("/api/admin/users/ban", {
                method: "POST", body: { user_id: parseInt(b.dataset.ban, 10), banned },
            });
            Toast.show(banned ? "已封禁" : "已解封", "success");
            renderUsers(c);
        };
    });
    // 重置密码（二次确认）
    c.querySelectorAll("[data-reset]").forEach((b) => {
        b.onclick = async () => {
            const sure = await Modal.confirm({
                title: "重置密码",
                body: `确定重置 <b>${b.dataset.nick}</b> 的密码吗？将生成临时密码。`,
                okText: "重置",
            });
            if (!sure) return;
            const r2 = await fetchJSON("/api/admin/users/reset_password", {
                method: "POST", body: { user_id: parseInt(b.dataset.reset, 10) },
            });
            Modal.open({
                title: "临时密码已生成",
                body: `请通过安全渠道告知该同学：<br><b class="num" style="font-size:18px">${esc(r2.temp_password)}</b><br><span class="text-faint" style="font-size:12px">此密码仅显示一次</span>`,
                foot: '<button class="btn btn--primary" onclick="Modal.close()">知道了</button>',
            });
        };
    });
    // 改角色（二次确认）
    c.querySelectorAll("[data-role-uid]").forEach((sel) => {
        sel.onchange = async () => {
            const sure = await Modal.confirm({
                title: "修改角色", body: "确定修改该用户的角色吗？", okText: "修改", danger: false,
            });
            if (!sure) { renderUsers(c); return; }
            await fetchJSON("/api/admin/users/role", {
                method: "POST",
                body: { user_id: parseInt(sel.dataset.roleUid, 10), role_id: parseInt(sel.value, 10) },
            });
            Toast.show("角色已更新", "success");
        };
    });
}

/* ================= 邀请码管理 ================= */
async function renderInvites(c) {
    Skeleton.inject(c, 4);
    const r = await fetchJSON("/api/admin/invites");
    c.innerHTML = `<div class="card">
        <div class="flex-between">
            <div class="card__title" style="margin-bottom:0">${icon("key", 18)} 邀请码</div>
            <div class="flex gap-8">
                <select class="input" id="inv-uses" style="min-height:38px;width:auto">
                    <option value="1">限 1 次</option><option value="5">限 5 次</option>
                    <option value="20">限 20 次</option><option value="100">限 100 次</option>
                </select>
                <select class="input" id="inv-days" style="min-height:38px;width:auto">
                    <option value="1">1 天</option><option value="7" selected>7 天</option>
                    <option value="30">30 天</option><option value="90">90 天</option>
                </select>
                <button class="btn btn--primary" id="inv-gen">${icon("plus", 16)} 生成</button>
            </div>
        </div>
        <div id="inv-result"></div>
        <div style="overflow-x:auto" class="mt-16"><table class="admin-table">
        <thead><tr><th>邀请码</th><th>使用</th><th>有效期(北京)</th><th>状态</th><th>创建人</th><th>操作</th></tr></thead>
        <tbody>${r.list.map((iv) => `<tr>
            <td><b class="num" style="letter-spacing:2px">${esc(iv.code)}</b>
                <button class="btn btn--sm btn--ghost" data-copy="${esc(iv.code)}" title="复制">${icon("copy", 14)}</button></td>
            <td class="num">${iv.used_count}/${iv.max_uses}</td>
            <td class="num">${fmtBeijing(iv.expires_at)}</td>
            <td>${iv.revoked ? '<span class="tag tag--danger">已作废</span>'
                : iv.expired ? '<span class="tag tag--warning">已过期</span>'
                : '<span class="tag tag--success">有效</span>'}</td>
            <td>${esc(iv.creator)}</td>
            <td>${iv.revoked ? "—" : `<button class="btn btn--sm btn--danger" data-revoke="${iv.id}" data-code="${esc(iv.code)}">作废</button>`}</td>
            </tr>`).join("") || '<tr><td colspan="6" class="text-center text-faint">还没有邀请码</td></tr>'}
        </tbody></table></div></div>`;

    document.getElementById("inv-gen").onclick = async () => {
        const r2 = await fetchJSON("/api/admin/invites/generate", {
            method: "POST",
            body: {
                max_uses: parseInt(document.getElementById("inv-uses").value, 10),
                valid_days: parseInt(document.getElementById("inv-days").value, 10),
            },
        });
        document.getElementById("inv-result").innerHTML =
            `<div class="tag tag--primary mt-16" style="font-size:15px;padding:8px 16px">
                新邀请码：<b class="num" style="letter-spacing:3px">${esc(r2.code)}</b>（已写入列表）</div>`;
        Toast.show("邀请码已生成", "success");
        renderInvites(c);
    };
    c.querySelectorAll("[data-revoke]").forEach((b) => {
        b.onclick = async () => {
            const sure = await Modal.confirm({
                title: "作废邀请码", body: `确定作废邀请码 <b>${b.dataset.code}</b> 吗？`, okText: "作废",
            });
            if (!sure) return;
            await fetchJSON("/api/admin/invites/revoke", {
                method: "POST", body: { invite_id: parseInt(b.dataset.revoke, 10) },
            });
            Toast.show("已作废", "success");
            renderInvites(c);
        };
    });
    c.querySelectorAll("[data-copy]").forEach((b) => {
        b.onclick = () => {
            (navigator.clipboard ? navigator.clipboard.writeText(b.dataset.copy) : Promise.reject())
                .then(() => Toast.show("已复制", "success", 1200))
                .catch(() => prompt("复制邀请码：", b.dataset.copy));
        };
    });
}

/* ================= 公告管理 ================= */
async function renderAnnouncements(c) {
    Skeleton.inject(c, 4);
    const r = await fetchJSON("/api/admin/announcements");
    c.innerHTML = `<div class="card">
        <div class="card__title">${icon("megaphone", 18)} 公告管理</div>
        <div class="field">
            <label class="field__label">标题（≤60 字）</label>
            <input class="input" id="ann-title" maxlength="60" placeholder="例如：运动会报名开始">
        </div>
        <div class="field">
            <label class="field__label">正文（≤2000 字，可留空）</label>
            <textarea class="input" id="ann-content" data-max="2000" maxlength="2000"
                placeholder="公告详细内容…"></textarea>
            <div class="field__count" id="ann-count"></div>
        </div>
        <button class="btn btn--primary" id="ann-pub">${icon("send", 16)} 发布公告</button>
        <div class="mt-24" id="ann-list">${r.list.map((a) => `
            <div class="flex-between gap-12" style="padding:10px 0;border-bottom:1px solid var(--border-1)">
                <div style="min-width:0">
                    <b>${esc(a.title)}</b>
                    <div class="text-faint" style="font-size:12px">${esc(a.author)} · ${fmtBeijing(a.created_at)}</div>
                </div>
                <button class="btn btn--sm btn--danger" data-del="${a.id}" data-title="${esc(a.title)}">删除</button>
            </div>`).join("") || '<div class="text-faint text-center mt-16">暂无公告</div>'}
        </div></div>`;

    bindCharCount(document.getElementById("ann-content"), document.getElementById("ann-count"));
    document.getElementById("ann-pub").onclick = async () => {
        const title = document.getElementById("ann-title").value.trim();
        if (!title) { Toast.show("标题不能为空", "warning"); return; }
        await fetchJSON("/api/admin/announcements/create", {
            method: "POST",
            body: { title, content: document.getElementById("ann-content").value.trim() },
        });
        Toast.show("公告已发布", "success");
        renderAnnouncements(c);
    };
    c.querySelectorAll("[data-del]").forEach((b) => {
        b.onclick = async () => {
            const sure = await Modal.confirm({
                title: "删除公告", body: `确定删除公告《${b.dataset.title}》吗？`, okText: "删除",
            });
            if (!sure) return;
            await fetchJSON("/api/admin/announcements/delete", {
                method: "POST", body: { id: parseInt(b.dataset.del, 10) },
            });
            Toast.show("已删除", "success");
            renderAnnouncements(c);
        };
    });
}

/* ================= 审计日志 ================= */
async function renderAudit(c) {
    Skeleton.inject(c, 6);
    const r = await fetchJSON("/api/admin/audit");
    c.innerHTML = `<div class="card">
        <div class="card__title">${icon("file-text", 18)} 审计日志（最近 ${r.list.length} 条）</div>
        <div style="overflow-x:auto"><table class="admin-table">
        <thead><tr><th>时间(北京)</th><th>操作人</th><th>动作</th><th>对象</th><th>详情</th><th>IP</th></tr></thead>
        <tbody>${r.list.map((a) => `<tr>
            <td class="num" style="white-space:nowrap">${fmtBeijing(a.created_at)}</td>
            <td>${esc(a.username)}</td>
            <td><span class="tag">${esc(a.action)}</span></td>
            <td>${esc(a.target)}</td>
            <td class="text-muted">${esc(a.detail)}</td>
            <td class="num text-faint">${esc(a.ip)}</td></tr>`).join("")}
        </tbody></table></div></div>`;
}

/* ================= 游戏房间清理 ================= */
async function renderGame(c) {
    c.innerHTML = `<div class="card">
        <div class="card__title">${icon("gamepad", 18)} 游戏房间管理</div>
        <p class="text-muted">平台无定时任务，这里手动清理 <b>10 分钟无活动</b> 的房间
            （标记为 abandoned，不影响进行中的对局）。</p>
        <button class="btn btn--danger mt-16" id="cleanup-btn">${icon("refresh", 16)} 立即清理闲置房间</button>
        <div id="cleanup-result" class="mt-16"></div>
    </div>`;
    document.getElementById("cleanup-btn").onclick = async () => {
        const sure = await Modal.confirm({
            title: "清理闲置房间",
            body: "将把 10 分钟无活动的房间标记为废弃，确定执行吗？",
            okText: "清理",
        });
        if (!sure) return;
        const r = await fetchJSON("/api/admin/cleanup_rooms", { method: "POST", body: {} });
        document.getElementById("cleanup-result").innerHTML =
            `<span class="tag tag--success">本次清理 ${r.cleaned} 个房间</span>`;
        Toast.show(`清理完成：${r.cleaned} 个房间`, "success");
    };
}

/* ================= 第二期：新闻源与同步状态 ================= */
async function renderNewsAdmin(c) {
    Skeleton.inject(c, 4);
    const r = await fetchJSON("/api/admin/news/status");
    const st = r.status || {};
    const bad = !st.ok && st.error;
    c.innerHTML = `<div class="card">
        <div class="card__title">${icon("file-text", 18)} 新闻源与同步状态</div>
        <div class="admin-stat">
            <div class="admin-stat__item">
                <div class="admin-stat__num num">${st.item_count || 0}</div>
                <div class="admin-stat__label">已入库新闻</div></div>
            <div class="admin-stat__item">
                <div class="admin-stat__num num">${st.ttl || 300}s</div>
                <div class="admin-stat__label">本地缓存 TTL</div></div>
            <div class="admin-stat__item">
                <div class="admin-stat__num" style="font-size:var(--fs-18)">
                    ${st.last_sync_at > 0 ? fmtBeijing(st.last_sync_at) : "从未同步"}</div>
                <div class="admin-stat__label">上次同步（北京时间）</div></div>
            <div class="admin-stat__item">
                <div class="admin-stat__num" style="font-size:var(--fs-18)">
                    ${bad ? '<span class="text-danger">异常</span>' : '<span class="text-success">正常</span>'}</div>
                <div class="admin-stat__label">同步状态</div></div>
        </div>
        ${bad ? `<p class="mt-16 text-danger" style="font-size:var(--fs-13)">
            ⚠️ 上次同步失败：${esc(st.error)}<br>
            <span class="text-muted">站点已自动降级为展示旧缓存，不会白屏。
            请检查 NEWS_REPO 仓库名是否正确、仓库是否公开、Actions 是否已产出 news/ 数据。</span></p>` : ""}
        <p class="text-muted mt-16" style="font-size:var(--fs-12)">
            仓库：<b>${esc(st.repo || "-")}</b> ｜ 分支：<b>${esc(st.branch || "-")}</b>
            （仅访问 api.github.com 与 raw.githubusercontent.com，均在平台白名单内）
        </p>
        <button class="btn btn--primary mt-16" id="news-sync-btn">
            ${icon("refresh", 16)} 立即同步（忽略缓存 TTL）</button>
        <div id="news-sync-result" class="mt-16"></div>

        <div class="card__title mt-24">本地来源分布</div>
        <div class="chips">${(st.sources || []).map((s) =>
            `<span class="chip">${esc(s.name)} <b class="num">${s.count}</b></span>`).join("")
            || '<span class="text-faint">尚无数据</span>'}</div>

        <p class="text-faint mt-16" style="font-size:var(--fs-12)">
            数据链路：GitHub Actions 每 20 分钟抓取 → 本站缓存 5 分钟 → 用户可见，
            端到端延迟约 20~25 分钟。
        </p>
    </div>`;
    document.getElementById("news-sync-btn").onclick = async () => {
        const btn = document.getElementById("news-sync-btn");
        btn.classList.add("btn--loading");
        try {
            const r2 = await fetchJSON("/api/admin/news/sync", { method: "POST", body: {} });
            const box = document.getElementById("news-sync-result");
            if (r2.error) {
                box.innerHTML = `<span class="tag tag--danger">同步失败：${esc(r2.error)}</span>
                    <div class="text-muted mt-8" style="font-size:var(--fs-12)">
                    已保留旧数据；修复仓库配置后可再试。</div>`;
            } else {
                box.innerHTML = `<span class="tag tag--success">新增新闻 ${r2.added} 条
                    · 新增总结 ${r2.digests_added} 份</span>`;
            }
            renderNewsAdmin(c);
        } catch (e) { /* 已 Toast */ }
        btn.classList.remove("btn--loading");
    };
}

/* ================= 第二期：每日总结管理 ================= */
async function renderDigestAdmin(c) {
    Skeleton.inject(c, 4);
    const r = await fetchJSON("/api/admin/digest/list");
    const list = r.list || [];
    const genBadge = (g) => ({
        action: '<span class="tag tag--success">GitHub 定时</span>',
        manual: '<span class="tag tag--info">手动重生成</span>',
        lazy: '<span class="tag">站点兜底</span>',
    }[g] || `<span class="tag">${esc(g)}</span>`);
    c.innerHTML = `<div class="card">
        <div class="card__title">${icon("clock", 18)} 每日总结（共 ${list.length} 份）</div>
        <p class="text-muted" style="font-size:var(--fs-13)">
            北京时间 <b>00:00</b> 生成昨日总结（覆盖昨天全天）、
            <b>09:00</b> 生成今日总结（覆盖今天 00:00~09:00）。两份互不覆盖；
            以 (type,date) 唯一键保证重复生成幂等。
        </p>
        <div class="flex gap-8 mt-16 flex-wrap">
            <button class="btn btn--primary btn--sm" data-regen="today">
                ${icon("refresh", 15)} 重生成今日总结</button>
            <button class="btn btn--secondary btn--sm" data-regen="yesterday">
                ${icon("refresh", 15)} 重生成昨日总结</button>
        </div>
        <div style="overflow-x:auto" class="mt-16"><table class="admin-table">
        <thead><tr><th>日期</th><th>类型</th><th>来源</th><th>状态</th><th>更新时间(北京)</th><th>操作</th></tr></thead>
        <tbody>${list.map((d) => `<tr>
            <td class="num">${esc(d.date)}</td>
            <td>${d.type === "today" ? "今日" : "昨日"}</td>
            <td>${genBadge(d.generator)}</td>
            <td>${d.edited ? '<span class="tag tag--warning">已编辑</span>' : '<span class="tag tag--success">自动</span>'}</td>
            <td class="num">${fmtBeijing(d.updated_at)}</td>
            <td><button class="btn btn--sm btn--outline"
                data-view="${esc(d.type)}|${esc(d.date)}">编辑要点</button></td>
        </tr>`).join("") || '<tr><td colspan="6" class="text-center text-faint">暂无总结（请先同步新闻）</td></tr>'}
        </tbody></table></div>
    </div>`;

    c.querySelectorAll("[data-regen]").forEach((b) => {
        b.onclick = async () => {
            const sure = await Modal.confirm({
                title: "重新生成总结",
                body: "将用当前已入库的新闻重新跑一遍规则引擎，覆盖同名（type+日期）总结并把「已编辑」标记清零。确定吗？",
                okText: "重新生成", danger: false,
            });
            if (!sure) return;
            await fetchJSON("/api/admin/digest/regenerate", {
                method: "POST", body: { type: b.dataset.regen },
            });
            Toast.show("已重新生成", "success");
            renderDigestAdmin(c);
        };
    });

    c.querySelectorAll("[data-view]").forEach((b) => {
        b.onclick = async () => {
            const [ntype, ndate] = b.dataset.view.split("|");
            let r2;
            try {
                r2 = await fetchJSON(`/api/digest/detail?type=${ntype}&date=${ndate}`);
            } catch (e) { return; }
            const d = r2.digest || {};
            if (!d.exists) { Toast.show("该总结不存在", "warning"); return; }
            const pts = d.points || [];
            Modal.open({
                title: `${ndate} · ${ntype === "today" ? "今日" : "昨日"}总结要点（可编辑）`,
                body: `
                    <div class="text-faint mb-8" style="font-size:var(--fs-12)">
                        留空则不修改该条；保存后标记为「已编辑」，GitHub 同步不会再覆盖。
                    </div>
                    ${pts.map((p, i) => `
                        <div class="field">
                            <label class="field__label">要点 ${i + 1}</label>
                            <textarea class="input" data-pt="${i}" maxlength="120">${esc(p.text)}</textarea>
                        </div>`).join("") || '<div class="text-faint">暂无要点</div>'}
                    <div class="field">
                        <label class="field__label">关键词（英文逗号分隔）</label>
                        <input class="input" id="digest-kw" value="${esc((d.keywords || []).join(","))}">
                    </div>`,
                foot: `<button class="btn btn--secondary" onclick="Modal.close()">取消</button>
                       <button class="btn btn--primary" id="digest-save">保存编辑</button>`,
            });
            document.getElementById("digest-save").onclick = async () => {
                const points = Array.from(document.querySelectorAll("[data-pt]"))
                    .map((t) => t.value.trim());
                const kw = document.getElementById("digest-kw").value
                    .split(",").map((s) => s.trim()).filter(Boolean);
                await fetchJSON("/api/admin/digest/edit", {
                    method: "POST",
                    body: { type: ntype, date: ndate, points, keywords: kw },
                });
                Toast.show("已保存（标记为已编辑）", "success");
                Modal.close();
                renderDigestAdmin(c);
            };
        };
    });
}

/* ================= 第二期：吐槽审核队列 ================= */
let tucaoFilter = "all";
async function renderTucaoAdmin(c) {
    Skeleton.inject(c, 4);
    const [r, rep] = await Promise.all([
        fetchJSON(`/api/admin/tucao/list?status=${tucaoFilter}`),
        fetchJSON("/api/admin/tucao/reports"),
    ]);
    const list = r.list || [];
    const reports = rep.list || [];
    const statusTag = (s) => ({
        ok: '<span class="tag tag--success">正常</span>',
        pending: '<span class="tag tag--warning">待审核</span>',
        deleted: '<span class="tag tag--danger">已删除</span>',
    }[s] || esc(s));
    c.innerHTML = `<div class="card">
        <div class="card__title">${icon("message", 18)} 吐槽审核（${list.length}）</div>
        <div class="chips">
            ${["all", "pending", "ok", "deleted"].map((s) =>
                `<button class="chip${tucaoFilter === s ? " chip--active" : ""}"
                    data-filter="${s}">${({ all: "全部", pending: "待审核", ok: "已发布", deleted: "已删除" }[s])}</button>`
            ).join("")}
        </div>
        <p class="text-faint" style="font-size:var(--fs-12)">
            出于隐私设计，后台只显示发帖身份类型与马甲名，不展示账号信息。
        </p>
        <div class="mt-16">${list.map((p) => `
            <div class="tucao-post">
                <div class="tucao-post__head">
                    <span class="tag">${esc(p.persona_label)}${p.anon_name ? " · " + esc(p.anon_name) : ""}</span>
                    ${statusTag(p.status)}
                    ${p.pinned ? '<span class="tag tag--warning">置顶</span>' : ""}
                    <span class="text-faint" style="font-size:var(--fs-12);margin-left:auto">
                        #${p.id} · ${fmtBeijing(p.created_at)}</span>
                </div>
                <div class="tucao-post__content">${esc(p.content)}</div>
                <div class="tucao-post__foot">
                    <span class="text-faint" style="font-size:var(--fs-12)">
                        ❤️ ${p.like_count} ｜ 🚩 ${p.report_count}</span>
                    <span style="margin-left:auto" class="flex gap-8">
                        ${p.status === "pending" ? `<button class="btn btn--sm btn--primary"
                            data-mod="approve" data-id="${p.id}">通过</button>` : ""}
                        ${p.status === "deleted"
                            ? `<button class="btn btn--sm btn--secondary" data-mod="restore" data-id="${p.id}">恢复</button>`
                            : `<button class="btn btn--sm btn--danger" data-mod="delete" data-id="${p.id}">删除</button>`}
                        <button class="btn btn--sm btn--outline" data-mod="${p.pinned ? "unpin" : "pin"}"
                            data-id="${p.id}">${p.pinned ? "取消置顶" : "置顶"}</button>
                    </span>
                </div>
            </div>`).join("") || '<div class="text-faint text-center mt-16">该筛选下没有帖子</div>'}
        </div>
    </div>

    <div class="card mt-16">
        <div class="card__title">${icon("flag", 18)} 举报队列（未处理 ${reports.filter((x) => !x.handled).length}）</div>
        <div style="overflow-x:auto"><table class="admin-table">
        <thead><tr><th>时间(北京)</th><th>帖子</th><th>原因</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>${reports.map((x) => `<tr>
            <td class="num">${fmtBeijing(x.created_at)}</td>
            <td>#${x.post_id} ${esc(x.post_content)}…</td>
            <td>${esc(x.reason) || '<span class="text-faint">未填写</span>'}</td>
            <td>${x.handled ? '<span class="tag tag--success">已处理</span>'
                : '<span class="tag tag--warning">待处理</span>'}</td>
            <td>${x.handled ? "—" : `<button class="btn btn--sm btn--secondary"
                data-handle="${x.id}">标记已处理</button>`}</td>
        </tr>`).join("") || '<tr><td colspan="5" class="text-center text-faint">暂无举报</td></tr>'}
        </tbody></table></div>
    </div>

    <div class="card mt-16">
        <div class="card__title">${icon("settings", 18)} 审核模式</div>
        <p class="text-muted" style="font-size:var(--fs-13)" id="mode-now">加载中…</p>
        <div class="flex gap-8 mt-8">
            <button class="btn btn--outline btn--sm" data-mode="post_first">切为「先发后审」</button>
            <button class="btn btn--outline btn--sm" data-mode="audit_first">切为「先审后发」</button>
        </div>
    </div>`;

    fetchJSON("/api/admin/tucao/settings").then((s) => {
        document.getElementById("mode-now").innerHTML =
            `当前模式：<b>${s.mode === "post_first" ? "先发后审（立即可见）" : "先审后发（需管理员通过）"}</b>`;
    }).catch(() => {});

    c.querySelectorAll("[data-filter]").forEach((b) => {
        b.onclick = () => { tucaoFilter = b.dataset.filter; renderTucaoAdmin(c); };
    });
    c.querySelectorAll("[data-mode]").forEach((b) => {
        b.onclick = async () => {
            await fetchJSON("/api/admin/tucao/settings/set", {
                method: "POST", body: { mode: b.dataset.mode },
            });
            Toast.show("审核模式已切换", "success");
            renderTucaoAdmin(c);
        };
    });
    c.querySelectorAll("[data-mod]").forEach((b) => {
        b.onclick = async () => {
            const act = b.dataset.mod;
            if (act === "delete") {
                const sure = await Modal.confirm({
                    title: "删除该楼",
                    body: `删除后该内容不再对任何人可见（可在「已删除」筛选中恢复）。确定删除 #${b.dataset.id} 吗？`,
                    okText: "删除",
                });
                if (!sure) return;
            }
            await fetchJSON("/api/admin/tucao/moderate", {
                method: "POST", body: { post_id: parseInt(b.dataset.id, 10), action: act },
            });
            Toast.show("操作已生效", "success");
            renderTucaoAdmin(c);
        };
    });
    c.querySelectorAll("[data-handle]").forEach((b) => {
        b.onclick = async () => {
            await fetchJSON("/api/admin/tucao/reports/handle", {
                method: "POST", body: { report_id: parseInt(b.dataset.handle, 10) },
            });
            Toast.show("已标记处理", "success");
            renderTucaoAdmin(c);
        };
    });
}

/* ================= 第二期：敏感词库 ================= */
async function renderSensitiveAdmin(c) {
    Skeleton.inject(c, 4);
    const r = await fetchJSON("/api/admin/sensitive_words");
    c.innerHTML = `<div class="card">
        <div class="card__title">${icon("lock", 18)} 敏感词库</div>
        <p class="text-muted" style="font-size:var(--fs-13)">
            发帖时命中任一词即被拦截（含内置词库）。匹配会忽略空格与常见分隔符，
            "傻 逼""傻*逼"等变体同样会被拦截。
        </p>
        <div class="flex gap-8 mt-16 flex-wrap">
            <input class="input" id="sw-input" placeholder="新增敏感词（≤30 字）"
                maxlength="30" style="max-width:240px">
            <button class="btn btn--primary" id="sw-add">${icon("plus", 16)} 添加</button>
        </div>
        <div class="card__title mt-24">管理员词库（${r.list.length}）</div>
        <div class="chips">${r.list.map((w) =>
            `<span class="chip">${esc(w.word)}
                <button class="btn btn--sm btn--ghost" data-del="${w.id}"
                    title="删除" style="min-height:auto;padding:0 4px">${icon("x", 13)}</button>
            </span>`).join("") || '<span class="text-faint">尚未添加自定义词</span>'}
        </div>
        <div class="card__title mt-24">内置词库（${r.builtin.length}，只读）</div>
        <div class="chips">${r.builtin.map((w) => `<span class="chip">${esc(w)}</span>`).join("")}</div>
    </div>`;
    document.getElementById("sw-add").onclick = async () => {
        const word = document.getElementById("sw-input").value.trim();
        if (!word) { Toast.show("请输入敏感词", "warning"); return; }
        await fetchJSON("/api/admin/sensitive_words/add", { method: "POST", body: { word } });
        Toast.show("已添加", "success");
        renderSensitiveAdmin(c);
    };
    c.querySelectorAll("[data-del]").forEach((b) => {
        b.onclick = async () => {
            await fetchJSON("/api/admin/sensitive_words/delete", {
                method: "POST", body: { id: parseInt(b.dataset.del, 10) },
            });
            Toast.show("已删除", "success");
            renderSensitiveAdmin(c);
        };
    });
}

/* ================= 第三期：活动与比赛管理 ================= */
async function renderActAdmin(c) {
    Skeleton.inject(c, 5);
    const [r, tagRes] = await Promise.all([
        fetchJSON("/api/admin/activities/list"),
        fetchJSON("/api/admin/activity_tags"),
    ]);
    const list = r.list || [];
    const tags = tagRes.list || [];
    const tagChips = (ids) => tags.filter((t) => ids.includes(t.id))
        .map((t) => `<span class="tag" style="background:${esc(t.color)}22;color:${esc(t.color)}">${esc(t.name)}</span>`)
        .join("");
    c.innerHTML = `<div class="card">
        <div class="flex-between flex-wrap gap-8">
            <div class="card__title" style="margin-bottom:0">${icon("calendar", 18)} 活动与比赛（${list.length}）</div>
            <button class="btn btn--primary btn--sm" id="act-new">${icon("plus", 15)} 发布活动</button>
        </div>
        <div style="overflow-x:auto" class="mt-16"><table class="admin-table">
        <thead><tr><th>ID</th><th>标题</th><th>类型</th><th>状态</th><th>时间(北京)</th><th>报名</th><th>标签</th><th>操作</th></tr></thead>
        <tbody>${list.map((a) => `<tr>
            <td class="num">${a.id}</td>
            <td>${a.hidden ? '<span class="tag">已下架</span> ' : ""}<b>${esc(a.title)}</b></td>
            <td>${esc(a.atype_label)}</td>
            <td><span class="tag st-${a.status}">${esc(a.status_label)}</span></td>
            <td class="num" style="white-space:nowrap">${fmtBeijing(a.start_at)}<br>${fmtBeijing(a.end_at)}</td>
            <td class="num">${a.reg_count}${a.reg_limit ? " / " + a.reg_limit : ""}</td>
            <td>${tagChips(a.tags.map((t) => t.id)) || '<span class="text-faint">—</span>'}</td>
            <td style="white-space:nowrap">
                <button class="btn btn--sm btn--outline" data-act-edit="${a.id}">编辑</button>
                <button class="btn btn--sm btn--secondary" data-act-reg="${a.id}">名单</button>
                <button class="btn btn--sm ${a.hidden ? "btn--secondary" : "btn--danger"}"
                    data-act-hide="${a.id}" data-hidden="${a.hidden ? 1 : 0}">
                    ${a.hidden ? "恢复" : "下架"}</button>
            </td></tr>`).join("") || '<tr><td colspan="8" class="text-center text-faint">还没有活动</td></tr>'}
        </tbody></table></div>
    </div>

    <div class="card mt-16">
        <div class="card__title">${icon("star", 18)} 活动标签库（${tags.length}）</div>
        <div class="flex gap-8 flex-wrap mt-8">
            <input class="input" id="tag-name" placeholder="标签名" maxlength="20" style="max-width:160px">
            <input class="input" id="tag-color" type="color" value="#4F6EF7" style="max-width:70px;min-height:42px">
            <button class="btn btn--primary" id="tag-add">${icon("plus", 15)} 新增标签</button>
        </div>
        <div class="chips mt-16">${tags.map((t) => `<span class="chip">
            <i class="tag-dot" style="background:${esc(t.color)}"></i> ${esc(t.name)}
            <button class="btn btn--sm btn--ghost" data-tag-del="${t.id}"
                style="min-height:auto;padding:0 4px">${icon("x", 13)}</button></span>`).join("")
            || '<span class="text-faint">暂无标签</span>'}</div>
    </div>`;

    document.getElementById("act-new").onclick = () => openActForm(c, null, tags);
    c.querySelectorAll("[data-act-edit]").forEach((b) => {
        const a = list.find((x) => x.id === parseInt(b.dataset.actEdit, 10));
        b.onclick = () => openActForm(c, a, tags);
    });
    c.querySelectorAll("[data-act-hide]").forEach((b) => {
        b.onclick = async () => {
            const hidden = b.dataset.hidden !== "1";
            const sure = await Modal.confirm({
                title: hidden ? "下架活动" : "恢复活动",
                body: hidden ? "下架后用户端不再显示该活动（数据保留，不删除）。确定吗？"
                             : "确定恢复该活动在前台展示吗？",
                okText: hidden ? "下架" : "恢复",
            });
            if (!sure) return;
            await fetchJSON("/api/admin/activities/hide", {
                method: "POST", body: { id: parseInt(b.dataset.actHide, 10), hidden },
            });
            Toast.show(hidden ? "已下架" : "已恢复", "success");
            renderActAdmin(c);
        };
    });
    c.querySelectorAll("[data-act-reg]").forEach((b) => {
        b.onclick = async () => {
            const rr = await fetchJSON("/api/admin/activities/registrants?id=" +
                parseInt(b.dataset.actReg, 10));
            const rows = rr.list || [];
            Modal.open({
                title: `报名名单（${rows.length} 人）`,
                body: rows.length ? `<div style="overflow-x:auto"><table class="admin-table">
                    <thead><tr><th>#</th><th>昵称</th><th>用户名</th><th>报名时间(北京)</th></tr></thead>
                    <tbody>${rows.map((x, i) => `<tr><td class="num">${i + 1}</td>
                        <td>${avatarHtml(x.nickname, "avatar--sm")} ${esc(x.nickname)}</td>
                        <td class="text-faint">${esc(x.username)}</td>
                        <td class="num">${fmtBeijing(x.created_at)}</td></tr>`).join("")}
                    </tbody></table></div>` : '<div class="text-faint">还没有人报名</div>',
                foot: '<button class="btn btn--secondary" onclick="Modal.close()">关闭</button>',
            });
        };
    });
    document.getElementById("tag-add").onclick = async () => {
        const name = document.getElementById("tag-name").value.trim();
        if (!name) { Toast.show("请输入标签名", "warning"); return; }
        await fetchJSON("/api/admin/activity_tags/create", {
            method: "POST",
            body: { name, color: document.getElementById("tag-color").value },
        });
        Toast.show("已新增标签", "success");
        renderActAdmin(c);
    };
    c.querySelectorAll("[data-tag-del]").forEach((b) => {
        b.onclick = async () => {
            const sure = await Modal.confirm({
                title: "删除标签", body: "删除后该标签会从所有活动上移除，确定吗？", okText: "删除",
            });
            if (!sure) return;
            await fetchJSON("/api/admin/activity_tags/delete", {
                method: "POST", body: { id: parseInt(b.dataset.tagDel, 10) },
            });
            Toast.show("已删除标签", "success");
            renderActAdmin(c);
        };
    });
}

function openActForm(c, a, tags) {
    const isEdit = a !== null;
    const checked = (id) => (isEdit && a.tags.some((t) => t.id === id) ? "checked" : "");
    Modal.open({
        title: isEdit ? "编辑活动" : "发布活动",
        body: `
            <div class="field"><label class="field__label">标题</label>
                <input class="input" id="a-title" maxlength="80" value="${isEdit ? esc(a.title) : ""}"></div>
            <div class="field"><label class="field__label">类型</label>
                <select class="input" id="a-type">
                    <option value="activity" ${isEdit && a.atype === "activity" ? "selected" : ""}>活动</option>
                    <option value="contest" ${isEdit && a.atype === "contest" ? "selected" : ""}>比赛</option>
                </select></div>
            <div class="field"><label class="field__label">开始时间（北京时间）</label>
                <input class="input" id="a-start" type="datetime-local"
                    value="${isEdit ? esc(a.start_input) : ""}"></div>
            <div class="field"><label class="field__label">结束时间（北京时间）</label>
                <input class="input" id="a-end" type="datetime-local" value=""></div>
            <div class="field"><label class="field__label">地点</label>
                <input class="input" id="a-place" maxlength="60" value="${isEdit ? esc(a.place) : ""}"></div>
            <div class="field"><label class="field__label">详情</label>
                <textarea class="input" id="a-detail" maxlength="2000">${isEdit ? esc(a.detail) : ""}</textarea></div>
            <div class="field">
                <label class="persona-opt" style="display:inline-flex">
                    <input type="checkbox" id="a-reg" ${isEdit && a.reg_enabled ? "checked" : ""}> 开放报名</label>
            </div>
            <div class="field"><label class="field__label">报名截止（北京时间）</label>
                <input class="input" id="a-deadline" type="datetime-local"
                    value="${isEdit ? esc(a.reg_deadline_input || "") : ""}"></div>
            <div class="field"><label class="field__label">人数上限（0 = 不限）</label>
                <input class="input" id="a-limit" type="number" min="0" value="${isEdit ? a.reg_limit : 0}"></div>
            <div class="field"><label class="field__label">标签</label>
                <div class="flex gap-8 flex-wrap">${tags.map((t) => `
                    <label class="persona-opt"><input type="checkbox" name="a-tags"
                        value="${t.id}" ${checked(t.id)}> ${esc(t.name)}</label>`).join("")
                    || '<span class="text-faint">先去下方新增标签</span>'}</div></div>`,
        foot: `<button class="btn btn--secondary" onclick="Modal.close()">取消</button>
               <button class="btn btn--primary" id="a-save">${isEdit ? "保存" : "发布"}</button>`,
    });
    // 编辑时结束时间用原始值回填（上面字符串技巧易错，这里显式覆盖）
    if (isEdit) {
        const endEl = document.getElementById("a-end");
        const d = new Date(a.end_at * 1000);
        const bj = new Date(d.getTime() + (d.getTimezoneOffset() * 60000) + 8 * 3600000);
        endEl.value = bj.toISOString().slice(0, 16);
    }
    document.getElementById("a-save").onclick = async () => {
        const body = {
            title: document.getElementById("a-title").value.trim(),
            atype: document.getElementById("a-type").value,
            start_input: document.getElementById("a-start").value,
            end_input: document.getElementById("a-end").value,
            place: document.getElementById("a-place").value.trim(),
            detail: document.getElementById("a-detail").value.trim(),
            reg_enabled: document.getElementById("a-reg").checked,
            reg_deadline_input: document.getElementById("a-deadline").value,
            reg_limit: parseInt(document.getElementById("a-limit").value || "0", 10),
            tag_ids: Array.from(document.querySelectorAll("[name=a-tags]:checked"))
                .map((x) => parseInt(x.value, 10)),
        };
        if (isEdit) body.id = a.id;
        try {
            await fetchJSON(isEdit ? "/api/admin/activities/update"
                                   : "/api/admin/activities/create",
                            { method: "POST", body });
            Toast.show(isEdit ? "已保存" : "已发布", "success");
            Modal.close();
            renderActAdmin(c);
        } catch (e) { /* 已提示 */ }
    };
}

/* ================= 第三期：作业管理 ================= */
async function renderHwAdmin(c) {
    Skeleton.inject(c, 4);
    const r = await fetchJSON("/api/admin/homework/list");
    const list = r.list || [];
    c.innerHTML = `<div class="card">
        <div class="flex-between flex-wrap gap-8">
            <div class="card__title" style="margin-bottom:0">${icon("file-text", 18)} 作业管理（${list.length}）</div>
            <button class="btn btn--primary btn--sm" id="hw-new">${icon("plus", 15)} 发布作业</button>
        </div>
        <p class="text-faint mt-8" style="font-size:var(--fs-12)">
            学生的"我已完成"状态仅本人可见，后台刻意不统计完成率，避免形成攀比。
        </p>
        <div style="overflow-x:auto" class="mt-16"><table class="admin-table">
        <thead><tr><th>ID</th><th>科目</th><th>内容</th><th>截止(北京)</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>${list.map((h) => `<tr>
            <td class="num">${h.id}</td>
            <td><span class="tag tag--primary">${esc(h.subject)}</span></td>
            <td style="max-width:380px">${esc(h.content.length > 60 ? h.content.slice(0, 60) + "…" : h.content)}</td>
            <td class="num" style="white-space:nowrap">${fmtBeijing(h.due_at)}</td>
            <td>${h.expired ? '<span class="tag tag--danger">已过期</span>'
                : '<span class="tag tag--success">进行中</span>'}</td>
            <td style="white-space:nowrap">
                <button class="btn btn--sm btn--outline" data-hw-edit="${h.id}">编辑</button>
                <button class="btn btn--sm btn--danger" data-hw-del="${h.id}">删除</button>
            </td></tr>`).join("") || '<tr><td colspan="6" class="text-center text-faint">暂无作业</td></tr>'}
        </tbody></table></div>
        ${r.subjects.length ? `<div class="text-faint mt-8" style="font-size:var(--fs-12)">
            已有科目：${r.subjects.map(esc).join(" / ")}</div>` : ""}
    </div>`;

    document.getElementById("hw-new").onclick = () => openHwForm(c, null, r.subjects);
    c.querySelectorAll("[data-hw-edit]").forEach((b) => {
        const h = list.find((x) => x.id === parseInt(b.dataset.hwEdit, 10));
        b.onclick = () => openHwForm(c, h, r.subjects);
    });
    c.querySelectorAll("[data-hw-del]").forEach((b) => {
        b.onclick = async () => {
            const sure = await Modal.confirm({
                title: "删除作业",
                body: "删除后所有同学的完成记录也会一并删除，确定吗？", okText: "删除",
            });
            if (!sure) return;
            await fetchJSON("/api/admin/homework/delete", {
                method: "POST", body: { id: parseInt(b.dataset.hwDel, 10) },
            });
            Toast.show("已删除", "success");
            renderHwAdmin(c);
        };
    });
}

function openHwForm(c, h, subjects) {
    const isEdit = h !== null;
    const opts = ["语文", "数学", "英语", "物理", "化学", "生物", "其他"]
        .concat(subjects || []).filter((v, i, arr) => arr.indexOf(v) === i);
    Modal.open({
        title: isEdit ? "编辑作业" : "发布作业",
        body: `
            <div class="field"><label class="field__label">科目</label>
                <select class="input" id="h-subject">
                    ${opts.map((s) => `<option value="${esc(s)}"
                        ${isEdit && h.subject === s ? "selected" : ""}>${esc(s)}</option>`).join("")}
                </select></div>
            <div class="field"><label class="field__label">内容（≤500 字）</label>
                <textarea class="input" id="h-content" maxlength="500"
                    data-max="500">${isEdit ? esc(h.content) : ""}</textarea>
                <div class="field__count" id="h-count"></div></div>
            <div class="field"><label class="field__label">截止时间（北京时间）</label>
                <input class="input" id="h-due" type="datetime-local"
                    value="${isEdit ? esc(h.due_input) : ""}"></div>`,
        foot: `<button class="btn btn--secondary" onclick="Modal.close()">取消</button>
               <button class="btn btn--primary" id="h-save">${isEdit ? "保存" : "发布"}</button>`,
    });
    bindCharCount(document.getElementById("h-content"), document.getElementById("h-count"));
    document.getElementById("h-save").onclick = async () => {
        const body = {
            subject: document.getElementById("h-subject").value,
            content: document.getElementById("h-content").value.trim(),
            due_input: document.getElementById("h-due").value,
        };
        if (isEdit) body.id = h.id;
        try {
            await fetchJSON(isEdit ? "/api/admin/homework/update" : "/api/admin/homework/create",
                            { method: "POST", body });
            Toast.show(isEdit ? "已保存" : "已发布", "success");
            Modal.close();
            renderHwAdmin(c);
        } catch (e) { /* 已提示 */ }
    };
}

/* ================= 第三期：资料库管理（含存储用量仪表） ================= */
async function renderResAdmin(c) {
    Skeleton.inject(c, 4);
    const r = await fetchJSON("/api/admin/resources/list");
    const st = r.storage || {};
    const list = r.list || [];
    const pct = Math.min(100, st.percent || 0);
    c.innerHTML = `<div class="card">
        <div class="card__title">${icon("inbox", 18)} 存储用量</div>
        <div class="flex-between" style="font-size:var(--fs-13)">
            <span class="text-muted">全站上传配额（上限 400MB）</span>
            <b class="num">${esc(st.used_text || "")} / ${esc(st.quota_text || "")}（${pct}%）</b>
        </div>
        <div class="progress mt-8"><div class="progress__bar"
            style="width:${pct}%${pct >= 90 ? ";background:var(--c-danger)" : ""}"></div></div>
        <div class="text-faint mt-8" style="font-size:var(--fs-12)">
            uploads/ 目录实际占用：${esc(st.disk_text || "")}（含荣誉图片，用于交叉核对）</div>
        <div class="flex gap-8 mt-16 flex-wrap">
            <input class="input" id="res-subjects-input" style="max-width:460px"
                value="${esc((r.list || []).length ? "" : "")}"
                placeholder="学科标签，英文逗号分隔">
            <button class="btn btn--primary btn--sm" id="res-subjects-save">保存学科标签</button>
        </div>
    </div>

    <div class="card mt-16">
        <div class="card__title">${icon("file-text", 18)} 资料管理（${list.length}）</div>
        <div style="overflow-x:auto"><table class="admin-table">
        <thead><tr><th>ID</th><th>标题</th><th>学科</th><th>类型</th><th>大小</th><th>上传者</th><th>下载</th><th>操作</th></tr></thead>
        <tbody>${list.map((x) => `<tr>
            <td class="num">${x.id}</td>
            <td>${esc(x.title)}${x.rtype === "file" ? `<div class="text-faint" style="font-size:11px">${esc(x.file_name)}</div>` : ""}</td>
            <td><span class="tag tag--primary">${esc(x.subject)}</span></td>
            <td>${x.rtype === "file" ? '<span class="tag">文件</span>' : '<span class="tag">外链</span>'}</td>
            <td class="num">${esc(x.size_text) || "—"}</td>
            <td>${esc(x.uploader)}</td>
            <td class="num">${x.downloads}</td>
            <td><button class="btn btn--sm btn--danger" data-res-del="${x.id}">删除</button></td>
        </tr>`).join("") || '<tr><td colspan="8" class="text-center text-faint">暂无资料</td></tr>'}
        </tbody></table></div>
    </div>`;

    fetchJSON("/api/admin/resources/subjects").then((s) => {
        document.getElementById("res-subjects-input").value = (s.subjects || []).join(",");
    }).catch(() => {});
    document.getElementById("res-subjects-save").onclick = async () => {
        const arr = document.getElementById("res-subjects-input").value
            .split(",").map((x) => x.trim()).filter(Boolean);
        if (!arr.length) { Toast.show("至少保留一个学科", "warning"); return; }
        await fetchJSON("/api/admin/resources/subjects/set", {
            method: "POST", body: { subjects: arr },
        });
        Toast.show("学科标签已更新", "success");
        renderResAdmin(c);
    };
    c.querySelectorAll("[data-res-del]").forEach((b) => {
        b.onclick = async () => {
            const sure = await Modal.confirm({
                title: "删除资料",
                body: "删除记录的同时会删除磁盘文件并回收配额，确定吗？", okText: "删除",
            });
            if (!sure) return;
            const rr = await fetchJSON("/api/resources/delete", {
                method: "POST", body: { id: parseInt(b.dataset.resDel, 10) },
            });
            Toast.show("已删除，释放 " + (rr.freed_text || "0 B"), "success");
            renderResAdmin(c);
        };
    });
}

/* ================= 第三期：值日管理 ================= */
async function renderDutyAdmin(c) {
    Skeleton.inject(c, 4);
    const r = await fetchJSON("/api/duty/week");
    const week = r.week || [];
    c.innerHTML = `<div class="card">
        <div class="flex-between flex-wrap gap-8">
            <div class="card__title" style="margin-bottom:0">${icon("grid", 18)} 值日管理</div>
            <div class="flex gap-8">
                <a class="btn btn--secondary btn--sm" href="/duty">在周视图中编辑</a>
                <button class="btn btn--danger btn--sm" id="duty-rotate-btn">
                    ${icon("refresh", 15)} 一键顺延轮换</button>
            </div>
        </div>
        <p class="text-muted" style="font-size:var(--fs-13)">
            每组值日后挪一天（周日 → 周一）。该操作需二次确认，并记入审计日志。
        </p>
        <div style="overflow-x:auto" class="mt-16"><table class="admin-table">
        <thead><tr><th>星期</th><th>小组</th><th>成员</th><th>任务</th><th>操作</th></tr></thead>
        <tbody>${week.map((d) => `<tr>
            <td>${esc(d.day_name)}${d.day === r.today_dow ? ' <span class="tag tag--primary">今天</span>' : ""}</td>
            <td>${esc(d.group_name) || '<span class="text-faint">—</span>'}</td>
            <td class="text-muted">${esc(d.members) || "—"}</td>
            <td class="text-muted">${esc(d.task) || "—"}</td>
            <td><button class="btn btn--sm btn--danger" data-duty-clear="${d.day}">清空</button></td>
        </tr>`).join("")}
        </tbody></table></div>
    </div>`;

    document.getElementById("duty-rotate-btn").onclick = async () => {
        const sure = await Modal.confirm({
            title: "一键顺延轮换",
            body: "将把 7 天的值日内容全部平移一天（周日内容移到周一）。确定执行吗？",
            okText: "确认轮换",
        });
        if (!sure) return;
        const rr = await fetchJSON("/api/admin/duty/rotate", {
            method: "POST", body: { confirm: true },
        });
        Toast.show(`已顺延轮换（影响 ${rr.affected} 天）`, "success");
        renderDutyAdmin(c);
    };
    c.querySelectorAll("[data-duty-clear]").forEach((b) => {
        b.onclick = async () => {
            const sure = await Modal.confirm({
                title: "清空该日值日", body: "确定清空这一天吗？", okText: "清空",
            });
            if (!sure) return;
            await fetchJSON("/api/admin/duty/delete", {
                method: "POST", body: { day_of_week: parseInt(b.dataset.dutyClear, 10) },
            });
            Toast.show("已清空", "success");
            renderDutyAdmin(c);
        };
    });
}

/* ================= 第三期：荣誉管理 ================= */
async function renderHonorAdmin(c) {
    Skeleton.inject(c, 4);
    const r = await fetchJSON("/api/admin/honors/list");
    const list = r.list || [];
    c.innerHTML = `<div class="card">
        <div class="flex-between flex-wrap gap-8">
            <div class="card__title" style="margin-bottom:0">${icon("trophy", 18)} 荣誉管理（${list.length}）</div>
            <a class="btn btn--primary btn--sm" href="/honors">${icon("plus", 15)} 去荣誉墙添加</a>
        </div>
        <div style="overflow-x:auto" class="mt-16"><table class="admin-table">
        <thead><tr><th>ID</th><th>奖项</th><th>获奖人</th><th>日期</th><th>配图</th><th>操作</th></tr></thead>
        <tbody>${list.map((h) => `<tr>
            <td class="num">${h.id}</td>
            <td>${h.is_pinned ? '<span class="tag tag--warning">置顶</span> ' : ""}<b>${esc(h.title)}</b></td>
            <td>${esc(h.owner) || '<span class="text-faint">—</span>'}</td>
            <td class="num">${esc(h.date) || "—"}</td>
            <td>${h.has_image ? '<span class="tag tag--success">有</span>' : '<span class="text-faint">无</span>'}</td>
            <td style="white-space:nowrap">
                <button class="btn btn--sm btn--outline" data-honor-pin="${h.id}">
                    ${h.is_pinned ? "取消置顶" : "置顶"}</button>
                <button class="btn btn--sm btn--danger" data-honor-del="${h.id}">删除</button>
            </td></tr>`).join("") || '<tr><td colspan="6" class="text-center text-faint">还没有荣誉</td></tr>'}
        </tbody></table></div>
    </div>`;

    c.querySelectorAll("[data-honor-pin]").forEach((b) => {
        b.onclick = async () => {
            const rr = await fetchJSON("/api/admin/honors/pin", {
                method: "POST", body: { id: parseInt(b.dataset.honorPin, 10) },
            });
            Toast.show(rr.is_pinned ? "已置顶" : "已取消置顶", "success");
            renderHonorAdmin(c);
        };
    });
    c.querySelectorAll("[data-honor-del]").forEach((b) => {
        b.onclick = async () => {
            const sure = await Modal.confirm({
                title: "删除荣誉",
                body: "关联图片会同步删除并回收配额，确定吗？", okText: "删除",
            });
            if (!sure) return;
            const rr = await fetchJSON("/api/admin/honors/delete", {
                method: "POST", body: { id: parseInt(b.dataset.honorDel, 10) },
            });
            Toast.show("已删除，释放 " + (rr.freed_text || "0 B"), "success");
            renderHonorAdmin(c);
        };
    });
}

/* ================= 第四期：每日一句审核 ================= */
let quoteFilter = "pending";
async function renderQuoteAdmin(c) {
    Skeleton.inject(c, 4);
    const r = await fetchJSON(`/api/admin/quotes?status=${quoteFilter}`);
    const list = r.list || [];
    const statusTag = (s) => ({
        pending: '<span class="tag tag--warning">待审核</span>',
        approved: '<span class="tag tag--success">已通过</span>',
        rejected: '<span class="tag tag--danger">未通过</span>',
    }[s] || esc(s));
    c.innerHTML = `<div class="card">
        <div class="card__title">${icon("heart", 18)} 每日一句审核</div>
        <p class="text-muted" style="font-size:var(--fs-13)">
            通过后才会出现在前台与首页横幅（先审后显）。首页每天按北京日期自动轮换一句。
            待审 <b class="num">${r.pending}</b> 句 · 已通过 <b class="num">${r.approved}</b> 句
        </p>
        <div class="chips">
            ${["pending", "approved", "rejected", "all"].map((s) =>
                `<button class="chip${quoteFilter === s ? " chip--active" : ""}"
                    data-qf="${s}">${{ pending: "待审核", approved: "已通过",
                                        rejected: "未通过", all: "全部" }[s]}</button>`).join("")}
        </div>
        <div class="mt-8">${list.map((q) => `
            <div class="compose" style="margin-bottom:var(--sp-12)">
                <div class="flex-between flex-wrap gap-8">
                    <div style="flex:1;min-width:0">
                        <div style="font-size:var(--fs-15);line-height:1.7">${esc(q.content)}</div>
                        <div class="text-faint mt-8" style="font-size:var(--fs-12)">
                            — ${esc(q.author)} · ${fmtBeijing(q.created_at)}
                            ${q.status === "approved" ? " · ❤️ " + q.like_count : ""}
                        </div>
                    </div>
                    ${statusTag(q.status)}
                </div>
                <div class="flex gap-8 mt-12 flex-wrap">
                    ${q.status !== "approved"
                        ? `<button class="btn btn--primary btn--sm" data-qok="${q.id}">通过</button>` : ""}
                    ${q.status !== "rejected"
                        ? `<button class="btn btn--secondary btn--sm" data-qno="${q.id}">不通过</button>` : ""}
                    <button class="btn btn--danger btn--sm" data-qdel="${q.id}">删除</button>
                </div>
            </div>`).join("") || '<div class="text-faint text-center mt-16">该筛选下没有投稿</div>'}
        </div>
    </div>`;

    c.querySelectorAll("[data-qf]").forEach((b) => {
        b.onclick = () => { quoteFilter = b.dataset.qf; renderQuoteAdmin(c); };
    });
    const review = async (id, action) => {
        await fetchJSON("/api/admin/quotes/review", {
            method: "POST", body: { id, action },
        });
        Toast.show(action === "approve" ? "已通过（前台可见）" : "已标记未通过", "success");
        renderQuoteAdmin(c);
    };
    c.querySelectorAll("[data-qok]").forEach((b) => {
        b.onclick = () => review(parseInt(b.dataset.qok, 10), "approve");
    });
    c.querySelectorAll("[data-qno]").forEach((b) => {
        b.onclick = () => review(parseInt(b.dataset.qno, 10), "reject");
    });
    c.querySelectorAll("[data-qdel]").forEach((b) => {
        b.onclick = async () => {
            const sure = await Modal.confirm({
                title: "删除句子", body: "删除后不可恢复，确定吗？", okText: "删除",
            });
            if (!sure) return;
            await fetchJSON("/api/admin/quotes/delete", {
                method: "POST", body: { id: parseInt(b.dataset.qdel, 10) },
            });
            Toast.show("已删除", "success");
            renderQuoteAdmin(c);
        };
    });
}

/* ================= 第四期：打卡挑战管理 ================= */
async function renderCheckinAdmin(c) {
    Skeleton.inject(c, 4);
    const r = await fetchJSON("/api/admin/challenges");
    const list = r.list || [];
    const icons = r.icons || ["target"];
    c.innerHTML = `<div class="card">
        <div class="flex-between flex-wrap gap-8">
            <div class="card__title" style="margin-bottom:0">
                ${icon("target", 18)} 打卡挑战（${list.length}）</div>
            <div class="flex gap-8">
                <button class="btn btn--secondary btn--sm" id="ck-archive">
                    ${icon("refresh", 15)} 归档已结束</button>
                <button class="btn btn--primary btn--sm" id="ck-new">
                    ${icon("plus", 15)} 创建挑战</button>
            </div>
        </div>
        <p class="text-muted" style="font-size:var(--fs-13)">
            模板示例：早起 / 背单词 / 晚跑。结束日期过后会自动归档（不再出现在用户端进行中列表）。
        </p>
        <div style="overflow-x:auto" class="mt-16"><table class="admin-table">
        <thead><tr><th>ID</th><th>名称</th><th>图标</th><th>起止</th><th>人数</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>${list.map((x) => `<tr>
            <td class="num">${x.id}</td>
            <td><b>${esc(x.name)}</b></td>
            <td>${icon(x.icon || "target", 16)} ${esc(x.icon)}</td>
            <td class="num" style="white-space:nowrap">${esc(x.start_date)} ~ ${esc(x.end_date)}</td>
            <td class="num">${x.participants}</td>
            <td>${x.archived ? '<span class="tag">已归档</span>'
                : '<span class="tag tag--success">进行中</span>'}</td>
            <td><button class="btn btn--sm btn--danger" data-ck-del="${x.id}">删除</button></td>
        </tr>`).join("") || '<tr><td colspan="7" class="text-center text-faint">还没有挑战</td></tr>'}
        </tbody></table></div>
    </div>`;

    document.getElementById("ck-archive").onclick = async () => {
        const rr = await fetchJSON("/api/admin/challenges/archive", { method: "POST", body: {} });
        Toast.show(`已归档 ${rr.archived} 个挑战`, "success");
        renderCheckinAdmin(c);
    };
    document.getElementById("ck-new").onclick = () => {
        const today = new Date().toISOString().slice(0, 10);
        Modal.open({
            title: "创建打卡挑战",
            body: `
                <div class="field"><label class="field__label">名称</label>
                    <input class="input" id="ck-name" maxlength="30" placeholder="如：早起打卡"></div>
                <div class="field"><label class="field__label">图标</label>
                    <div class="chips">${icons.map((ic, i) => `
                        <button class="chip${i === 0 ? " chip--active" : ""}" data-ic="${ic}">
                            ${icon(ic, 14)} ${ic}</button>`).join("")}</div>
                    <input type="hidden" id="ck-icon" value="${icons[0]}"></div>
                <div class="field"><label class="field__label">开始日期（北京日期）</label>
                    <input class="input" id="ck-start" type="date" value="${today}"></div>
                <div class="field"><label class="field__label">结束日期</label>
                    <input class="input" id="ck-end" type="date" value="${today}"></div>`,
            foot: `<button class="btn btn--secondary" onclick="Modal.close()">取消</button>
                   <button class="btn btn--primary" id="ck-save">创建</button>`,
        });
        document.querySelectorAll("[data-ic]").forEach((b) => {
            b.onclick = () => {
                document.querySelectorAll("[data-ic]").forEach((x) => x.classList.remove("chip--active"));
                b.classList.add("chip--active");
                document.getElementById("ck-icon").value = b.dataset.ic;
            };
        });
        document.getElementById("ck-save").onclick = async () => {
            const body = {
                name: document.getElementById("ck-name").value.trim(),
                icon: document.getElementById("ck-icon").value,
                start_date: document.getElementById("ck-start").value,
                end_date: document.getElementById("ck-end").value,
            };
            try {
                await fetchJSON("/api/admin/challenges/create", { method: "POST", body });
                Toast.show("已创建挑战", "success");
                Modal.close();
                renderCheckinAdmin(c);
            } catch (e) { /* 已提示 */ }
        };
    };
    c.querySelectorAll("[data-ck-del]").forEach((b) => {
        b.onclick = async () => {
            const sure = await Modal.confirm({
                title: "删除挑战",
                body: "会同时删除所有人的打卡记录，确定吗？", okText: "删除",
            });
            if (!sure) return;
            await fetchJSON("/api/admin/challenges/delete", {
                method: "POST", body: { id: parseInt(b.dataset.ckDel, 10) },
            });
            Toast.show("已删除", "success");
            renderCheckinAdmin(c);
        };
    });
}

/* ================= 第四期：点名名单管理 ================= */
async function renderRosterAdmin(c) {
    Skeleton.inject(c, 4);
    const r = await fetchJSON("/api/admin/roster");
    const list = r.list || [];
    c.innerHTML = `<div class="card">
        <div class="flex-between flex-wrap gap-8">
            <div class="card__title" style="margin-bottom:0">
                ${icon("zap", 18)} 点名名单（在册 ${r.active_count} / 共 ${list.length}）</div>
            <button class="btn btn--primary btn--sm" id="ro-import">
                ${icon("refresh", 15)} 从用户一键导入</button>
        </div>
        <p class="text-muted" style="font-size:var(--fs-13)">
            从 users 导入会取昵称并自动去重；已停用的成员不参与抽取。
            随机点名页支持"排除已点过的"与"勾选请假者不参与"。
        </p>
        <div class="flex gap-8 mt-16 flex-wrap">
            <input class="input" id="ro-name" placeholder="姓名" maxlength="20" style="max-width:180px">
            <input class="input" id="ro-seat" type="number" min="0" placeholder="座位号"
                style="max-width:120px">
            <button class="btn btn--primary btn--sm" id="ro-add">${icon("plus", 15)} 新增</button>
        </div>
        <div style="overflow-x:auto" class="mt-16"><table class="admin-table">
        <thead><tr><th>ID</th><th>座位</th><th>姓名</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>${list.map((m) => `<tr>
            <td class="num">${m.id}</td>
            <td class="num">${m.seat_no || "—"}</td>
            <td>${avatarHtml(m.name, "avatar--sm")} <b>${esc(m.name)}</b></td>
            <td>${m.is_active ? '<span class="tag tag--success">在册</span>'
                : '<span class="tag">已停用</span>'}</td>
            <td style="white-space:nowrap">
                <button class="btn btn--sm btn--outline" data-ro-toggle="${m.id}"
                    data-active="${m.is_active ? 1 : 0}"
                    data-name="${esc(m.name)}" data-seat="${m.seat_no}">
                    ${m.is_active ? "停用" : "启用"}</button>
                <button class="btn btn--sm btn--danger" data-ro-del="${m.id}">删除</button>
            </td></tr>`).join("") || '<tr><td colspan="5" class="text-center text-faint">名单为空，点右上角一键导入</td></tr>'}
        </tbody></table></div>
    </div>`;

    document.getElementById("ro-import").onclick = async () => {
        const rr = await fetchJSON("/api/admin/roster/import_users", { method: "POST", body: {} });
        Toast.show(`导入完成：新增 ${rr.added} 人，在册共 ${rr.total} 人`, "success");
        renderRosterAdmin(c);
    };
    document.getElementById("ro-add").onclick = async () => {
        const name = document.getElementById("ro-name").value.trim();
        if (!name) { Toast.show("请输入姓名", "warning"); return; }
        try {
            await fetchJSON("/api/admin/roster/add", {
                method: "POST",
                body: { name, seat_no: parseInt(document.getElementById("ro-seat").value || "0", 10) },
            });
            Toast.show("已新增", "success");
            renderRosterAdmin(c);
        } catch (e) { /* 已提示 */ }
    };
    c.querySelectorAll("[data-ro-toggle]").forEach((b) => {
        b.onclick = async () => {
            const active = b.dataset.active !== "1";
            await fetchJSON("/api/admin/roster/update", {
                method: "POST",
                body: { id: parseInt(b.dataset.roToggle, 10), name: b.dataset.name,
                        seat_no: parseInt(b.dataset.seat || "0", 10),
                        is_active: active },
            });
            Toast.show(active ? "已启用" : "已停用", "success");
            renderRosterAdmin(c);
        };
    });
    c.querySelectorAll("[data-ro-del]").forEach((b) => {
        b.onclick = async () => {
            const sure = await Modal.confirm({
                title: "删除成员", body: "确定从名单中删除吗？（不再参与随机点名）", okText: "删除",
            });
            if (!sure) return;
            await fetchJSON("/api/admin/roster/delete", {
                method: "POST", body: { id: parseInt(b.dataset.roDel, 10) },
            });
            Toast.show("已删除", "success");
            renderRosterAdmin(c);
        };
    });
}

/* ================= 登录日志（root 角色 / admin.full 专属） ================= */
let llPage = 1, llOnlyFail = false;
async function renderLoginLogs(c) {
    Skeleton.inject(c, 6);
    const r = await fetchJSON(
        `/api/admin/login_logs?page=${llPage}${llOnlyFail ? "&only_fail=1" : ""}`);
    c.innerHTML = `<div class="card">
        <div class="card__title">${icon("shield", 18)} 登录日志（共 ${r.total} 条）</div>
        <div class="chips">
            <button class="chip ${llOnlyFail ? "" : "chip--active"}" data-ll="all">全部</button>
            <button class="chip ${llOnlyFail ? "chip--active" : ""}" data-ll="fail">仅失败</button>
        </div>
        <div style="overflow-x:auto"><table class="admin-table">
        <thead><tr><th>时间(北京)</th><th>用户</th><th>结果</th><th>IP</th><th>UA</th></tr></thead>
        <tbody>${r.list.map((x) => `<tr>
            <td class="num">${fmtBeijing(x.created_at)}</td>
            <td><b>${esc(x.nickname || "—")}</b>
                <span class="text-faint" style="font-size:12px">@${esc(x.username)}</span></td>
            <td>${x.success ? '<span class="tag tag--success">成功</span>'
                             : '<span class="tag tag--danger">失败</span>'}</td>
            <td class="num" style="font-size:12px">${esc(x.ip || "—")}</td>
            <td class="text-faint" style="font-size:12px;max-width:260px;overflow:hidden;
                text-overflow:ellipsis;white-space:nowrap" title="${esc(x.ua)}">${esc(x.ua || "—")}</td>
        </tr>`).join("") || '<tr><td colspan="5" class="text-faint">暂无记录</td></tr>'}
        </tbody></table></div>
        <div class="mt-16" id="ll-pager"></div>
    </div>`;
    c.querySelectorAll("[data-ll]").forEach((b) => {
        b.onclick = () => { llOnlyFail = b.dataset.ll === "fail"; llPage = 1; renderLoginLogs(c); };
    });
    Pagination.render(document.getElementById("ll-pager"), {
        page: r.page, total: r.total, pageSize: r.per,
        onChange: (p) => { llPage = p; renderLoginLogs(c); },
    });
}

/* ================= 资源导航管理（link.manage） ================= */
async function renderLinksAdmin(c) {
    Skeleton.inject(c, 6);
    const [cats, links, subs] = await Promise.all([
        fetchJSON("/api/admin/links/categories"),
        fetchJSON("/api/admin/links/list"),
        fetchJSON("/api/admin/links/submissions?status=pending"),
    ]);
    const catName = (id) => {
        const x = cats.list.find((k) => k.id === id);
        return x ? x.name : "—";
    };
    c.innerHTML = `
    ${subs.list.length ? `<div class="card">
        <div class="card__title">${icon("inbox", 18)} 学生推荐待审核（${subs.list.length}）</div>
        <div style="overflow-x:auto"><table class="admin-table">
        <thead><tr><th>名称</th><th>链接</th><th>建议分类</th><th>推荐人</th><th>操作</th></tr></thead>
        <tbody>${subs.list.map((s) => `<tr>
            <td><b>${esc(s.name)}</b><div class="text-faint" style="font-size:12px">${esc(s.description)}</div></td>
            <td style="font-size:12px;max-width:200px;overflow:hidden;text-overflow:ellipsis;
                white-space:nowrap">${esc(s.url)}</td>
            <td>${esc(s.cat_name || "（未定）")}</td>
            <td>${esc(s.submitter)}</td>
            <td style="white-space:nowrap">
                <button class="btn btn--primary btn--sm" data-sub-ok="${s.id}">通过</button>
                <button class="btn btn--ghost btn--sm" data-sub-no="${s.id}">拒绝</button>
            </td>
        </tr>`).join("")}</tbody></table></div>
    </div>` : ""}
    <div class="card">
        <div class="card__title">${icon("grid", 18)} 分类管理（${cats.list.length}）
            <button class="btn btn--primary btn--sm" id="lk-cat-add" style="float:right">+ 新增分类</button>
        </div>
        <div style="overflow-x:auto"><table class="admin-table">
        <thead><tr><th>排序</th><th>名称</th><th>颜色</th><th>操作</th></tr></thead>
        <tbody>${cats.list.map((k) => `<tr>
            <td class="num">${k.sort}</td>
            <td><b>${esc(k.name)}</b></td>
            <td><span class="link-card__badge" style="background:${esc(k.color)};
                width:22px;height:22px;border-radius:6px;font-size:11px">${esc(k.name.charAt(0))}</span>
                <span class="text-faint" style="font-size:12px">${esc(k.color)}</span></td>
            <td style="white-space:nowrap">
                <button class="btn btn--ghost btn--sm" data-cat-edit="${k.id}">编辑</button>
                <button class="btn btn--ghost btn--sm" data-cat-del="${k.id}">删除</button>
            </td>
        </tr>`).join("")}</tbody></table></div>
    </div>
    <div class="card">
        <div class="card__title">${icon("link", 18)} 链接管理（${links.list.length}）
            <button class="btn btn--primary btn--sm" id="lk-add" style="float:right">+ 新增链接</button>
        </div>
        <div style="overflow-x:auto"><table class="admin-table">
        <thead><tr><th>排序</th><th>名称</th><th>分类</th><th>链接</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>${links.list.map((l) => `<tr>
            <td class="num">${l.sort}</td>
            <td><b>${esc(l.name)}</b><div class="text-faint" style="font-size:12px">${esc(l.description)}</div></td>
            <td>${esc(l.cat_name)}</td>
            <td style="font-size:12px;max-width:180px;overflow:hidden;text-overflow:ellipsis;
                white-space:nowrap">${esc(l.url)}</td>
            <td>${l.is_active ? '<span class="tag tag--success">启用</span>'
                              : '<span class="tag">停用</span>'}</td>
            <td style="white-space:nowrap">
                <button class="btn btn--ghost btn--sm" data-lk-edit="${l.id}">编辑</button>
                <button class="btn btn--ghost btn--sm" data-lk-del="${l.id}">删除</button>
            </td>
        </tr>`).join("")}</tbody></table></div>
    </div>`;

    /* ---- 分类：新增/编辑（Modal 表单） ---- */
    function catForm(k) {
        const ov = Modal.open({
            title: k ? "编辑分类" : "新增分类",
            body: `<div class="field"><label class="field__label">名称 *</label>
                <input class="input" id="lc-name" maxlength="20" value="${k ? esc(k.name) : ""}"></div>
                <div class="field"><label class="field__label">颜色（#RRGGBB）</label>
                <input class="input" id="lc-color" maxlength="7" value="${k ? esc(k.color) : "#4F6EF7"}"></div>
                <div class="field"><label class="field__label">排序（小在前）</label>
                <input class="input" id="lc-sort" type="number" value="${k ? k.sort : 0}"></div>`,
            foot: '<button class="btn btn--secondary" data-act="no">取消</button>'
                + '<button class="btn btn--primary" data-act="ok">保存</button>',
        });
        ov.querySelector('[data-act="no"]').onclick = () => Modal.close(ov);
        ov.querySelector('[data-act="ok"]').onclick = async () => {
            try {
                await fetchJSON("/api/admin/links/categories/save", {
                    method: "POST",
                    body: {
                        id: k ? k.id : 0,
                        name: document.getElementById("lc-name").value.trim(),
                        color: document.getElementById("lc-color").value.trim(),
                        sort: parseInt(document.getElementById("lc-sort").value, 10) || 0,
                    },
                });
                Modal.close(ov);
                Toast.show("已保存", "success");
                renderLinksAdmin(c);
            } catch (e) { /* 已提示 */ }
        };
    }
    document.getElementById("lk-cat-add").onclick = () => catForm(null);
    c.querySelectorAll("[data-cat-edit]").forEach((b) => {
        b.onclick = () => catForm(cats.list.find((k) => k.id === parseInt(b.dataset.catEdit, 10)));
    });
    c.querySelectorAll("[data-cat-del]").forEach((b) => {
        b.onclick = async () => {
            const k = cats.list.find((x) => x.id === parseInt(b.dataset.catDel, 10));
            const okc = await Modal.confirm({
                title: "删除分类",
                body: `删除分类「${esc(k.name)}」将<b>同时删除其下全部链接</b>，确定？`,
                okText: "删除",
            });
            if (!okc) return;
            try {
                await fetchJSON("/api/admin/links/categories/delete", {
                    method: "POST", body: { id: k.id },
                });
                Toast.show("已删除", "success");
                renderLinksAdmin(c);
            } catch (e) { /* 已提示 */ }
        };
    });

    /* ---- 链接：新增/编辑 ---- */
    function linkForm(l) {
        const opts = cats.list.map((k) =>
            `<option value="${k.id}" ${l && l.category_id === k.id ? "selected" : ""}>${esc(k.name)}</option>`).join("");
        const ov = Modal.open({
            title: l ? "编辑链接" : "新增链接",
            body: `<div class="field"><label class="field__label">名称 *</label>
                <input class="input" id="lf-name" maxlength="30" value="${l ? esc(l.name) : ""}"></div>
                <div class="field"><label class="field__label">URL（http/https）*</label>
                <input class="input" id="lf-url" maxlength="200" value="${l ? esc(l.url) : ""}"></div>
                <div class="field"><label class="field__label">分类 *</label>
                <select class="input" id="lf-cat">${opts}</select></div>
                <div class="field"><label class="field__label">一句话说明</label>
                <input class="input" id="lf-desc" maxlength="80" value="${l ? esc(l.description) : ""}"></div>
                <div class="field"><label class="field__label">排序</label>
                <input class="input" id="lf-sort" type="number" value="${l ? l.sort : 0}"></div>
                <label class="field__label" style="font-weight:400">
                <input type="checkbox" id="lf-active" ${!l || l.is_active ? "checked" : ""}> 启用（停用后前台不展示）</label>`,
            foot: '<button class="btn btn--secondary" data-act="no">取消</button>'
                + '<button class="btn btn--primary" data-act="ok">保存</button>',
        });
        ov.querySelector('[data-act="no"]').onclick = () => Modal.close(ov);
        ov.querySelector('[data-act="ok"]').onclick = async () => {
            try {
                await fetchJSON("/api/admin/links/save", {
                    method: "POST",
                    body: {
                        id: l ? l.id : 0,
                        name: document.getElementById("lf-name").value.trim(),
                        url: document.getElementById("lf-url").value.trim(),
                        category_id: parseInt(document.getElementById("lf-cat").value, 10),
                        description: document.getElementById("lf-desc").value.trim(),
                        sort: parseInt(document.getElementById("lf-sort").value, 10) || 0,
                        is_active: document.getElementById("lf-active").checked,
                    },
                });
                Modal.close(ov);
                Toast.show("已保存", "success");
                renderLinksAdmin(c);
            } catch (e) { /* 已提示 */ }
        };
    }
    document.getElementById("lk-add").onclick = () => linkForm(null);
    c.querySelectorAll("[data-lk-edit]").forEach((b) => {
        b.onclick = () => linkForm(links.list.find((l) => l.id === parseInt(b.dataset.lkEdit, 10)));
    });
    c.querySelectorAll("[data-lk-del]").forEach((b) => {
        b.onclick = async () => {
            const l = links.list.find((x) => x.id === parseInt(b.dataset.lkDel, 10));
            const okc = await Modal.confirm({
                title: "删除链接", body: `确定删除「${esc(l.name)}」？`, okText: "删除",
            });
            if (!okc) return;
            try {
                await fetchJSON("/api/admin/links/delete", {
                    method: "POST", body: { id: l.id },
                });
                Toast.show("已删除", "success");
                renderLinksAdmin(c);
            } catch (e) { /* 已提示 */ }
        };
    });

    /* ---- 推荐审批（通过→自动转正式链接；拒绝→标记；均幂等） ---- */
    c.querySelectorAll("[data-sub-ok]").forEach((b) => {
        b.onclick = async () => {
            try {
                await fetchJSON("/api/admin/links/submissions/review", {
                    method: "POST", body: { id: parseInt(b.dataset.subOk, 10), action: "approve" },
                });
                Toast.show("已通过并上墙", "success");
                renderLinksAdmin(c);
            } catch (e) { /* 已提示 */ }
        };
    });
    c.querySelectorAll("[data-sub-no]").forEach((b) => {
        b.onclick = async () => {
            const okc = await Modal.confirm({
                title: "拒绝推荐", body: "确定拒绝这条推荐？", okText: "拒绝",
            });
            if (!okc) return;
            try {
                await fetchJSON("/api/admin/links/submissions/review", {
                    method: "POST", body: { id: parseInt(b.dataset.subNo, 10), action: "reject" },
                });
                Toast.show("已拒绝", "success");
                renderLinksAdmin(c);
            } catch (e) { /* 已提示 */ }
        };
    });
}

/* ================= 站点设置 ================= */
async function renderSiteSettings(c) {
    c.innerHTML = `<div class="card">
        <div class="card__title">${icon("settings", 18)} 站点设置</div>
        <div class="text-faint mt-8" style="font-size:var(--fs-13)">可在后台修改、全站生效的全局配置。</div>
        <div class="field mt-16">
            <label class="field__label">GitHub 仓库链接</label>
            <input class="input" id="gh-url" type="url" maxlength="200"
                placeholder="https://github.com/你的组织/仓库" style="max-width:520px">
            <div class="text-faint mt-8" style="font-size:var(--fs-12)">
                留空则不在前台展示 GitHub 入口。配置后显示在「设置 → 关于」中。
            </div>
            <div class="mt-16"><button class="btn btn--primary" id="gh-save">保存</button></div>
        </div>
    </div>`;
    try {
        const r = await fetchJSON("/api/admin/settings/github");
        document.getElementById("gh-url").value = r.github_url || "";
    } catch (e) { /* 已提示 */ }
    document.getElementById("gh-save").onclick = async () => {
        const url = document.getElementById("gh-url").value.trim();
        try {
            await fetchJSON("/api/admin/settings/github", {
                method: "POST", body: { github_url: url },
            });
            Toast.show("已保存", "success");
        } catch (e) { /* 已提示 */ }
    };
}
