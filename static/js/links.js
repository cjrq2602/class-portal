/* ============================================================
   links.js —— 资源导航页
   职责：分类 Tab 切换 / 搜索框前端过滤（名称+简介+网址）/ 卡片网格渲染
        （彩色首字母方块图标，禁外链 favicon）/ 登录用户推荐链接（Modal 表单）
   数据：GET /api/links/list → {categories, links}；POST /api/links/submit
   ============================================================ */
"use strict";
(function () {
    var box = document.getElementById("links-grid");
    var tabsBox = document.getElementById("links-tabs");
    var searchInput = document.getElementById("links-search");
    var subBtn = document.getElementById("link-sub-btn");
    var DATA = { categories: [], links: [] };
    var curCat = 0;                       // 0 = 全部
    var kw = "";

    function esc(s) {
        return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
    }

    /* 首字母方块：取名称首字符（中文取首字，英文取首字母大写），底色用分类色 */
    function initial(name) {
        var ch = (name || "?").trim().charAt(0);
        return /[a-z]/i.test(ch) ? ch.toUpperCase() : ch;
    }

    function render() {
        var list = DATA.links.filter(function (l) {
            if (curCat && l.category_id !== curCat) return false;
            if (kw) {
                var s = (l.name + " " + l.description + " " + l.url).toLowerCase();
                if (s.indexOf(kw) === -1) return false;
            }
            return true;
        });
        if (!list.length) {
            box.innerHTML = '<div class="card" style="grid-column:1/-1"><div class="empty">' +
                icon("link", 40) + '<div class="empty__text">暂无链接</div></div></div>';
            return;
        }
        box.innerHTML = list.map(function (l) {
            return '<div class="link-card">' +
                '<div class="link-card__head">' +
                '<span class="link-card__badge" style="background:' + esc(l.cat_color) + '">' +
                esc(initial(l.name)) + "</span>" +
                '<span><div class="link-card__name">' + esc(l.name) + "</div>" +
                '<div class="link-card__cat">' + esc(l.cat_name) + "</div></span>" +
                "</div>" +
                '<div class="link-card__desc">' + esc(l.description || "—") + "</div>" +
                /* 安全：全部新窗口 + noopener noreferrer，URL 服务端已限定 http/https */
                '<a class="btn btn--outline btn--sm link-card__go" href="' + esc(l.url) +
                '" target="_blank" rel="noopener noreferrer">' +
                '访问 <span data-go-icon></span></a>' +
                "</div>";
        }).join("");
        box.querySelectorAll("[data-go-icon]").forEach(function (s) {
            s.outerHTML = icon("external", 14);
        });
    }

    function renderTabs() {
        var tabs = [{ id: 0, name: "全部" }].concat(DATA.categories);
        tabsBox.innerHTML = tabs.map(function (c) {
            return '<button class="chip' + (c.id === curCat ? " chip--active" : "") +
                '" data-cat="' + c.id + '" type="button">' + esc(c.name) + "</button>";
        }).join("");
        tabsBox.querySelectorAll("[data-cat]").forEach(function (b) {
            b.onclick = function () {
                curCat = parseInt(b.dataset.cat, 10);
                renderTabs();
                render();
            };
        });
    }

    /* ---- 推荐链接（登录用户可见按钮；进审核队列，批准后上墙） ---- */
    function openSubmit() {
        var catOpts = DATA.categories.map(function (c) {
            return '<option value="' + c.id + '">' + esc(c.name) + "</option>";
        }).join("");
        var ov = Modal.open({
            title: "推荐链接",
            body:
                '<div class="field"><label class="field__label">网站名称 *</label>' +
                '<input class="input" id="ls-name" maxlength="30" placeholder="例如：中国知网"></div>' +
                '<div class="field"><label class="field__label">链接地址 *</label>' +
                '<input class="input" id="ls-url" maxlength="200" placeholder="https://…"></div>' +
                '<div class="field"><label class="field__label">建议分类</label>' +
                '<select class="input" id="ls-cat"><option value="">（由管理员决定）</option>' +
                catOpts + "</select></div>" +
                '<div class="field"><label class="field__label">一句话说明</label>' +
                '<input class="input" id="ls-desc" maxlength="80" placeholder="这个网站有什么用（≤80 字）"></div>' +
                '<p class="text-faint" style="font-size:var(--fs-12)">提交后进入审核队列，管理员批准后展示。每分钟最多推荐 3 条。</p>',
            foot: '<button class="btn btn--secondary" data-act="cancel">取消</button>' +
                  '<button class="btn btn--primary" data-act="ok">提交推荐</button>'
        });
        ov.querySelector('[data-act="cancel"]').onclick = function () { Modal.close(ov); };
        ov.querySelector('[data-act="ok"]').onclick = async function () {
            var body = {
                name: document.getElementById("ls-name").value.trim(),
                url: document.getElementById("ls-url").value.trim(),
                category_id: document.getElementById("ls-cat").value || null,
                description: document.getElementById("ls-desc").value.trim()
            };
            if (!body.name || !body.url) { Toast.show("请填写名称和链接", "warning"); return; }
            try {
                var r = await fetchJSON("/api/links/submit", { method: "POST", body: body });
                Modal.close(ov);
                Toast.show(r.msg || "已提交，审核通过后展示", "success");
            } catch (e) { /* fetchJSON 已提示 */ }
        };
    }

    /* ---- 初始化 ---- */
    Skeleton.cards(box, 6);
    fetchJSON("/api/links/list").then(function (r) {
        DATA.categories = r.categories || [];
        DATA.links = r.links || [];
        renderTabs();
        render();
    }).catch(function () {
        box.innerHTML = '<div class="card" style="grid-column:1/-1"><div class="empty">' +
            icon("alert", 40) + '<div class="empty__text">加载失败，请稍后重试</div></div></div>';
    });

    if (searchInput) {
        searchInput.addEventListener("input", function () {
            kw = searchInput.value.trim().toLowerCase();
            render();
        });
    }
    if (subBtn) subBtn.onclick = openSubmit;
})();
