"use strict";

const $ = (id) => document.getElementById(id);
const fmt = new Intl.NumberFormat();
const km = (m) => `${fmt.format(Math.round(m / 100) / 10)} km`;
const SEEN_KEY = "orienter.seenWalkedM";

const map = L.map("map", { worldCopyJump: true }).setView([45.5, -73.6], 5);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
}).addTo(map);
const routeLayer = L.layerGroup().addTo(map);
const placeLayer = L.layerGroup().addTo(map);
let hereMarker = null;
let homeMarker = null;
let firstRender = true;
let state = null;
let pollTimer = null;
let lastPhotoKey = null;

async function api(path, options = {}) {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || resp.statusText);
  return resp.json();
}

// ---------------------------------------------------------------- rendering

const STYLE = {
  walk: { color: "#d0562b", weight: 5 },
  ferry: { color: "#2b6cd0", weight: 3, dashArray: "8 8" },
  flight: { color: "#8a3fd0", weight: 3, dashArray: "2 8" },
};

function renderMap(s) {
  routeLayer.clearLayers();
  for (const seg of s.ahead) {
    L.polyline(seg.path, { ...STYLE[seg.mode], opacity: 0.35, dashArray: "6 6" }).addTo(routeLayer);
  }
  for (const seg of s.walked) {
    const line = L.polyline(seg.path, STYLE[seg.mode]).addTo(routeLayer);
    if (seg.label) line.bindTooltip(`${seg.mode}: ${seg.label}`);
  }

  const home = [s.home.lat, s.home.lon];
  if (!homeMarker) homeMarker = L.marker(home, { title: "Home" }).addTo(map).bindPopup(`Home: ${s.home.label}`);
  homeMarker.setLatLng(home);

  const here = [s.position.lat, s.position.lon];
  if (!hereMarker) {
    hereMarker = L.marker(here, {
      icon: L.divIcon({ className: "", html: '<div class="here-marker"></div>', iconSize: [18, 18] }),
      zIndexOffset: 1000,
    }).addTo(map);
  }
  hereMarker.setLatLng(here).bindTooltip("You are here");

  placeLayer.clearLayers();
  for (const p of s.places) {
    L.circleMarker([p.lat, p.lon], { radius: p.kind === "city" ? 4 : 6, color: "#555", weight: 1, fillOpacity: 0.7 })
      .bindTooltip(`${p.name} (${p.kind})`)
      .addTo(placeLayer);
  }

  if (firstRender) {
    map.invalidateSize();
    map.setView(here, 12);
    firstRender = false;
  }
}

function renderSidebar(s) {
  const st = s.stats;
  $("s-steps").textContent = fmt.format(st.steps);
  $("s-km").textContent = km(st.walked_m);
  $("s-crow").textContent = km(st.straight_line_m);
  $("s-days").textContent = fmt.format(st.days);
  $("s-avg").textContent = fmt.format(st.avg_steps);
  $("s-last").textContent = st.last_day ? `${fmt.format(st.last_day.steps)} (${st.last_day.day})` : "–";
  $("banked").hidden = !st.banked_m;
  $("banked").textContent = `${km(st.banked_m)} of walking banked while you decide where to go next.`;

  const m = s.milestones;
  if (m.next_steps) {
    const from = m.latest_steps || 0;
    $("m-steps").textContent = fmt.format(m.next_steps);
    $("m-steps-bar").style.width = `${(100 * (st.steps - from)) / (m.next_steps - from)}%`;
  }
  if (m.next_km) {
    const from = m.latest_km || 0;
    $("m-km").textContent = `${m.next_km} km`;
    $("m-km-bar").style.width = `${(100 * (st.walked_m / 1000 - from)) / (m.next_km - from)}%`;
  }

  renderPlaces(s.places);

  $("stride").value = s.stride_m;
  $("settings-info").textContent =
    `Home: ${s.home.label}. Counting since ${s.start_date}. Heading: ${s.heading.replace("_", " ")}.` +
    (s.last_sync ? ` Last sync ${new Date(s.last_sync).toLocaleString()}.` : "");
}

// ---------------------------------------------------------------- places, grouped by month

const monthFmt = new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric" });
const dayFmt = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short" });
const KIND_LABEL = { country: ["country", "countries"], region: ["region", "regions"], city: ["city", "cities"] };
const openMonths = new Set();
const openPlaces = new Set();
const placeDetails = new Map();
let placesSignature = null;

async function loadPlace(id) {
  if (placeDetails.has(id)) return placeDetails.get(id);
  const d = await api(`/api/places/${id}`);
  if (!d.error) placeDetails.set(id, d); // Wikipedia busy: ask again next time
  return d;
}

