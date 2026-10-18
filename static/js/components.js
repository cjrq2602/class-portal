/* ============================================================
   components.js —— 通用组件 JS 封装
   Toast（顶部滑入）/ Modal（Esc 可关 + 二次确认）/ 骨架屏 / 分页器 /
   fetchJSON（自动带 CSRF、401 跳登录、错误自动 Toast）/
   首字母头像（用户名哈希配色）/ 北京时间格式化
   依赖：icons.js（icon 函数）
   ============================================================ */
"use strict";

/* ================= Toast ================= */
const Toast = {
    _wrap: null,
    _ensure() {
        if (!this._wrap) {
            this._wrap = document.createElement("div");
            this._wrap.className = "toast-wrap";
            document.body.appendChild(this._wrap);
        }
        return this._wrap;
    },
    /** 弹出提示：type = success | danger | warning | info */
    show(msg, type = "info", ms = 2600) {
        const wrap = this._ensure();
        const iconName = { success: "check", danger: "alert", warning: "alert", info: "info" }[type] || "info";
        const el = document.createElement("div");
        el.className = `toast toast--${type}`;
        el.innerHTML = `${icon(iconName, 17)}<span></span>`;
        el.querySelector("span").textContent = msg;
        wrap.appendChild(el);
        setTimeout(() => {
            el.classList.add("toast--out");
            el.addEventListener("animationend", () => el.remove(), { once: true });
        }, ms);
    },
};

/* ================= Modal（Esc 可关） ================= */
const Modal = {
    _stack: [],
    /** 打开弹窗：{title, body(HTML字符串), foot(按钮区HTML)} 返回 overlay 元素 */
    open({ title = "", body = "", foot = "" }) {
        const overlay = document.createElement("div");
        overlay.className = "modal-overlay";
        overlay.innerHTML = `<div class="modal" role="dialog" aria-modal="true">
            ${title ? `<div class="modal__title">${title}</div>` : ""}
            <div class="modal__body">${body}</div>
            ${foot ? `<div class="modal__foot">${foot}</div>` : ""}
        </div>`;
        overlay.addEventListener("mousedown", (e) => {
            if (e.target === overlay) Modal.close(overlay); // 点遮罩关闭
        });
        document.body.appendChild(overlay);
        this._stack.push(overlay);
        return overlay;
    },
    close(overlay) {
        const el = overlay || this._stack[this._stack.length - 1];
        if (!el) return;
        this._stack = this._stack.filter((o) => o !== el);
        el.remove();
    },
    /**
     * 二次确认（危险操作用）：{title, body, okText, danger} → Promise<boolean>
     */
    confirm({ title = "确认操作", body = "", okText = "确认", danger = true }) {
        return new Promise((resolve) => {
            const overlay = this.open({
                title,
                body,
                foot: `<button class="btn btn--secondary" data-act="no">取消</button>
                       <button class="btn ${danger ? "btn--danger" : "btn--primary"}" data-act="yes">${okText}</button>`,
            });
            overlay.querySelector('[data-act="no"]').onclick = () => { this.close(overlay); resolve(false); };
            overlay.querySelector('[data-act="yes"]').onclick = () => { this.close(overlay); resolve(true); };
        });
    },
};
// Esc 关闭最上层弹窗（全局单监听）
document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && Modal._stack.length) Modal.close();
});

/* ================= 骨架屏 ================= */
const Skeleton = {
    /** 往容器注入骨架块：rows 个文本条 + 可选标题条 */
    inject(el, rows = 3, withTitle = true) {
        if (!el) return;
        let html = withTitle ? '<div class="skeleton skeleton--title"></div>' : "";
        for (let i = 0; i < rows; i++) html += '<div class="skeleton skeleton--text"></div>';
        el.innerHTML = html;
    },
    /** 卡片骨架 */
    cards(el, count = 2) {
        if (!el) return;
        el.innerHTML = '<div class="skeleton skeleton--card"></div>'.repeat(count);
    },
};

