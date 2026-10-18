/*
 * static/js/app.js —— 前端唯一脚本（原生 JS，无框架无构建工具）
 * 职责：
 * 1. 页面流管理：主菜单 → 匹配 → 大厅 → 房间 → 游戏 → 结算；
 * 2. 自适应轮询：大厅 3s / 备战 1s / 战斗 0.5s；页面切后台暂停、回前台立即拉取；
 * 3. Canvas 渲染战场（地图/炮塔/怪物/弹道），客户端只渲染不计算胜负；
 * 4. localStorage 持久化 token/昵称（断线 60s 内刷新自动恢复身份）；
 * 5. 声音（WebAudio 合成音，无外部资源）；统一错误/断线提示 UI。
 */

"use strict";

// ============================================================
// 全局状态
// ============================================================
const S = {
    token: localStorage.getItem("nw_token") || "",
    nickname: localStorage.getItem("nw_nick") || "",
    roomCode: "",
    isSpectator: false,
    version: 0,          // 服务端状态版本（轮询变更检测）
    lastChatId: 0,       // 聊天增量游标
    state: null,         // 最近一次完整状态
    selectedTower: null, // 当前选择建造的炮塔类型
    dualMode: false,     // 双人炮塔模式
    dualPartner: null,   // 双人炮塔队友 id
    selectedTowerId: null,
    polling: null,       // 轮询定时器
    pollInterval: 3000,
    soundOn: localStorage.getItem("nw_sound") !== "off",
    screen: "menu",
    // 渲染插值用（客户端平滑移动）
    renderMonsters: new Map(),
};

// ===== 门户账号打通（登录态注入） =====
// 已登录：服务端注入派生令牌与账号昵称 → 自动带入游戏（战绩同步入库）；
// 未登录：__PORTAL__ 为 {logged_in:false}，完全走下方匿名 token 旧流程。
if (window.__PORTAL__ && window.__PORTAL__.token) {
    S.token = window.__PORTAL__.token;
    S.nickname = window.__PORTAL__.nickname || S.nickname;
}

// ============================================================
// 工具
// ============================================================
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

function toast(msg, type = "", ms = 2600) {
    const el = $("#toast");
    el.textContent = msg;
    el.className = type ? type : "";
    el.classList.remove("hidden");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.add("hidden"), ms);
}

function showPage(name) {
    S.screen = name;
    $$(".page").forEach((p) => p.classList.add("hidden"));
    const page = $("#page-" + name);
    if (page) page.classList.remove("hidden");
    window.scrollTo(0, 0);
}

async function api(path, data, method = "POST") {
    // 统一请求封装：自动带 token + CSRF 头，统一错误提示
    try {
        const opt = { method, headers: { "Content-Type": "application/json" } };
        if (method === "POST") {
            opt.body = JSON.stringify(Object.assign({ token: S.token }, data || {}));
            // 门户 CSRF：从模板 meta 读令牌随 POST 提交
            const _csrf = document.querySelector('meta[name="csrf-token"]');
            if (_csrf) opt.headers["X-CSRF-Token"] = _csrf.content;
        }
        const res = await fetch(path, opt);
        const json = await res.json();
        if (!json.ok) {
            toast(json.msg || json.error || "操作失败", "err");
            if (res.status === 401) {
                // token 失效：清空回主菜单
                S.token = "";
                localStorage.removeItem("nw_token");
                stopPolling();
                showPage("menu");
            }
            return null;
        }
        return json;
    } catch (e) {
        toast("网络异常，请检查连接", "err");
        return null;
    }
}

// ============================================================
// 声音（WebAudio 全程序化合成：音效 + 背景音乐，零外部音频文件）
// 浏览器自动播放策略：AudioContext 必须在用户首次交互后才能运行，
// 因此所有发声函数都走 ensureAudio() 惰性创建 + resume。
// ============================================================
let audioCtx = null, masterGain = null, sfxBus = null, musicBus = null;
let musicTimer = null, musicStep = 0;
const _sfxLastAt = {};   // 高频音效节流（防止怪多时机枪式叠音爆噪）

/** 惰性创建/恢复 AudioContext；静音或不可用时返回 null */
function ensureAudio() {
    if (!S.soundOn) return null;
    try {
        if (!audioCtx) {
            const AC = window.AudioContext || window.webkitAudioContext;
            if (!AC) return null;
            audioCtx = new AC();
            masterGain = audioCtx.createGain();
            masterGain.gain.value = 0.9;
            masterGain.connect(audioCtx.destination);
            sfxBus = audioCtx.createGain();      // 音效总线
            sfxBus.gain.value = 1.0;
            sfxBus.connect(masterGain);
            musicBus = audioCtx.createGain();    // 音乐总线（独立低音量）
            musicBus.gain.value = 0.55;
            musicBus.connect(masterGain);
        }
        if (audioCtx.state === "suspended") audioCtx.resume();
        return audioCtx;
    } catch (e) { return null; }
}

/** 带包络的单音：{type, f0, f1(滑向), dur, vol, when(延迟), bus} */
function tone(opts) {
    const ac = ensureAudio();
    if (!ac || ac.state !== "running") return;
    try {
        const t0 = ac.currentTime + (opts.when || 0);
        const o = ac.createOscillator();
        const g = ac.createGain();
        o.type = opts.type || "sine";
        o.frequency.setValueAtTime(Math.max(1, opts.f0), t0);
        if (opts.f1) o.frequency.exponentialRampToValueAtTime(Math.max(1, opts.f1), t0 + opts.dur);
        g.gain.setValueAtTime(0.0001, t0);
        g.gain.exponentialRampToValueAtTime(opts.vol || 0.15, t0 + 0.012);
        g.gain.exponentialRampToValueAtTime(0.0001, t0 + opts.dur);
        o.connect(g);
        g.connect(opts.bus || sfxBus);
        o.start(t0);
        o.stop(t0 + opts.dur + 0.05);
    } catch (e) { /* 忽略音频失败 */ }
}

/** 白噪声爆裂（爆炸/击杀质感），带低通滤波 + 自然衰减 */
function noiseBurst(dur = 0.15, vol = 0.2, filterFreq = 900) {
    const ac = ensureAudio();
    if (!ac || ac.state !== "running") return;
    try {
        const len = Math.max(1, Math.floor(ac.sampleRate * dur));
        const buf = ac.createBuffer(1, len, ac.sampleRate);
        const data = buf.getChannelData(0);
        for (let i = 0; i < len; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / len);
        const src = ac.createBufferSource();
        src.buffer = buf;
        const f = ac.createBiquadFilter();
        f.type = "lowpass";
        f.frequency.value = filterFreq;
        const g = ac.createGain();
        g.gain.value = vol;
        src.connect(f); f.connect(g); g.connect(sfxBus);
        src.start();
    } catch (e) { /* 忽略音频失败 */ }
}

/** 音效表：全部程序化合成；高频音效自动节流 */
function sfx(type) {
    if (!S.soundOn) return;
    const now = performance.now();
    const minGap = { fire: 70, hit: 90, kill: 80 }[type] || 0;
    if (minGap && now - (_sfxLastAt[type] || 0) < minGap) return;
    _sfxLastAt[type] = now;
    switch (type) {
        case "fire":    // 炮塔开火：短促方波下扫
            tone({ type: "square", f0: 760, f1: 320, dur: 0.07, vol: 0.05 });
            break;
        case "hit":     // 命中：三角波下扫
            tone({ type: "triangle", f0: 330, f1: 140, dur: 0.09, vol: 0.07 });
            break;
        case "kill":    // 击杀：噪声爆裂 + 低频闷响
            noiseBurst(0.16, 0.16, 1100);
            tone({ type: "sine", f0: 150, f1: 55, dur: 0.18, vol: 0.14 });
            break;
        case "leak":    // 漏怪警报：双低频锯齿下坠
            tone({ type: "sawtooth", f0: 170, f1: 60, dur: 0.4, vol: 0.16 });
            tone({ type: "sawtooth", f0: 172, f1: 62, dur: 0.4, vol: 0.1, when: 0.02 });
            break;
        case "build":   // 建造：上行双音
            tone({ type: "sine", f0: 392, dur: 0.09, vol: 0.12 });
            tone({ type: "sine", f0: 659, dur: 0.12, vol: 0.12, when: 0.07 });
            break;
        case "upgrade": // 升级：快速上行琶音
            tone({ type: "triangle", f0: 523, dur: 0.09, vol: 0.11 });
            tone({ type: "triangle", f0: 659, dur: 0.09, vol: 0.11, when: 0.07 });
            tone({ type: "triangle", f0: 784, dur: 0.14, vol: 0.12, when: 0.14 });
            break;
        case "skill":   // 技能：噪声嗖声 + 上行正弦
            noiseBurst(0.22, 0.1, 2400);
            tone({ type: "sine", f0: 440, f1: 990, dur: 0.25, vol: 0.1 });
            break;
        case "revive":  // 复活：温暖上行和弦
            tone({ type: "sine", f0: 392, dur: 0.35, vol: 0.1 });
            tone({ type: "sine", f0: 494, dur: 0.35, vol: 0.1, when: 0.1 });
            tone({ type: "sine", f0: 587, dur: 0.45, vol: 0.12, when: 0.2 });
            break;
        case "wave":    // 波次号角：双锯齿五度 + 高音点缀
            tone({ type: "sawtooth", f0: 196, dur: 0.5, vol: 0.08 });
            tone({ type: "sawtooth", f0: 294, dur: 0.5, vol: 0.07, when: 0.03 });
            tone({ type: "sine", f0: 392, dur: 0.4, vol: 0.06, when: 0.12 });
            break;
        case "prep":    // 备战/休整：柔和风铃
            tone({ type: "sine", f0: 660, dur: 0.18, vol: 0.09 });
            tone({ type: "sine", f0: 880, dur: 0.26, vol: 0.08, when: 0.12 });
            break;
        case "victory": // 胜利旋律：C-E-G-C 上行
            [523, 659, 784, 1047].forEach((f, i) =>
                tone({ type: "triangle", f0: f, dur: 0.22, vol: 0.12, when: i * 0.14 }));
            break;
        case "defeat":  // 失败旋律：G-E-C-G 下行
            [392, 330, 262, 196].forEach((f, i) =>
                tone({ type: "triangle", f0: f, dur: 0.3, vol: 0.11, when: i * 0.2 }));
            break;
        case "click":   // UI 轻点
            tone({ type: "sine", f0: 900, dur: 0.04, vol: 0.05 });
            break;
    }
}

