/* ============================================================
   theme.js —— 外观设置（明暗模式 / 皮肤包 / 背景图 / 语言 / 音效）
   ============================================================
   存储（localStorage）：
     portal_mode    auto | light | dark     （默认 auto，跟随系统）
     portal_skin    default | sakura | forest | sunset | ocean | mono
     portal_bg      aurora | sakura | ... | twilight | none（背景图）
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
    const LS_BG = "portal_bg";
    const LS_NAVGLASS = "portal_navglass";
    const LS_LOCALE = "portal_locale";
    const LS_THEME_OLD = "portal_theme";

    const MODES = ["auto", "light", "dark"];
    const SKINS = ["default", "sakura", "forest", "sunset", "ocean", "mono"];
    /* 背景图清单：与 static/img/bg/bg-<id>.jpg 一一对应；
       none = 无背景（纯色页面）。aurora 为默认。
       第九期新增 24 张插画图：anime 动漫风景 / animechar 动漫人物 /
       game 游戏人物 / nature 自然 / wild 旷野 / cozy 温馨，各 4 张。 */
    const BGS = [
        "aurora", "sakura", "forest", "sunset", "ocean", "mono",
        "mint", "lavender", "peach", "sky", "lilac", "lime",
        "rosegold", "butter", "glacier", "matcha", "taro", "coral",
        "sage", "twilight",
        "anime1", "anime2", "anime3", "anime4",
        "animechar1", "animechar2", "animechar3", "animechar4",
        "game1", "game2", "game3", "game4",
        "nature1", "nature2", "nature3", "nature4",
        "wild1", "wild2", "wild3", "wild4",
        "cozy1", "cozy2", "cozy3", "cozy4",
        "none"
    ];
    const LOCALES = ["zh-CN", "zh-TW"];
    /* 导航栏质感：default 现有半透明白 / frost 加强毛玻璃 /
       dark 深色玻璃（导航内文字转浅色）/ tint 主色彩玻璃（跟随皮肤色） */
    const NAVGLASSES = ["default", "frost", "dark", "tint"];

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

    function getBg() {
        const saved = localStorage.getItem(LS_BG);
        return BGS.indexOf(saved) >= 0 ? saved : "aurora";
    }

    function getNavglass() {
        const saved = localStorage.getItem(LS_NAVGLASS);
        return NAVGLASSES.indexOf(saved) >= 0 ? saved : "default";
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

    function applyBg(bg, persist) {
        if (BGS.indexOf(bg) < 0) bg = "aurora";
        if (persist) localStorage.setItem(LS_BG, bg);
        document.documentElement.dataset.bg = bg;
        if (bg === "none") {
            // data-bg="none" 时 CSS 关掉伪元素，变量无需设置
            document.documentElement.style.removeProperty("--bg-image");
        } else {
            document.documentElement.style.setProperty(
                "--bg-image", 'url("/static/img/bg/bg-' + bg + '.jpg")');
        }
    }

    function applyNavglass(v, persist) {
        if (NAVGLASSES.indexOf(v) < 0) v = "default";
        if (persist) localStorage.setItem(LS_NAVGLASS, v);
        document.documentElement.dataset.navglass = v;
    }

    function applyLocale(locale, persist) {
        if (LOCALES.indexOf(locale) < 0) locale = "zh-CN";
        if (persist) localStorage.setItem(LS_LOCALE, locale);
        document.documentElement.lang = locale;
    }

    /* ---------------- 初始化（head 阶段，防闪屏） ---------------- */
    applyMode(getMode(), false);
    applySkin(getSkin(), false);
    applyBg(getBg(), false);
    applyNavglass(getNavglass(), false);
    applyLocale(getLocale(), false);

    // 系统偏好变化：仅 auto 模式跟随
    mq.addEventListener("change", () => {
        if (getMode() === "auto") applyMode("auto", false);
    });

    // 暴露给设置面板与其他脚本
    window.PortalAppearance = {
        getMode: getMode, getSkin: getSkin, getBg: getBg, getLocale: getLocale,
        getNavglass: getNavglass,
        setMode: function (m) { applyMode(m, true); },
        setSkin: function (s) { applySkin(s, true); },
        setBg: function (b) { applyBg(b, true); },
        setNavglass: function (v) { applyNavglass(v, true); },
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

        /* 背景图 */
        document.querySelectorAll("[data-set-bg]").forEach((btn) => {
            btn.addEventListener("click", () => {
                applyBg(btn.dataset.setBg, true);
                syncPanel();
            });
        });

        /* 导航栏质感 */
        document.querySelectorAll("[data-set-navglass]").forEach((btn) => {
            btn.addEventListener("click", () => {
                applyNavglass(btn.dataset.setNavglass, true);
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
            const bg = getBg();
            const locale = getLocale();
            const navglass = getNavglass();
            document.querySelectorAll("[data-set-mode]").forEach((b) => {
                b.classList.toggle("is-active", b.dataset.setMode === mode);
            });
            document.querySelectorAll("[data-set-skin]").forEach((b) => {
                b.classList.toggle("is-active", b.dataset.setSkin === skin);
            });
            document.querySelectorAll("[data-set-bg]").forEach((b) => {
                b.classList.toggle("is-active", b.dataset.setBg === bg);
            });
            document.querySelectorAll("[data-set-navglass]").forEach((b) => {
                b.classList.toggle("is-active", b.dataset.setNavglass === navglass);
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
