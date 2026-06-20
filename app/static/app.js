"use strict";

const $ = (id) => document.getElementById(id);
let candidate = null;
let evtSource = null;

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.json();
}

function post(path, body) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function loadProfiles() {
  const data = await api("/api/profiles");
  const select = $("candidate");
  select.innerHTML = "";
  data.profiles.forEach((name) => {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    select.appendChild(opt);
  });

  const state = $("register-state");
  if (data.sponsor_register_loaded) {
    state.textContent = "Sponsor register loaded";
    state.className = "register-state loaded";
  } else {
    state.textContent = "Sponsor register not found";
    state.className = "register-state missing";
  }

  if (data.profiles.length) selectCandidate(data.profiles[0]);
}

function selectCandidate(name) {
  candidate = name;
  $("candidate").value = name;
  $("feed").innerHTML = "";
  refreshStatus();
  loadTracker();
  loadReadiness();
  openStream();
}

// ---- setup / activation ----
function syncProviderFields() {
  const p = $("llm-provider").value;
  const needsKey = p === "anthropic" || p === "openai";
  $("llm-key").hidden = !needsKey;
  const modelPlaceholders = { local: "llama3.1", agent: "kiro-agent",
    anthropic: "claude-3-5-sonnet-20241022", openai: "gpt-4o" };
  $("llm-model").placeholder = "model (e.g. " + (modelPlaceholders[p] || "") + ")";
}

async function loadSetup() {
  try {
    const s = await api("/api/setup/status");
    $("llm-provider").value = s.llm.llm_provider;
    syncProviderFields();
    const active = s.llm.active ?? false;
    $("llm-state").textContent = active
      ? `Active: ${s.llm.llm_provider} / ${s.llm.llm_model}` +
        (s.llm.needs_key ? ` (key ${s.llm.active_key_preview})` : " (no key needed)")
      : (s.llm.llm_provider === "local"
          ? "Local provider selected but Ollama is not running. Start Ollama or it falls back to the template."
          : "Not active. Letters use the honest template until the model is ready.");
    $("browser-state").textContent = s.browser_profile_exists
      ? `Browser profile ready at ${s.browser_profile_dir}`
      : "No browser profile yet. Open the sign-in window once to create it.";
  } catch (err) {
    $("llm-state").textContent = "Failed to load setup: " + err.message;
  }
}

async function loadReadiness() {
  if (!candidate) return;
  try {
    const r = await api(`/api/setup/readiness/${encodeURIComponent(candidate)}`);
    const ul = $("readiness");
    ul.innerHTML = "";
    const items = [
      ["Model activated", r.checks.llm_active],
      ["Sponsor register loaded", r.checks.sponsor_register_loaded],
      ["Logged-in browser ready", r.checks.browser_profile_exists],
      ["Routed CVs present", r.checks.cvs_present],
    ];
    items.forEach(([label, ok]) => {
      const li = document.createElement("li");
      li.className = ok ? "ready-ok" : "ready-no";
      li.textContent = (ok ? "ready  " : "todo  ") + label;
      ul.appendChild(li);
    });
    r.notes.forEach((n) => {
      const li = document.createElement("li");
      li.className = "ready-note";
      li.textContent = n;
      ul.appendChild(li);
    });
  } catch (_) {}
}

async function activateLLM() {
  const provider = $("llm-provider").value;
  const api_key = $("llm-key").value.trim();
  const model = $("llm-model").value.trim();
  $("llm-state").textContent = "Saving...";
  try {
    const r = await post("/api/setup/llm", {
      provider,
      api_key: api_key || null,
      model: model || null,
    });
    $("llm-key").value = "";
    const active = r.llm.active ?? false;
    $("llm-state").textContent = active
      ? `Active: ${r.llm.llm_provider} / ${r.llm.llm_model}` +
        (r.llm.needs_key ? ` (key ${r.llm.active_key_preview})` : " (no key needed)")
      : "Saved. " + (provider === "local"
          ? "Start Ollama to use it, or it falls back to the template."
          : "Model not ready yet.");
    loadReadiness();
  } catch (err) {
    $("llm-state").textContent = "Error: " + err.message;
  }
}

async function testLLM() {
  $("llm-state").textContent = "Testing the model...";
  try {
    const r = await post("/api/setup/llm/test", {});
    $("llm-state").textContent = r.ok
      ? `Model OK (${r.model}). Sample: ${r.sample}`
      : `Test failed: ${r.reason}`;
  } catch (err) {
    $("llm-state").textContent = "Error: " + err.message;
  }
}

