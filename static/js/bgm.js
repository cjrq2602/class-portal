/* ============================================================
   bgm.js —— 背景音乐悬浮播放器（右下角迷你播放器 + 音效设置面板）
   规则：
   1. 多曲目清单 TRACKS：启动时逐首 HEAD 探测，缺失的自动跳过，
      全部缺失才静默隐藏整个播放器（控制台零报错噪音）；
   2. 默认关闭；开关 / 音量 / 当前曲目 / 每首各自进度全部存 localStorage；
   3. 跨页面导航后自动续播（恢复上次曲目 + 进度 + 400ms 淡入）；
   4. 浏览器自动播放政策：开关为开但用户尚未交互过时，等首次
      pointerdown/keydown/touchstart 后再起播，此前绝不出声；
   5. 标签页隐藏时暂停、可见时若开关为开则恢复；
   6. 面板同时管界面音效（联动 sfx.js 的 Sfx 设置）；
   7. 换曲：保存旧曲进度 → 切 src → 元数据就绪后恢复新曲进度。
   ============================================================ */
"use strict";
(function () {
    /* 曲目清单：file 相对站点根，name 显示在面板里 */
    var TRACKS = [
        { id: "calm",      file: "/static/audio/bgm-calm.mp3",      name: "自作 · 轻音乐" },
        { id: "gufeng",    file: "/static/audio/bgm-gufeng.mp3",    name: "自作 · 古风" },
        { id: "summer",    file: "/static/audio/bgm-summer.mp3",    name: "Summer Melody（夏日旋律）" },
        { id: "afterrain", file: "/static/audio/bgm-afterrain.mp3", name: "After The Rain（雨后）" },
        { id: "concert",   file: "/static/audio/bgm-concert.mp3",   name: "她来听我的演唱会" },
        { id: "ocean",     file: "/static/audio/bgm-ocean.mp3",     name: "Oceanfront（海滨）" },
    ];

    var LS_ON = "bgm_on", LS_VOL = "bgm_vol", LS_TRACK = "bgm_track";
    function lsT(id) { return "bgm_t_" + id; }          // 每首各自记进度
    var wantPlay = localStorage.getItem(LS_ON) === "1"; // 默认关闭
    var vol = parseFloat(localStorage.getItem(LS_VOL) || "0.5");
    if (isNaN(vol)) vol = 0.5;
    var interacted = false, audio = null, dead = false, fadeTimer = null, saveTimer = null;
    var avail = [];        // 可用曲目（TRACKS 子集，保持原顺序）
    var cur = null;        // 当前曲目
    var pendingT = 0;      // 待恢复进度（loadedmetadata 后生效）

    /* ---- 构建 DOM（FAB + 面板） ---- */
    var wrap = document.createElement("div");
    wrap.className = "bgm-player";
    wrap.innerHTML =
        '<div class="bgm-panel" id="bgm-panel">' +
        '  <div class="bgm-panel__row"><label>背景音乐</label>' +
        '    <button class="bgm-toggle" id="bgm-toggle" type="button"></button></div>' +
        '  <div class="bgm-panel__row"><label>曲目</label>' +
        '    <select class="bgm-track" id="bgm-track"></select></div>' +
        '  <div class="bgm-panel__row bgm-panel__row--btns">' +
        '    <button class="bgm-mini-btn" id="bgm-prev" type="button">⏮ 上一首</button>' +
        '    <button class="bgm-mini-btn" id="bgm-next" type="button">下一首 ⏭</button></div>' +
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
    var trackSel = document.getElementById("bgm-track");
    var prevBtn = document.getElementById("bgm-prev");
    var nextBtn = document.getElementById("bgm-next");
    var bgmVol = document.getElementById("bgm-vol");
    var sfxToggle = document.getElementById("sfx-toggle");
    var sfxVol = document.getElementById("sfx-vol");
    fab.innerHTML = icon("music", 20);

    /* ---- 面板状态回填 ---- */
    function refreshPanel() {
        bgmToggle.textContent = wantPlay ? "已开启" : "已关闭";
        bgmToggle.classList.toggle("bgm-toggle--on", wantPlay);
        bgmVol.value = String(Math.round(vol * 100));
        var multi = avail.length > 1;
        trackSel.disabled = !avail.length;
        prevBtn.disabled = !multi;
        nextBtn.disabled = !multi;
        if (cur) trackSel.value = cur.id;
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

    function fillTrackSelect() {
        trackSel.innerHTML = avail.map(function (t) {
            return '<option value="' + t.id + '">' + t.name + "</option>";
        }).join("");
    }

    /* ---- 进度读写 ---- */
    function saveProgress() {
        if (!audio || !cur) return;
        try { localStorage.setItem(lsT(cur.id), String(audio.currentTime || 0)); } catch (e) { }
    }
    function restoreProgress() {
        pendingT = 0;
        if (!cur) return;
        var t = parseFloat(localStorage.getItem(lsT(cur.id)) || "0");
        if (t > 1 && isFinite(t)) pendingT = t;
    }

    /* ---- 音频对象 ---- */
    function initAudio() {
        audio = new Audio();
        audio.loop = true;
        audio.volume = 0;                       // 起播前从 0 淡入
        audio.addEventListener("loadedmetadata", function () {
            if (pendingT > 1) {
                try { audio.currentTime = pendingT; } catch (e) { /* 忽略 */ }
                pendingT = 0;
            }
        });
        audio.addEventListener("error", function () {
            /* 当前曲目播放中损坏：从可用清单剔除并自动换下一首 */
            if (cur) avail = avail.filter(function (t) { return t.id !== cur.id; });
            if (!avail.length) {
                dead = true;
                wrap.style.display = "none";
                return;
            }
            fillTrackSelect();
            switchTrack(avail[0], true);
        });
        /* 播放中每 3s 记一次进度（timeupdate 节流），保证跨页续播精度 */
        audio.addEventListener("timeupdate", function () {
            if (saveTimer) return;
            saveTimer = setTimeout(function () {
                saveTimer = null;
                if (audio && !audio.paused) saveProgress();
            }, 3000);
        });
    }

    /* 换曲：存旧进度 → 换 src → 恢复新曲进度（元数据就绪后生效） */
    function switchTrack(track, autoplay) {
        if (!track) return;
        saveProgress();
        cur = track;
        try { localStorage.setItem(LS_TRACK, track.id); } catch (e) { }
        restoreProgress();
        audio.src = track.file;                  // loadedmetadata 里恢复 pendingT
        if (autoplay && wantPlay) {
            audio.volume = 0;
            var p = audio.play();
            if (p && p.catch) p.catch(function () { /* 政策拦截：等交互 */ });
            fadeIn();
        }
        refreshPanel();
    }

    /* ---- 逐首 HEAD 探测：缺失跳过，全缺才隐藏 ---- */
    Promise.all(TRACKS.map(function (t) {
        return fetch(t.file, { method: "HEAD" })
            .then(function (r) { return r.ok ? t : null; })
            .catch(function () { return null; });
    })).then(function (results) {
        avail = results.filter(function (t) { return !!t; });
        if (!avail.length) {
            dead = true;
            wrap.style.display = "none";
            return;
        }
        initAudio();
        /* 初始曲目：上次选的还在可用清单里就用它，否则第一首可用的 */
        var savedId = localStorage.getItem(LS_TRACK) || "";
        cur = null;
        for (var i = 0; i < avail.length; i++) {
            if (avail[i].id === savedId) { cur = avail[i]; break; }
        }
        if (!cur) cur = avail[0];
        fillTrackSelect();
        restoreProgress();
        audio.src = cur.file;                    // 预载元数据，不起播
        refreshPanel();
        /* 探测期间用户已交互且开关为开 → 立即起播（否则等首次交互钩子） */
        if (wantPlay && interacted) doPlay();
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
        if (dead || !audio || !cur) return;
        if (!audio.src) {                        // 尚未装曲：装当前曲目并恢复进度
            restoreProgress();
            audio.src = cur.file;
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
        saveProgress();
        refreshPanel();
    }

    /* 上一首 / 下一首（按可用清单循环） */
    function step(d) {
        if (avail.length < 2 || !cur) return;
        var i = avail.indexOf(cur);
        i = (i + d + avail.length) % avail.length;
        switchTrack(avail[i], true);
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
    trackSel.addEventListener("change", function () {
        var id = trackSel.value;
        for (var i = 0; i < avail.length; i++) {
            if (avail[i].id === id) { switchTrack(avail[i], true); break; }
        }
    });
    prevBtn.addEventListener("click", function () { step(-1); });
    nextBtn.addEventListener("click", function () { step(1); });
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
