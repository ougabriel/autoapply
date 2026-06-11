"use strict";

const $ = (id) => document.getElementById(id);
let currentProfile = null;
let lastEvaluation = null;

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json();
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

  if (data.profiles.length) {
    await selectProfile(data.profiles[0]);
  }
}

async function selectProfile(name) {
  $("candidate").value = name;
  currentProfile = await api(`/api/profiles/${encodeURIComponent(name)}`);
  renderProfile();
  await loadTracker();
}

function renderProfile() {
  const p = currentProfile;
  $("profile-summary").innerHTML =
    `<strong>${p.candidate}</strong> · ${p.sector}<br/>` +
    `${p.address.city} ${p.address.postcode} · ${p.email}<br/>` +
    `Visa route sought: ${p.visa.routeSought}`;

  const lanes = $("lanes");
  lanes.innerHTML = "";
  Object.keys(p.cvLanes || {}).forEach((lane) => {
    const chip = document.createElement("span");
    chip.className = "lane-chip";
    chip.textContent = lane;
    lanes.appendChild(chip);
  });
}

async function evaluate(event) {
  event.preventDefault();
  if (!currentProfile) return;

  const seal = $("seal");
  $("verdict-panel").hidden = false;
  seal.className = "seal checking";
  $("seal-label").textContent = "checking";
  $("verdict-actions").hidden = true;

  const payload = {
    candidate: currentProfile.candidate.toLowerCase().includes(" ")
      ? $("candidate").value
      : $("candidate").value,
    company: $("company").value.trim(),
    title: $("title").value.trim(),
    url: $("url").value.trim(),
    description: $("description").value.trim(),
  };
  // The profile filename is the candidate key the API expects.
  payload.candidate = $("candidate").value;

  let result;
  try {
    result = await api("/api/jobs/evaluate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (err) {
    seal.className = "seal block";
    $("seal-label").textContent = "error";
    $("verdict-meta").textContent = String(err.message);
    return;
  }

  lastEvaluation = { ...result, _payload: payload };
  renderVerdict(result);
}

function renderVerdict(r) {
  const seal = $("seal");
  const label = $("seal-label");

  if (r.verdict === "Ready") {
    seal.className = "seal ok";
    label.textContent = "integrity\npassed";
  } else if (r.verdict === "Skip") {
    seal.className = "seal block";
    label.textContent = "filtered\nout";
  } else {
    seal.className = "seal block";
    label.textContent = "blocked";
  }

  const meta = $("verdict-meta");
  const rows = [
    ["Verdict", r.verdict],
    ["Sponsor matched", r.sponsor_matched ? "yes" : "no (treating JD wording as signal)"],
  ];
  if (r.filter_reason) rows.push(["Filter", r.filter_reason]);
  if (r.lane) rows.push(["CV lane", `${r.lane} (${r.cv_file || "no file set"})`]);
  if (r.matched_strengths) rows.push(["Matched strengths", r.matched_strengths.join(", ")]);
  meta.innerHTML = rows
    .map((x) => `<div class="row"><span class="key">${x[0]}</span>${x[1]}</div>`)
    .join("");

  $("letter").textContent = r.letter || "(no letter - job was filtered out before tailoring)";

  const vio = $("violations");
  vio.innerHTML = "";
  (r.integrity_violations || []).forEach((v) => {
    const li = document.createElement("li");
    li.textContent = v;
    vio.appendChild(li);
  });

  const warn = $("warnings");
  warn.innerHTML = "";
  (r.integrity_warnings || []).forEach((w) => {
    const li = document.createElement("li");
    li.textContent = w;
    warn.appendChild(li);
  });

  $("verdict-actions").hidden = r.verdict !== "Ready";
}

async function recordOutcome(status) {
  if (!lastEvaluation) return;
  const p = lastEvaluation._payload;

  // Ensure the job exists, then record the application.
  const job = await api("/api/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(p),
  });

  await api("/api/applications", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      candidate: p.candidate,
      job_id: job.job_id,
      company: p.company,
      title: p.title,
      lane: lastEvaluation.lane || "default",
      status,
      ats: lastEvaluation.ats || null,
      note: "",
    }),
  });

  await loadTracker();
}

async function loadTracker() {
  if (!currentProfile) return;
  const data = await api(
    `/api/applications?candidate=${encodeURIComponent($("candidate").value)}`
  );

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
  data.applications.forEach((a) => {
    const tr = document.createElement("tr");
    const when = (a.created_at || "").slice(0, 10);
    tr.innerHTML =
      `<td>${a.company}</td><td>${a.title}</td><td>${a.lane}</td>` +
      `<td><span class="status-tag status-${a.status}">${a.status}</span></td>` +
      `<td>${when}</td>`;
    body.appendChild(tr);
  });
}

$("candidate").addEventListener("change", (e) => selectProfile(e.target.value));
$("evaluate-form").addEventListener("submit", evaluate);
$("record-submitted").addEventListener("click", () => recordOutcome("Submitted"));
$("record-skipped").addEventListener("click", () => recordOutcome("Skipped-blocked"));

loadProfiles().catch((err) => {
  $("profile-summary").textContent = "Failed to load: " + err.message;
});