async function openLogin() {
  const url = $("login-url").value.trim();
  $("browser-state").textContent = "Opening sign-in window...";
  try {
    const r = await post("/api/setup/browser/login", { url });
    $("browser-state").textContent = r.opened
      ? r.note
      : `Not opened: ${r.reason}`;
    setTimeout(() => { loadSetup(); loadReadiness(); }, 4000);
  } catch (err) {
    $("browser-state").textContent = "Error: " + err.message;
  }
}

const SEAL_STATE = {
  running: { cls: "seal ok", label: "running" },
  queued: { cls: "seal checking", label: "queued" },
  paused: { cls: "seal block", label: "paused" },
  idle: { cls: "seal", label: "idle" },
};

async function refreshStatus() {
  if (!candidate) return;
  const s = await api(`/api/runs/status?candidate=${encodeURIComponent(candidate)}`);

  const seal = $("seal");
  const view = SEAL_STATE[s.loop_state] || SEAL_STATE.idle;
  seal.className = view.cls;
  $("seal-label").textContent = view.label;

  const rows = [
    ["State", s.loop_state],
    ["Batch", s.batch_id || "none"],
    ["Lock age", s.lock_age_minutes == null ? "no lock" : `${s.lock_age_minutes} min${s.lock_stale ? " (stale)" : ""}`],
    ["Queue depth", s.queue_depth],
    ["Last source", s.last_source || "n/a"],
    ["Sponsor row", s.last_sponsor_row],
  ];
  $("status-readout").innerHTML = rows
    .map((r) => `<div class="row"><span class="key">${r[0]}</span><span>${r[1]}</span></div>`)
    .join("");

  const pct = s.daily_cap ? Math.min(100, Math.round((s.today_count / s.daily_cap) * 100)) : 0;
  $("progress-fill").style.width = pct + "%";
  $("progress-label").textContent =
    `${s.today_count} / ${s.daily_cap}` + (s.daily_cap_met ? " · cap met" : "");
}

const KIND_META = {
  source: ["src", "Sourced"],
  filter: ["flt", "Filtered"],
  route: ["rte", "Routed"],
  integrity: ["int", "Integrity"],
  submit: ["sub", "Submitted"],
  skip: ["skp", "Skipped"],
  needs_user: ["usr", "Needs you"],
  batch: ["bch", "Batch"],
  error: ["err", "Error"],
  info: ["inf", "Info"],
};

function addFeedEvent(ev) {
  const feed = $("feed");
  const li = document.createElement("li");
  li.className = `feed-item kind-${ev.kind}`;
  const meta = KIND_META[ev.kind] || ["", ev.kind];
  const time = (ev.ts || "").slice(11, 19);
  li.innerHTML =
    `<span class="feed-time">${time}</span>` +
    `<span class="feed-tag tag-${ev.kind}">${meta[0]}</span>` +
    `<span class="feed-msg">${escapeHtml(ev.message)}</span>`;
  feed.prepend(li);
  while (feed.childElementCount > 200) feed.removeChild(feed.lastChild);

  // A submission or batch-end is a good moment to refresh derived views.
  if (ev.kind === "submit" || ev.kind === "batch") {
    refreshStatus();
    loadTracker();
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])
  );
}

function openStream() {
  if (evtSource) evtSource.close();
  evtSource = new EventSource(`/api/runs/stream?candidate=${encodeURIComponent(candidate)}`);
  evtSource.onmessage = (e) => {
    try {
      addFeedEvent(JSON.parse(e.data));
    } catch (_) {}
  };
  evtSource.onerror = () => {
    // Browser auto-reconnects EventSource; nothing to do.
  };
}

