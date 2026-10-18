/* ============================================================
   picker.js —— 随机点名
   职责：名单加载 / 请假勾选 / 抽取（2 秒减速翻滚 + 定格放大）/
        本次会话历史 / 全屏（Fullscreen API，失败降级 CSS 全屏）/
        鼓点音效（Web Audio 程序化生成，零音频文件）
   ★ 抽取结果由服务端随机决定（防前端可预测），前端只负责动画表现
   依赖：components.js（fetchJSON/Toast/icon）
   ============================================================ */
"use strict";

const PK = window.__PK__ || { can_manage: false };

let ROSTER = [];          // 全部在册成员 [{id,name,seat_no}]
let ABSENT = new Set();   // 本次请假（id）
let HISTORY = [];         // 本次已点姓名
let DRAWING = false;

function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

document.querySelectorAll(".empty__art").forEach((el) => {
    if (!el.innerHTML.trim()) el.innerHTML = icon("zap", 40);
});

/* ================= 鼓点音效（Web Audio 程序化生成） =================
   浏览器自动播放策略：AudioContext 必须在用户手势后才能运行，
   因此这里用"首次点击开始点名"作为解锁时机（ensureAudio 惰性创建 + resume）。 */
let audioCtx = null;
function ensureAudio() {
    try {
        if (!audioCtx) {
            const AC = window.AudioContext || window.webkitAudioContext;
            if (!AC) return null;
            audioCtx = new AC();
        }
        if (audioCtx.state === "suspended") audioCtx.resume();
        return audioCtx;
    } catch (e) { return null; }
}

/** 一声鼓点：低频正弦"咚" + 短噪声"哒" */
function drum(vol = 0.16, when = 0) {
    const ac = ensureAudio();
    if (!ac || ac.state !== "running") return;
    try {
        const t0 = ac.currentTime + when;
        // 低频鼓体
        const o = ac.createOscillator();
        const g = ac.createGain();
        o.type = "sine";
        o.frequency.setValueAtTime(180, t0);
        o.frequency.exponentialRampToValueAtTime(55, t0 + 0.14);
        g.gain.setValueAtTime(0.0001, t0);
        g.gain.exponentialRampToValueAtTime(vol, t0 + 0.008);
        g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.18);
        o.connect(g); g.connect(ac.destination);
        o.start(t0); o.stop(t0 + 0.2);
        // 高频"哒"（噪声，短促）
        const len = Math.max(1, Math.floor(ac.sampleRate * 0.05));
        const buf = ac.createBuffer(1, len, ac.sampleRate);
        const data = buf.getChannelData(0);
        for (let i = 0; i < len; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / len);
        const src = ac.createBufferSource(); src.buffer = buf;
        const f = ac.createBiquadFilter(); f.type = "highpass"; f.frequency.value = 2200;
        const g2 = ac.createGain(); g2.gain.value = vol * 0.5;
        src.connect(f); f.connect(g2); g2.connect(ac.destination);
        src.start(t0);
    } catch (e) { /* 忽略音频失败 */ }
}

/** 定格音效：三连鼓 + 上扬 */
function fanfare() {
    drum(0.2, 0); drum(0.18, 0.12); drum(0.22, 0.26);
    const ac = ensureAudio();
    if (!ac || ac.state !== "running") return;
    try {
        const t0 = ac.currentTime + 0.26;
        const o = ac.createOscillator(); const g = ac.createGain();
        o.type = "triangle";
        o.frequency.setValueAtTime(523, t0);
        o.frequency.exponentialRampToValueAtTime(1047, t0 + 0.24);
        g.gain.setValueAtTime(0.0001, t0);
        g.gain.exponentialRampToValueAtTime(0.14, t0 + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.3);
        o.connect(g); g.connect(ac.destination);
        o.start(t0); o.stop(t0 + 0.34);
    } catch (e) { /* 忽略 */ }
}

/* ================= 名单与请假 ================= */
function renderRoster() {
    const box = document.getElementById("pk-roster");
    document.getElementById("pk-count").textContent = `在册 ${ROSTER.length} 人`;
    if (!ROSTER.length) {
        box.innerHTML = '<span class="text-faint">名单为空，请让管理员在后台维护或从用户导入</span>';
        return;
    }
    box.innerHTML = ROSTER.map((m) => `
        <button class="chip${ABSENT.has(m.id) ? " chip--active" : ""}"
                data-absent="${m.id}">
            ${m.seat_no ? m.seat_no + "号 " : ""}${esc(m.name)}${ABSENT.has(m.id) ? "（请假）" : ""}
        </button>`).join("");
    box.querySelectorAll("[data-absent]").forEach((b) => {
        b.onclick = () => {
            const id = parseInt(b.dataset.absent, 10);
            if (ABSENT.has(id)) ABSENT.delete(id); else ABSENT.add(id);
            document.getElementById("pk-absent-count").textContent = `已选 ${ABSENT.size} 人`;
            renderRoster();
        };
    });
}

function renderHistory() {
    const box = document.getElementById("pk-history");
    document.getElementById("pk-history-count").textContent = `${HISTORY.length} 人`;
    box.innerHTML = HISTORY.length
        ? HISTORY.map((n) => `<span class="chip">${esc(n)}</span>`).join("")
        : '<span class="text-faint">还没有点过</span>';
}

