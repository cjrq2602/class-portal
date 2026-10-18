/* ============================================================
   fx.js —— 全站轻量动效（零依赖，事件委托，不侵入业务代码）
   1. 点击涟漪：.btn / .chip / .tabbar__item 按下时注入 .ripple-ink；
   2. 数字滚动：Fx.numRoll(el, 新值) —— 300ms 向上滚出再滚入；
   3. prefers-reduced-motion 时全部关闭（涟漪不生成、数字直接赋值）。
   ============================================================ */
"use strict";
(function () {
    var reduced = window.matchMedia &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    /* ---- 点击涟漪（passive 监听 pointerdown，不阻塞滚动/点击） ---- */
    if (!reduced) {
        document.addEventListener("pointerdown", function (e) {
            var t = e.target.closest ? e.target.closest(".btn, .chip, .tabbar__item") : null;
            if (!t || t.disabled) return;
            var rect = t.getBoundingClientRect();
            var d = Math.max(rect.width, rect.height);
            var ink = document.createElement("span");
            ink.className = "ripple-ink";
            ink.style.width = ink.style.height = d + "px";
            ink.style.left = (e.clientX - rect.left - d / 2) + "px";
            ink.style.top = (e.clientY - rect.top - d / 2) + "px";
            t.appendChild(ink);
            setTimeout(function () { ink.remove(); }, 520);
        }, { passive: true });
    }

    /**
     * 数字滚动动画：元素文本变化时 300ms 滚出→换新值→滚入。
     * @param {HTMLElement} el 目标元素（自动加 .num-roll）
     * @param {string|number} newText 新文本
     */
    function numRoll(el, newText) {
        if (!el) return;
        var text = String(newText);
        if (reduced || el.textContent === text) { el.textContent = text; return; }
        el.classList.add("num-roll");
        el.classList.add("num-roll--out");
        setTimeout(function () {
            el.textContent = text;
            el.classList.remove("num-roll--out");
        }, 150);
    }

    window.Fx = { numRoll: numRoll, reduced: reduced };
})();
