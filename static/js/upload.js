/* ============================================================
   upload.js —— 浏览器端图片压缩 + 通用上传封装
   ★ 平台硬性约束：图片压缩必须在浏览器端用 Canvas 完成
     （质量 0.7、最长边 1280），服务端零图像处理。
     因此这里承担全部图像工作，服务端只做校验与落盘。
   依赖：components.js（Toast/csrfToken）
   ============================================================ */
"use strict";

/**
 * 压缩图片：Canvas 缩放 + 重编码为 JPEG(质量 0.7)。
 * - 非图片文件直接原样返回（跳过压缩）
 * - 最长边超过 maxEdge 时等比缩小；不超过也重编码以降低体积
 * - 画布先铺白底，避免 PNG 透明区域变成黑块
 * - 若压缩结果反而更大，则返回原文件（不做负优化）
 * @param {File} file
 * @param {{maxEdge?:number, quality?:number}} opts
 * @returns {Promise<File>}
 */
async function compressImage(file, opts = {}) {
    const maxEdge = opts.maxEdge || 1280;
    const quality = opts.quality === undefined ? 0.7 : opts.quality;
    if (!file || !/^image\//.test(file.type || "")) return file;

    const img = await loadImage(file);
    const w0 = img.naturalWidth || img.width;
    const h0 = img.naturalHeight || img.height;
    if (!w0 || !h0) return file;

    const scale = Math.min(1, maxEdge / Math.max(w0, h0));
    const w = Math.max(1, Math.round(w0 * scale));
    const h = Math.max(1, Math.round(h0 * scale));

    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#ffffff";           // 白底：PNG 透明区域不留黑块
    ctx.fillRect(0, 0, w, h);
    ctx.drawImage(img, 0, 0, w, h);

    const blob = await new Promise((resolve) =>
        canvas.toBlob(resolve, "image/jpeg", quality));
    if (!blob) return file;
    if (blob.size >= file.size) return file;      // 压不小就保留原文件

    const baseName = (file.name || "image").replace(/\.[^.]+$/, "");
    return new File([blob], baseName + ".jpg", { type: "image/jpeg" });
}

/** 加载图片为 HTMLImageElement（objectURL，用后即释放） */
function loadImage(file) {
    return new Promise((resolve, reject) => {
        const url = URL.createObjectURL(file);
        const img = new Image();
        img.onload = () => { URL.revokeObjectURL(url); resolve(img); };
        img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("图片读取失败")); };
        img.src = url;
    });
}

/**
 * multipart 上传封装：自动带 CSRF 头、统一失败 Toast。
 * @param {string} url
 * @param {FormData} form
 * @returns {Promise<object>} 服务端 JSON（ok=true 时）
 */
async function uploadForm(url, form) {
    let res, json;
    try {
        res = await fetch(url, {
            method: "POST",
            headers: { "X-CSRF-Token": csrfToken() },
            body: form,
        });
        json = await res.json();
    } catch (e) {
        Toast.show("网络异常，上传失败", "danger");
        throw e;
    }
    if (res.status === 401) {
        Toast.show("请先登录", "warning");
        setTimeout(() => { location.href = "/login"; }, 600);
        throw new Error("unauthorized");
    }
    if (!json.ok) {
        Toast.show(json.msg || "上传失败", "danger");
        throw new Error(json.msg || "error");
    }
    return json;
}

/** 便捷函数：带进度的"选文件→（图片则压缩）→上传" */
async function pickAndUploadImage(inputEl, url, buildForm, opts = {}) {
    const file = inputEl.files && inputEl.files[0];
    if (!file) return null;
    let out = file;
    if (/^image\//.test(file.type || "")) {
        try {
            out = await compressImage(file, opts);
        } catch (e) {
            Toast.show("图片处理失败，将上传原图", "warning");
            out = file;
        }
    }
    const form = buildForm(out);
    return uploadForm(url, form);
}
