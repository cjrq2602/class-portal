/* ============================================================
   bgm.js —— 背景音乐悬浮播放器（右下角迷你播放器 + 音效设置面板）
   规则（严格对齐任务书）：
   1. 音频仅 /static/audio/bgm.mp3（≤1MB，免版权或自制）；文件缺失时整个
      播放器静默隐藏（audio error 事件降级，控制台零报错噪音）；
   2. 默认关闭；开关 / 音量 / 播放进度全部存 localStorage；
   3. 跨页面导航后自动续播（恢复上次进度 + 400ms 淡入）；
   4. 浏览器自动播放政策：开关为开但用户尚未交互过时，等首次
      pointerdown/keydown/touchstart 后再起播，此前绝不出声；
   5. 标签页隐藏时暂停、可见时若开关为开则恢复；
   6. 面板同时管界面音效（联动 sfx.js 的 Sfx 设置）。
   ============================================================ */
"use strict";
(function () {
    var LS_ON = "bgm_on", LS_VOL = "bgm_vol", LS_T = "bgm_t";
    var wantPlay = localStorage.getItem(LS_ON) === "1";      // 默认关闭
    var vol = parseFloat(localStorage.getItem(LS_VOL) || "0.5");
    if (isNaN(vol)) vol = 0.5;
    var interacted = false, audio = null, dead = false, fadeTimer = null, saveTimer = null;

    /* ---- 构建 DOM（FAB + 面板） ---- */
    var wrap = document.createElement("div");
    wrap.className = "bgm-player";
    wrap.innerHTML =
        '<div class="bgm-panel" id="bgm-panel">' +
        '  <div class="bgm-panel__row"><label>背景音乐</label>' +
        '    <button class="bgm-toggle" id="bgm-toggle" type="button"></button></div>' +
        '  <div class="bgm-panel__row"><label>音乐音量</label>' +
        '    <input type="range" id="bgm-vol" min="0" max="100"></div>' +
        '  <div class="bgm-panel__row"><label>界面音效</label>' +
        '    <button class="bgm-toggle" id="sfx-toggle" type="button"></button></div>' +
        '  <div class="bgm-panel__row"><label>音效音量</label>' +
        '    <input type="range" id="sfx-vol" min="0" max="100"></div>' +
        '</div>' +
        '<button class="bgm-fab" id="bgm-fab" type="button" aria-label="音乐与音效设置"></button>';
    document.body.appendChild(wrap);

    var fab = document.getElementById("bgm-fab");
    var panel = document.getElementById("bgm-panel");
    var bgmToggle = document.getElementById("bgm-toggle");
    var bgmVol = document.getElementById("bgm-vol");
    var sfxToggle = document.getElementById("sfx-toggle");
    var sfxVol = document.getElementById("sfx-vol");
    fab.innerHTML = icon("music", 20);

    /* ---- 面板状态回填 ---- */
    function refreshPanel() {
        bgmToggle.textContent = wantPlay ? "已开启" : "已关闭";
        bgmToggle.classList.toggle("bgm-toggle--on", wantPlay);
        bgmVol.value = String(Math.round(vol * 100));
        if (window.Sfx) {
            var on = Sfx.isEnabled();
            sfxToggle.textContent = on ? "已开启" : "已关闭";
            sfxToggle.classList.toggle("bgm-toggle--on", on);
            sfxVol.value = String(Math.round(Sfx.getVolume() * 100));
        } else {
            sfxToggle.disabled = true;
            sfxVol.disabled = true;
        }
        fab.classList.toggle("bgm-fab--playing", !!(audio && !audio.paused));
    }

    /* ---- 音频对象（缺失 → 静默隐藏整个播放器） ----
       先 HEAD 探测再创建 Audio：避免文件未部署时控制台出现
       红色媒体请求错误（404 在 Network 面板可见但控制台保持干净） */
    var BGM_URL = "/static/audio/bgm.mp3";
    function initAudio() {
        audio = new Audio(BGM_URL);
        audio.loop = true;
        audio.volume = 0;                       // 起播前从 0 淡入
        audio.addEventListener("error", function () {
            dead = true;
            wrap.style.display = "none";        // bgm.mp3 未部署：降级隐藏
        });
        /* 播放中每 3s 记一次进度（timeupdate 节流），保证跨页续播精度 */
        audio.addEventListener("timeupdate", function () {
            if (saveTimer) return;
            saveTimer = setTimeout(function () {
                saveTimer = null;
                if (audio && !audio.paused) {
                    try { localStorage.setItem(LS_T, String(audio.currentTime || 0)); } catch (e) { }
                }
            }, 3000);
        });
    }
    fetch(BGM_URL, { method: "HEAD" }).then(function (r) {
        if (!r.ok) { dead = true; wrap.style.display = "none"; return; }
        initAudio();
        refreshPanel();
        /* 探测期间用户已交互且开关为开 → 立即起播（否则等首次交互钩子） */
        if (wantPlay && interacted) doPlay();
    }).catch(function () {
        dead = true;
        wrap.style.display = "none";
    });

    /* 400ms 淡入到目标音量 */
    function fadeIn() {
        clearInterval(fadeTimer);
        fadeTimer = setInterval(function () {
            if (!audio) return;
            var nv = Math.min(vol, audio.volume + vol / 4);
            audio.volume = nv;
            if (nv >= vol) clearInterval(fadeTimer);
        }, 100);
    }

    function doPlay() {
        if (dead || !audio) return;
        /* 恢复上次进度（跨页续播核心） */
        var t = parseFloat(localStorage.getItem(LS_T) || "0");
        if (t > 1 && isFinite(t)) {
            try { audio.currentTime = t; } catch (e) { /* 元数据未就绪时忽略 */ }
        }
        audio.volume = 0;
        var p = audio.play();
        if (p && p.catch) p.catch(function () { /* 政策拦截：保持静默等待交互 */ });
        fadeIn();
        refreshPanel();
    }

    function doPause() {
        if (!audio) return;
        clearInterval(fadeTimer);
        audio.pause();
        try { localStorage.setItem(LS_T, String(audio.currentTime || 0)); } catch (e) { }
        refreshPanel();
    }

    /* ---- 事件绑定 ---- */
    fab.addEventListener("click", function () {
        panel.classList.toggle("bgm-panel--open");
        refreshPanel();
    });
    bgmToggle.addEventListener("click", function () {
        wantPlay = !wantPlay;
        localStorage.setItem(LS_ON, wantPlay ? "1" : "0");
        if (wantPlay) doPlay(); else doPause();
        refreshPanel();
    });
    bgmVol.addEventListener("input", function () {
        vol = Math.max(0, Math.min(1, (+bgmVol.value || 0) / 100));
        localStorage.setItem(LS_VOL, String(vol));
        if (audio && !audio.paused) audio.volume = vol;
    });
    sfxToggle.addEventListener("click", function () {
        if (window.Sfx) Sfx.setEnabled(!Sfx.isEnabled());
        refreshPanel();
    });
    sfxVol.addEventListener("input", function () {
        if (window.Sfx) Sfx.setVolume((+sfxVol.value || 0) / 100);
    });

    /* 标签页隐藏暂停、可见恢复（开关为开时） */
    document.addEventListener("visibilitychange", function () {
        if (!audio || dead) return;
        if (document.hidden) {
            if (!audio.paused) { doPause(); audio._autoResume = true; }
        } else if (audio._autoResume && wantPlay) {
            audio._autoResume = false;
            doPlay();
        }
    });

    /* 首次用户交互：若开关为开则在此刻起播（政策合规的关键路径） */
    ["pointerdown", "keydown", "touchstart"].forEach(function (ev) {
        document.addEventListener(ev, function () {
            if (!interacted) {
                interacted = true;
                if (wantPlay && !dead && audio && audio.paused) doPlay();
            }
        }, { passive: true });
    });

    refreshPanel();
})();
