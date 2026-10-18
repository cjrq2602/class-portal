/* ============================================================
   sfx.js —— UI 音效（Web Audio API 程序化合成，零音频文件、零依赖）
   音色：tick 轻点按 / ding 点赞叮 / checkin 打卡上行双音 / error 错误低鸣
   合规：首次用户交互（pointerdown/keydown/touchstart）前绝不创建 AudioContext、
         绝不发出任何声音（浏览器自动播放政策）。
   设置：开关（默认开）+ 音量（默认 50%）存 localStorage，BGM 面板可改。
   用法：Sfx.play("ding")；全站 .btn 点击自动 tick（事件委托）。
   ============================================================ */
"use strict";
(function () {
    var LS_ON = "sfx_on", LS_VOL = "sfx_vol";
    var enabled = localStorage.getItem(LS_ON) !== "0";        // 默认开启
    var vol = parseFloat(localStorage.getItem(LS_VOL) || "0.5");
    if (isNaN(vol)) vol = 0.5;
    var ctx = null, interacted = false;

    /* 首次交互后才允许创建/恢复 AudioContext */
    function ensureCtx() {
        if (!interacted) return null;
        if (!ctx) {
            var AC = window.AudioContext || window.webkitAudioContext;
            if (!AC) return null;
            try { ctx = new AC(); } catch (e) { return null; }
        }
        if (ctx.state === "suspended") ctx.resume();
        return ctx;
    }
    ["pointerdown", "keydown", "touchstart"].forEach(function (ev) {
        document.addEventListener(ev, function () {
            if (!interacted) {
                interacted = true;
                if (ctx && ctx.state === "suspended") ctx.resume();
            }
        }, { passive: true });
    });

    /* 单音合成：振荡器 + 指数衰减包络（linear 起音防爆音） */
    function tone(ac, freq, t0, dur, type, peak) {
        var o = ac.createOscillator(), g = ac.createGain();
        o.type = type;
        o.frequency.value = freq;
        g.gain.setValueAtTime(0.0001, t0);
        g.gain.linearRampToValueAtTime(peak, t0 + 0.012);
        g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
        o.connect(g);
        g.connect(ac.destination);
        o.start(t0);
        o.stop(t0 + dur + 0.02);
    }

    /* 四种程序化音色 */
    var SOUNDS = {
        /* 轻点按：短促三角波 */
        tick: function (ac, t, v) {
            tone(ac, 1500, t, 0.05, "triangle", 0.22 * v);
        },
        /* 点赞"叮"：清亮双正弦叠加 */
        ding: function (ac, t, v) {
            tone(ac, 1318, t, 0.28, "sine", 0.38 * v);
            tone(ac, 1976, t, 0.20, "sine", 0.14 * v);
        },
        /* 打卡成功：上行双音（E5 → B5） */
        checkin: function (ac, t, v) {
            tone(ac, 660, t, 0.12, "sine", 0.34 * v);
            tone(ac, 990, t + 0.10, 0.24, "sine", 0.34 * v);
        },
        /* 错误低鸣：低沉锯齿波 */
        error: function (ac, t, v) {
            tone(ac, 180, t, 0.26, "sawtooth", 0.16 * v);
        }
    };

    function play(name) {
        if (!enabled) return;
        var ac = ensureCtx();              // 未交互过 → null → 静默
        if (!ac || !SOUNDS[name]) return;
        try { SOUNDS[name](ac, ac.currentTime, vol); } catch (e) { /* 忽略 */ }
    }

    /* 轻点按 tick：事件委托绑定全部 .btn（禁用/加载态不响） */
    document.addEventListener("click", function (e) {
        var t = e.target.closest ? e.target.closest(".btn") : null;
        if (t && !t.disabled && !t.classList.contains("btn--loading")) play("tick");
    }, { passive: true });

    window.Sfx = {
        play: play,
        isEnabled: function () { return enabled; },
        setEnabled: function (on) {
            enabled = !!on;
            localStorage.setItem(LS_ON, on ? "1" : "0");
        },
        getVolume: function () { return vol; },
        setVolume: function (v) {
            vol = Math.max(0, Math.min(1, +v || 0));
            localStorage.setItem(LS_VOL, String(vol));
        }
    };
})();
