// 45black Agent Console — tiny vanilla JS client.
// Talks to FastAPI backend on the same origin.

const state = {
  agents: [],
  activeAgent: null,
  activeSession: null,
  eventSource: null,
};

const $ = (id) => document.getElementById(id);

async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { "content-type": "application/json" },
    ...opts,
  });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

async function loadHealth() {
  try {
    const h = await api("/api/health");
    const el = $("health");
    if (h.ok && h.api_key_set) {
      el.textContent = "● managed agents · api key set";
      el.classList.add("ok");
    } else {
      el.textContent = "● api key missing";
      el.classList.add("err");
    }
  } catch (e) {
    $("health").textContent = "● backend offline";
    $("health").classList.add("err");
  }
}

async function loadAgents() {
  const data = await api("/api/agents");
  state.agents = data.local;
  const list = $("agent-list");
  list.innerHTML = "";
  if (state.agents.length === 0) {
    list.innerHTML = '<li class="loading">no agents defined</li>';
    return;
  }
  for (const a of state.agents) {
    const li = document.createElement("li");
    li.innerHTML = `${a.name}<span class="meta">${a.description || ""}</span>`;
    li.addEventListener("click", () => selectAgent(a));
    list.appendChild(li);
  }
}

function selectAgent(agent) {
  state.activeAgent = agent;
  document.querySelectorAll("#agent-list li").forEach((li) => li.classList.remove("active"));
  const idx = state.agents.indexOf(agent);
  document.querySelectorAll("#agent-list li")[idx]?.classList.add("active");
  $("agent-title").textContent = agent.name;
  $("agent-desc").textContent = agent.description || "";
  loadSessions(agent.slug);
}

async function loadSessions(slug) {
  // For now: list the user's recent sessions metadata-filtered by slug.
  // Remote may be unavailable in scaffold mode; fail gracefully.
  try {
    const data = await api(`/api/sessions?agent_id=${encodeURIComponent(slug)}`);
    const ul = $("session-list");
    ul.innerHTML = "";
    if (!data.data || data.data.length === 0) {
      ul.innerHTML = '<li class="loading">no sessions yet</li>';
      return;
    }
    for (const s of data.data.slice(0, 20)) {
      const li = document.createElement("li");
      li.textContent = `${s.id?.slice(0, 8)} · ${s.status || "idle"}`;
      li.addEventListener("click", () => openSession(s.id));
      ul.appendChild(li);
    }
  } catch {
    $("session-list").innerHTML = '<li class="loading">remote unavailable</li>';
  }
}

async function openSession(id) {
  state.activeSession = id;
  $("event-stream").innerHTML = "";
  try {
    const data = await api(`/api/sessions/${id}/events`);
    for (const e of data.data || []) appendEvent(e);
    subscribe(id);
  } catch (e) {
    appendEvent({ type: "error", content: e.message });
  }
}

function subscribe(id) {
  if (state.eventSource) state.eventSource.close();
  state.eventSource = new EventSource(`/api/sessions/${id}/stream`);
  state.eventSource.onmessage = (m) => {
    try {
      appendEvent(JSON.parse(m.data));
    } catch {}
  };
  state.eventSource.onerror = () => { /* backoff handled by browser */ };
}

function appendEvent(evt) {
  const stream = $("event-stream");
  stream.querySelector(".empty")?.remove();
  const div = document.createElement("div");
  const kind = evt.type || evt.kind || "event";
  div.className = `event ${kind}`;
  const body = typeof evt.content === "string"
    ? evt.content
    : JSON.stringify(evt.content ?? evt, null, 2);
  div.innerHTML = `<div class="kind">${kind}</div><div class="body"></div>`;
  div.querySelector(".body").textContent = body;
  stream.appendChild(div);
  stream.scrollTop = stream.scrollHeight;
}

async function newSession() {
  if (!state.activeAgent) return alert("Pick an agent first.");
  try {
    const s = await api("/api/sessions", {
      method: "POST",
      body: JSON.stringify({ agent_id: state.activeAgent.slug }),
    });
    openSession(s.id);
  } catch (e) {
    alert(`Could not create session: ${e.message}`);
  }
}

async function sendMessage(e) {
  e.preventDefault();
  if (!state.activeSession) return alert("No active session.");
  const input = $("composer-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  appendEvent({ type: "user", content: text });
  try {
    await api(`/api/sessions/${state.activeSession}/events`, {
      method: "POST",
      body: JSON.stringify({ content: text }),
    });
  } catch (err) {
    appendEvent({ type: "error", content: err.message });
  }
}

// Init
document.addEventListener("DOMContentLoaded", () => {
  loadHealth();
  loadAgents();
  $("new-session-btn").addEventListener("click", newSession);
  $("composer").addEventListener("submit", sendMessage);
});
