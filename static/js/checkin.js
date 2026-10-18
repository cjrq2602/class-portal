/* ============================================================
   checkin.js —— 打卡挑战页
   职责：进行中挑战列表（今日打卡大按钮 + 对勾描边动画）/ 班级排行榜
        （前 10 + 固定的"我的名次"）/ 月历热力图（纯 CSS grid 方格）
   依赖：components.js（fetchJSON/Toast/Skeleton/icon/fmtBeijing）
   ============================================================ */
"use strict";

const CK = window.__CK__ || { logged_in: false, today: "" };

function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

document.querySelectorAll(".empty__art").forEach((el) => {
    if (!el.innerHTML.trim()) el.innerHTML = icon("target", 40);
});

function challengeCard(c) {
    const checked = c.checked_today;
    const btn = !CK.logged_in
        ? `<a class="btn btn--outline btn--lg" href="/login">登录后打卡</a>`
        : checked
            ? `<button class="big-check big-check--done" disabled>
                 <span class="big-check__ring">${icon("check", 30)}</span>
                 今日已打卡 ✓</button>`
            : `<button class="big-check" data-do="${c.id}">
                 <span class="big-check__ring">${icon("check", 30)}</span>
                 今日打卡</button>`;
    return `
    <div class="card card--hover">
        <div class="flex-between flex-wrap gap-12">
            <div class="flex gap-12" style="align-items:center">
                <span class="hub-card__icon" style="background:var(--grad-brand);width:44px;height:44px"
                      data-ck-icon="${esc(c.icon)}"></span>
                <div>
                    <b style="font-size:var(--fs-16)">${esc(c.name)}</b>
                    <div class="text-muted" style="font-size:var(--fs-12)">
                        ${esc(c.start_date)} ~ ${esc(c.end_date)}
                        · ${c.participants} 人参与
                        ${c.ongoing ? "" : " · 不在进行期"}
                    </div>
                </div>
            </div>
            <div style="text-align:right">
                <div class="streak-num">🔥 <b class="num">${c.my_streak}</b> 天</div>
                <div class="text-faint" style="font-size:var(--fs-12)">累计 ${c.my_total} 天</div>
            </div>
        </div>
        <div class="mt-16 flex gap-8 flex-wrap" style="align-items:center">
            ${c.ongoing ? btn : '<span class="tag">已归档/未开始</span>'}
            <button class="btn btn--ghost btn--sm" data-board="${c.id}">看排行榜</button>
        </div>
    </div>`;
}

function loadChallenges() {
    const box = document.getElementById("ck-list");
    if (!box.dataset.loaded) Skeleton.cards(box, 2);
    fetchJSON("/api/checkin/challenges").then((r) => {
        box.dataset.loaded = "1";
        const list = r.list || [];
        if (!list.length) {
            box.innerHTML = '<div class="card"><div class="empty">' + icon("target", 44) +
                '<div class="empty__text">暂时没有进行中的挑战</div></div></div>';
            return;
        }
        box.innerHTML = '<div class="stagger" style="display:grid;gap:var(--sp-12)">' +
            list.map(challengeCard).join("") + "</div>";
        document.querySelectorAll("[data-ck-icon]").forEach((s) => {
            s.innerHTML = icon(s.dataset.ckIcon, 24);
        });
        box.querySelectorAll("[data-do]").forEach((b) => {
            b.onclick = async () => {
                const id = parseInt(b.dataset.do, 10);
                try {
                    const rr = await fetchJSON("/api/checkin/do", {
                        method: "POST", body: { challenge_id: id },
                    });
                    Toast.show(rr.already ? "今天已经打过卡啦" : `打卡成功！连续 ${rr.streak} 天 🔥`,
                               "success");
                    if (!rr.already) {
                        if (window.Sfx) Sfx.play("checkin");          // 上行双音
                        /* 连续 7 天里程碑：撒花庆祝（14/21… 天同理） */
                        if (rr.streak > 0 && rr.streak % 7 === 0 && window.Confetti) {
                            Confetti.fire({ count: 150 });
                        }
                    }
                    b.classList.add("big-check--pop");   // 对勾描边动画（450ms）
                    setTimeout(loadChallenges, 480);
                    loadBoard(id);
                    loadCalendar();
                } catch (e) { /* 已提示 */ }
            };
        });
        box.querySelectorAll("[data-board]").forEach((b) => {
            b.onclick = () => loadBoard(parseInt(b.dataset.board, 10));
        });
        if (list.length) loadBoard(list[0].id);
    }).catch(() => {
        box.dataset.loaded = "1";
        box.innerHTML = '<div class="empty">' + icon("alert", 40) +
            '<div class="empty__text">加载失败，请稍后重试</div></div>';
    });
}

