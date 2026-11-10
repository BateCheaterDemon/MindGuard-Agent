/* 管理端:登录 + 存档/复核/预警/配置 */
(function () {
  "use strict";
  const $ = (sel) => document.querySelector(sel);

  async function api(path, options) {
    const resp = await fetch(path, options);
    if (resp.status === 401) {
      showLogin();
      throw new Error("未登录或登录已过期");
    }
    return resp;
  }

  function showLogin() {
    $("#login").classList.remove("hidden");
    $("#panel").classList.add("hidden");
  }
  function showPanel() {
    $("#login").classList.add("hidden");
    $("#panel").classList.remove("hidden");
  }

  /* ---------- 登录 ---------- */
  $("#btn-login").addEventListener("click", async function () {
    const err = $("#login-error");
    err.textContent = "";
    try {
      const resp = await fetch("/api/admin/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: $("#pwd").value }),
      });
      if (!resp.ok) {
        err.textContent = "密码错误";
        return;
      }
      showPanel();
      loadArchives();
      loadReview();
      loadAlerts();
      loadConfig();
    } catch (e) {
      err.textContent = "网络错误";
    }
  });

  /* ---------- 页签 ---------- */
  document.querySelectorAll(".tabs button").forEach((btn) => {
    btn.addEventListener("click", function () {
      document.querySelectorAll(".tabs button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      ["archives", "review", "alerts", "config"].forEach((t) => {
        $("#tab-" + t).classList.toggle("hidden", t !== btn.dataset.tab);
      });
    });
  });

  /* ---------- 存档 ---------- */
  function riskTag(level) {
    if (level >= 3) return '<span class="tag r3">高风险</span>';
    if (level === 2) return '<span class="tag r2">中低</span>';
    return '<span class="tag ok">正常</span>';
  }
  function esc(s) {
    const div = document.createElement("div");
    div.textContent = s == null ? "" : String(s);
    return div.innerHTML;
  }

  async function loadArchives() {
    const date = $("#flt-date").value;
    const student = $("#flt-student").value.trim();
    const params = new URLSearchParams();
    if (date) params.set("date", date);
    if (student) params.set("student_id", student);
    const resp = await api("/api/admin/archives?" + params.toString());
    const data = await resp.json();
    const body = $("#archives-body");
    body.textContent = "";
    data.sessions.forEach((s) => {
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + esc(s.updated_at) + "</td>" +
        "<td>" + esc(s.student_id) + "</td>" +
        "<td>" + esc(s.name) + "</td>" +
        "<td>" + esc(s.intent || "-") + "</td>" +
        "<td>" + riskTag(s.max_risk_level) + "</td>" +
        "<td>" + (s.alerted ? "已预警" : "否") + "</td>" +
        "<td class='conv'>" + esc((s.risk_summary || "") + "\n" +
          (s.intent || "")) + "</td>";
      body.appendChild(tr);
    });
  }
  $("#btn-search").addEventListener("click", loadArchives);

  /* ---------- 复核队列 ---------- */
  async function loadReview() {
    const resp = await api("/api/admin/review");
    const data = await resp.json();
    const body = $("#review-body");
    body.textContent = "";
    data.items.forEach((it) => {
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + esc(it.created_at) + "</td>" +
        "<td>" + esc(it.session_id.slice(0, 12)) + "</td>" +
        "<td>" + esc(it.reason) + "</td>" +
        "<td>" + esc(it.status === "pending" ? "待复核" : "已处理") + "</td>" +
        "<td>" + (it.status === "pending"
          ? '<button class="row-btn" data-id="' + it.id + '">标记已处理</button>'
          : esc(it.resolution || "")) + "</td>";
      body.appendChild(tr);
    });
    body.querySelectorAll(".row-btn").forEach((btn) => {
      btn.addEventListener("click", async function () {
        const id = btn.dataset.id;
        const note = prompt("处理说明(可选):", "已人工复核");
        if (note === null) return;
        await api("/api/admin/review/" + id + "/resolve", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ resolution: note }),
        });
        loadReview();
      });
    });
  }

  /* ---------- 预警记录 ---------- */
  async function loadAlerts() {
    const resp = await api("/api/admin/alerts");
    const data = await resp.json();
    const body = $("#alerts-body");
    body.textContent = "";
    data.alerts.forEach((a) => {
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + esc(a.sent_at) + "</td>" +
        "<td>" + esc(a.session_id.slice(0, 12)) + "</td>" +
        "<td>" + esc(a.risk_level) + "</td>" +
        "<td>" + esc(a.status === "sent" ? "已发送" : "失败") + "</td>" +
        "<td>" + esc(a.error || "") + "</td>";
      body.appendChild(tr);
    });
  }

  /* ---------- 配置 ---------- */
  async function loadConfig() {
    const resp = await api("/api/admin/config");
    const c = await resp.json();
    $("#cfg-email").value = c.alert_email_to || "";
    $("#cfg-smtp-host").value = c.smtp_host || "";
    $("#cfg-smtp-port").value = c.smtp_port || 465;
    $("#cfg-smtp-username").value = c.smtp_username || "";
    $("#cfg-smtp-from").value = c.smtp_from || "";
    $("#cfg-rag-k").value = c.rag_top_k;
    $("#cfg-rag-threshold").value = c.rag_threshold;
  }
  $("#btn-save-config").addEventListener("click", async function () {
    const payload = {
      rag_top_k: parseInt($("#cfg-rag-k").value, 10) || 4,
      rag_threshold: parseFloat($("#cfg-rag-threshold").value) || 0.35,
    };
    const resp = await api("/api/admin/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (resp.ok) alert("已保存。");
    else alert("保存失败:" + (await resp.json()).detail);
    loadConfig();
  });

  /* 进入页面先探一次登录态 */
  api("/api/admin/alerts").then(() => {
    showPanel();
    loadArchives();
    loadReview();
    loadAlerts();
    loadConfig();
  }).catch(() => showLogin());
})();