/** 兼容旧接口的简单单音 */
function beep(freq = 660, dur = 0.08, vol = 0.15) {
    tone({ type: "sine", f0: freq, dur, vol });
}

// ============ 背景音乐：极简氛围循环（仅战斗阶段播放） ============
// 低音进行 A1-A1-F1-G1-A1-A1-C2-G1 + 随机五声音阶拨弦，500ms 一拍
const MUSIC_BASS = [55, 55, 43.65, 49, 55, 55, 65.41, 49];
const MUSIC_PLUCK = [220, 261.63, 329.63, 392, 440, 523.25];
function startMusic() {
    if (musicTimer) return;
    if (!ensureAudio()) return;
    musicStep = 0;
    musicTimer = setInterval(() => {
        if (!S.soundOn || document.hidden || !audioCtx || audioCtx.state !== "running") return;
        const step = musicStep++;
        const bass = MUSIC_BASS[step % MUSIC_BASS.length];
        tone({ type: "sine", f0: bass, dur: 0.5, vol: 0.05, bus: musicBus });       // 低音铺底
        tone({ type: "sine", f0: bass * 2, dur: 0.4, vol: 0.018, bus: musicBus });  // 八度微光
        if (step % 2 === 0) {
            const n = MUSIC_PLUCK[Math.floor(Math.random() * MUSIC_PLUCK.length)];
            tone({ type: "triangle", f0: n, dur: 0.32, vol: 0.026, bus: musicBus, when: 0.1 });
        }
    }, 500);
}
function stopMusic() {
    if (musicTimer) { clearInterval(musicTimer); musicTimer = null; }
}

// 首次交互解锁音频（指针/触摸/键盘任一即可）
["pointerdown", "touchstart", "keydown"].forEach((evt) => {
    document.addEventListener(evt, () => ensureAudio(), { passive: true });
});

// ============================================================
// 轮询（核心通信）
// ============================================================
function stopPolling() {
    if (S.polling) clearInterval(S.polling);
    S.polling = null;
}

function startPolling(interval) {
    stopPolling();
    S.pollInterval = interval;
    S.polling = setInterval(() => {
        if (document.hidden) return; // 切后台暂停轮询（省 CPU/流量）
        pollState();
    }, interval);
}

function adjustPollInterval() {
    // 自适应：大厅/房间 3s → 备战 1s → 战斗 0.5s
    let target = 3000;
    const st = S.state;
    if (st) {
        if (st.phase === "battle") target = 500;
        else if (st.phase === "prep" || st.phase === "break") target = 1000;
    }
    if (target !== S.pollInterval && S.screen === "game") startPolling(target);
}

// 页面回前台立即拉一次（要求：回来立即拉）
document.addEventListener("visibilitychange", () => {
    if (!document.hidden && S.roomCode) pollState();
});

// 按钮点击涟漪微交互（纯装饰，不绑定业务）
document.addEventListener("click", (e) => {
    if (!(e.target instanceof Element)) return;
    const btn = e.target.closest(".btn, .tower-btn, .side-tab, .icon-btn");
    if (!btn || btn.disabled) return;
    const rect = btn.getBoundingClientRect();
    const size = Math.max(rect.width, rect.height) * 2;
    const span = document.createElement("span");
    span.className = "ripple";
    span.style.width = span.style.height = size + "px";
    span.style.left = (e.clientX - rect.left - size / 2) + "px";
    span.style.top = (e.clientY - rect.top - size / 2) + "px";
    btn.appendChild(span);
    span.addEventListener("animationend", () => span.remove());
});

async function pollState() {
    if (!S.roomCode) return;
    const url = `/api/game/state?room=${encodeURIComponent(S.roomCode)}` +
        `&token=${encodeURIComponent(S.token)}&v=${S.version}&chat=${S.lastChatId}`;
    try {
        const res = await fetch(url);
        const json = await res.json();
        // /api/state 成功响应不带 ok 字段，只在显式失败时拦截
        if (!res.ok || json.ok === false || json.error) {
            if (res.status === 404) {
                toast("房间已不存在", "err");
                leaveToMenu();
            }
            return;
        }
        if (json.changed === false) {
            // 轻响应：只处理聊天增量 + 服务器时间
            appendChat(json.chat || []);
            return;
        }
        S.version = json.version;
        S.state = json;
        // 记录两次完整状态到达的间隔 → 怪物平滑插值的时长基准
        const _nowP = performance.now();
        if (fx.lastStateAt) {
            const gap = (_nowP - fx.lastStateAt) / 1000;
            if (gap > 0.15 && gap < 3) fx.stateInterval = fx.stateInterval * 0.6 + gap * 0.4;
        }
        fx.lastStateAt = _nowP;
        appendChat(json.chat || []);
        onStateUpdate(json);
    } catch (e) {
        // 断线：提示 + 保持轮询（自动恢复）
        $("#battle-hint").textContent = "📶 连接断开，重连中…";
        $("#battle-hint").classList.remove("hidden");
    }
}

function appendChat(rows) {
    if (!rows.length) return;
    const lists = [$("#chat-list"), $("#game-chat-list")];
    for (const item of rows) {
        S.lastChatId = Math.max(S.lastChatId, item.id);
        for (const ul of lists) {
            if (!ul) continue;
            const li = document.createElement("li");
            li.innerHTML = `<span class="who">${escapeHtml(item.nickname)}</span>${escapeHtml(item.text)}`;
            ul.appendChild(li);
            ul.scrollTop = ul.scrollHeight;
        }
    }
}

function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ============================================================
// 状态分发
// ============================================================
function onStateUpdate(st) {
    // 房间页 / 游戏页通用渲染
    if (st.phase === "lobby" || (st.room && st.room.status === "waiting")) {
        renderRoomPage(st);
        if (S.screen !== "room" && S.screen !== "queue") showPage("room");
        startPolling(3000);
        return;
    }
    // 游戏中
    renderGame(st);
    if (S.screen !== "game") {
        showPage("game");
        initCanvas(st);
    }
    adjustPollInterval();

    // 终局 → 结算页
    if (st.phase === "victory" || st.phase === "defeat") {
        stopPolling();
        showResult(st);
    }
}

// ============================================================
// 主菜单
// ============================================================
function initMenu() {
    $("#input-nick").value = S.nickname;
    // ?room=XXXX 分享链接直接加入
    const params = new URLSearchParams(location.search);
    const roomParam = params.get("room");
    if (roomParam && S.token) {
        $("#input-nick").value = S.nickname || "";
        joinRoom(roomParam.toUpperCase(), false);
    } else if (roomParam) {
        S._pendingRoom = roomParam.toUpperCase();
        toast("请先设置昵称，然后将自动加入房间 " + roomParam);
    }

    $("#btn-quick").onclick = async () => {
        if (!(await saveNickname())) return;
        const r = await api("/api/game/quick_match", {});
        if (!r) return;
        if (r.status === "matched") { enterRoom(r.code); return; }
        showPage("queue");
        startQueueTimer(r.waited || 0);
    };
    $("#btn-create").onclick = async () => {
        if (!(await saveNickname())) return;
        const r = await api("/api/game/create_room", { is_public: true, max_players: 4, allow_join: true });
        if (r) { toast("房间已创建：" + r.code, "ok"); enterRoom(r.code); }
    };
    $("#btn-join").onclick = async () => {
        const code = prompt("请输入 4 位房间码：");
        if (!code) return;
        if (!(await saveNickname())) return;
        joinRoom(code.trim().toUpperCase(), false);
    };
    $("#btn-lobby").onclick = async () => {
        showPage("lobby");
        refreshLobby();
    };
    $("#btn-lobby-back").onclick = () => showPage("menu");
    $("#btn-lobby-refresh").onclick = refreshLobby;

    $("#btn-help").onclick = () => $("#modal-help").classList.remove("hidden");
    $("#btn-close-help").onclick = () => $("#modal-help").classList.add("hidden");
    $("#btn-sound").onclick = toggleSound;
    const sg = $("#btn-sound-game");
    if (sg) sg.onclick = toggleSound;
    syncSoundIcons();
}

/** 声音开关：主菜单与游戏内 HUD 按钮共用 */
function toggleSound() {
    S.soundOn = !S.soundOn;
    localStorage.setItem("nw_sound", S.soundOn ? "on" : "off");
    syncSoundIcons();
    if (S.soundOn) { ensureAudio(); beep(880); } else { stopMusic(); }
}

function syncSoundIcons() {
    const icon = S.soundOn ? "🔊" : "🔇";
    for (const sel of ["#btn-sound", "#btn-sound-game"]) {
        const el = $(sel);
        if (el) el.textContent = icon;
    }
}

async function saveNickname() {
    const nick = $("#input-nick").value.trim();
    if (!nick) { toast("请先输入昵称", "err"); return false; }
    const r = await api("/api/game/nickname", { nickname: nick });
    if (!r) return false;
    S.token = r.token;
    S.nickname = r.nickname;
    localStorage.setItem("nw_token", S.token);
    localStorage.setItem("nw_nick", S.nickname);
    if (S._pendingRoom) {
        const code = S._pendingRoom;
        S._pendingRoom = null;
        joinRoom(code, false);
        return false;
    }
    return true;
}