function loadRoster() {
    fetchJSON("/api/picker/roster").then((r) => {
        ROSTER = r.list || [];
        renderRoster();
    }).catch(() => {
        ROSTER = [];
        document.getElementById("pk-roster").innerHTML =
            '<span class="text-danger">名单加载失败，请刷新重试</span>';
    });
}

/* ================= 抽取：2 秒减速翻滚 → 放大定格 ================= */
async function draw() {
    if (DRAWING) return;
    const nameEl = document.getElementById("pk-name");
    const hintEl = document.getElementById("pk-hint");
    if (!ROSTER.length) { Toast.show("名单为空，无法抽取", "warning"); return; }
    if (ROSTER.length - ABSENT.size === 0) {
        Toast.show("可抽取人数为 0（都请假了）", "warning"); return;
    }
    DRAWING = true;
    ensureAudio();                       // 用户手势内解锁音频
    nameEl.className = "picker-name picker-name--rolling";

    // 先由服务端决定结果（防前端可预测），再动画滚到该结果
    let picked;
    try {
        const r = await fetchJSON("/api/picker/draw", {
            method: "POST",
            body: {
                exclude_drawn: document.getElementById("pk-exclude-drawn").checked,
                drawn_names: HISTORY,
                absent_ids: Array.from(ABSENT),
            },
        });
        picked = r.picked;
    } catch (e) {
        nameEl.className = "picker-name";
        nameEl.textContent = "没有可抽取的人了";
        DRAWING = false;
        return;
    }

    // 翻滚：间隔从 60ms 逐渐加大到 320ms（2 秒内减速停住）
    const pool = ROSTER.filter((m) => !ABSENT.has(m.id));
    let elapsed = 0;
    let delay = 60;
    const total = 2000;
    const tick = () => {
        const m = pool[Math.floor(Math.random() * pool.length)];
        nameEl.textContent = m.name;
        drum(0.08 + 0.10 * (elapsed / total));
        elapsed += delay;
        delay = 60 + 260 * Math.pow(elapsed / total, 2);
        if (elapsed < total) {
            setTimeout(tick, delay);
        } else {
            // 定格：放大 + 渐变背景
            nameEl.textContent = picked.name;
            nameEl.className = "picker-name picker-name--final";
            fanfare();
            HISTORY.push(picked.name);
            renderHistory();
            hintEl.textContent = `${picked.name}${picked.seat_no ? "（" + picked.seat_no + " 号）" : ""}` +
                ` · 本次候选 ${picked.candidates !== undefined ? picked.candidates : ""} 人`;
            Toast.show(`🎯 抽中：${picked.name}`, "success", 2000);
            DRAWING = false;
        }
    };
    tick();
}

/* ================= 全屏（Fullscreen API + CSS 降级） ================= */
function toggleFullscreen() {
    const stage = document.getElementById("pk-stage");
    const btn = document.getElementById("pk-fullscreen");
    const isCss = stage.classList.contains("picker-stage--cssfull");
    if (isCss) {                       // 退出 CSS 全屏
        stage.classList.remove("picker-stage--cssfull");
        btn.textContent = "⛶ 全屏";
        return;
    }
    if (document.fullscreenElement) {  // 退出浏览器全屏
        document.exitFullscreen().catch(() => {});
        btn.textContent = "⛶ 全屏";
        return;
    }
    const el = document.documentElement;
    const req = el.requestFullscreen || el.webkitRequestFullscreen;
    if (req) {
        req.call(el).then(() => { btn.textContent = "⤡ 退出全屏"; })
                    .catch(() => {       // API 失败 → 降级 CSS 全屏
                        stage.classList.add("picker-stage--cssfull");
                        btn.textContent = "⤡ 退出全屏";
                    });
    } else {                             // 老浏览器无 Fullscreen API → 直接用 CSS 全屏
        stage.classList.add("picker-stage--cssfull");
        btn.textContent = "⤡ 退出全屏";
    }
}

/* ================= 绑定 ================= */
document.getElementById("pk-draw").onclick = draw;
document.getElementById("pk-fullscreen").onclick = toggleFullscreen;
document.getElementById("pk-clear").onclick = async () => {
    const sure = await Modal.confirm({
        title: "清空本次记录",
        body: "清空后已点过的人会重新参与抽取（请假勾选保留）。",
        okText: "清空", danger: false,
    });
    if (!sure) return;
    HISTORY = [];
    renderHistory();
    document.getElementById("pk-name").className = "picker-name";
    document.getElementById("pk-name").textContent = "准备好了吗？";
    Toast.show("已清空本次记录", "success");
};
// 键盘快捷键：空格 / 回车 抽取（电脑端）
document.addEventListener("keydown", (e) => {
    if (e.key === " " || e.key === "Enter") {
        const tag = (e.target.tagName || "").toLowerCase();
        if (tag === "input" || tag === "textarea" || tag === "button") return;
        e.preventDefault();
        draw();
    }
});

loadRoster();
renderHistory();
document.getElementById("pk-absent-count").textContent = "已选 0 人";
