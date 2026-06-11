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
  openStream();
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
    tr.innerHTML =
      `<td>${escapeHtml(a.company)}</td><td>${escapeHtml(a.title)}</td><td>${a.lane}</td>` +
      `<td><span class="status-tag status-${a.status}">${a.status}</span></td><td>${when}</td>`;
    body.appendChild(tr);
  });
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
$("btn-refresh").addEventListener("click", () => { refreshStatus(); loadTracker(); });

loadProfiles().catch((err) => {
  $("status-readout").textContent = "Failed to load: " + err.message;
});
