/* ============================================================
   theme.js —— 外观设置（明暗模式 / 皮肤包 / 语言 / 音效）
   ============================================================
   存储（localStorage）：
     portal_mode    auto | light | dark     （默认 auto，跟随系统）
     portal_skin    default | sakura | forest | sunset | ocean | mono
     portal_locale  zh-CN | zh-TW
     portal_theme   兼容第五期旧键（dark/light），仅作为 mode 的初值
   在 <head> 中执行，先于渲染应用属性，避免闪屏。
   设置面板的 DOM 绑定在 DOMContentLoaded 后进行。
   依赖：icons.js（图标）、sfx.js（音效开关，可选）、i18n.js（语言）
   ============================================================ */
"use strict";

(function () {
    const LS_MODE = "portal_mode";
    const LS_SKIN = "portal_skin";
    const LS_LOCALE = "portal_locale";
    const LS_THEME_OLD = "portal_theme";

    const MODES = ["auto", "light", "dark"];
    const SKINS = ["default", "sakura", "forest", "sunset", "ocean", "mono"];
    const LOCALES = ["zh-CN", "zh-TW"];

    const mq = window.matchMedia("(prefers-color-scheme: dark)");

    /* ---------------- 读取 ---------------- */
    function getMode() {
        const saved = localStorage.getItem(LS_MODE);
        if (MODES.indexOf(saved) >= 0) return saved;
        // 兼容第五期：旧键只有 dark/light，迁移为显式模式
        const old = localStorage.getItem(LS_THEME_OLD);
        if (old === "dark" || old === "light") return old;
        return "auto";
    }

    function getSkin() {
        const saved = localStorage.getItem(LS_SKIN);
        return SKINS.indexOf(saved) >= 0 ? saved : "default";
    }

    function getLocale() {
        const saved = localStorage.getItem(LS_LOCALE);
        return LOCALES.indexOf(saved) >= 0 ? saved : "zh-CN";
    }

    function resolveTheme(mode) {
        if (mode === "light" || mode === "dark") return mode;
        return mq.matches ? "dark" : "light";
    }

    /* ---------------- 应用 ---------------- */
    function applyMode(mode, persist) {
        if (MODES.indexOf(mode) < 0) mode = "auto";
        if (persist) localStorage.setItem(LS_MODE, mode);
        document.documentElement.dataset.theme = resolveTheme(mode);
        // 同步 meta theme-color，手机浏览器地址栏跟随
        const meta = document.querySelector('meta[name="theme-color"]');
        if (meta) {
            meta.setAttribute("content",
                resolveTheme(mode) === "dark" ? "#0B0F19" : "#F8FAFC");
        }
    }

    function applySkin(skin, persist) {
        if (SKINS.indexOf(skin) < 0) skin = "default";
        if (persist) localStorage.setItem(LS_SKIN, skin);
        document.documentElement.dataset.skin = skin;
    }

    function applyLocale(locale, persist) {
        if (LOCALES.indexOf(locale) < 0) locale = "zh-CN";
        if (persist) localStorage.setItem(LS_LOCALE, locale);
        document.documentElement.lang = locale;
    }

    /* ---------------- 初始化（head 阶段，防闪屏） ---------------- */
    applyMode(getMode(), false);
    applySkin(getSkin(), false);
    applyLocale(getLocale(), false);

    // 系统偏好变化：仅 auto 模式跟随
    mq.addEventListener("change", () => {
        if (getMode() === "auto") applyMode("auto", false);
    });

    // 暴露给设置面板与其他脚本
    window.PortalAppearance = {
        getMode: getMode, getSkin: getSkin, getLocale: getLocale,
        setMode: function (m) { applyMode(m, true); },
        setSkin: function (s) { applySkin(s, true); },
        setLocale: function (l) {
            applyLocale(l, true);
            // 简繁转换依赖整页重渲染，切换后直接刷新最稳
            location.reload();
        }
    };

    /* ---------------- 设置面板绑定 ---------------- */
    document.addEventListener("DOMContentLoaded", () => {
        /* 旧的日/月切换按钮（保留兼容：点一下在浅色/深色间切换） */
        document.querySelectorAll("#theme-toggle, .theme-toggle").forEach((btn) => {
            btn.innerHTML = `<span class="icon-sun">${icon("sun", 18)}</span>` +
                            `<span class="icon-moon">${icon("moon", 18)}</span>`;
            btn.addEventListener("click", () => {
                const cur = document.documentElement.dataset.theme;
                applyMode(cur === "dark" ? "light" : "dark", true);
                syncPanel();
            });
        });

        const panel = document.getElementById("settings-panel");
        const backdrop = document.getElementById("settings-backdrop");
        const opener = document.getElementById("settings-toggle");
        if (!panel) return;

        function openPanel() {
            panel.classList.add("is-open");
            if (backdrop) backdrop.classList.add("is-open");
            panel.setAttribute("aria-hidden", "false");
            syncPanel();
        }
        function closePanel() {
            panel.classList.remove("is-open");
            if (backdrop) backdrop.classList.remove("is-open");
            panel.setAttribute("aria-hidden", "true");
        }
        if (opener) opener.addEventListener("click", openPanel);
        if (backdrop) backdrop.addEventListener("click", closePanel);
        const closeBtn = document.getElementById("settings-close");
        if (closeBtn) closeBtn.addEventListener("click", closePanel);
        document.addEventListener("keydown", (e) => {
            if (e.key === "Escape") closePanel();
        });

        /* 明暗模式 */
        document.querySelectorAll("[data-set-mode]").forEach((btn) => {
            btn.addEventListener("click", () => {
                applyMode(btn.dataset.setMode, true);
                syncPanel();
            });
        });

        /* 皮肤 */
        document.querySelectorAll("[data-set-skin]").forEach((btn) => {
            btn.addEventListener("click", () => {
                applySkin(btn.dataset.setSkin, true);
                syncPanel();
            });
        });

        /* 语言 */
        document.querySelectorAll("[data-set-locale]").forEach((btn) => {
            btn.addEventListener("click", () => {
                window.PortalAppearance.setLocale(btn.dataset.setLocale);
            });
        });

        /* 音效开关（复用 Sfx） */
        const sfxSwitch = document.getElementById("set-sfx");
        if (sfxSwitch) {
            sfxSwitch.addEventListener("click", () => {
                if (!window.Sfx) return;
                const next = !Sfx.isEnabled();
                Sfx.setEnabled(next);
                syncPanel();
            });
        }

        /* 面板状态同步（当前选中项高亮） */
        function syncPanel() {
            const mode = getMode();
            const skin = getSkin();
            const locale = getLocale();
            document.querySelectorAll("[data-set-mode]").forEach((b) => {
                b.classList.toggle("is-active", b.dataset.setMode === mode);
            });
            document.querySelectorAll("[data-set-skin]").forEach((b) => {
                b.classList.toggle("is-active", b.dataset.setSkin === skin);
            });
            document.querySelectorAll("[data-set-locale]").forEach((b) => {
                b.classList.toggle("is-active", b.dataset.setLocale === locale);
            });
            if (sfxSwitch && window.Sfx) {
                sfxSwitch.classList.toggle("is-on", Sfx.isEnabled());
            }
        }
        syncPanel();
    });
})();