function placeItem(p) {
  const li = document.createElement("li");
  const det = document.createElement("details");
  const sum = document.createElement("summary");
  sum.innerHTML = `<span class="kind"></span><strong></strong> <span class="muted small"></span>`;
  sum.querySelector(".kind").textContent = p.kind;
  sum.querySelector("strong").textContent = p.name;
  const when = p.reached_on ? `${dayFmt.format(new Date(`${p.reached_on}T12:00:00`))} · ` : "";
  sum.querySelector(".small").textContent = `${when}${km(p.at_walk_m)}`;
  det.append(sum);
  const fill = async () => {
    if (det.dataset.filled) return;
    det.dataset.filled = "1";
    const para = document.createElement("p");
    para.textContent = "…";
    det.append(para);
    try {
      const d = await loadPlace(p.id);
      para.textContent = d.error ? `${d.error}. Close and reopen to retry.` : d.summary || "No Wikipedia article found.";
      if (d.error) delete det.dataset.filled;
      if (d.url) {
        const a = Object.assign(document.createElement("a"), { href: d.url, target: "_blank", rel: "noopener", textContent: "Wikipedia" });
        det.append(a);
      }
    } catch (e) {
      para.textContent = e.message;
      delete det.dataset.filled;
    }
  };
  det.open = openPlaces.has(p.id);
  if (det.open) fill();
  det.ontoggle = () => {
    if (det.open) { openPlaces.add(p.id); fill(); } else openPlaces.delete(p.id);
  };
  li.append(det);
  return li;
}

function renderPlaces(places) {
  // Rebuild only when the list changed: keeps the page cheap and expanded items open.
  const signature = `${places.length}:${places.at(-1)?.id}`;
  if (signature === placesSignature) return;
  placesSignature = signature;

  $("places-count").textContent = places.length ? `(${fmt.format(places.length)})` : "";
  const groups = new Map(); // newest month first, newest place first
  for (const p of [...places].reverse()) {
    const key = (p.reached_on || "").slice(0, 7);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(p);
  }
  if (!openMonths.size && groups.size) openMonths.add(groups.keys().next().value);

  const list = $("places");
  list.replaceChildren();
  for (const [key, items] of groups) {
    const li = document.createElement("li");
    li.className = "month";
    const det = document.createElement("details");
    const sum = document.createElement("summary");
    sum.innerHTML = `<strong></strong> <span class="muted small"></span>`;
    sum.querySelector("strong").textContent = key ? monthFmt.format(new Date(`${key}-01T12:00:00`)) : "Unknown date";
    sum.querySelector(".small").textContent = Object.entries(KIND_LABEL)
      .map(([kind, [one, many]]) => {
        const n = items.filter((p) => p.kind === kind).length;
        return n ? `${n} ${n === 1 ? one : many}` : "";
      })
      .filter(Boolean)
      .join(" · ");
    const ol = document.createElement("ol");
    const fill = () => { if (!ol.childElementCount) ol.append(...items.map(placeItem)); };
    det.append(sum, ol);
    det.open = openMonths.has(key);
    if (det.open) fill();
    det.ontoggle = () => {
      if (det.open) { openMonths.add(key); fill(); } else openMonths.delete(key);
    };
    li.append(det);
    list.append(li);
  }
}

function renderStatus(s) {
  const sync = s.sync;
  $("sync-btn").disabled = sync.busy;
  $("sync-status").textContent = sync.busy ? sync.message || "Syncing…" : s.ended ? "Journey ended." : "";
  const problems = [...s.problems, ...sync.errors];
  $("problems").hidden = !problems.length;
  $("problems").replaceChildren(...problems.map((t) => Object.assign(document.createElement("p"), { textContent: t })));
}

let lastWhereKey = null;

async function renderWhere(s) {
  const key = `${s.position.lat.toFixed(2)},${s.position.lon.toFixed(2)}`;
  if (key === lastWhereKey) return;
  lastWhereKey = key;
  try {
    const w = await api(`/api/whereami?lat=${s.position.lat}&lon=${s.position.lon}`);
    $("place-name").textContent = w.city || w.region || w.country || "Somewhere on the road";
    $("place-sub").textContent = [w.city && w.region, w.country].filter(Boolean).join(", ");
  } catch (e) {
    lastWhereKey = null;
    console.warn("whereami", e);
  }
}

async function renderPhoto(s) {
  const key = `${s.position.lat.toFixed(3)},${s.position.lon.toFixed(3)}`;
  if (key === lastPhotoKey) return;
  lastPhotoKey = key;
  try {
    const photo = await api(`/api/photo?lat=${s.position.lat}&lon=${s.position.lon}`);
    $("photo").hidden = !photo.url;
    if (!photo.url) return;
    $("photo-img").src = photo.url;
    $("photo-link").href = photo.page_url || photo.url;
    $("photo-credit").textContent =
      `${photo.source} · ${photo.credit}${photo.license ? ` · ${photo.license}` : ""} · ${km(photo.distance_m)} away`;
  } catch (e) {
    console.warn("photo", e);
  }
}

