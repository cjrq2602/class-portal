/* ============================================================
   reset_password.js —— 凭重置码修改密码（无需登录）
   职责：前端密码强度即时提示 + 提交 /api/auth/reset-by-code
   依赖：components.js（fetchJSON/Toast）
   ============================================================ */
"use strict";

/* ---------------- 前端强度提示（与服务端 check_password_strength 同规则） ---------------- */
const rpNew = document.getElementById("rp-new");
const rpBar = document.getElementById("rp-strength");

function strengthOf(pw) {
    if (!pw) return null;
    if (pw.length < 8) return { cls: "pwd-strength--weak", text: "太短：至少 8 位" };
    const hasAlpha = /[A-Za-z]/.test(pw), hasDigit = /\d/.test(pw);
    if (!(hasAlpha && hasDigit)) return { cls: "pwd-strength--weak", text: "需同时包含字母与数字" };
    if (pw.length >= 12 && /[^A-Za-z0-9]/.test(pw))
        return { cls: "pwd-strength--strong", text: "强" };
    return { cls: "pwd-strength--mid", text: "中" };
}

rpNew.addEventListener("input", () => {
    const s = strengthOf(rpNew.value);
    rpBar.textContent = s ? "强度：" + s.text : "";
    rpBar.className = "pwd-strength" + (s ? " " + s.cls : "");
});

/* ---------------- 提交 ---------------- */
document.getElementById("rp-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const n1 = rpNew.value, n2 = document.getElementById("rp-new2").value;
    if (n1 !== n2) { Toast.show("两次输入的新密码不一致", "danger"); return; }
    const btn = document.getElementById("rp-submit");
    btn.classList.add("btn--loading");
    try {
        await fetchJSON("/api/auth/reset-by-code", {
            method: "POST",
            body: {
                username: document.getElementById("rp-username").value.trim(),
                code: document.getElementById("rp-code").value.trim(),
                new_password: n1,
            },
        });
        Toast.show("密码已重置，请用新密码登录", "success", 1800);
        setTimeout(() => { location.href = "/login"; }, 900);
    } catch (err) { /* fetchJSON 已 Toast 报错（含 423 锁定提示） */ }
    btn.classList.remove("btn--loading");
});