/* 排行榜：前 10 + 固定显示"我的名次" */
function loadBoard(chalId) {
    const box = document.getElementById("ck-board");
    Skeleton.inject(box, 4, false);
    fetchJSON(`/api/checkin/board?challenge_id=${chalId}`).then((r) => {
        const top = r.top || [];
        const me = r.me;
        const medal = (i) => ["🥇", "🥈", "🥉"][i] || (i + 1);
        const rows = top.map((x, i) => {
            const isMe = me && x.user_id === me.user_id;
            return `<div class="board-row${isMe ? " board-row--me" : ""}">
                <span class="board-row__rank">${medal(i)}</span>
                <span class="board-row__name">${esc(x.nickname)}${isMe ? "（我）" : ""}</span>
                <span class="board-row__streak">🔥 <b class="num">${x.streak}</b> 天</span>
                <span class="text-faint" style="font-size:var(--fs-12)">累计 ${x.total} 天</span>
            </div>`;
        }).join("");
        box.innerHTML = (rows || '<div class="empty">' + icon("trophy", 36) +
            '<div class="empty__text">还没有人打卡</div></div>') +
            (me ? `<div class="board-me mt-12">
                <b>我的名次：第 ${me.rank} 名</b>
                <span class="text-muted" style="font-size:var(--fs-12)">
                    （共 ${me.of} 人参与 · 连续 ${me.streak} 天 · 累计 ${me.total} 天）</span>
            </div>` : (CK.logged_in
                ? '<div class="text-faint mt-12" style="font-size:var(--fs-12)">你还没参与该挑战</div>'
                : ""));
    }).catch(() => {
        box.innerHTML = '<div class="empty">' + icon("alert", 36) +
            '<div class="empty__text">排行榜加载失败</div></div>';
    });
}

/* 月历热力图（纯 CSS grid 方格，无 Canvas） */
let ckYear = 0, ckMonth = 0;
function loadCalendar() {
    const box = document.getElementById("ck-calendar");
    if (!CK.logged_in) {
        box.innerHTML = '<div class="empty">' + icon("lock", 36) +
            '<div class="empty__text">登录后查看你的打卡日历</div>' +
            '<a class="btn btn--primary" href="/login">登录</a></div>';
        return;
    }
    Skeleton.inject(box, 3, false);
    const q = ckYear && ckMonth ? `?year=${ckYear}&month=${ckMonth}` : "";
    fetchJSON("/api/checkin/calendar" + q).then((r) => {
        ckYear = r.year; ckMonth = r.month;
        document.getElementById("ck-prev").dataset.ym = `${r.year}|${r.month}`;
        const offset = (new Date(r.year, r.month - 1, 1).getDay() + 6) % 7;   // 周一为首列
        const cells = Array.from({ length: offset }, () =>
            '<span class="heat-cell heat-cell--blank"></span>').join("") +
            r.days.map((d) => {
                const cls = ["heat-cell"];
                if (d.checked) cls.push("heat-cell--on");
                if (d.is_today) cls.push("heat-cell--today");
                if (d.future) cls.push("heat-cell--future");
                return `<span class="${cls.join(" ")}" title="${d.date}${d.checked ? " 已打卡" : ""}">
                    ${parseInt(d.date.slice(-2), 10)}</span>`;
            }).join("");
        box.innerHTML = `
            <div class="flex-between mb-8">
                <b class="num">${r.year} 年 ${r.month} 月</b>
                <span class="text-faint" style="font-size:var(--fs-12)">
                    本月打卡 ${r.active_days} 天</span>
            </div>
            <div class="heat-grid">
                ${["一", "二", "三", "四", "五", "六", "日"]
                    .map((w) => `<span class="heat-head">${w}</span>`).join("")}
                ${cells}
            </div>`;
    }).catch(() => {
        box.innerHTML = '<div class="empty">' + icon("alert", 36) +
            '<div class="empty__text">日历加载失败</div></div>';
    });
}

document.getElementById("ck-prev").onclick = () => {
    const d = new Date(ckYear, ckMonth - 2, 1);
    ckYear = d.getFullYear(); ckMonth = d.getMonth() + 1; loadCalendar();
};
document.getElementById("ck-next").onclick = () => {
    const d = new Date(ckYear, ckMonth, 1);
    ckYear = d.getFullYear(); ckMonth = d.getMonth() + 1; loadCalendar();
};

loadChallenges();
loadCalendar();