/* ================= 分页器 ================= */
const Pagination = {
    /**
     * 渲染分页：{page 当前, total 总条数, pageSize, onChange(fn)}
     * 页数多时省略中间（1 … 4 5 6 … 10）。
     */
    render(el, { page = 1, total = 0, pageSize = 20, onChange = () => {} }) {
        if (!el) return;
        const pages = Math.max(1, Math.ceil(total / pageSize));
        const nums = [];
        for (let p = 1; p <= pages; p++) {
            if (p === 1 || p === pages || Math.abs(p - page) <= 1) nums.push(p);
            else if (nums[nums.length - 1] !== "…") nums.push("…");
        }
        el.className = "pagination";
        el.innerHTML =
            `<button class="pagination__btn" data-p="${page - 1}" ${page <= 1 ? "disabled" : ""}>‹</button>` +
            nums.map((p) => (p === "…"
                ? '<span class="text-faint">…</span>'
                : `<button class="pagination__btn ${p === page ? "pagination__btn--active" : ""}" data-p="${p}">${p}</button>`
            )).join("") +
            `<button class="pagination__btn" data-p="${page + 1}" ${page >= pages ? "disabled" : ""}>›</button>`;
        el.querySelectorAll("button[data-p]").forEach((b) => {
            b.onclick = () => {
                const p = parseInt(b.dataset.p, 10);
                if (p >= 1 && p <= pages && p !== page) onChange(p);
            };
        });
    },
};

/* ================= fetch 封装 ================= */
/** 从模板 meta 读 CSRF 令牌（服务端 context_processor 注入） */
function csrfToken() {
    const m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.content : "";
}

/**
 * 统一请求封装：
 * - POST/PUT 自动带 X-CSRF-Token 头；
 * - 401 → 跳转登录页；
 * - ok=false → 自动 Toast 报错并抛错（调用方 await 后无需重复提示）。
 */
async function fetchJSON(url, { method = "GET", body = null } = {}) {
    const opt = { method, headers: { "Content-Type": "application/json" } };
    if (body !== null) {
        opt.body = JSON.stringify(body);
        opt.headers["X-CSRF-Token"] = csrfToken();
    }
    let res, json;
    try {
        res = await fetch(url, opt);
        json = await res.json();
    } catch (e) {
        Toast.show("网络异常，请检查连接", "danger");
        throw e;
    }
    if (res.status === 401) {
        Toast.show("请先登录", "warning");
        setTimeout(() => { location.href = "/login"; }, 600);
        throw new Error("unauthorized");
    }
    if (!json.ok) {
        Toast.show(json.msg || "操作失败", "danger");
        if (window.Sfx) Sfx.play("error");   // 错误低鸣（sfx.js 全局挂点）
        throw new Error(json.msg || "error");
    }
    return json;
}

/* ================= 首字母头像（用户名哈希配色） ================= */
const AVATAR_COLORS = ["#4F6EF7", "#8B5CF6", "#EC4899", "#F59E0B", "#10B981",
    "#06B6D4", "#EF4444", "#6366F1", "#14B8A6", "#F97316"];
/** 返回头像 HTML：取昵称首字符，颜色由名字哈希决定（同名永远同色） */
function avatarHtml(name = "?", sizeClass = "") {
    const ch = (name || "?").trim().charAt(0).toUpperCase() || "?";
    let hash = 0;
    for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
    const color = AVATAR_COLORS[hash % AVATAR_COLORS.length];
    return `<span class="avatar ${sizeClass}" style="background:${color}">${ch}</span>`;
}

/* ================= 时间格式化（库存 UTC → 展示北京时间） ================= */
/** Unix 秒 → 北京时间字符串（Intl 指定 Asia/Shanghai，与服务器 UTC+8 换算一致） */
function fmtBeijing(unixSec, withTime = true) {
    const d = new Date(unixSec * 1000);
    const datePart = new Intl.DateTimeFormat("zh-CN", {
        timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
    }).format(d);
    if (!withTime) return datePart;
    const timePart = new Intl.DateTimeFormat("zh-CN", {
        timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", hour12: false,
    }).format(d);
    return `${datePart} ${timePart}`;
}

/* ================= 输入框字数统计 ================= */
/** 给 textarea/input 挂字数统计：data-max 为上限 */
function bindCharCount(input, countEl) {
    if (!input || !countEl) return;
    const max = parseInt(input.dataset.max || "0", 10);
    const update = () => {
        countEl.textContent = max ? `${input.value.length} / ${max}` : `${input.value.length}`;
    };
    input.addEventListener("input", update);
    update();
}
