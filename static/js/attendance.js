/* ============================================================
   attendance.js —— 日常考勤前端
   状态机（参考开源定位考勤实践）：
     locating（定位中）→ ready（已定位，持续刷新距离）
     → done（签到成功）/ error（定位失败可重试）
   要点：进页面立即开始定位，不等用户点按钮；距离只作展示，
   判定以服务端 Haversine 复核为准；范围内【自动】提交签到。
   ============================================================ */
"use strict";
(function () {
    var CFG = window.__ATT__ || { logged_in: false, can_manage: false };
    var studentEl = document.getElementById("att-student");
    var manageEl = document.getElementById("att-manage");
    var myEl = document.getElementById("att-my");

    var S = {
        session: null,        // 当前进行中的考勤
        groups: [],           // 名单分组（管理端发起表单下拉用）
        loc: null,            // {lat, lng, accuracy}
        locState: "idle",     // idle/locating/ready/error/insecure
        locError: "",         // 定位失败的具体原因（展示给用户）
        checked: false,
        submitting: false,
        locTimer: null,
        pollTimer: null,
    };

    /* ---------- Haversine（仅展示用，判定以服务端为准） ---------- */
    function distM(lat1, lng1, lat2, lng2) {
        var R = 6371000, rad = Math.PI / 180;
        var dLat = (lat2 - lat1) * rad, dLng = (lng2 - lng1) * rad;
        var a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
            Math.cos(lat1 * rad) * Math.cos(lat2 * rad) *
            Math.sin(dLng / 2) * Math.sin(dLng / 2);
        return 2 * R * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    }

    function fmtTime(unixSec) {
        return fmtBeijing(unixSec, true);
    }

    /* ---------- 定位（持续刷新，GPS 漂移取最新） ----------
       失败必答"为什么"：非安全上下文（http://）/ 拒绝授权 / 信号弱 / 超时，
       分别给出可操作的修复指引，而不是一句笼统的"定位失败"。 */
    function locErrorText(err) {
        if (!err || typeof err.code === "undefined") return "未知原因，请重试";
        var ua = navigator.userAgent || "";
        var isIOS = /iPhone|iPad|iPod/.test(ua);
        var isAndroid = /Android/.test(ua);
        if (err.code === 1) {
            if (isIOS) {
                return "定位权限被拒绝。iPhone 请依次检查：①「设置 → 隐私与安全性 → 定位服务」" +
                    "总开关要打开；② 同一列表中找到本浏览器（Safari/夸克）→ 选「使用 App 期间」；" +
                    "③ 回到本页点「重新定位」。若开了「屏幕使用时间」，还需在" +
                    "「内容与隐私限制 → 定位服务」里选允许更改";
            }
            if (isAndroid) {
                return "定位权限被拒绝。请到「系统设置 → 应用 → 当前浏览器 → 权限 → 位置信息」" +
                    "选允许，再回到本页点「重新定位」（微信/QQ 内打开请改用系统浏览器）";
            }
            return "定位权限被拒绝。请点地址栏左侧的锁形/设置图标 → 网站设置 → 位置信息 →" +
                " 允许，然后点「重新定位」（微信内打开请改用系统浏览器）";
        }
        if (err.code === 2) return "暂时获取不到位置（GPS 信号弱）。请到窗边或室外、" +
            " 或打开 Wi-Fi 辅助定位后重试";
        if (err.code === 3) return "定位超时。请确认已开启系统定位服务后重试";
        return err.message || "未知原因，请重试";
    }

    function startLocate() {
        if (!window.isSecureContext) {
            S.locState = "insecure";
            S.locError = "浏览器只在 HTTPS 安全连接下提供定位。" +
                "请确认网址以 https:// 开头（把 http:// 改成 https:// 再打开）";
            renderStudent();
            if (CFG.can_manage) renderManage();
            return;
        }
        if (!navigator.geolocation) {
            S.locState = "error";
            S.locError = "当前浏览器不支持定位，请换用系统浏览器（Safari/Chrome/Edge）打开";
            renderStudent();
            if (CFG.can_manage) renderManage();
            return;
        }
        S.locState = "locating";
        S.locError = "";
        renderStudent();
        navigator.geolocation.getCurrentPosition(function (pos) {
            S.loc = { lat: pos.coords.latitude, lng: pos.coords.longitude,
                      accuracy: pos.coords.accuracy || 0 };
            S.locState = "ready";
            renderStudent();
            maybeAutoCheckin();
        }, function (err) {
            S.locState = "error";
            S.locError = locErrorText(err);
            renderStudent();
            if (CFG.can_manage) renderManage();
        }, { enableHighAccuracy: true, timeout: 15000, maximumAge: 5000 });
    }

    /* 定位状态一句话（管理区/学生区共用） */
    function locStateLine() {
        if (S.locState === "ready" && S.loc) {
            return "✅ 已定位（精度约 " + Math.round(S.loc.accuracy) + " 米）";
        }
        if (S.locState === "locating" || S.locState === "idle") return "⏳ 正在获取定位…";
        if (S.locState === "insecure") return "⚠️ " + S.locError;
        return "⚠️ 定位失败：" + S.locError;
    }

    /* ---------- 拉取考勤状态 ---------- */
    async function refresh() {
        if (!CFG.logged_in) { renderStudent(); return; }
        try {
            var r = await fetchJSON("/api/attendance/state");
            S.session = r.session;
            S.groups = r.groups || S.groups;
            if (S.session) S.checked = !!S.session.checked_in;
            renderStudent();
            // 焦点在管理区内（正在填标题/选分组）时只刷名单，不重建表单
            if (CFG.can_manage) {
                if (manageEl && manageEl.contains(document.activeElement)) {
                    loadLive();
                } else {
                    renderManage();
                }
            }
            maybeAutoCheckin();
        } catch (e) { /* fetchJSON 已提示 */ }
    }

    /* ---------- 范围内自动签到（需求：100 米内默认签到成功） ---------- */
    function maybeAutoCheckin() {
        if (!S.session || S.checked || S.submitting) return;
        if (S.locState !== "ready" || !S.loc) return;
        var d = distM(S.loc.lat, S.loc.lng, S.session.lat, S.session.lng);
        if (d - Math.min(S.loc.accuracy, 30) > S.session.radius_m) return; // 范围外不提交
        S.submitting = true;
        fetchJSON("/api/attendance/checkin", { method: "POST", body: {
            lat: S.loc.lat, lng: S.loc.lng, accuracy: S.loc.accuracy,
        } }).then(function (r) {
            S.checked = true;
            if (window.Sfx) Sfx.play("checkin");
            if (window.Confetti) Confetti.fire();
            Toast.show("签到成功，距考勤点 " + Math.round(r.distance_m || d) + " 米", "success");
            renderStudent();
            loadMy();
        }).catch(function () { /* 已提示 */ })
          .finally(function () { S.submitting = false; });
    }

    /* ---------- 学生视图 ---------- */
    function renderStudent() {
        if (!studentEl) return;
        if (!CFG.logged_in) {
            studentEl.innerHTML = '<section class="card text-center">' +
                '<p class="text-muted">登录后才能参与考勤签到</p>' +
                '<a class="btn btn--primary mt-12" href="/login">去登录</a></section>';
            return;
        }
        if (!S.session) {
            studentEl.innerHTML = '<section class="card text-center">' +
                '<div style="font-size:40px">🕐</div>' +
                '<p class="text-muted mt-8">当前没有进行中的考勤</p></section>';
            return;
        }
        var s = S.session;
        var left = Math.max(0, Math.round(s.ends_at - (Date.now() / 1000)));
        var leftText = left >= 90
            ? "剩余 " + Math.ceil(left / 60) + " 分钟"
            : "剩余 " + left + " 秒";
        var head = '<div class="flex-between flex-wrap gap-8">' +
            '<h2>🟢 ' + escapeHtml(s.title) + '</h2>' +
            '<span class="text-faint" style="font-size:var(--fs-12)">发起人：' +
            escapeHtml(s.creator_name) + ' · ' + leftText + '</span></div>';

        if (S.checked) {
            studentEl.innerHTML = '<section class="card">' + head +
                '<div class="text-center mt-16" style="font-size:44px">✅</div>' +
                '<p class="text-center text-success mt-8" style="font-weight:700">本场已签到成功</p></section>';
            return;
        }

        var body;
        if (S.locState === "locating" || S.locState === "idle") {
            body = '<div class="text-center mt-16"><div class="skeleton skeleton--text"></div>' +
                '<p class="text-muted mt-8">正在获取你的位置…（请授权定位）</p></div>';
        } else if (S.locState === "error" || S.locState === "insecure") {
            body = '<div class="text-center mt-16">' +
                '<p class="text-danger" style="max-width:420px;margin:0 auto">' +
                escapeHtml(locStateLine().replace(/^⚠️\s*/, "")) + '</p>' +
                (S.locState === "error"
                    ? '<button class="btn btn--secondary mt-12" id="att-retry">重新定位</button>'
                    : "") +
                '</div>';
        } else {
            var d = distM(S.loc.lat, S.loc.lng, s.lat, s.lng);
            var inRange = (d - Math.min(S.loc.accuracy, 30)) <= s.radius_m;
            body = '<div class="text-center mt-16">' +
                '<div style="font-size:var(--fs-36);font-weight:800;color:var(--c-primary)">' +
                Math.round(d) + '<span style="font-size:var(--fs-16);font-weight:600"> 米</span></div>' +
                '<p class="text-muted" style="font-size:var(--fs-13)">距考勤点（半径 ' + s.radius_m + ' 米）</p>' +
                (inRange
                    ? '<p class="text-success mt-8" style="font-weight:700">✓ 已进入签到范围，正在自动签到…</p>'
                    : '<p class="text-muted mt-8">还差约 ' + Math.max(0, Math.round(d - s.radius_m)) +
                      ' 米，走近一点自动签到</p>') +
                '<button class="btn btn--primary mt-12" id="att-manual">我已到达，手动签到</button>' +
                '</div>';
        }
        studentEl.innerHTML = '<section class="card">' + head + body + '</section>';

        var retry = document.getElementById("att-retry");
        if (retry) retry.onclick = startLocate;
        var manual = document.getElementById("att-manual");
        if (manual) manual.onclick = function () {
            if (S.locState !== "ready") { Toast.show("还在定位中，请稍候", "warning"); return; }
            fetchJSON("/api/attendance/checkin", { method: "POST", body: {
                lat: S.loc.lat, lng: S.loc.lng, accuracy: S.loc.accuracy,
            } }).then(function (r) {
                S.checked = true;
                if (window.Sfx) Sfx.play("checkin");
                Toast.show("签到成功", "success");
                renderStudent(); loadMy();
            }).catch(function () {});
        };
    }

    function escapeHtml(t) {
        var d = document.createElement("div");
        d.textContent = String(t);
        return d.innerHTML;
    }

    /* ---------- 管理视图 ---------- */
    function renderManage() {
        if (!manageEl) return;
        var html = '<section class="card"><h2>🛠 考勤管理</h2>';
        if (S.session) {
            html += '<div class="mt-12 flex-between flex-wrap gap-8">' +
                '<span>进行中场次：<b>' + escapeHtml(S.session.title) + '</b>（已签 ' +
                (S.session.record_count || 0) + ' 人' +
                (S.session.group_name ? ' · 名单：' + escapeHtml(S.session.group_name) : '') +
                '）</span>' +
                '<button class="btn btn--danger btn--sm" id="att-close">结束本场考勤</button></div>' +
                '<div id="att-live" class="mt-12"></div>';
        } else {
            var groupOpts = '<option value="0">不关联名单</option>' +
                (S.groups || []).map(function (g) {
                    return '<option value="' + g.id + '">' + escapeHtml(g.name) +
                        '（' + g.count + ' 人）</option>';
                }).join("");
            html += '<div class="mt-12 flex flex-wrap gap-8">' +
                '<input class="input" id="att-title" maxlength="30" placeholder="考勤名称（默认：日常考勤）" style="max-width:240px">' +
                '<select class="input" id="att-minutes" style="max-width:140px">' +
                '<option value="0.5">30 秒</option><option value="1">1 分钟</option>' +
                '<option value="2" selected>2 分钟</option><option value="5">5 分钟</option>' +
                '<option value="10">10 分钟</option></select>' +
                '<select class="input" id="att-group" style="max-width:190px">' +
                groupOpts + '</select>' +
                '<button class="btn btn--primary" id="att-create">📍 以我的定位发起</button></div>' +
                '<p class="text-faint mt-8" style="font-size:var(--fs-12)">发起时将获取你的实时定位作为考勤点，半径 ' +
                (CFG.radius || 300) + ' 米 · ' +
                '<span id="att-loc-state">' + escapeHtml(locStateLine()) + '</span></p>' +
                '<p class="text-faint" style="font-size:var(--fs-12)">关联名单后，结束后自动列出<b>未签到同学</b>，' +
                '可手动补签；名单分组在<a href="/picker">随机点名</a>页创建与导入（如 燃气2602）· ' +
                '场次与名单保留 2 天</p>';
        }
        html += '<div class="mt-16"><h3 style="font-size:var(--fs-14)">近期场次（点场次看明细）</h3>' +
            '<div id="att-history" class="mt-8"></div></div></section>';
        manageEl.innerHTML = html;

        var createBtn = document.getElementById("att-create");
        if (createBtn) createBtn.onclick = function () {
            if (S.locState !== "ready" || !S.loc) {
                if (S.locState === "error" || S.locState === "insecure") {
                    Toast.show(S.locError || "定位失败，请先解决定位问题", "danger", 4000);
                } else {
                    Toast.show("正在获取你的定位，请稍候再点", "warning");
                }
                if (S.locState !== "insecure") startLocate();
                return;
            }
            var title = document.getElementById("att-title").value.trim();
            var minutes = parseFloat(document.getElementById("att-minutes").value);
            var groupId = parseInt(document.getElementById("att-group").value, 10) || 0;
            fetchJSON("/api/attendance/create", { method: "POST", body: {
                title: title, minutes: minutes, lat: S.loc.lat, lng: S.loc.lng,
                group_id: groupId,
            } }).then(function () {
                Toast.show("考勤已发起", "success");
                refresh();
            }).catch(function () {});
        };
        var closeBtn = document.getElementById("att-close");
        if (closeBtn) closeBtn.onclick = function () {
            var closingId = S.session.id;
            Modal.confirm({ title: "结束考勤", body: "结束后同学将无法再签到，确认结束？",
                            okText: "结束" }).then(function (yes) {
                if (!yes) return;
                fetchJSON("/api/attendance/close", { method: "POST",
                    body: { session_id: closingId } })
                    .then(function () {
                        Toast.show("已结束", "success");
                        refresh();
                        // 结束后自动展开该场明细（未签到名单在这里）
                        setTimeout(function () { toggleHist(closingId, true); }, 700);
                    })
                    .catch(function () {});
            });
        };
        loadHistory();
        loadLive();
    }

    /* ---------- 对账名单（关联分组的场次：未签到 + 补签） ---------- */
    function rosterHtml(r) {
        var html = "";
        if (!r.roster) {
            // 未关联名单：退回旧的纯签到记录列表
            if (!r.records.length) return '<p class="text-faint">还没有人签到…</p>';
            return r.records.map(function (x) {
                return '<div class="flex-between" style="padding:6px 0;border-bottom:1px dashed var(--border-1)">' +
                    '<span>' + escapeHtml(x.nickname) + '</span>' +
                    '<span class="text-faint" style="font-size:var(--fs-12)">' +
                    Math.round(x.distance_m) + ' 米 · ' + fmtTime(x.created_at) + '</span></div>';
            }).join("");
        }
        var signed = [], unsigned = [];
        r.roster.forEach(function (x) { (x.checked_in ? signed : unsigned).push(x); });
        html += '<div class="mt-8" style="font-size:var(--fs-13)">📋 <b>' +
            escapeHtml(r.session.group_name) + '</b> 对账：应到 ' + r.roster.length +
            ' · 已签 ' + signed.length + ' · ' +
            (unsigned.length
                ? '<span class="text-danger" style="font-weight:700">未签 ' + unsigned.length + '</span>'
                : '<span class="text-success" style="font-weight:700">全员到齐 ✅</span>') +
            '</div>';
        if (unsigned.length) {
            html += '<div class="mt-8">' + unsigned.map(function (x) {
                return '<div class="flex-between" style="padding:6px 0;border-bottom:1px dashed var(--border-1)">' +
                    '<span>' + escapeHtml(x.name) +
                    (x.has_account ? "" :
                        ' <span class="text-faint" style="font-size:var(--fs-12)">（无账号）</span>') +
                    '</span>' +
                    (x.has_account
                        ? '<button class="btn btn--outline btn--sm" data-att-mid="' + x.member_id + '">补签</button>'
                        : "") +
                    '</div>';
            }).join("") + '</div>';
        }
        if (signed.length) {
            html += '<details class="mt-8"><summary class="text-faint" style="cursor:pointer;font-size:var(--fs-12)">' +
                '已签到名单（' + signed.length + ' 人）</summary>' +
                signed.map(function (x) {
                    return '<div class="flex-between" style="padding:6px 0;border-bottom:1px dashed var(--border-1)">' +
                        '<span>' + escapeHtml(x.name) +
                        (x.manual ? ' <span class="text-faint" style="font-size:var(--fs-12)">（补签）</span>' : "") +
                        '</span>' +
                        '<span class="text-faint" style="font-size:var(--fs-12)">' +
                        (x.checked_at ? fmtTime(x.checked_at) : "") + '</span></div>';
                }).join("") + '</details>';
        }
        return html;
    }

    /* 绑定补签按钮（box 内渲染后调用；reloadFn 补签成功后刷新本区域） */
    function bindManual(box, sid, reloadFn) {
        box.querySelectorAll("[data-att-mid]").forEach(function (btn) {
            btn.onclick = function () {
                btn.disabled = true;
                fetchJSON("/api/attendance/manual_checkin", { method: "POST",
                    body: { session_id: sid, member_id: parseInt(btn.dataset.attMid, 10) } })
                    .then(function (r) {
                        Toast.show("已为 " + (r.name || "该同学") + " 补签", "success");
                        reloadFn();
                        loadMy();
                    })
                    .catch(function () { btn.disabled = false; });
            };
        });
    }

    function loadLive() {
        var box = document.getElementById("att-live");
        if (!box || !S.session) return;
        var sid = S.session.id;
        fetchJSON("/api/attendance/records?session_id=" + sid)
            .then(function (r) {
                box.innerHTML = rosterHtml(r);
                bindManual(box, sid, loadLive);
            }).catch(function () {});
    }

    /* 历史场次：点击展开/收起明细（含未签到名单与补签） */
    function toggleHist(sid, forceOpen) {
        var detail = document.getElementById("att-hist-" + sid);
        if (!detail) return;
        var open = detail.style.display !== "none";
        if (open && !forceOpen) { detail.style.display = "none"; return; }
        detail.style.display = "block";
        detail.innerHTML = '<p class="text-faint" style="font-size:var(--fs-12)">加载中…</p>';
        fetchJSON("/api/attendance/records?session_id=" + sid)
            .then(function (r) {
                detail.innerHTML = rosterHtml(r);
                bindManual(detail, sid, function () { toggleHist(sid, true); });
            }).catch(function () { detail.innerHTML = ""; });
    }

    function loadHistory() {
        var box = document.getElementById("att-history");
        if (!box) return;
        fetchJSON("/api/attendance/history").then(function (r) {
            if (!r.sessions.length) {
                box.innerHTML = '<p class="text-faint">暂无历史场次</p>';
                return;
            }
            box.innerHTML = r.sessions.map(function (x) {
                return '<div>' +
                    '<div class="flex-between att-hist-row" data-sid="' + x.id + '" ' +
                    'style="padding:6px 0;border-bottom:1px dashed var(--border-1);cursor:pointer">' +
                    '<span>' + escapeHtml(x.title) +
                    (x.status === "open" ? ' <span class="text-success">●进行中</span>' : "") +
                    (x.group_name ? ' <span class="text-faint" style="font-size:var(--fs-12)">📋' +
                        escapeHtml(x.group_name) + '</span>' : "") +
                    '</span><span class="text-faint" style="font-size:var(--fs-12)">' +
                    x.count + ' 人 · ' + escapeHtml(x.creator_name) + ' · ' + fmtTime(x.created_at) +
                    '</span></div>' +
                    '<div id="att-hist-' + x.id + '" style="display:none;padding:4px 0 8px"></div>' +
                    '</div>';
            }).join("");
            box.querySelectorAll(".att-hist-row").forEach(function (row) {
                row.onclick = function () { toggleHist(parseInt(row.dataset.sid, 10), false); };
            });
        }).catch(function () {});
    }

    /* ---------- 我的记录 ---------- */
    function loadMy() {
        if (!myEl || !CFG.logged_in) {
            if (myEl) myEl.innerHTML = '<p class="text-faint">登录后可见</p>';
            return;
        }
        fetchJSON("/api/attendance/my").then(function (r) {
            if (!r.records.length) {
                myEl.innerHTML = '<p class="text-faint">还没有签到记录</p>';
                return;
            }
            myEl.innerHTML = r.records.map(function (x) {
                return '<div class="flex-between" style="padding:6px 0;border-bottom:1px dashed var(--border-1)">' +
                    '<span>' + escapeHtml(x.title) + '</span>' +
                    '<span class="text-faint" style="font-size:var(--fs-12)">' +
                    Math.round(x.distance_m) + ' 米 · ' + fmtTime(x.created_at) + '</span></div>';
            }).join("");
        }).catch(function () {});
    }

    /* ---------- 启动 ---------- */
    refresh();
    loadMy();
    if (CFG.logged_in) {
        startLocate();
        S.locTimer = setInterval(function () {
            if (S.session && !S.checked && !document.hidden) startLocate();
        }, 10000);   // 进行中每 10 秒刷新一次定位（走向考勤点时距离实时变小）
        S.pollTimer = setInterval(function () {
            if (!document.hidden) refresh();
        }, 5000);
    }
})();
