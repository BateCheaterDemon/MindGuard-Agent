/* 学生端聊天页:同意 → 身份 → SSE 流式聊天 */
(function () {
  "use strict";
  const $ = (sel) => document.querySelector(sel);

  const messages = $("#messages");
  const input = $("#input");
  const btnSend = $("#btn-send");
  const who = $("#who");

  let sessionId = localStorage.getItem("mg_session_id") || null;
  let sending = false;

  function addBubble(role, text) {
    const div = document.createElement("div");
    div.className = "bubble " + (role === "user" ? "user" : "assistant");
    div.textContent = text;
    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
    return div;
  }

  function addNote(text) {
    const div = document.createElement("div");
    div.className = "bubble sys-note";
    div.textContent = text;
    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
  }

  function showOverlay(sel) {
    $(sel).classList.remove("hidden");
  }
  function hideOverlay(sel) {
    $(sel).classList.add("hidden");
  }

  function refreshWho() {
    const name = localStorage.getItem("mg_name") || "";
    const sid = localStorage.getItem("mg_student_id") || "";
    who.textContent = name ? `${name}(${sid})` : "";
  }

  /* ---------- 告知同意 ---------- */
  if (localStorage.getItem("mg_consent") !== "1") {
    showOverlay("#overlay-consent");
  } else if (!sessionId) {
    showOverlay("#overlay-identity");
  } else {
    refreshWho();
  }

  $("#chk-consent").addEventListener("change", function () {
    $("#btn-consent").disabled = !this.checked;
  });
  $("#btn-consent").addEventListener("click", function () {
    localStorage.setItem("mg_consent", "1");
    hideOverlay("#overlay-consent");
    showOverlay("#overlay-identity");
  });

  /* ---------- 身份与建会话 ---------- */
  $("#btn-identity").addEventListener("click", async function () {
    const studentId = $("#fld-student-id").value.trim();
    const name = $("#fld-name").value.trim();
    const err = $("#identity-error");
    if (!studentId || !name) {
      err.textContent = "学号和姓名都需要填写";
      return;
    }
    err.textContent = "";
    try {
      const resp = await fetch("/api/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ student_id: studentId, name: name }),
      });
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        err.textContent = data.detail || "创建会话失败,请重试";
        return;
      }
      const data = await resp.json();
      sessionId = data.session_id;
      localStorage.setItem("mg_session_id", sessionId);
      localStorage.setItem("mg_student_id", studentId);
      localStorage.setItem("mg_name", name);
      hideOverlay("#overlay-identity");
      refreshWho();
      addNote("你好,我在。有什么想聊聊的吗?");
    } catch (e) {
      err.textContent = "网络错误,请重试";
    }
  });

  /* ---------- 发送与 SSE 解析 ---------- */
  async function send() {
    const text = input.value.trim();
    if (!text || sending) return;
    sending = true;
    btnSend.disabled = true;
    input.value = "";
    addBubble("user", text);
    const bubble = addBubble("assistant", "");
    bubble.classList.add("waiting");
    bubble.textContent = "正在输入…";

    try {
      const resp = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, message: text }),
      });
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        if (resp.status === 400 && data.detail && data.detail.includes("会话")) {
          localStorage.removeItem("mg_session_id");
          location.reload();
          return;
        }
        bubble.textContent = "发送失败:" + (data.detail || resp.status);
        return;
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let done = false;
      while (!done) {
        const { value, done: streamDone } = await reader.read();
        if (streamDone) break;
        buf += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) !== -1) {
          const chunk = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          const lines = chunk.split("\n");
          const event = lines.find((l) => l.startsWith("event:"));
          const dataLine = lines.find((l) => l.startsWith("data:"));
          if (!event || !dataLine) continue;
          const payload = JSON.parse(dataLine.slice(5));
          if (payload.event === "delta") {
            if (bubble.classList.contains("waiting")) {
              bubble.classList.remove("waiting");
              bubble.textContent = "";
            }
            bubble.textContent += payload.text;
          } else if (payload.event === "risk_done") {
            if (payload.risk_level === null) {
              addNote("对话已记录,稍后有人工复核。");
            } else {
              addNote("对话已记录。");
            }
          } else if (payload.event === "error") {
            if (bubble.classList.contains("waiting")) {
              bubble.classList.remove("waiting");
              bubble.textContent = "";
            }
            bubble.textContent += "\n(系统提示:" + payload.message + ")";
          } else if (payload.event === "done") {
            done = true;
          }
          messages.scrollTop = messages.scrollHeight;
        }
      }
    } catch (e) {
      bubble.textContent = "网络错误,请稍后重试";
    } finally {
      sending = false;
      btnSend.disabled = false;
      input.focus();
    }
  }

  btnSend.addEventListener("click", send);
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });

  $("#btn-restart").addEventListener("click", function () {
    if (confirm("重新开始将创建新的对话,确定吗?")) {
      localStorage.removeItem("mg_session_id");
      location.reload();
    }
  });
})();
