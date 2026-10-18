/* ============================================================
   theme.js —— 深色模式切换
   策略：localStorage("portal_theme") 记忆；未记忆时跟随
         prefers-color-scheme 系统偏好。切换按钮图标为日/月。
   依赖：icons.js
   ============================================================ */
"use strict";

(function initTheme() {
    const saved = localStorage.getItem("portal_theme");
    let theme;
    if (saved === "dark" || saved === "light") {
        theme = saved;
    } else {
        theme = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    }
    document.documentElement.dataset.theme = theme;

    // 系统偏好变化且用户未手动选择时跟随
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
        if (!localStorage.getItem("portal_theme")) {
            document.documentElement.dataset.theme = e.matches ? "dark" : "light";
        }
    });

    // 绑定所有主题切换按钮（导航可能渲染多个实例）
    document.addEventListener("DOMContentLoaded", () => {
        document.querySelectorAll("#theme-toggle, .theme-toggle").forEach((btn) => {
            btn.innerHTML = `<span class="icon-sun">${icon("sun", 18)}</span><span class="icon-moon">${icon("moon", 18)}</span>`;
            btn.addEventListener("click", () => {
                const cur = document.documentElement.dataset.theme;
                const next = cur === "dark" ? "light" : "dark";
                document.documentElement.dataset.theme = next;
                localStorage.setItem("portal_theme", next);
            });
        });
    });
})();