// ---------------------------------------------------------------- game events

async function announceNewPlaces(s) {
  // Remember only how far we'd walked at the last visit: places beyond that are news.
  let seen;
  try { seen = JSON.parse(localStorage.getItem(SEEN_KEY) || "null"); } catch { seen = null; }
  try { localStorage.setItem(SEEN_KEY, JSON.stringify(s.stats.walked_m)); } catch { /* private mode */ }
  if (typeof seen !== "number") return; // first visit: don't flood with everything so far
  const fresh = s.places.filter((p) => p.at_walk_m > seen);
  if (!fresh.length) return;
  // Show the biggest news: a new country beats a new region beats a new city.
  const rank = { country: 0, region: 1, city: 2 };
  const p = fresh.sort((a, b) => rank[a.kind] - rank[b.kind])[0];
  let d = {};
  try { d = await loadPlace(p.id); } catch { /* show the name anyway */ }
  $("arrival-kind").textContent = `New ${p.kind}${fresh.length > 1 ? ` (+${fresh.length - 1} more)` : ""}`;
  $("arrival-name").textContent = p.name;
  $("arrival-summary").textContent = d.summary || "";
  $("arrival-img").hidden = !d.thumbnail;
  if (d.thumbnail) $("arrival-img").src = d.thumbnail;
  $("arrival-link").hidden = !d.url;
  if (d.url) $("arrival-link").href = d.url;
  if (!$("arrival").open) $("arrival").showModal();
}

function showDecision(s) {
  const d = $("decision");
  if (!s.decision || s.ended) {
    if (d.open) d.close();
    return;
  }
  $("decision-reason").textContent = `${s.decision.reason} (after ${km(s.decision.walk_m)})`;
  if (!d.open) {
    $("travel").hidden = true;
    d.showModal();
    map.setView([s.decision.lat, s.decision.lon], 10);
  }
}

// ---------------------------------------------------------------- refresh loop

async function refresh() {
  clearTimeout(pollTimer);
  try {
    state = await api("/api/state");
  } catch (e) {
    $("sync-status").textContent = `Server unreachable: ${e.message}`;
    pollTimer = setTimeout(refresh, 10000);
    return;
  }
  if (!state.configured) {
    openSetup();
    return;
  }
  renderStatus(state);
  renderMap(state);
  renderSidebar(state);
  renderPhoto(state);
  renderWhere(state);
  if (!state.sync.busy) {
    announceNewPlaces(state);
    showDecision(state);
  }
  pollTimer = setTimeout(refresh, state.sync.busy ? 2000 : 5 * 60 * 1000);
}

// ---------------------------------------------------------------- search helper

function wireSearch(input, button, results, onPick) {
  const run = async () => {
    const q = input.value.trim();
    if (!q) return;
    results.replaceChildren(Object.assign(document.createElement("li"), { textContent: "Searching…" }));
    try {
      const hits = await api(`/api/geocode?q=${encodeURIComponent(q)}`);
      results.replaceChildren(
        ...(hits.length ? hits : [{ label: "No results" }]).map((h) => {
          const li = document.createElement("li");
          li.textContent = h.label;
          if (h.lat !== undefined) li.onclick = () => { results.replaceChildren(); onPick(h); };
          return li;
        }),
      );
    } catch (e) {
      results.replaceChildren(Object.assign(document.createElement("li"), { textContent: e.message }));
    }
  };
  button.onclick = run;
  input.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); run(); } };
}

// ---------------------------------------------------------------- setup

let chosenHome = null;
let setupMarker = null;

function chooseHome(h) {
  chosenHome = h;
  $("home-chosen").textContent = h.label;
  $("setup-save").disabled = false;
  if (!setupMarker) setupMarker = L.marker([h.lat, h.lon]).addTo(map);
  setupMarker.setLatLng([h.lat, h.lon]);
  map.setView([h.lat, h.lon], 15);
}

function openSetup() {
  const today = new Date().toISOString().slice(0, 10);
  $("start-date").max = today;
  if (!$("start-date").value) $("start-date").value = state?.start_date || today;
  if (state?.stride_m) $("setup-stride").value = state.stride_m;
  if (state?.home && !chosenHome) chooseHome(state.home);
  $("setup").showModal();
}

wireSearch($("home-q"), $("home-search"), $("home-results"), chooseHome);