// ============================================================
// 匹配等待
// ============================================================
let queueTimer = null;
function startQueueTimer(startWaited) {
    let waited = Math.floor(startWaited || 0);
    $("#queue-info").textContent = `已等待 ${waited} 秒`;
    if (queueTimer) clearInterval(queueTimer);
    queueTimer = setInterval(async () => {
        waited += 1;
        $("#queue-info").textContent = `已等待 ${waited} 秒`;
        if (waited % 3 === 0) {
            const r = await api("/api/game/quick_match", {});
            if (r && r.status === "matched") {
                clearInterval(queueTimer);
                enterRoom(r.code);
            }
        }
    }, 1000);
    $("#btn-cancel-queue").onclick = async () => {
        clearInterval(queueTimer);
        await api("/api/game/leave", {});
        showPage("menu");
    };
}

// ============================================================
// 大厅
// ============================================================
async function refreshLobby() {
    const r = await fetch("/api/game/lobby").then((x) => x.json()).catch(() => null);
    if (!r || !r.ok) return;
    $("#lobby-online").textContent = `在线 ${r.online} 人`;
    const ul = $("#room-list");
    ul.innerHTML = "";
    if (!r.rooms.length) {
        ul.innerHTML = `<li><span class="dim">暂无公开房间，快去创建一个吧</span></li>`;
        return;
    }
    for (const room of r.rooms) {
        const li = document.createElement("li");
        li.innerHTML = `<span>房间 <b class="code">${room.code}</b> · ${room.player_count}/${room.max_players} 人
            · ${room.status === "playing" ? "对局中" : "等待中"}</span>`;
        const btn = document.createElement("button");
        btn.className = "btn small";
        btn.textContent = room.status === "playing" ? "观战" : "加入";
        btn.onclick = async () => {
            if (!(await saveNickname())) return;
            joinRoom(room.code, room.status === "playing");
        };
        li.appendChild(btn);
        ul.appendChild(li);
    }
}

// ============================================================
// 房间
// ============================================================
async function joinRoom(code, spectate) {
    const r = await api("/api/game/join", { code, spectate: !!spectate });
    if (!r) return;
    S.isSpectator = !!r.spectating;
    enterRoom(r.code);
}

function enterRoom(code) {
    S.roomCode = code;
    S.version = 0;
    S.lastChatId = 0;
    S.state = null;
    $("#room-code").textContent = code;
    showPage("room");
    pollState();
    startPolling(3000);
}

function renderRoomPage(st) {
    const members = st.members || [];
    const ul = $("#player-list");
    ul.innerHTML = "";
    for (const m of members) {
        const li = document.createElement("li");
        const tags = [];
        if (m.is_host) tags.push('<span class="tag host">房主</span>');
        if (m.is_bot) tags.push('<span class="tag bot">机器人</span>');
        else if (m.is_ready) tags.push('<span class="tag ready">已准备</span>');
        if (m.is_me) tags.push('<span class="tag">我</span>');
        li.innerHTML = `<span>💺${m.seat + 1} ${escapeHtml(m.nickname)}
            <small class="dim">（${m.wins}胜/${m.games}局）</small></span>
            <span class="tags">${tags.join("")}</span>`;
        ul.appendChild(li);
    }
    // 我的准备状态
    const me = members.find((m) => m.is_me);
    if (me) {
        $("#btn-ready").textContent = me.is_ready ? "❌ 取消准备" : "✅ 准备";
    }
    // 双人模式选队友（房间页多选一）
    if (!S.dualPartner && members.length > 1) {
        const mate = members.find((m) => !m.is_me && !m.is_bot);
        if (mate) S.dualPartner = mate.player_id;
    }
}

function initRoomButtons() {
    $("#btn-ready").onclick = async () => {
        const me = (S.state && S.state.members || []).find((m) => m.is_me);
        const nowReady = me ? !me.is_ready : true;
        const r = await api("/api/game/ready", { code: S.roomCode, ready: nowReady });
        if (r && r.started) toast("全员就绪，开战！", "ok");
        pollState(); // 立即刷新房间列表，不等下一轮轮询
    };
    $("#btn-invite-bot").onclick = async () => {
        const r = await api("/api/game/invite_bot", { code: S.roomCode });
        if (r) toast("机器人已入座", "ok", 1200);
        pollState();
    };
    $("#btn-leave-room").onclick = async () => {
        await api("/api/game/leave", { code: S.roomCode });
        leaveToMenu();
    };
    $("#btn-copy-link").onclick = () => copyLink(false);
    $("#btn-share").onclick = () => copyLink(true);
    $("#btn-chat-send").onclick = sendChat;
    $("#chat-input").addEventListener("keydown", (e) => {
        if (e.key === "Enter") sendChat();
    });
}

function copyLink(asPrompt) {
    const link = `${location.origin}/game?room=${S.roomCode}`;
    if (asPrompt) {
        prompt("把链接发给好友即可加入：", link);
        return;
    }
    if (navigator.clipboard) {
        navigator.clipboard.writeText(link).then(
            () => toast("链接已复制：" + link, "ok"),
            () => prompt("复制此链接邀请好友：", link));
    } else {
        prompt("复制此链接邀请好友：", link);
    }
}

async function sendChat() {
    const input = $("#chat-input");
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    await api("/api/game/chat", { code: S.roomCode, text });
}

function leaveToMenu() {
    stopPolling();
    stopMusic();
    S.roomCode = "";
    S.state = null;
    S.version = 0;
    showPage("menu");
}

// ============================================================
// 游戏渲染（Canvas）
// ============================================================
let canvas, ctx, cellSize = 60, animId = null;

/* ============ 二次元深色风格 Canvas 绘制辅助 ============ */
const floatTexts = [];                   // 伤害飘字池
const stars = [];                        // 缓慢下移星点粒子
(function initStars() {
    // 提前生成 50 个星点：坐标随机、速度极慢、透明度 0.25~0.6
    for (let i = 0; i < 50; i++) {
        stars.push({
            x: Math.random(),
            y: Math.random(),
            size: 0.5 + Math.random() * 1.8,
            speed: 0.00015 + Math.random() * 0.0003,
            alpha: 0.25 + Math.random() * 0.4,
        });
    }
})();

/* ============ 视觉动效系统（纯客户端渲染层，不影响任何游戏逻辑） ============ */
const fx = {
    dt: 0,                     // 本帧 dt（秒）
    roomCode: undefined,       // 换房检测 → 清理瞬态动效
    prevMonsterHp: new Map(),  // 怪物上次血量 → 伤害飘字/光束/闪白
    prevMonsterIds: new Set(), // 上次帧怪物集合 → 击杀粒子爆裂
    prevTowerIds: new Set(),    // 上次帧炮塔集合 → 放置涟漪
    monsterFx: new Map(),      // 怪物渲染态 {seed, spawnAt, flash, color}
    towerFx: new Map(),        // 炮塔渲染态 {angle, recoil, seed}
    particles: [],             // 粒子池（击杀爆裂/放置火花）
    beams: [],                 // 攻击光束池
    ripples: [],               // 涟漪池（出生/放置）
    banner: null,              // 阶段/波次横幅 {text, sub, t0, dur}
    shake: 0,                  // 震屏强度（漏怪）
    dangerFlash: 0,            // 漏怪红色边缘闪光
    lastKillPos: null,         // 最近击杀位置（赏金飘字）
    prevGold: null, prevLives: null,
    prevPhase: null, prevWave: null,
    stateInterval: 0.5,        // 实测的两次完整状态到达间隔（秒）→ 插值时长
    lastStateAt: 0,            // 上次完整状态到达时刻（performance.now）
};

function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3); }
function easeOutBack(t) {
    const c1 = 1.70158, c3 = c1 + 1;
    return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2);
}

function spawnParticles(x, y, color, count = 14, speed = 95) {
    for (let i = 0; i < count; i++) {
        const ang = Math.random() * Math.PI * 2;
        const sp = speed * (0.35 + Math.random() * 0.9);
        fx.particles.push({
            x, y, vx: Math.cos(ang) * sp, vy: Math.sin(ang) * sp,
            life: 1, color, size: 1.4 + Math.random() * 2.4,
        });
    }
}

function spawnBeam(x1, y1, x2, y2, color) {
    fx.beams.push({ x1, y1, x2, y2, color, life: 1 });
}

function spawnRipple(x, y, color, maxR = 40, dur = 0.5) {
    fx.ripples.push({ x, y, color, maxR, dur, t: 0 });
}

function showBanner(text, sub = "", dur = 1.6) {
    fx.banner = { text, sub, t0: performance.now() / 1000, dur };
}

// 悬停预览（电脑鼠标）与长按提示（手机触摸）
const hoverCell = { x: -1, y: -1 };
let longPressTimer = null;

function initCanvas(st) {
    if (canvas) { resizeCanvas(); return; } // 防重复绑定事件
    canvas = $("#battle");
    ctx = canvas.getContext("2d");
    resizeCanvas();
    window.addEventListener("resize", resizeCanvas);
    window.addEventListener("orientationchange", () => setTimeout(resizeCanvas, 300));
    canvas.onclick = onCanvasClick;

    // 触摸：点击建造 + 长按 500ms 显示格子提示（帮助手机精确点位）
    canvas.addEventListener("touchstart", (e) => {
        const t = e.touches[0];
        const cell = eventToCell(t.clientX, t.clientY);
        clearTimeout(longPressTimer);
        if (cell) {
            longPressTimer = setTimeout(() => {
                const inPath = S.state && S.state.paths.some((p) =>
                    p.some(([px, py]) => px === cell.x && py === cell.y));
                toast(`格子 (${cell.x}, ${cell.y}) ${inPath ? "· 怪物路径" : "· 可建造"}`, "", 1600);
            }, 500);
        }
    }, { passive: true });
    canvas.addEventListener("touchmove", () => clearTimeout(longPressTimer), { passive: true });
    canvas.addEventListener("touchend", (e) => {
        clearTimeout(longPressTimer);
        e.preventDefault();
        const t = e.changedTouches[0];
        onCanvasClick({ clientX: t.clientX, clientY: t.clientY });
    }, { passive: false });

    // 电脑：悬停显示建造预览（幽灵塔 + 射程圈）
    canvas.addEventListener("mousemove", (e) => {
        const cell = eventToCell(e.clientX, e.clientY);
        hoverCell.x = cell ? cell.x : -1;
        hoverCell.y = cell ? cell.y : -1;
    });
    canvas.addEventListener("mouseleave", () => { hoverCell.x = -1; hoverCell.y = -1; });

    // 电脑：右键 / Esc 取消建造选择
    canvas.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        clearTowerSelection();
    });

    if (!animId) renderLoop();
}

