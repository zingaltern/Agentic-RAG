/* Agentic RAG 测试页逻辑（第 7 章：五区块 + api() 统一封装） */
"use strict";

/** 统一 fetch 封装：带 X-Trace-Id；返回 {code, message, data}，code!=0 时高亮错误码。 */
async function api(path, options = {}) {
  const headers = { "X-Trace-Id": (crypto.randomUUID && crypto.randomUUID()) || String(Date.now()) };
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  let resp;
  try {
    resp = await fetch(path, { ...options, headers });
  } catch (e) {
    showError("网络请求失败: " + e.message);
    return { code: 50001, message: "网络请求失败", data: {} };
  }
  let json;
  try {
    json = await resp.json();
  } catch (e) {
    showError(`响应解析失败（HTTP ${resp.status}）`);
    return { code: 50001, message: "响应解析失败", data: {} };
  }
  if (json.code !== 0) {
    showError(`[错误码 ${json.code}] ${json.message}`);
  }
  return json;
}

let lastErrorEl = null;
function showError(text) {
  let el = document.getElementById("error-bar");
  if (!el) {
    el = document.createElement("div");
    el.id = "error-bar";
    el.className = "error-bar";
    document.querySelector("main").prepend(el);
  }
  el.textContent = text;
}

const $ = (id) => document.getElementById(id);

/* ---------- ① 上传（上传后 500ms 轮询 /api/jobs/{id}） ---------- */
$("btn-upload").addEventListener("click", async () => {
  const file = $("file-input").files[0];
  if (!file) { showError("请先选择文件"); return; }
  const form = new FormData();
  form.append("file", file);
  const res = await api("/api/documents/upload", { method: "POST", body: form });
  if (res.code !== 0) return;
  $("upload-status").textContent = `Job ${res.data.job_id} 已提交（status=${res.data.status}），轮询中…`;
  const timer = setInterval(async () => {
    const job = await api(`/api/jobs/${res.data.job_id}`);
    if (job.code !== 0 || job.data.status === "pending" || job.data.status === "running") return;
    clearInterval(timer);
    if (job.data.status === "success") {
      $("upload-status").textContent = `✅ ${file.name} 入库成功：document_id=${job.data.document_id}，chunk=${job.data.chunk_count}`;
      refreshDocs();
    } else {
      $("upload-status").textContent = `❌ 入库失败：error_code=${job.data.error_code}`;
    }
  }, 500);
});

/* ---------- ② 文档管理 ---------- */
async function refreshDocs() {
  const res = await api("/api/documents?page=1&size=50");
  if (res.code !== 0) return;
  const tbody = $("doc-tbody");
  if (!res.data.items || res.data.items.length === 0) {
    tbody.innerHTML = '<tr><td colspan="4" class="muted">暂无文档</td></tr>';
    return;
  }
  tbody.innerHTML = res.data.items.map((d) => `
    <tr>
      <td>${d.document_id || "-"}</td>
      <td>${d.filename || "-"}</td>
      <td>${d.chunk_count ?? "-"}</td>
      <td><button class="danger" onclick="deleteDoc('${d.document_id}')">删除</button></td>
    </tr>`).join("");
}
window.deleteDoc = async (id) => {
  const res = await api(`/api/documents/${id}`, { method: "DELETE" });
  if (res.code === 0) refreshDocs();
};
$("btn-refresh-docs").addEventListener("click", refreshDocs);

/* ---------- ③ 问答控制台 + ④ 反思过程（debug） ---------- */
$("btn-query").addEventListener("click", async () => {
  const query = $("query-input").value.trim();
  if (!query) { showError("请输入问题"); return; }
  const payload = { query, debug: $("debug-toggle").checked };
  const res = await api("/api/query", { method: "POST", body: JSON.stringify(payload) });
  $("answer-box").classList.remove("hidden");
  $("answer-meta").classList.remove("hidden");
  $("sources-box").classList.remove("hidden");
  if (res.code !== 0) {
    $("answer-box").textContent = res.message;
    $("answer-meta").textContent = "";
    $("sources-box").textContent = "";
    $("debug-box").textContent = "";
    return;
  }
  const d = res.data;
  $("answer-box").textContent = d.answer || "";
  const hb = d.hallucination_check === "passed"
    ? '<span class="badge badge-ok">幻觉检测通过</span>'
    : '<span class="badge badge-down">幻觉检测未通过</span>';
  $("answer-meta").innerHTML =
    `引用 ${JSON.stringify(d.citations || {})} · 迭代 ${d.iterations} · 耗时 ${d.elapsed_ms}ms ${hb}`;
  $("sources-box").innerHTML = "<strong>来源：</strong><ol>" +
    (d.sources || []).map((s) =>
      `<li>[${s.document_id}] ${s.filename}（page=${s.page ?? "-"}，score=${s.score.toFixed(3)}）<br>${s.chunk || ""}</li>`
    ).join("") + "</ol>";
  $("debug-box").textContent = d.debug_trace
    ? JSON.stringify(d.debug_trace, null, 2)
    : "（未返回 debug_trace）";
});

/* ---------- ⑤ 插件与健康 ---------- */
async function loadHealth() {
  const res = await api("/api/health");
  if (res.code !== 0) return;
  const d = res.data;
  const badge = $("health-badge");
  badge.className = `badge badge-${d.status}`;
  badge.textContent = `健康状态: ${d.status}（${JSON.stringify(d.checks)}）`;
}
async function loadPlugins() {
  const res = await api("/api/plugins/status");
  if (res.code !== 0) return;
  const modules = res.data.modules || {};
  $("plugin-tbody").innerHTML = Object.entries(modules)
    .map(([m, p]) => `<tr><td>${m}</td><td>${p}</td></tr>`).join("");
}
$("btn-health").addEventListener("click", () => { loadHealth(); loadPlugins(); });

/* 启动：健康 + 插件 + 文档列表 */
loadHealth();
loadPlugins();
refreshDocs();