async function loadTracker() {
  if (!candidate) return;
  const data = await api(`/api/applications?candidate=${encodeURIComponent(candidate)}`);

  const counts = $("counts");
  counts.innerHTML = "";
  Object.entries(data.counts).forEach(([status, n]) => {
    const pill = document.createElement("span");
    pill.className = "count-pill";
    pill.innerHTML = `${status}: <b>${n}</b>`;
    counts.appendChild(pill);
  });

  const body = $("tracker-body");
  body.innerHTML = "";
  data.applications.slice(0, 50).forEach((a) => {
    const tr = document.createElement("tr");
    const when = (a.created_at || "").slice(0, 10);
    const canMark = ["Submitted", "Interview", "Offer", "Rejected"].includes(a.status);
    const outcome = canMark
      ? `<button class="mini" data-id="${a.id}" data-st="Interview">Interview</button>` +
        `<button class="mini" data-id="${a.id}" data-st="Offer">Offer</button>` +
        `<button class="mini ghost" data-id="${a.id}" data-st="Rejected">Rejected</button>`
      : "";
    tr.innerHTML =
      `<td>${escapeHtml(a.company)}</td><td>${escapeHtml(a.title)}</td><td>${a.lane}</td>` +
      `<td><span class="status-tag status-${a.status}">${a.status}</span></td><td>${when}</td>` +
      `<td class="outcome-cell">${outcome}</td>`;
    body.appendChild(tr);
  });
  body.querySelectorAll("button.mini").forEach((b) => {
    b.addEventListener("click", () => markOutcome(Number(b.dataset.id), b.dataset.st));
  });

  loadAnalytics();
}

async function markOutcome(applicationId, status) {
  try {
    await api("/api/applications/status", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ application_id: applicationId, status }),
    });
    loadTracker();
  } catch (err) {
    /* ignore */
  }
}

async function loadAnalytics() {
  if (!candidate) return;
  try {
    const a = await api(`/api/applications/analytics?candidate=${encodeURIComponent(candidate)}`);
    const funnel = $("funnel");
    funnel.innerHTML = "";
    Object.entries(a.funnel).forEach(([k, n]) => {
      if (!n) return;
      const pill = document.createElement("span");
      pill.className = "count-pill";
      pill.innerHTML = `${k}: <b>${n}</b>`;
      funnel.appendChild(pill);
    });
    const ovr = a.overall;
    const o = document.createElement("span");
    o.className = "count-pill";
    o.innerHTML = `Callback rate: <b>${Math.round((ovr.callback_rate || 0) * 100)}%</b> (${ovr.callbacks}/${ovr.submitted})`;
    funnel.appendChild(o);

    renderRates("by-source", a.by_source);
    renderRates("by-lane", a.by_lane);
  } catch (_) {}
}

function renderRates(id, obj) {
  const ul = $(id);
  ul.innerHTML = "";
  Object.entries(obj)
    .sort((x, y) => y[1].callback_rate - x[1].callback_rate)
    .forEach(([key, v]) => {
      const li = document.createElement("li");
      li.innerHTML =
        `<span class="rate-key">${escapeHtml(key)}</span>` +
        `<span class="rate-val">${Math.round((v.callback_rate || 0) * 100)}% ` +
        `<span class="rate-sub">(${v.callbacks}/${v.submitted})</span></span>`;
      ul.appendChild(li);
    });
  if (!Object.keys(obj).length) ul.innerHTML = '<li class="rate-empty">No data yet</li>';
}

async function control(action) {
  const note = $("control-note");
  try {
    let r;
    if (action === "start") {
      const mode = $("mode").value;
      r = await post("/api/runs/start", { candidate, mode });
    } else if (action === "pause") r = await post("/api/runs/pause", { candidate });
    else if (action === "resume") r = await post("/api/runs/resume", { candidate });

    if (action === "start" || action === "resume") {
      note.textContent = r.started
        ? `Batch launched (mode=${r.mode || "demo"}). Watch the live feed below.`
        : `Not started: ${r.reason}`;
    } else {
      note.textContent = "Pause requested. The current batch stops at its next checkpoint.";
    }
  } catch (err) {
    note.textContent = "Error: " + err.message;
  }
  refreshStatus();
}

$("candidate").addEventListener("change", (e) => selectCandidate(e.target.value));
$("btn-start").addEventListener("click", () => control("start"));
$("btn-pause").addEventListener("click", () => control("pause"));
$("btn-resume").addEventListener("click", () => control("resume"));
$("btn-refresh").addEventListener("click", () => { refreshStatus(); loadTracker(); loadReadiness(); });
$("btn-activate").addEventListener("click", activateLLM);
$("btn-test-llm").addEventListener("click", testLLM);
$("btn-login").addEventListener("click", openLogin);
$("llm-provider").addEventListener("change", syncProviderFields);

loadSetup();
loadProfiles().catch((err) => {
  $("status-readout").textContent = "Failed to load: " + err.message;
});