map.on("click", (e) => {
  if (state?.configured) return;
  chooseHome({ lat: e.latlng.lat, lon: e.latlng.lng, label: `${e.latlng.lat.toFixed(5)}, ${e.latlng.lng.toFixed(5)}` });
  $("setup").showModal();
});
$("setup").addEventListener("cancel", (e) => {
  if (!state?.configured) { e.preventDefault(); $("setup").close(); } // let them click the map
});

$("setup-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!chosenHome) return;
  const save = $("setup-save");
  save.disabled = true;
  save.textContent = "Saving…";
  try {
    await api("/api/setup", {
      method: "POST",
      body: {
        lat: chosenHome.lat,
        lon: chosenHome.lon,
        label: chosenHome.label,
        start_date: $("start-date").value,
        stride_m: parseFloat($("setup-stride").value),
      },
    });
  } catch (err) {
    alert(err.message);
    return;
  } finally {
    save.disabled = false;
    save.textContent = "Start walking";
  }
  $("setup").close();
  setupMarker?.remove();
  setupMarker = null;
  firstRender = true;
  lastPhotoKey = null;
  lastWhereKey = null;
  refresh();
});

$("reconfigure").onclick = () => {
  if (confirm("Changing home or start date restarts the journey from scratch. Continue?")) openSetup();
};

// ---------------------------------------------------------------- decisions

let travelMode = null;
for (const btn of document.querySelectorAll("#decision [data-choice]")) {
  btn.onclick = async () => {
    const choice = btn.dataset.choice;
    if (choice === "ferry" || choice === "flight") {
      travelMode = choice;
      $("travel-title").textContent = choice === "ferry" ? "Ferry to…" : "Fly to…";
      $("travel").hidden = false;
      $("travel-q").focus();
      return;
    }
    if (choice === "end" && !confirm("End the journey for good?")) return;
    await decide({ choice });
  };
}
wireSearch($("travel-q"), $("travel-search"), $("travel-results"), (h) => {
  const short = h.label.split(",").slice(0, 2).join(",");
  if (confirm(`${travelMode === "ferry" ? "Ferry" : "Fly"} to ${short}?`)) {
    decide({ choice: travelMode, lat: h.lat, lon: h.lon, label: short });
  }
});
$("decision").addEventListener("cancel", (e) => e.preventDefault());

async function decide(body) {
  try {
    await api("/api/decision", { method: "POST", body });
  } catch (e) {
    alert(e.message);
    return;
  }
  $("decision").close();
  refresh();
}

// ---------------------------------------------------------------- CSV import

async function importFiles(files, out = $("import-result")) {
  out.classList.remove("error");
  const lines = [];
  for (const file of files) {
    try {
      const r = await api("/api/import", { method: "POST", body: { name: file.name, text: await file.text() } });
      lines.push(`${file.name}: ${r.days} days (${r.first} → ${r.last})`);
    } catch (e) {
      out.classList.add("error");
      lines.push(e.message);
    }
  }
  out.textContent = lines.join(" · ");
  if (state?.configured) refresh();
}

const dropzone = $("dropzone");
$("setup-csv").onchange = (e) => importFiles([...e.target.files], $("setup-import-result"));
$("csv-files").onchange = (e) => { importFiles([...e.target.files]); e.target.value = ""; };
dropzone.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.classList.add("over"); });
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("over"));
dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.classList.remove("over");
  importFiles([...e.dataTransfer.files]);
});

// ---------------------------------------------------------------- misc

$("sync-btn").onclick = async () => {
  await api("/api/sync?force=true", { method: "POST" });
  refresh();
};

$("stride-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  await api("/api/stride", { method: "POST", body: { stride_m: parseFloat($("stride").value) } });
  refresh();
});

refresh();

// ---------------------------------------------------------------- map buttons

const MapButtons = L.Control.extend({
  options: { position: "topleft" },
  onAdd() {
    const box = L.DomUtil.create("div", "leaflet-bar map-buttons");
    const add = (label, title, fn) => {
      const a = L.DomUtil.create("a", "", box);
      a.href = "#";
      a.textContent = label;
      a.title = title;
      L.DomEvent.on(a, "click", (e) => { L.DomEvent.preventDefault(e); fn(); });
    };
    add("◎", "Where I am", () => state && map.setView([state.position.lat, state.position.lon], 12));
    add("⤢", "Whole journey", () => {
      if (!state) return;
      const pts = [[state.home.lat, state.home.lon], [state.position.lat, state.position.lon]];
      for (const seg of [...state.walked, ...state.ahead]) pts.push(...seg.path);
      map.fitBounds(L.latLngBounds(pts).pad(0.05));
    });
    L.DomEvent.disableClickPropagation(box);
    return box;
  },
});
new MapButtons().addTo(map);