// 将屏幕坐标换算为网格坐标（容错返回 null；与 DPR 无关，按网格比例换算）
function eventToCell(clientX, clientY) {
    if (!canvas) return null;
    const st = S.state;
    if (!st) return null;
    const rect = canvas.getBoundingClientRect();
    const gx = Math.floor((clientX - rect.left) / rect.width * st.grid[0]);
    const gy = Math.floor((clientY - rect.top) / rect.height * st.grid[1]);
    if (gx < 0 || gy < 0 || gx >= st.grid[0] || gy >= st.grid[1]) return null;
    return { x: gx, y: gy };
}

function clearTowerSelection() {
    S.selectedTower = null;
    S.dualMode = false;
    if (S.state) renderTowerButtons(S.state);
}

function resizeCanvas() {
    if (!canvas) return;
    const st = S.state;
    const gw = st ? st.grid[0] : 12, gh = st ? st.grid[1] : 8;
    const wrapW = $("#battle-wrap").clientWidth;
    cellSize = Math.max(24, Math.floor(wrapW / gw));
    // 高清渲染：按设备像素比放大画布，CSS 尺寸不变（手机 Retina 不糊）
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = cellSize * gw * dpr;
    canvas.height = cellSize * gh * dpr;
    canvas.style.width = cellSize * gw + "px";
    canvas.style.height = cellSize * gh + "px";
    if (ctx) ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

let _lastFrameTs = performance.now();
function renderLoop() {
    const now = performance.now();
    const dt = Math.min(0.05, (now - _lastFrameTs) / 1000); // 秒，上限防 tab 切回大跳
    _lastFrameTs = now;
    fx.dt = dt;
    // 震屏：漏怪时短促抖动（随时间衰减）
    let shook = false;
    if (fx.shake > 0.05 && ctx) {
        ctx.save();
        ctx.translate((Math.random() - 0.5) * fx.shake, (Math.random() - 0.5) * fx.shake);
        fx.shake = Math.max(0, fx.shake - dt * 16);
        shook = true;
    } else {
        fx.shake = 0;
    }
    draw();
    drawFloatTexts(ctx, dt);
    if (shook) ctx.restore();
    animId = requestAnimationFrame(renderLoop);
}

function draw() {
    const st = S.state;
    if (!st || !ctx) return;
    const cs = cellSize;
    const gw = st.grid[0], gh = st.grid[1];
    const cw = gw * cs, ch = gh * cs;
    const dt = fx.dt || 0.016;           // 帧间隔（秒），所有动效统一用它驱动
    const tSec = Date.now() / 1000;      // 动画时钟

    // ===== 换房/新对局：清理上一局残留的瞬态动效 =====
    if (fx.roomCode !== S.roomCode) {
        fx.roomCode = S.roomCode;
        fx.prevMonsterHp.clear();
        fx.prevMonsterIds.clear();
        fx.monsterFx.clear();
        fx.towerFx.clear();
        fx.prevTowerIds.clear();
        fx.particles.length = 0;
        fx.beams.length = 0;
        fx.ripples.length = 0;
        fx.prevGold = null;
        fx.prevLives = null;
        fx.lastKillPos = null;
        fx.prevPhase = null;
        fx.prevWave = null;
        fx.banner = null;
        fx.stateInterval = 0.5;
        fx.lastStateAt = 0;
    }

    // ===== 背景：深靛蓝渐变底（外扩 10px 防震屏露边）=====
    const bgGrad = ctx.createLinearGradient(0, 0, 0, ch);
    bgGrad.addColorStop(0, "#0b1426");
    bgGrad.addColorStop(0.5, "#0d1a30");
    bgGrad.addColorStop(1, "#0a1020");
    ctx.fillStyle = bgGrad;
    ctx.fillRect(-10, -10, cw + 20, ch + 20);

    // ===== 漂移星云：缓慢游移的径向光斑，营造深空氛围 =====
    const n1x = cw * (0.28 + 0.08 * Math.sin(tSec * 0.13));
    const n1y = ch * (0.32 + 0.07 * Math.cos(tSec * 0.10));
    const neb1 = ctx.createRadialGradient(n1x, n1y, 0, n1x, n1y, ch * 0.55);
    neb1.addColorStop(0, "rgba(0,212,255,0.05)");
    neb1.addColorStop(1, "rgba(0,212,255,0)");
    ctx.fillStyle = neb1;
    ctx.fillRect(-10, -10, cw + 20, ch + 20);
    const n2x = cw * (0.72 + 0.09 * Math.cos(tSec * 0.11));
    const n2y = ch * (0.66 + 0.08 * Math.sin(tSec * 0.14));
    const neb2 = ctx.createRadialGradient(n2x, n2y, 0, n2x, n2y, ch * 0.5);
    neb2.addColorStop(0, "rgba(124,58,237,0.06)");
    neb2.addColorStop(1, "rgba(124,58,237,0)");
    ctx.fillStyle = neb2;
    ctx.fillRect(-10, -10, cw + 20, ch + 20);

    // ===== 极淡青蓝网格线（透明度 0.06） =====
    ctx.strokeStyle = "rgba(0,212,255,0.06)";
    ctx.lineWidth = 1;
    for (let gx = 1; gx < gw; gx++) {
        ctx.beginPath();
        ctx.moveTo(gx * cs, 0);
        ctx.lineTo(gx * cs, ch);
        ctx.stroke();
    }
    for (let gy = 1; gy < gh; gy++) {
        ctx.beginPath();
        ctx.moveTo(0, gy * cs);
        ctx.lineTo(cw, gy * cs);
        ctx.stroke();
    }

    // ===== 缓慢下移星点粒子 =====
    for (const s of stars) {
        s.y += s.speed;
        if (s.y > 1) s.y -= 1;
        const sx = s.x * cw, sy = s.y * ch;
        ctx.fillStyle = `rgba(180,220,255,${s.alpha})`;
        ctx.fillRect(sx, sy, s.size, s.size);
    }

    // ===== 棋盘格底色（保留原有逻辑，颜色微调更统一） =====
    for (let x = 0; x < gw; x++) {
        for (let y = 0; y < gh; y++) {
            ctx.fillStyle = (x + y) % 2 ? "rgba(0,212,255,0.018)" : "rgba(124,58,237,0.025)";
            ctx.fillRect(x * cs, y * cs, cs, cs);
        }
    }

    // ===== 路径：深紫底 + 流动光 + 入口漩涡 + 营地心跳 =====
    for (const path of st.paths) {
        for (const [x, y] of path) {
            ctx.fillStyle = "#1e1a38";
            ctx.fillRect(x * cs, y * cs, cs, cs);
            ctx.fillStyle = "rgba(124,58,237,0.25)";
            ctx.fillRect(x * cs + cs * 0.35, y * cs + cs * 0.35, cs * 0.3, cs * 0.3);
        }

        // 流动虚线：光点沿怪物行进方向流动
        ctx.save();
        ctx.strokeStyle = "rgba(124,58,237,0.45)";
        ctx.lineWidth = 2;
        ctx.shadowColor = "rgba(124,58,237,0.7)";
        ctx.shadowBlur = 6;
        ctx.setLineDash([cs * 0.16, cs * 0.4]);
        ctx.lineDashOffset = -tSec * cs * 0.85;
        ctx.beginPath();
        path.forEach(([x, y], i) => {
            const lx = x * cs + cs / 2, ly = y * cs + cs / 2;
            if (i === 0) ctx.moveTo(lx, ly); else ctx.lineTo(lx, ly);
        });
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.restore();

        // 入口漩涡（路径起点，怪物从这里涌入）
        const [sx, sy] = path[0];
        const scx = sx * cs + cs / 2, scy = sy * cs + cs / 2;
        ctx.save();
        ctx.strokeStyle = "rgba(244,63,94,0.55)";
        ctx.lineWidth = 2;
        ctx.shadowColor = "rgba(244,63,94,0.6)";
        ctx.shadowBlur = 10;
        ctx.setLineDash([cs * 0.12, cs * 0.2]);
        ctx.lineDashOffset = tSec * cs * 0.5;
        ctx.beginPath();
        ctx.arc(scx, scy, cs * 0.42, 0, Math.PI * 2);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.restore();

        // 营地（路径终点）：呼吸光圈 + 心形标识
        const [ex, ey] = path[path.length - 1];
        const ecx = ex * cs + cs / 2, ecy = ey * cs + cs / 2;
        const campGlow = 0.35 + (Math.sin(tSec * 3 + ey) * 0.5 + 0.5) * 0.4;
        ctx.save();
        ctx.globalAlpha = campGlow;
        drawAura(ctx, ecx, ecy, cs * 0.42);
        ctx.restore();
        ctx.font = `${Math.floor(cs * 0.38)}px serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText("❤️", ecx, ecy);
    }

    // ===== 当前建造选中高亮可建造格 =====
    if (S.selectedTower && (st.phase === "prep" || st.phase === "break")) {
        const occupied = new Set(st.towers.map((t) => t.x + "," + t.y));
        const pathSet = new Set(st.paths.flat().map((p) => p[0] + "," + p[1]));
        for (let x = 0; x < gw; x++) {
            for (let y = 0; y < gh; y++) {
                const key = x + "," + y;
                if (pathSet.has(key) || occupied.has(key)) continue;
                ctx.strokeStyle = "rgba(255,210,87,0.4)";
                ctx.lineWidth = 2;
                ctx.strokeRect(x * cs + 3, y * cs + 3, cs - 6, cs - 6);
            }
        }
    }

    // ===== 炮塔：呼吸发光 + 可动炮管（追踪最近怪物） + 放置涟漪 =====
    const curTowerIds = new Set();
    for (const t of st.towers) {
        curTowerIds.add(t.id);
        const px = t.x * cs, py = t.y * cs;
        const conf = st.towers_config[t.type] || {};
        const cx = px + cs / 2, cy = py + cs / 2;
        const innerPad = 4, innerSize = cs - 8;

        // 新放置炮塔 → 涟漪 + 火花（建造反馈）
        if (!fx.prevTowerIds.has(t.id)) {
            spawnRipple(cx, cy, t.dual ? "rgba(124,58,237,0.85)" : "rgba(0,212,255,0.85)", cs * 0.75, 0.55);
            spawnParticles(cx, cy, t.dual ? "#a78bfa" : "#7dd3fc", 10, 70);
        }

        // 渲染态：炮管角度 / 后坐 / 呼吸种子
        let tf = fx.towerFx.get(t.id);
        if (!tf) {
            tf = { angle: -Math.PI / 2, recoil: 0, seed: Math.random() * 10 };
            fx.towerFx.set(t.id, tf);
        }

        // 选中的炮塔画呼吸光圈
        if (S.selectedTowerId && t.id === S.selectedTowerId) {
            drawAura(ctx, cx, cy, cs * 0.5);
        }

        // 主体：渐变底 + 呼吸外发光
        const breathe = 0.8 + Math.sin(tSec * 2.2 + tf.seed) * 0.2;
        ctx.save();
        ctx.shadowColor = t.dual ? "rgba(124,58,237,0.7)" : "rgba(0,212,255,0.6)";
        ctx.shadowBlur = 10 + 8 * breathe;
        const grad = ctx.createLinearGradient(px, py, px + innerSize, py + innerSize);
        if (t.dual) {
            grad.addColorStop(0, "rgba(124,58,237,0.7)");
            grad.addColorStop(1, "rgba(91,33,182,0.9)");
        } else {
            grad.addColorStop(0, "rgba(0,212,255,0.28)");
            grad.addColorStop(1, "rgba(0,90,160,0.55)");
        }
        ctx.fillStyle = grad;
        roundRect(px + innerPad, py + innerPad, innerSize, innerSize, 10);
        ctx.fill();
        ctx.restore();

        ctx.strokeStyle = t.dual ? "rgba(180,130,255,0.9)" : "rgba(0,212,255,0.9)";
        ctx.lineWidth = 1.5;
        ctx.stroke();

        // ===== 炮管：平滑追踪最近怪物；无目标时怠速扫描 =====
        let best = null, bestD = Infinity;
        for (const m of st.monsters) {
            const mp = S.renderMonsters.get(m.id);
            if (!mp) continue;
            const mdx = mp.x - (t.x + 0.5), mdy = mp.y - (t.y + 0.5);
            const md = mdx * mdx + mdy * mdy;
            if (md < bestD) { bestD = md; best = mp; }
        }
        const targetAngle = best
            ? Math.atan2(best.y - (t.y + 0.5), best.x - (t.x + 0.5))
            : -Math.PI / 2 + Math.sin(tSec * 0.6 + tf.seed) * 0.5;
        let da = targetAngle - tf.angle;
        while (da > Math.PI) da -= Math.PI * 2;
        while (da < -Math.PI) da += Math.PI * 2;
        tf.angle += da * Math.min(1, dt * 9);
        if (tf.recoil > 0) tf.recoil = Math.max(0, tf.recoil - dt * 5);

        const bl = cs * 0.46, rec = tf.recoil * cs * 0.09;
        const bx = cx - Math.cos(tf.angle) * rec;
        const by = cy - Math.sin(tf.angle) * rec;
        ctx.save();
        ctx.strokeStyle = t.dual ? "#c4b5fd" : "#8be4ff";
        ctx.lineWidth = Math.max(3, cs * 0.11);
        ctx.lineCap = "round";
        ctx.shadowColor = t.dual ? "rgba(124,58,237,0.8)" : "rgba(0,212,255,0.8)";
        ctx.shadowBlur = 8;
        ctx.beginPath();
        ctx.moveTo(bx + Math.cos(tf.angle) * cs * 0.08, by + Math.sin(tf.angle) * cs * 0.08);
        ctx.lineTo(bx + Math.cos(tf.angle) * bl, by + Math.sin(tf.angle) * bl);
        ctx.stroke();
        ctx.fillStyle = "rgba(255,255,255,0.9)";
        ctx.beginPath();
        ctx.arc(bx + Math.cos(tf.angle) * bl, by + Math.sin(tf.angle) * bl, 2, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();

        // 中心徽记（emoji 作为炮塔核心）
        ctx.font = `${Math.floor(cs * 0.3)}px serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(conf.emoji || "🔫", cx, cy);

        // 等级星（等宽字体 + 微弱发光）
        if (t.level > 0) {
            ctx.save();
            ctx.shadowColor = "rgba(255,210,87,0.7)";
            ctx.shadowBlur = 6;
            ctx.font = `${Math.floor(cs * 0.18)}px ui-monospace, Menlo, monospace`;
            ctx.fillStyle = "#ffd257";
            ctx.fillText("★".repeat(t.level), cx, py + cs * 0.84);
            ctx.restore();
        }
    }
    // 清理已出售/消失炮塔的渲染态
    for (const tid of fx.prevTowerIds) {
        if (!curTowerIds.has(tid)) fx.towerFx.delete(tid);
    }
    fx.prevTowerIds = curTowerIds;

    // ===== 悬停建造预览（电脑鼠标）：幽灵塔 + 射程圈 =====
    if (S.selectedTower && hoverCell.x >= 0 && (st.phase === "prep" || st.phase === "break")) {
        const conf = st.towers_config[S.selectedTower];
        const occupiedSet = new Set(st.towers.map((t) => t.x + "," + t.y));
        const pathSet = new Set(st.paths.flat().map((p) => p[0] + "," + p[1]));
        const key = hoverCell.x + "," + hoverCell.y;
        const legal = !pathSet.has(key) && !occupiedSet.has(key);
        const hx = hoverCell.x * cs, hy = hoverCell.y * cs;

        const rangeCells = 2.6;
        ctx.beginPath();
        ctx.arc(hx + cs / 2, hy + cs / 2, rangeCells * cs, 0, Math.PI * 2);
        ctx.fillStyle = legal ? "rgba(0,212,255,0.12)" : "rgba(244,63,94,0.10)";
        ctx.fill();
        ctx.strokeStyle = legal ? "rgba(0,212,255,0.55)" : "rgba(244,63,94,0.65)";
        ctx.lineWidth = 2;
        ctx.stroke();

        // 幽灵塔（带发光）
        ctx.globalAlpha = 0.55;
        ctx.save();
        ctx.shadowColor = legal ? "rgba(0,212,255,0.6)" : "rgba(244,63,94,0.6)";
        ctx.shadowBlur = 10;
        ctx.fillStyle = legal ? "rgba(0,212,255,0.45)" : "rgba(244,63,94,0.45)";
        roundRect(hx + 4, hy + 4, cs - 8, cs - 8, 10);
        ctx.fill();
        ctx.restore();
        ctx.globalAlpha = 1;
        if (legal && conf) {
            ctx.font = `${Math.floor(cs * 0.42)}px serif`;
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            ctx.fillText(conf.emoji || "🔫", hx + cs / 2, hy + cs / 2);
        }
    }

    // ===== 怪物：时间插值平滑移动（服务端每 0.5s 推一次位置，客户端在两次推送间匀速补间）=====
    const nowMs = performance.now();
    const lerpDur = Math.max(0.25, fx.stateInterval * 1.05); // 略长于实测间隔，避免中途停顿
    const curIds = new Set();
    for (const m of st.monsters) {
        curIds.add(m.id);
        let rm = S.renderMonsters.get(m.id);
        if (!rm) {
            // 新怪物：直接落在服务端位置（配合出生弹入动画）
            rm = { x: m.x, y: m.y, fromX: m.x, fromY: m.y, toX: m.x, toY: m.y, t0: nowMs };
            S.renderMonsters.set(m.id, rm);
        } else if (m.x !== rm.toX || m.y !== rm.toY) {
            // 服务端推送了新坐标 → 从当前绘制位置开启新插值段，保证速度方向连贯
            rm.fromX = rm.x; rm.fromY = rm.y;
            rm.toX = m.x; rm.toY = m.y;
            rm.t0 = nowMs;
        }
        // 线性插值：匀速滑行，无顿挫
        const lt = Math.min(1, (nowMs - rm.t0) / lerpDur);
        rm.x = rm.fromX + (rm.toX - rm.fromX) * lt;
        rm.y = rm.fromY + (rm.toY - rm.fromY) * lt;
        const ix = rm.x, iy = rm.y;
        const px = ix * cs, py = iy * cs;
        const mColors = { grunt: "#e05555", runner: "#e0a13e", brute: "#b04fd0", boss: "#ff3355" };
        const mColor = mColors[m.type] || "#e05555";
        const r = m.type === "boss" ? cs * 0.34 : m.type === "brute" ? cs * 0.28 : cs * 0.22;

        // 渲染态（种子/出生时刻/闪白），新怪物 → 出生涟漪
        let mf = fx.monsterFx.get(m.id);
        if (!mf) {
            mf = { seed: Math.random() * 10, spawnAt: tSec, flash: 0, color: mColor };
            fx.monsterFx.set(m.id, mf);
            spawnRipple(px, py, mColor, r * 2.4, 0.45);
        }

        // ===== 伤害检测：血量下降 → 飘字 + 闪白 + 最近塔光束 + 后坐 =====
        const prevHp = fx.prevMonsterHp.get(m.id);
        if (prevHp !== undefined && m.hp < prevHp) {
            const dmg = prevHp - m.hp;
            const ratio = Math.max(0, m.hp / m.max_hp);
            addFloatText(px, py - r - 16, "-" + Math.round(dmg),
                ratio <= 0.3 ? "#f87171" : "#7dd3fc");
            mf.flash = 1;
            let bt = null, bd = Infinity;
            for (const tw of st.towers) {
                const ddx = tw.x - ix, ddy = tw.y - iy;
                const dd = ddx * ddx + ddy * ddy;
                if (dd < bd) { bd = dd; bt = tw; }
            }
            if (bt) {
                spawnBeam(bt.x * cs + cs / 2, bt.y * cs + cs / 2, px, py,
                    bt.dual ? "rgba(196,181,253,0.95)" : "rgba(0,212,255,0.95)");
                const btf = fx.towerFx.get(bt.id);
                if (btf) btf.recoil = 1;
                sfx("fire");
            }
        }
        fx.prevMonsterHp.set(m.id, m.hp);

        // ===== 绘制：弹入缩放 + 呼吸挤压 + 果冻质感 =====
        const age = tSec - mf.spawnAt;
        const pop = age < 0.35 ? easeOutBack(Math.max(0.001, age / 0.35)) : 1;
        const bob = 1 + Math.sin(tSec * 9 + mf.seed) * 0.06;
        ctx.save();
        ctx.translate(px, py);
        ctx.scale(pop, pop * bob);
        ctx.shadowColor = m.slowed ? "rgba(34,211,238,0.85)" : "rgba(224,85,85,0.55)";
        ctx.shadowBlur = 12;
        ctx.fillStyle = mColor;
        ctx.beginPath();
        ctx.arc(0, 0, r, 0, Math.PI * 2);
        ctx.fill();
        ctx.shadowBlur = 0;
        // 高光（果冻质感）
        ctx.fillStyle = "rgba(255,255,255,0.32)";
        ctx.beginPath();
        ctx.arc(-r * 0.3, -r * 0.35, r * 0.42, 0, Math.PI * 2);
        ctx.fill();
        // 瞳孔
        ctx.fillStyle = "rgba(15,20,40,0.75)";
        ctx.beginPath();
        ctx.arc(r * 0.18, r * 0.1, r * 0.22, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();

        // Boss 环绕尖刺（缓慢旋转）
        if (m.type === "boss") {
            ctx.save();
            ctx.translate(px, py);
            ctx.rotate(tSec * 0.8);
            ctx.strokeStyle = "rgba(255,51,85,0.6)";
            ctx.lineWidth = 2;
            for (let s = 0; s < 8; s++) {
                const sa = (s / 8) * Math.PI * 2;
                ctx.beginPath();
                ctx.moveTo(Math.cos(sa) * (r + 2), Math.sin(sa) * (r + 2));
                ctx.lineTo(Math.cos(sa) * (r + 8), Math.sin(sa) * (r + 8));
                ctx.stroke();
            }
            ctx.restore();
        }

        // 受击闪白（快速衰减）
        if (mf.flash > 0) {
            mf.flash = Math.max(0, mf.flash - dt * 6);
            ctx.save();
            ctx.globalAlpha = mf.flash * 0.8;
            ctx.fillStyle = "#fff";
            ctx.beginPath();
            ctx.arc(px, py, r, 0, Math.PI * 2);
            ctx.fill();
            ctx.restore();
        }

        // 减速冰环
        if (m.slowed) {
            ctx.strokeStyle = "rgba(34,211,238,0.85)";
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.arc(px, py, r + 3, 0, Math.PI * 2);
            ctx.stroke();
        }

        // 血条
        const barW = r * 2.6, barH = Math.max(4, cs * 0.07);
        drawHealthBar(ctx, px - barW / 2, py - r - barH - 4, barW, barH, m.hp, m.max_hp);
    }

    // ===== 漏怪检测（先于击杀判定：区分"漏掉消失"与"被打死消失"）=====
    const livesDropped = fx.prevLives !== null && st.lives < fx.prevLives;
    if (livesDropped) {
        fx.shake = 7;
        fx.dangerFlash = 1;
        sfx("leak");
    }
    fx.prevLives = st.lives;

    // ===== 击杀/消散检测：粒子爆裂 + 记录位置 =====
    for (const id of fx.prevMonsterIds) {
        if (curIds.has(id)) continue;
        const pos = S.renderMonsters.get(id);
        if (pos) {
            const dead = fx.monsterFx.get(id);
            spawnParticles(pos.x * cs, pos.y * cs, dead ? dead.color : "#e05555", 16, 105);
            fx.lastKillPos = { x: pos.x * cs, y: pos.y * cs };
            if (!livesDropped) sfx("kill"); // 漏怪那一帧已播警报音，避免叠音
        }
        S.renderMonsters.delete(id);
        fx.monsterFx.delete(id);
        fx.prevMonsterHp.delete(id);
    }
    fx.prevMonsterIds = curIds;

    // ===== 击杀赏金 → 击杀位置飘 +N💰 =====
    if (fx.prevGold !== null && st.phase === "battle" &&
        st.gold > fx.prevGold && fx.lastKillPos) {
        addFloatText(fx.lastKillPos.x, fx.lastKillPos.y - 22,
            "+" + (st.gold - fx.prevGold) + "💰", "#ffd257");
    }
    fx.prevGold = st.gold;

    // ===== 阶段/波次横幅（连贯的节奏提示 + 号角音效） =====
    const phaseChanged = fx.prevPhase !== st.phase;
    if (phaseChanged) {
        if (st.phase === "battle") { showBanner("⚔️ 战斗开始", `第 ${st.wave} 波来袭`, 1.7); sfx("wave"); }
        else if (st.phase === "prep") { showBanner("🛠️ 备战阶段", "建造并升级炮塔，抵御怪潮", 1.7); sfx("prep"); }
        else if (st.phase === "break") { showBanner("☕ 休整", "抓紧时间升级炮塔", 1.5); sfx("prep"); }
        fx.prevPhase = st.phase;
    }
    if (!phaseChanged && fx.prevWave !== null && st.wave !== fx.prevWave && st.phase === "battle") {
        showBanner(`🌊 第 ${st.wave} 波`, "怪物变得更强了", 1.7);
        sfx("wave");
    }
    fx.prevWave = st.wave;

    // ===== 急速冷却路径提示 =====
    const nowTs = Date.now() / 1000;
    for (const pidx of [0, 1]) {
        if (parseFloat(st.haste[pidx] || st.haste[String(pidx)] || 0) > nowTs - 2) {
            for (const [x, y] of st.paths[pidx]) {
                ctx.fillStyle = "rgba(0,212,255,0.14)";
                ctx.fillRect(x * cs, y * cs, cs, cs);
            }
        }
    }

    // ===== 顶层动效合成：光束 → 粒子 → 涟漪 → 横幅 =====
    drawBeams(ctx, dt);
    drawParticles(ctx, dt);
    drawRipples(ctx, dt);
    drawBanner(ctx, cw, ch);

    // ===== 暗角（聚焦战场中心） =====
    const vg = ctx.createRadialGradient(cw / 2, ch / 2, Math.min(cw, ch) * 0.45,
        cw / 2, ch / 2, Math.max(cw, ch) * 0.72);
    vg.addColorStop(0, "rgba(0,0,0,0)");
    vg.addColorStop(1, "rgba(0,0,0,0.4)");
    ctx.fillStyle = vg;
    ctx.fillRect(0, 0, cw, ch);

    // ===== 漏怪红色边缘脉冲 =====
    if (fx.dangerFlash > 0) {
        fx.dangerFlash = Math.max(0, fx.dangerFlash - dt * 1.5);
        const dg = ctx.createRadialGradient(cw / 2, ch / 2, Math.min(cw, ch) * 0.35,
            cw / 2, ch / 2, Math.max(cw, ch) * 0.7);
        dg.addColorStop(0, "rgba(244,63,94,0)");
        dg.addColorStop(1, "rgba(244,63,94," + (fx.dangerFlash * 0.4).toFixed(3) + ")");
        ctx.fillStyle = dg;
        ctx.fillRect(0, 0, cw, ch);
    }
}

function roundRect(x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
}

/* ============ 二次元风格 Canvas 绘制辅助函数 ============ */

/**
 * 发光血条：圆角描边外框 + 渐变填充
 * - 满血青绿渐变（#10b981 → #22d3ee）
 * - hp < 30% 变红渐变（#ef4444 → #f97316）
 */
function drawHealthBar(ctx2, x, y, w, h, hp, maxHp) {
    const ratio = Math.max(0, hp / maxHp);
    const r = Math.min(h / 2, 3);

    // 外框（极暗底 + 青蓝细描边）
    ctx2.fillStyle = "rgba(0,0,0,0.45)";
    roundRect(x, y, w, h, r);
    ctx2.fill();
    ctx2.strokeStyle = "rgba(0,212,255,0.35)";
    ctx2.lineWidth = 1;
    ctx2.stroke();

    // 渐变填充
    const grad = ctx2.createLinearGradient(x, y, x + w, y);
    if (ratio > 0.3) {
        grad.addColorStop(0, "#10b981");        // 青绿
        grad.addColorStop(1, "#22d3ee");        // 青蓝
    } else {
        grad.addColorStop(0, "#ef4444");        // 红
        grad.addColorStop(1, "#f97316");        // 橙
    }
    ctx2.fillStyle = grad;
    roundRect(x + 1, y + 1, (w - 2) * ratio, h - 2, Math.max(0, r - 1));
    ctx2.fill();
}

/**
 * 呼吸脉动光圈：内外两圈，sin 驱动脉动
 * 用于选中炮塔时显示"被选中"视觉反馈
 */
function drawAura(ctx2, x, y, radius) {
    const pulse = Math.sin(Date.now() / 300) * 0.5 + 0.5; // 0~1 脉动值
    const innerR = radius * (0.85 + pulse * 0.15);
    const outerR = radius * (1.15 + pulse * 0.25);

    // 外圈
    ctx2.save();
    ctx2.globalAlpha = 0.5 + pulse * 0.4;
    ctx2.strokeStyle = "rgba(0,212,255,0.7)";
    ctx2.lineWidth = 2;
    ctx2.shadowColor = "rgba(0,212,255,0.8)";
    ctx2.shadowBlur = 14 + pulse * 10;
    ctx2.beginPath();
    ctx2.arc(x, y, outerR, 0, Math.PI * 2);
    ctx2.stroke();
    ctx2.restore();

    // 内圈（反向脉动 + 紫色）
    ctx2.save();
    ctx2.globalAlpha = 0.4 + (1 - pulse) * 0.4;
    ctx2.strokeStyle = "rgba(124,58,237,0.6)";
    ctx2.lineWidth = 1.5;
    ctx2.beginPath();
    ctx2.arc(x, y, innerR, 0, Math.PI * 2);
    ctx2.stroke();
    ctx2.restore();
}

/** 添加一个伤害飘字 */
function addFloatText(x, y, text, color) {
    floatTexts.push({
        x, y, text, color,
        life: 1.0,
        vy: -40,          // 上移速度 px/s
        born: Date.now(),
    });
}

/** 绘制 + 更新飘字池（每帧调用） */
function drawFloatTexts(ctx2, dt) {
    if (!floatTexts.length) return;
    ctx2.save();
    ctx2.textAlign = "center";
    ctx2.textBaseline = "middle";
    ctx2.font = "bold 14px ui-monospace, Menlo, monospace";

    for (let i = floatTexts.length - 1; i >= 0; i--) {
        const f = floatTexts[i];
        f.y += f.vy * dt;
        f.life -= dt * 1.2;              // 约 0.83s 自动消失
        if (f.life <= 0) { floatTexts.splice(i, 1); continue; }

        ctx2.globalAlpha = f.life;
        ctx2.shadowColor = f.color;
        ctx2.shadowBlur = 8;
        ctx2.fillStyle = f.color;
        ctx2.fillText(f.text, f.x, f.y);
    }
    ctx2.restore();
}

/* ============ 动效池绘制（每帧调用） ============ */

function drawBeams(ctx2, dt) {
    for (let i = fx.beams.length - 1; i >= 0; i--) {
        const b = fx.beams[i];
        b.life -= dt * 4.2;
        if (b.life <= 0) { fx.beams.splice(i, 1); continue; }
        ctx2.save();
        ctx2.globalAlpha = b.life;
        ctx2.strokeStyle = b.color;
        ctx2.lineWidth = 2.6;
        ctx2.lineCap = "round";
        ctx2.shadowColor = b.color;
        ctx2.shadowBlur = 12;
        ctx2.beginPath();
        ctx2.moveTo(b.x1, b.y1);
        ctx2.lineTo(b.x2, b.y2);
        ctx2.stroke();
        ctx2.strokeStyle = "rgba(255,255,255,0.85)";
        ctx2.lineWidth = 1;
        ctx2.shadowBlur = 4;
        ctx2.stroke();
        ctx2.restore();
    }
}

function drawParticles(ctx2, dt) {
    for (let i = fx.particles.length - 1; i >= 0; i--) {
        const p = fx.particles[i];
        p.x += p.vx * dt;
        p.y += p.vy * dt;
        p.vx *= (1 - 2.6 * dt);
        p.vy = p.vy * (1 - 2.6 * dt) + 55 * dt; // 摩擦 + 轻微下坠
        p.life -= dt * 1.7;
        if (p.life <= 0) { fx.particles.splice(i, 1); continue; }
        ctx2.save();
        ctx2.globalAlpha = p.life;
        ctx2.fillStyle = p.color;
        ctx2.shadowColor = p.color;
        ctx2.shadowBlur = 7;
        ctx2.beginPath();
        ctx2.arc(p.x, p.y, p.size * p.life, 0, Math.PI * 2);
        ctx2.fill();
        ctx2.restore();
    }
}

function drawRipples(ctx2, dt) {
    for (let i = fx.ripples.length - 1; i >= 0; i--) {
        const r = fx.ripples[i];
        r.t += dt;
        const k = r.t / r.dur;
        if (k >= 1) { fx.ripples.splice(i, 1); continue; }
        ctx2.save();
        ctx2.globalAlpha = 1 - k;
        ctx2.strokeStyle = r.color;
        ctx2.lineWidth = 2.4 * (1 - k) + 0.4;
        ctx2.shadowColor = r.color;
        ctx2.shadowBlur = 10;
        ctx2.beginPath();
        ctx2.arc(r.x, r.y, r.maxR * easeOutCubic(k), 0, Math.PI * 2);
        ctx2.stroke();
        ctx2.restore();
    }
}

function drawBanner(ctx2, cw, ch) {
    const b = fx.banner;
    if (!b) return;
    const t = performance.now() / 1000 - b.t0;
    if (t > b.dur + 0.45) { fx.banner = null; return; }
    let alpha = 1, off = 0;
    if (t < 0.3) {
        const k = easeOutCubic(t / 0.3);
        alpha = k; off = (1 - k) * -34;
    } else if (t > b.dur) {
        alpha = 1 - (t - b.dur) / 0.45;
    }
    const y = ch * 0.32 + off;
    ctx2.save();
    ctx2.globalAlpha = Math.max(0, alpha);
    ctx2.textAlign = "center";
    ctx2.textBaseline = "middle";
    const grad = ctx2.createLinearGradient(cw * 0.2, 0, cw * 0.8, 0);
    grad.addColorStop(0, "#00d4ff");
    grad.addColorStop(1, "#a78bfa");
    ctx2.fillStyle = grad;
    ctx2.shadowColor = "rgba(0,212,255,0.75)";
    ctx2.shadowBlur = 24;
    ctx2.font = `bold ${Math.max(24, Math.floor(cw * 0.055))}px -apple-system, "PingFang SC", sans-serif`;
    ctx2.fillText(b.text, cw / 2, y);
    if (b.sub) {
        ctx2.shadowBlur = 8;
        ctx2.fillStyle = "rgba(226,232,240,0.92)";
        ctx2.font = `${Math.max(12, Math.floor(cw * 0.022))}px -apple-system, "PingFang SC", sans-serif`;
        ctx2.fillText(b.sub, cw / 2, y + Math.max(26, cw * 0.045));
    }
    const lineW = Math.min(cw * 0.52, 300) * Math.min(1, t / 0.45);
    ctx2.strokeStyle = "rgba(0,212,255,0.55)";
    ctx2.lineWidth = 2;
    ctx2.beginPath();
    ctx2.moveTo(cw / 2 - lineW / 2, y + Math.max(38, cw * 0.075));
    ctx2.lineTo(cw / 2 + lineW / 2, y + Math.max(38, cw * 0.075));
    ctx2.stroke();
    ctx2.restore();
}

function onCanvasClick(e) {
    const st = S.state;
    if (!st || !canvas) return;
    const cell = eventToCell(e.clientX, e.clientY);
    if (!cell) return;

    // 点到已有炮塔 → 打开升级/出售
    const hit = st.towers.find((t) => t.x === cell.x && t.y === cell.y);
    if (hit) {
        openTowerModal(hit);
        return;
    }
    // 建造模式
    if (S.selectedTower && (st.phase === "prep" || st.phase === "break")) {
        doBuild(cell.x, cell.y);
    }
}

// ============================================================
// 游戏 HUD / 操作
// ============================================================
// HUD 数字变化脉冲（金币/波次/生命跳动提醒）
const _hudPrev = {};
function hudPulse(sel, val) {
    if (_hudPrev[sel] !== undefined && _hudPrev[sel] !== val) {
        const el = $(sel);
        if (el) {
            el.classList.remove("pulse");
            void el.offsetWidth; // 强制重排以重启动画
            el.classList.add("pulse");
        }
    }
    _hudPrev[sel] = val;
}

function renderGame(st) {
    $("#hud-wave").textContent = st.wave;
    $("#hud-gold").textContent = st.gold;
    $("#hud-lives").textContent = st.lives;
    hudPulse("#hud-wave", st.wave);
    hudPulse("#hud-gold", st.gold);
    hudPulse("#hud-lives", st.lives);
    const remain = Math.max(0, Math.ceil(st.phase_end_at - st.now_ts));
    $("#hud-timer").textContent = remain + "s";
    const phaseNames = { prep: "🛠️ 备战", battle: "⚔️ 战斗", break: "☕ 休整", victory: "🎉 胜利", defeat: "💀 失败" };
    const phaseEl = $("#hud-phase");
    phaseEl.textContent = phaseNames[st.phase] || st.phase;
    phaseEl.classList.toggle("battle", st.phase === "battle");

    // 背景音乐：战斗阶段自动起，其他阶段自动停
    if (st.phase === "battle") startMusic(); else stopMusic();

    // 战斗遮罩提示
    const hint = $("#battle-hint");
    if (st.phase === "victory") {
        hint.textContent = "🎉 胜利！营地守住了！";
        hint.classList.remove("hidden");
    } else if (st.phase === "defeat") {
        hint.textContent = st.revive_used ? "💀 营地陷落…" : "⚠️ 危急！点击 💖 复活（150💰）挽救营地！";
        hint.classList.remove("hidden");
    } else {
        hint.classList.add("hidden");
    }

    // 战报日志
    const logUl = $("#log-list");
    logUl.innerHTML = "";
    for (const line of st.logs || []) {
        const li = document.createElement("li");
        li.textContent = line;
        logUl.appendChild(li);
    }

    // 建造按钮状态
    renderTowerButtons(st);
    $("#btn-revive").disabled = st.revive_used || st.gold < st.revive_cost;
    $("#btn-start-battle").style.display = st.phase === "prep" ? "" : "none";

    // 金币不足的按钮置灰
    $$(".tower-btn").forEach((b) => {
        const cost = parseInt(b.dataset.cost || "0", 10);
        const dualExtra = b.dataset.dual === "1";
        const need = dualExtra ? cost * 2 : cost;
        b.style.opacity = st.gold < need ? 0.45 : 1;
    });
}

function renderTowerButtons(st) {
    const wrap = $("#tower-buttons");
    if (wrap.dataset.built === "1" && wrap.children.length) {
        // 已构建过只更新选中态
        $$(".tower-btn").forEach((b) => {
            b.classList.toggle("sel", b.dataset.type === S.selectedTower);
            b.classList.toggle("dual-mode", S.dualMode && b.dataset.type === S.selectedTower);
        });
        return;
    }
    wrap.innerHTML = "";
    for (const type of st.tower_order) {
        const conf = st.towers_config[type];
        const btn = document.createElement("button");
        btn.className = "tower-btn";
        btn.dataset.type = type;
        btn.dataset.cost = conf.cost;
        btn.innerHTML = `<div class="tt-name">${conf.emoji} ${conf.name}</div>
            <div class="tt-cost">💰 ${conf.cost}｜双人 ${conf.dual_cost * 2}</div>`;
        btn.onclick = () => {
            if (S.selectedTower === type) {
                // 再点一次切换普通/双人模式
                S.dualMode = !S.dualMode;
                toast(S.dualMode ? "双人炮塔模式：选空地建造（威力翻倍，需队友共同出资）"
                    : "普通建造模式");
            } else {
                S.selectedTower = type;
                S.dualMode = false;
                toast(`选择了 ${conf.name}，点击地图空地建造`);
            }
            renderTowerButtons(st);
        };
        wrap.appendChild(btn);
    }
    wrap.dataset.built = "1";
}

async function doBuild(x, y) {
    const st = S.state;
    if (!st) return;
    if (S.dualMode && !S.dualPartner) {
        toast("双人炮塔需要一名真人队友，请先邀请好友", "err");
        return;
    }
    const payload = {
        code: S.roomCode,
        action: {
            type: "build", tower: S.selectedTower, x, y,
            mode: S.dualMode ? "dual" : "normal",
            partner_id: S.dualMode ? S.dualPartner : null,
        },
    };
    const r = await api("/api/game/action", payload);
    if (r) {
        sfx("build");
        toast(S.dualMode ? "双人炮塔建造成功！" : "建造成功", "ok", 1200);
        pollState();
    }
}

function openTowerModal(tower) {
    S.selectedTowerId = tower.id;
    const conf = S.state.towers_config[tower.type];
    $("#tower-modal-title").textContent =
        `${conf.emoji} ${conf.name} ${"★".repeat(tower.level)}${tower.dual ? "（双人）" : ""}`;
    const base = parseInt(conf.cost, 10);
    const upCost = Math.floor(base * 0.7 * (tower.level + 1));
    $("#tower-modal-desc").textContent =
        tower.level >= S.state.upgrade_max ? "已满级" : `升级费用：${upCost}💰（伤害 +35%、射程 +8%）`;
    $("#btn-upgrade").disabled = tower.level >= S.state.upgrade_max;
    $("#modal-tower").classList.remove("hidden");
}

function initGameButtons() {
    $("#btn-upgrade").onclick = async () => {
        const r = await api("/api/game/action", {
            code: S.roomCode,
            action: { type: "upgrade", tower_id: S.selectedTowerId },
        });
        if (r) { sfx("upgrade"); toast("升级成功", "ok", 1000); $("#modal-tower").classList.add("hidden"); pollState(); }
    };
    $("#btn-sell").onclick = async () => {
        const r = await api("/api/game/action", {
            code: S.roomCode,
            action: { type: "sell", tower_id: S.selectedTowerId },
        });
        if (r) { toast("已出售", "ok", 1000); $("#modal-tower").classList.add("hidden"); pollState(); }
    };
    $("#btn-close-tower").onclick = () => $("#modal-tower").classList.add("hidden");

    $$(".skill-btn").forEach((btn) => {
        if (btn.id === "btn-revive" || btn.id === "btn-start-battle") return;
        btn.onclick = async () => {
            const skill = btn.dataset.skill;
            let path = null;
            if (skill === "haste") {
                const pick = prompt("加速哪条路？输入 1（上路）或 2（下路）", "1");
                if (!pick) return;
                path = parseInt(pick, 10) - 1;
                if (path !== 0 && path !== 1) { toast("输入无效", "err"); return; }
            }
            const r = await api("/api/game/action", {
                code: S.roomCode,
                action: { type: "skill", skill, path },
            });
            if (r) { sfx("skill"); toast("技能已释放", "ok", 1000); pollState(); }
        };
    });

    $("#btn-revive").onclick = async () => {
        const r = await api("/api/game/action", { code: S.roomCode, action: { type: "revive" } });
        if (r) { sfx("revive"); toast("复活成功！", "ok"); pollState(); }
    };
    $("#btn-start-battle").onclick = async () => {
        await api("/api/game/action", { code: S.roomCode, action: { type: "start" } });
        pollState();
    };

    // 侧栏 tab 切换
    $$(".side-tab").forEach((tab) => {
        tab.onclick = () => {
            $$(".side-tab").forEach((t) => t.classList.remove("active"));
            tab.classList.add("active");
            const isChat = tab.dataset.tab === "chat";
            $("#log-list").classList.toggle("hidden", isChat);
            $("#side-chat").classList.toggle("hidden", !isChat);
        };
    });
    $("#btn-game-chat-send").onclick = sendGameChat;
    $("#game-chat-input").addEventListener("keydown", (e) => {
        if (e.key === "Enter") sendGameChat();
    });

    // 取消建造选择（手机按钮 + 电脑 Esc/右键共用）
    $("#btn-cancel-sel").onclick = () => {
        clearTowerSelection();
        toast("已取消选择", "", 1000);
    };

    // ============ 电脑键盘快捷键（"键位"） ============
    // 1~4 选塔 · Q 回血 · W 加速 · E 复活 · Enter 提前开战 · Esc 取消
    document.addEventListener("keydown", (e) => {
        if (S.screen !== "game") return;
        // 输入框聚焦时不抢按键
        const tag = (e.target.tagName || "").toLowerCase();
        if (tag === "input" || tag === "textarea") return;

        const k = e.key.toLowerCase();
        if (k >= "1" && k <= "4") {
            const st = S.state;
            const type = st && st.tower_order[parseInt(k, 10) - 1];
            if (type) {
                S.selectedTower = type;
                S.dualMode = false;
                renderTowerButtons(st);
                toast(`选择了 ${st.towers_config[type].name}（按 Esc 取消）`, "", 1200);
            }
            e.preventDefault();
        } else if (k === "q") {
            $(".skill-btn[data-skill='heal']").click();
        } else if (k === "w") {
            $(".skill-btn[data-skill='haste']").click();
        } else if (k === "e") {
            $("#btn-revive").click();
        } else if (k === "enter") {
            if ($("#btn-start-battle").style.display !== "none") $("#btn-start-battle").click();
        } else if (k === "escape") {
            clearTowerSelection();
            $("#modal-tower").classList.add("hidden");
        }
    });
}

async function sendGameChat() {
    const input = $("#game-chat-input");
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    await api("/api/game/chat", { code: S.roomCode, text });
}

// ============================================================
// 结算
// ============================================================
function showResult(st) {
    const won = st.phase === "victory";
    stopMusic();
    sfx(won ? "victory" : "defeat");
    if (won && window.Confetti) Confetti.fire({ count: 160 });  // 胜利撒花（第 5 期）
    $("#result-title").textContent = won ? "🎉 胜利！守住了营地！" : "💀 失败…营地陷落";
    const s = st.stats || {};
    $("#result-stats").innerHTML = `
        <div>🌊 到达波次：<b>${st.wave}</b></div>
        <div>🗡️ 击杀怪物：<b>${s.kills || 0}</b></div>
        <div>🕳️ 漏怪数量：<b>${s.leaks || 0}</b></div>
        <div>🏗️ 建造炮塔：<b>${s.built || 0}</b></div>
        <div>💰 剩余金币：<b>${st.gold}</b></div>`;
    showPage("result");
}

function initResultButtons() {
    $("#btn-again").onclick = async () => {
        // 再来一局：回房间等待（房间仍存在则重置由服务端 waiting 处理）
        await api("/api/game/leave", { code: S.roomCode });
        const r = await api("/api/game/create_room", { is_public: true, max_players: 4, allow_join: true });
        if (r) {
            toast("新房间已创建：" + r.code, "ok");
            S.version = 0;
            S.state = null;
            enterRoom(r.code);
        }
    };
    $("#btn-result-menu").onclick = async () => {
        await api("/api/game/leave", { code: S.roomCode });
        leaveToMenu();
    };
}

// ============================================================
// 启动
// ============================================================
document.addEventListener("DOMContentLoaded", () => {
    initMenu();
    initRoomButtons();
    initGameButtons();
    initResultButtons();
    showPage("menu");
    // 断线重连：有 token 时静默刷新昵称校验身份
    if (S.token && S.nickname) {
        api("/api/game/nickname", { nickname: S.nickname }).then((r) => {
            if (r) console.log("身份已恢复:", r.nickname);
        });
    }
});
