/* ============================================================
   fx.js —— 全站轻量动效（零依赖，事件委托，不侵入业务代码）
   1. 点击涟漪：.btn / .chip / .tabbar__item 按下时注入 .ripple-ink；
   2. 数字滚动：Fx.numRoll(el, 新值) —— 300ms 向上滚出再滚入；
   3. 页面平滑切换：点 Tab 先播 140ms 退出动画再跳转，
      并用 <link rel="prefetch"> 预取目标页（instant.page 思路），
      点击时目标 HTML 大多已在缓存，消解"点一下卡一下"的割裂感；
   4. prefers-reduced-motion 时全部关闭（涟漪不生成、数字直接赋值、
      Tab 保持原生直跳）。
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

    /* ================= 页面平滑切换（底部 Tab 坞专用） =================
       退出动画 140ms → 跳转；入场动画由 base.css 的 page-enter 自动播放。
       触摸/悬停瞬间预取目标页 HTML，配合退出动画遮住网络等待。 */
    if (!reduced) (function () {
        var leaving = false;

        /* 只接管「普通左键点击 + 同源站内」的 Tab 跳转，
           新窗口/修饰键/外链一律放行原生行为 */
        function isPlainNav(e, a) {
            return a && a.href && e.button === 0 &&
                !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey &&
                (!a.target || a.target === "_self") &&
                new URL(a.href, location.href).origin === location.origin;
        }

        /* 预取目标页（instant.page 思路）：pointerdown 比 click 早 ~100ms，
           桌面端再补 mouseover，两次预取只注入一次 */
        function prefetch(a) {
            if (!a || a.dataset.fxPrefetched) return;
            a.dataset.fxPrefetched = "1";
            var l = document.createElement("link");
            l.rel = "prefetch";
            l.href = a.href;
            document.head.appendChild(l);
        }
        function tabOf(e) {
            return e.target && e.target.closest ? e.target.closest(".tabbar__item[href]") : null;
        }
        document.addEventListener("pointerdown", function (e) {
            var a = tabOf(e);
            if (a) prefetch(a);
        }, { passive: true });
        document.addEventListener("mouseover", function (e) {
            var a = tabOf(e);
            if (a) prefetch(a);
        }, { passive: true });

        /* 点击 Tab：先播退出动画再跳转；当前页不播动画（避免原地闪） */
        document.addEventListener("click", function (e) {
            if (leaving || e.defaultPrevented) return;
            var a = tabOf(e);
            if (!isPlainNav(e, a)) return;
            var url = new URL(a.href, location.href);
            if (url.pathname === location.pathname && url.search === location.search) return;
            e.preventDefault();
            leaving = true;
            document.body.classList.add("is-leaving");
            setTimeout(function () { location.href = a.href; }, 140);
        });

        /* bfcache 恢复（iOS 手势返回）：摘掉退出态 + 不重播入场动画 */
        window.addEventListener("pageshow", function (e) {
            if (!e.persisted) return;
            leaving = false;
            document.body.classList.remove("is-leaving");
            document.body.classList.add("no-enter-anim");
        });
    })();
})();
