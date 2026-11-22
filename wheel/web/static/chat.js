/* Chat bubble: a compact conversation with the `wheels-chat` agent.
 *
 * The browser holds no secrets and does no thinking: it posts a message to
 * /api/chat, which runs `opencode run` and streams the reply back as SSE. The
 * opencode session id (and the transcript, for convenience) stays in
 * localStorage so collapsing the panel never loses the thread.
 */
(function () {
  "use strict";

  const KEY = "wheels.chat.v1";
  const MAX_STORED = 40;
  const SUGGESTIONS = [
    "What should I sell today?",
    "Why is my free cash this low?",
    "Is my covered call covered?",
    "Explain what assignment does to my cash.",
  ];

  const S = {
    session: null,
    messages: [], // { role: "user" | "agent", text }
    busy: false,
    controller: null,
    info: null,
    askedInfo: false,
    nearBottom: true,
    gen: 0, // bumped by "new conversation", so an in-flight reply cannot land in it
  };

  let els = null; // { root, bubble, panel, log, input, form, send, sub, ... }

  /* ---------- persistence ---------- */

  function load() {
    try {
      const saved = JSON.parse(localStorage.getItem(KEY) || "null");
      if (!saved || typeof saved !== "object") return;
      if (typeof saved.session === "string" && /^ses_[A-Za-z0-9]+$/.test(saved.session)) {
        S.session = saved.session;
      }
      if (Array.isArray(saved.messages)) {
        S.messages = saved.messages
          .filter((m) => m && (m.role === "user" || m.role === "agent") && typeof m.text === "string")
          .slice(-MAX_STORED);
      }
    } catch {
      /* localStorage is a nicety; a corrupt entry just means a fresh thread */
    }
  }

  function save() {
    try {
      // A stopped turn with no text is not worth keeping as an empty bubble.
      const messages = S.messages.filter((m) => m.text);
      localStorage.setItem(KEY, JSON.stringify({ session: S.session, messages: messages.slice(-MAX_STORED) }));
    } catch {
      /* quota or private mode: the conversation still works for this page */
    }
  }

  /* ---------- a small markdown renderer (escaped first, so it is safe) ---------- */

  function inline(s) {
    const codes = [];
    let t = s.replace(/`([^`\n]+)`/g, (_, code) => {
      codes.push(`<code>${code}</code>`);
      return `\u0001${codes.length - 1}\u0001`;
    });
    t = t
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>")
      .replace(
        /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>'
      );
    return t.replace(/\u0001(\d+)\u0001/g, (_, i) => codes[Number(i)]);
  }

  function markdown(src) {
    const fences = [];
    let text = esc(String(src || "")).replace(/```([\w+-]*)\r?\n([\s\S]*?)```/g, (_, lang, code) => {
      fences.push(`<pre class="chat-code"><code>${code.replace(/\n$/, "")}</code></pre>`);
      return `\u0000${fences.length - 1}\u0000`;
    });

    const out = [];
    let para = [];
    let list = null;

    const flushPara = () => {
      if (!para.length) return;
      out.push(`<p>${inline(para.join("\n")).replace(/\n/g, "<br>")}</p>`);
      para = [];
    };
    const flushList = () => {
      if (!list) return;
      out.push(`<${list.tag}>${list.items.map((i) => `<li>${inline(i)}</li>`).join("")}</${list.tag}>`);
      list = null;
    };
    const flush = () => {
      flushPara();
      flushList();
    };

    for (const line of text.split(/\r?\n/)) {
      const fence = line.match(/^\u0000(\d+)\u0000$/);
      if (fence) {
        flush();
        out.push(fences[Number(fence[1])]);
        continue;
      }
      if (!line.trim()) {
        flush();
        continue;
      }
      if (/^#{1,4}\s+/.test(line)) {
        flush();
        out.push(`<h4>${inline(line.replace(/^#{1,4}\s+/, ""))}</h4>`);
        continue;
      }
      if (/^(-{3,}|\*{3,})$/.test(line.trim())) {
        flush();
        out.push("<hr>");
        continue;
      }
      const bullet = line.match(/^\s*[-*]\s+(.*)$/);
      const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
      if (bullet || numbered) {
        flushPara();
        const tag = bullet ? "ul" : "ol";
        if (!list || list.tag !== tag) {
          flushList();
          list = { tag, items: [] };
        }
        list.items.push((bullet || numbered)[1]);
        continue;
      }
      flushList();
      para.push(line);
    }
    flush();
    return out.join("");
  }

  /* ---------- rendering ---------- */

  function scrollToEnd() {
    els.log.scrollTop = els.log.scrollHeight;
  }

  function nodeFor(msg) {
    const wrap = document.createElement("div");
    wrap.className = `chat-msg ${msg.role}`;
    const body = document.createElement("div");
    body.className = "chat-text";
    body.innerHTML =
      msg.role === "user" ? esc(msg.text).replace(/\n/g, "<br>") : markdown(msg.text);
    wrap.appendChild(body);
    els.log.appendChild(wrap);
    return { wrap, body, msg };
  }

  function paintEmpty() {
    els.log.innerHTML = `
      <div class="chat-empty">
        <div class="chat-empty-mark">◎</div>
        <p>Ask anything about your wheel — the book, a candidate, or why a number
        looks the way it does. I read the same engine the screens do.</p>
        <div class="chat-suggest">
          ${SUGGESTIONS.map((q) => `<button type="button" data-q="${esc(q)}">${esc(q)}</button>`).join("")}
        </div>
      </div>`;
    els.log.querySelectorAll("[data-q]").forEach((b) => {
      b.onclick = () => {
        els.input.value = b.dataset.q;
        send();
      };
    });
  }

  function paintSub() {
    if (!els) return;
    els.subEl.textContent = S.info && S.info.available === false
      ? "agent unavailable"
      : "same engine as the screens";
  }

  function paintNotice() {
    if (!S.info || S.info.available !== false) return;
    showNotice(S.info.hint || "The wheels agent is not available.");
  }

  function showNotice(text) {
    const note = document.createElement("div");
    note.className = "chat-notice";
    note.textContent = text;
    els.log.appendChild(note);
    if (S.nearBottom) scrollToEnd();
  }

  function renderThread() {
    els.log.innerHTML = "";
    if (!S.messages.length) {
      paintEmpty();
      paintNotice();
      return;
    }
    S.messages.forEach(nodeFor);
    scrollToEnd();
  }

  /* ---------- one turn ---------- */

  function setBusy(busy) {
    S.busy = busy;
    els.form.classList.toggle("busy", busy);
    els.send.title = busy ? "Stop" : "Send (Enter)";
    els.send.setAttribute("aria-label", busy ? "Stop" : "Send");
    els.send.innerHTML = busy
      ? '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor"/></svg>'
      : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5m0 0-6 6m6-6 6 6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    els.input.disabled = false; // typing the next question while it thinks is fine
  }

  function finish(agentMsg, node, tool, frame, gen, failure) {
    if (gen !== S.gen) return; // the thread was reset under this reply
    if (frame) cancelAnimationFrame(frame);
    node.wrap.classList.remove("pending");
    tool.hidden = true;
    node.body.innerHTML = agentMsg.text ? markdown(agentMsg.text) : "";
    save();
    setBusy(false);
    S.controller = null;
    if (failure) {
      const el = document.createElement("div");
      el.className = "chat-error";
      el.textContent = failure;
      node.wrap.appendChild(el);
    } else if (!agentMsg.text) {
      node.body.innerHTML = '<span class="dim">The agent returned no text.</span>';
    }
    if (S.nearBottom) scrollToEnd();
  }

  async function send() {
    if (S.busy) return;
    const text = els.input.value.trim();
    if (!text) return;

    const gen = S.gen;
    els.input.value = "";
    els.input.style.height = "auto";
    S.messages.push({ role: "user", text });
    if (S.messages.length === 1) els.log.innerHTML = "";
    nodeFor({ role: "user", text });
    scrollToEnd();
    save();

    const agentMsg = { role: "agent", text: "" };
    S.messages.push(agentMsg);
    const node = nodeFor(agentMsg);
    node.wrap.classList.add("pending");

    const tool = document.createElement("div");
    tool.className = "chat-tool";
    tool.innerHTML = '<span class="chat-tool-label">thinking</span>';
    node.wrap.insertBefore(tool, node.body);

    const dots = document.createElement("span");
    dots.className = "chat-dots";
    dots.innerHTML = "<i></i><i></i><i></i>";
    node.body.appendChild(dots);

    let painted = "";
    let frame = 0;
    const paint = () => {
      frame = 0;
      if (painted === agentMsg.text) return;
      painted = agentMsg.text;
      node.body.innerHTML = markdown(agentMsg.text);
      if (S.nearBottom) scrollToEnd();
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(paint);
    };

    S.controller = new AbortController();
    setBusy(true);

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, session: S.session }),
        signal: S.controller.signal,
      });

      if (!res.ok) {
        let detail = `request failed (${res.status})`;
        try {
          detail = (await res.json()).error || detail;
        } catch {
          /* keep the status line */
        }
        throw new Error(detail);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let failure = "";

      const onFrame = (frameText) => {
        let event = "message";
        const data = [];
        for (const raw of frameText.split("\n")) {
          const line = raw.replace(/\r$/, "");
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) data.push(line.slice(5).trim());
        }
        if (!data.length) return;
        let payload = {};
        try {
          payload = JSON.parse(data.join("\n"));
        } catch {
          return;
        }

        if (event === "session" && payload.session) {
          S.session = payload.session;
          save();
        } else if (event === "delta" && payload.text) {
          if (node.wrap.classList.contains("pending")) {
            node.wrap.classList.remove("pending");
            node.body.innerHTML = "";
          }
          tool.hidden = true;
          agentMsg.text += payload.text;
          schedule();
        } else if (event === "tool" || event === "status") {
          tool.hidden = false;
          tool.querySelector(".chat-tool-label").textContent = payload.label || "working";
        } else if (event === "error") {
          failure = payload.message || "The agent stopped.";
        }
      };

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let cut;
        while ((cut = buffer.indexOf("\n\n")) >= 0) {
          onFrame(buffer.slice(0, cut));
          buffer = buffer.slice(cut + 2);
        }
      }
      if (buffer.trim()) onFrame(buffer);
      finish(agentMsg, node, tool, frame, gen, failure);
    } catch (err) {
      const stopped = err && err.name === "AbortError";
      finish(agentMsg, node, tool, frame, gen,
        stopped ? "Stopped." : err.message || "Could not reach the agent.");
    } finally {
      if (gen === S.gen) setBusy(false);
    }
  }

  /* ---------- chrome ---------- */

  function openPanel() {
    els.root.classList.add("open");
    els.panel.hidden = false;
    els.bubble.setAttribute("aria-expanded", "true");
    if (!S.askedInfo) {
      S.askedInfo = true;
      api("/api/chat/info")
        .then((info) => {
          S.info = info;
          paintSub();
          if (info.available === false) showNotice(info.hint || "The wheels agent is not available.");
        })
        .catch((err) => {
          S.askedInfo = false; // try again next time the panel opens
          S.info = { available: false, hint: `Could not check the agent: ${err.message}` };
          paintSub();
          showNotice(S.info.hint);
        });
    }
    if (window.innerWidth > 860) els.input.focus();
  }

  function closePanel() {
    els.root.classList.remove("open");
    els.panel.hidden = true;
    els.bubble.setAttribute("aria-expanded", "false");
  }

  function newThread() {
    if (S.busy && S.controller) S.controller.abort();
    S.gen += 1;
    S.busy = false;
    S.controller = null;
    S.session = null;
    S.messages = [];
    save();
    renderThread();
    setBusy(false);
    if (window.innerWidth > 860) els.input.focus();
  }

  function mount() {
    const root = document.getElementById("chat");
    if (!root) return;
    els = {
      root,
      bubble: document.getElementById("chat-bubble"),
      panel: document.getElementById("chat-panel"),
      log: document.getElementById("chat-log"),
      input: document.getElementById("chat-input"),
      form: document.getElementById("chat-form"),
      send: document.getElementById("chat-send"),
      subEl: document.getElementById("chat-sub"),
    };

    load();
    renderThread();

    els.bubble.onclick = () => (els.panel.hidden ? openPanel() : closePanel());
    document.getElementById("chat-close").onclick = closePanel;
    document.getElementById("chat-new").onclick = newThread;

    els.form.onsubmit = (e) => {
      e.preventDefault();
      if (S.busy) {
        if (S.controller) S.controller.abort();
        return;
      }
      send();
    };

    els.input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        if (!S.busy) send();
      }
    });
    els.input.addEventListener("input", () => {
      els.input.style.height = "auto";
      els.input.style.height = Math.min(120, els.input.scrollHeight) + "px";
    });

    els.log.addEventListener("scroll", () => {
      S.nearBottom = els.log.scrollHeight - els.log.scrollTop - els.log.clientHeight < 64;
    });

    // Escape closes the bubble, but a modal on top keeps priority.
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape" || els.panel.hidden) return;
      if (typeof modalStack !== "undefined" && modalStack.length) return;
      closePanel();
    });
  }

  window.addEventListener("DOMContentLoaded", mount);
})();
