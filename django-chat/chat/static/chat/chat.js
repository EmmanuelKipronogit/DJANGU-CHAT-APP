(function () {
  "use strict";

  const boot = JSON.parse(document.getElementById("bootstrap").textContent);
  const $ = (id) => document.getElementById(id);
  const csrf = () => document.querySelector('meta[name="csrf-token"]').content;

  const state = {
    me: boot.me,
    convos: boot.conversations,
    activeId: boot.active ? boot.active.id : null,
    active: boot.active,
    messages: boot.messages,
    hasMore: boot.has_more,
    maxLen: boot.limits.message_max_length,
    typing: {}, // conversationId -> {name, timer}
    lastTypingSent: 0,
    socket: null,
    retry: 0,
    wasConnected: false,
  };

  // ------------------------------------------------------------------ helpers
  async function api(path, options = {}) {
    const res = await fetch(path, {
      credentials: "same-origin",
      headers: Object.assign(
        { "Content-Type": "application/json", "X-CSRFToken": csrf(), Accept: "application/json" },
        options.headers || {}
      ),
      method: options.method || "GET",
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
    if (res.status === 401 || (res.status === 403 && (await res.clone().text()).includes("verification_required"))) {
      window.location.href = "/login/";
      throw new Error("Please sign in again.");
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "Something went wrong.");
    return data;
  }

  const uid = () =>
    window.crypto && crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2);

  const timeFmt = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });
  const dayFmt = new Intl.DateTimeFormat(undefined, { weekday: "long", month: "short", day: "numeric" });
  const sameDay = (a, b) => a.toDateString() === b.toDateString();

  function shortTime(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    return sameDay(d, new Date()) ? timeFmt.format(d) : new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(d);
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  const initials = (name) =>
    (name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0].toUpperCase()).join("") || "?";

  // ------------------------------------------------------------------ sidebar
  function renderSidebar() {
    const list = $("convo-list");
    list.replaceChildren();
    $("no-convos").hidden = state.convos.length > 0;
    for (const c of state.convos) {
      const li = el("li");
      const a = el("a", "convo" + (c.id === state.activeId ? " active" : ""));
      a.href = "/c/" + c.id + "/";
      a.addEventListener("click", (e) => {
        e.preventDefault();
        openConversation(c.id);
      });
      a.appendChild(el("div", "avatar" + (c.kind === "group" ? " group" : ""), initials(c.title)));
      const main = el("div", "convo-main");
      main.appendChild(el("div", "convo-title", c.title));
      main.appendChild(el("div", "convo-last", c.last_body || "No messages yet"));
      a.appendChild(main);
      const meta = el("div", "convo-meta");
      meta.appendChild(el("span", "", shortTime(c.last_at)));
      if (c.unread > 0) meta.appendChild(el("span", "badge", c.unread > 99 ? "99+" : String(c.unread)));
      a.appendChild(meta);
      li.appendChild(a);
      list.appendChild(li);
    }
    document.title = (totalUnread() ? "(" + totalUnread() + ") " : "") + "Chat";
  }

  const totalUnread = () => state.convos.reduce((n, c) => n + (c.unread || 0), 0);

  async function refreshSidebar() {
    try {
      const data = await api("/api/conversations/");
      state.convos = data.conversations;
      // The conversation you're looking at is read by definition.
      const open = state.convos.find((c) => c.id === state.activeId);
      if (open) open.unread = 0;
      renderSidebar();
    } catch (_) {
      /* transient; the next event refreshes it */
    }
  }

  function touchConversation(msg) {
    const c = state.convos.find((x) => x.id === msg.conversation);
    if (!c) {
      refreshSidebar();
      return;
    }
    c.last_body = msg.body.slice(0, 120);
    c.last_at = msg.created_at;
    state.convos.splice(state.convos.indexOf(c), 1);
    state.convos.unshift(c);
  }

  // --------------------------------------------------------------------- room
  function renderRoom() {
    const inRoom = !!state.activeId;
    $("app").classList.toggle("in-room", inRoom);
    $("placeholder").hidden = inRoom;
    $("room").hidden = !inRoom;
    if (!inRoom) return;

    const a = state.active;
    $("room-title").textContent = a.title;
    $("room-sub").textContent =
      a.kind === "group" ? a.members.length + " members · " + a.members.map((m) => m.name).join(", ") : "Direct message";
    $("add-people-btn").hidden = !(a.kind === "group" && a.is_admin);
    $("load-more").hidden = !state.hasMore;
    renderMessages();
    renderTyping();
  }

  function messageNode(m, prev) {
    const li = el("li");
    const mine = m.sender.id === state.me.id;
    const group = state.active && state.active.kind === "group";
    const wrap = el("div", "msg " + (mine ? "mine" : "theirs") + (m.pending ? " pending" : "") + (m.failed ? " failed" : ""));
    if (!mine && group && (!prev || prev.sender.id !== m.sender.id)) wrap.appendChild(el("div", "who", m.sender.name));
    wrap.appendChild(el("div", "bubble", m.body));
    wrap.appendChild(el("div", "time", m.failed ? "Not sent" : m.pending ? "Sending…" : timeFmt.format(new Date(m.created_at))));
    li.appendChild(wrap);
    return li;
  }

  function renderMessages(keepScroll) {
    const box = $("messages");
    const list = $("message-list");
    const prevHeight = box.scrollHeight;
    const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 120;
    list.replaceChildren();
    let prev = null;
    for (const m of state.messages) {
      const d = new Date(m.created_at);
      if (!prev || !sameDay(new Date(prev.created_at), d)) {
        const sep = el("li", "day-sep", dayFmt.format(d));
        list.appendChild(sep);
        prev = null;
      }
      list.appendChild(messageNode(m, prev));
      prev = m;
    }
    if (keepScroll) box.scrollTop = box.scrollHeight - prevHeight + box.scrollTop;
    else if (nearBottom || keepScroll === false) box.scrollTop = box.scrollHeight;
  }

  function renderTyping() {
    const t = state.typing[state.activeId];
    $("typing").textContent = t ? t.name + " is typing…" : "";
  }

  async function openConversation(id, opts = {}) {
    if (!id) {
      state.activeId = null;
      state.active = null;
      if (!opts.fromPop) history.pushState({}, "", "/");
      renderSidebar();
      renderRoom();
      return;
    }
    try {
      const [meta, hist] = await Promise.all([
        api("/api/conversations/" + id + "/"),
        api("/api/conversations/" + id + "/messages/"),
      ]);
      state.active = meta.conversation;
      state.messages = hist.messages;
      state.hasMore = hist.has_more;
    } catch (err) {
      alert(err.message);
      return;
    }
    state.activeId = id;
    if (!opts.fromPop) history.pushState({ id }, "", "/c/" + id + "/");
    const c = state.convos.find((x) => x.id === id);
    if (c) c.unread = 0;
    markRead();
    renderSidebar();
    renderRoom();
    renderMessages(false);
    $("input").focus();
  }

  function markRead() {
    if (state.activeId && state.socket && state.socket.readyState === WebSocket.OPEN) {
      state.socket.send(JSON.stringify({ type: "read", conversation: state.activeId }));
    } else if (state.activeId) {
      api("/api/conversations/" + state.activeId + "/read/", { method: "POST" }).catch(() => {});
    }
  }

  $("load-more").addEventListener("click", async () => {
    const first = state.messages.find((m) => !m.pending);
    if (!first) return;
    try {
      const data = await api("/api/conversations/" + state.activeId + "/messages/?before=" + first.id);
      state.messages = data.messages.concat(state.messages);
      state.hasMore = data.has_more;
      $("load-more").hidden = !state.hasMore;
      renderMessages(true);
    } catch (err) {
      alert(err.message);
    }
  });

  $("back-btn").addEventListener("click", () => openConversation(null));
  window.addEventListener("popstate", () => {
    const m = location.pathname.match(/^\/c\/([0-9a-f-]{36})\/$/);
    openConversation(m ? m[1] : null, { fromPop: true });
  });

  // ---------------------------------------------------------------- composing
  const input = $("input");
  function autosize() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 160) + "px";
  }
  input.addEventListener("input", () => {
    autosize();
    const now = Date.now();
    if (now - state.lastTypingSent > 2500 && state.socket && state.socket.readyState === WebSocket.OPEN && input.value) {
      state.lastTypingSent = now;
      state.socket.send(JSON.stringify({ type: "typing", conversation: state.activeId }));
    }
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      $("composer").requestSubmit();
    }
  });

  $("composer").addEventListener("submit", (e) => {
    e.preventDefault();
    const body = input.value.trim();
    if (!body || !state.activeId) return;
    if (body.length > state.maxLen) {
      alert("Messages can be at most " + state.maxLen + " characters.");
      return;
    }
    const clientId = uid();
    const pending = {
      id: "pending-" + clientId,
      client_id: clientId,
      conversation: state.activeId,
      sender: { id: state.me.id, name: state.me.name },
      body,
      created_at: new Date().toISOString(),
      pending: true,
    };
    state.messages.push(pending);
    input.value = "";
    autosize();
    renderMessages(false);

    if (state.socket && state.socket.readyState === WebSocket.OPEN) {
      state.socket.send(JSON.stringify({ type: "message", conversation: state.activeId, body, client_id: clientId }));
      setTimeout(() => failIfStillPending(clientId), 10000);
    } else {
      failPending(clientId);
    }
  });

  function failPending(clientId) {
    const m = state.messages.find((x) => x.client_id === clientId && x.pending);
    if (m) {
      m.pending = false;
      m.failed = true;
      renderMessages();
    }
  }
  const failIfStillPending = failPending;

  // ---------------------------------------------------------------- websocket
  function setConn(status, text) {
    const conn = $("conn");
    conn.className = "conn " + status;
    $("conn-text").textContent = text;
  }

  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(proto + "://" + location.host + "/ws/chat/");
    state.socket = ws;

    ws.onopen = () => {
      setConn("online", "Connected");
      const reconnected = state.wasConnected;
      state.wasConnected = true;
      state.retry = 0;
      if (reconnected) catchUp();
    };

    ws.onmessage = (event) => {
      let data;
      try {
        data = JSON.parse(event.data);
      } catch (_) {
        return;
      }
      if (data.type === "message") onMessage(data.message);
      else if (data.type === "typing") onTyping(data);
      else if (data.type === "conversation.added") refreshSidebar();
      else if (data.type === "error") onServerError(data);
    };

    ws.onclose = (event) => {
      if (event.code === 4401) {
        window.location.href = "/verify/";
        return;
      }
      setConn("offline", "Offline – reconnecting…");
      const delay = Math.min(15000, 500 * 2 ** state.retry++) + Math.random() * 400;
      setTimeout(connect, delay);
    };
  }

  // After a dropped connection, pick up anything that arrived meanwhile.
  async function catchUp() {
    await refreshSidebar();
    if (!state.activeId) return;
    try {
      const data = await api("/api/conversations/" + state.activeId + "/messages/");
      const known = new Set(state.messages.filter((m) => !m.pending).map((m) => m.id));
      const fresh = data.messages.filter((m) => !known.has(m.id));
      if (fresh.length) {
        state.messages = state.messages.filter((m) => !m.pending).concat(fresh);
        renderMessages(false);
      }
    } catch (_) {}
  }

  function onMessage(msg) {
    const mine = msg.sender.id === state.me.id;
    touchConversation(msg);

    if (msg.conversation === state.activeId) {
      const idx = msg.client_id ? state.messages.findIndex((m) => m.client_id === msg.client_id) : -1;
      if (idx >= 0) state.messages[idx] = msg;
      else if (!state.messages.some((m) => m.id === msg.id)) state.messages.push(msg);
      renderMessages(false);
      delete state.typing[state.activeId];
      renderTyping();
      if (!mine) {
        if (document.visibilityState === "visible") markRead();
        else bumpUnread(msg.conversation);
      }
    } else if (!mine) {
      bumpUnread(msg.conversation);
    }
    renderSidebar();
  }

  function bumpUnread(conversationId) {
    const c = state.convos.find((x) => x.id === conversationId);
    if (c) c.unread = (c.unread || 0) + 1;
  }

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && state.activeId) {
      const c = state.convos.find((x) => x.id === state.activeId);
      if (c && c.unread) {
        c.unread = 0;
        markRead();
        renderSidebar();
      }
    }
  });

  function onTyping(data) {
    const prev = state.typing[data.conversation];
    if (prev) clearTimeout(prev.timer);
    state.typing[data.conversation] = {
      name: data.user.name,
      timer: setTimeout(() => {
        delete state.typing[data.conversation];
        renderTyping();
      }, 3500),
    };
    renderTyping();
  }

  function onServerError(data) {
    if (data.client_id) failPending(data.client_id);
    if (data.message) alert(data.message);
  }

  // ------------------------------------------------------------------- dialog
  const dlg = {
    mode: "direct", // direct | group | add
    picked: new Map(),
    timer: null,
  };
  const dialog = $("new-dialog");

  function setMode(mode) {
    dlg.mode = mode;
    dlg.picked.clear();
    $("dialog-tabs").hidden = mode === "add";
    for (const tab of document.querySelectorAll("#dialog-tabs .tab")) {
      tab.classList.toggle("active", tab.dataset.mode === mode);
    }
    $("dialog-title").textContent = mode === "add" ? "Add people" : "New chat";
    $("group-name-wrap").hidden = mode !== "group";
    $("dialog-submit").hidden = mode === "direct";
    $("dialog-submit").textContent = mode === "add" ? "Add" : "Create group";
    $("search-label").textContent = mode === "direct" ? "Find a person by name or email" : "Add people by name or email";
    $("user-search").value = "";
    $("group-name").value = "";
    $("results").replaceChildren();
    $("dialog-error").hidden = true;
    renderChips();
  }

  function openDialog(mode) {
    setMode(mode);
    dialog.showModal();
    $("user-search").focus();
  }

  function renderChips() {
    const chips = $("chips");
    chips.replaceChildren();
    for (const [id, u] of dlg.picked) {
      const chip = el("span", "chip", u.name);
      const x = el("button", "", "×");
      x.type = "button";
      x.setAttribute("aria-label", "Remove " + u.name);
      x.addEventListener("click", () => {
        dlg.picked.delete(id);
        renderChips();
      });
      chip.appendChild(x);
      chips.appendChild(chip);
    }
  }

  function showDialogError(text) {
    $("dialog-error").textContent = text;
    $("dialog-error").hidden = !text;
  }

  $("new-chat-btn").addEventListener("click", () => openDialog("direct"));
  $("add-people-btn").addEventListener("click", () => openDialog("add"));
  for (const tab of document.querySelectorAll("#dialog-tabs .tab")) {
    tab.addEventListener("click", () => setMode(tab.dataset.mode));
  }

  $("user-search").addEventListener("input", () => {
    clearTimeout(dlg.timer);
    const q = $("user-search").value.trim();
    if (q.length < 3) {
      $("results").replaceChildren();
      return;
    }
    dlg.timer = setTimeout(async () => {
      try {
        const data = await api("/api/users/search/?q=" + encodeURIComponent(q));
        const memberIds = new Set(dlg.mode === "add" && state.active ? state.active.members.map((m) => m.id) : []);
        const results = $("results");
        results.replaceChildren();
        for (const u of data.users) {
          if (memberIds.has(u.id) || dlg.picked.has(u.id)) continue;
          const li = el("li");
          li.appendChild(el("strong", "", u.name));
          li.appendChild(el("span", "muted small", u.email));
          li.addEventListener("click", () => pickUser(u));
          results.appendChild(li);
        }
        if (!results.children.length) results.appendChild(el("li", "muted", "No one found"));
      } catch (err) {
        showDialogError(err.message);
      }
    }, 250);
  });

  async function pickUser(u) {
    showDialogError("");
    if (dlg.mode === "direct") {
      try {
        const res = await api("/api/direct/", { method: "POST", body: { user_id: u.id } });
        dialog.close();
        await refreshSidebar();
        openConversation(res.id);
      } catch (err) {
        showDialogError(err.message);
      }
      return;
    }
    dlg.picked.set(u.id, u);
    renderChips();
    $("user-search").value = "";
    $("results").replaceChildren();
    $("user-search").focus();
  }

  $("dialog-submit").addEventListener("click", async () => {
    showDialogError("");
    const ids = [...dlg.picked.keys()];
    try {
      if (dlg.mode === "group") {
        const res = await api("/api/groups/", {
          method: "POST",
          body: { name: $("group-name").value, member_ids: ids },
        });
        dialog.close();
        await refreshSidebar();
        openConversation(res.id);
      } else if (dlg.mode === "add") {
        if (!ids.length) return showDialogError("Pick at least one person.");
        await api("/api/conversations/" + state.activeId + "/members/", { method: "POST", body: { user_ids: ids } });
        dialog.close();
        const meta = await api("/api/conversations/" + state.activeId + "/");
        state.active = meta.conversation;
        renderRoom();
      }
    } catch (err) {
      showDialogError(err.message);
    }
  });

  // --------------------------------------------------------------------- boot
  $("me-name").textContent = state.me.name;
  renderSidebar();
  renderRoom();
  if (state.activeId) renderMessages(false);
  connect();
})();
