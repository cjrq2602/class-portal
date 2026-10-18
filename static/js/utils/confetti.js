/* ============================================================
   confetti.js —— Canvas 撒花组件（≤3KB，零依赖，零外部图片）
   用法：Confetti.fire() 或 Confetti.fire({ count: 160 })
   触发点：游戏胜利结算、打卡连续 7 天里程碑。
   prefers-reduced-motion 时直接返回（不放任何粒子）。
   ============================================================ */
"use strict";
(function () {
    var COLORS = ["#4F6EF7", "#8B5CF6", "#EC4899", "#F59E0B", "#10B981", "#0EA5E9"];

    function fire(opts) {
        if (window.matchMedia &&
            window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
        opts = opts || {};
        var n = opts.count || 120;
        var cv = document.createElement("canvas");
        cv.className = "confetti-layer";           // fixed 全屏 + pointer-events:none
        cv.width = window.innerWidth;
        cv.height = window.innerHeight;
        document.body.appendChild(cv);
        var ctx = cv.getContext("2d");
        var parts = [];
        for (var i = 0; i < n; i++) {
            parts.push({
                x: cv.width / 2 + (Math.random() - 0.5) * cv.width * 0.4,
                y: cv.height * 0.35,
                vx: (Math.random() - 0.5) * 9,
                vy: -Math.random() * 10 - 4,
                g: 0.22 + Math.random() * 0.1,
                s: 4 + Math.random() * 5,
                r: Math.random() * Math.PI,
                vr: (Math.random() - 0.5) * 0.25,
                c: COLORS[(Math.random() * COLORS.length) | 0],
                life: 130 + Math.random() * 40
            });
        }
        var frame = 0;
        (function tick() {
            ctx.clearRect(0, 0, cv.width, cv.height);
            var alive = false;
            for (var i = 0; i < parts.length; i++) {
                var p = parts[i];
                if (frame > p.life) continue;
                alive = true;
                p.x += p.vx; p.y += p.vy; p.vy += p.g; p.r += p.vr;
                var fade = 1 - Math.max(0, frame - p.life + 30) / 30;  // 末尾 30 帧淡出
                ctx.save();
                ctx.globalAlpha = Math.max(0, Math.min(1, fade));
                ctx.translate(p.x, p.y);
                ctx.rotate(p.r);
                ctx.fillStyle = p.c;
                ctx.fillRect(-p.s / 2, -p.s / 4, p.s, p.s / 2);
                ctx.restore();
            }
            frame++;
            if (alive && frame < 220) requestAnimationFrame(tick);
            else cv.remove();                       // 播完自动清理 DOM
        })();
    }

    window.Confetti = { fire: fire };
})();
