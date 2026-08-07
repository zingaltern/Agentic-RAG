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
  const d = res.data || {};
  if (res.code !== 0) {
    $("answer-box").textContent = d.answer || res.message;
    $("answer-meta").textContent = `[错误码 ${res.code}] ${res.message}`;
    $("sources-box").textContent = "";
    renderDebug(d.debug_trace);
    return;
  }
  $("answer-box").textContent = d.answer || "";
  const hb = d.hallucination_check === "passed"
    ? '<span class="badge badge-ok">幻觉检测通过 (passed)</span>'
    : '<span class="badge badge-degraded">幻觉检测未通过 (suspected)</span>';
  const citations = d.citations || {};
  $("answer-meta").innerHTML =
    `引用 ${JSON.stringify(citations.used || [])} · 已证率 ${(citations.confirmed ?? 0).toFixed(2)} · ` +
    `迭代 ${d.iterations} · 耗时 ${d.elapsed_ms}ms ${hb}`;
  $("sources-box").innerHTML = "<strong>来源：</strong><ol>" +
    (d.sources || []).map((s) =>
      `<li><b>${s.filename || s.document_id}</b>（page=${s.page ?? "-"}，score=${(s.score ?? 0).toFixed(3)}）<br>${(s.chunk || "").slice(0, 200)}</li>`
    ).join("") + "</ol>";
  renderDebug(d.debug_trace);
});

/** ④ 反思过程：debug_trace 折叠面板（debug:true 时展示每次迭代得分与改写）。 */
function renderDebug(trace) {
  const box = $("debug-box");
  if (!trace || trace.length === 0) {
    box.innerHTML = "发送带 debug=true 的提问后，此处展示每次迭代的评估得分与改写。";
    return;
  }
  box.innerHTML = trace.map((step) => {
    if (step.rewritten !== undefined) {
      return `<p><b>iter ${step.iter}</b> 改写：${escapeHtml(step.rewritten)}</p>`;
    }
    if (step.llm_ms !== undefined) {
      return `<p><b>iter ${step.iter}</b> LLM 耗时 ${step.llm_ms}ms · 检测 ${JSON.stringify(step.detection)}</p>`;
    }
    return `<p><b>iter ${step.iter}</b> query=${escapeHtml(step.query)} · ` +
           `evaluator_score=${step.evaluator_score} · reason=${step.reason} · 候选 ${step.candidates}条</p>`;
  }).join("");
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

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
