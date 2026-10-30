"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

const TYPE_LABELS = { remote: "Remote", full_time: "Full-time", part_time: "Part-time", contract: "Contract" };
const SHIFT_LABELS = { morning: "morning", day: "day", evening: "evening", night: "night", rotating: "rotating", weekend: "weekend" };
const QUICK_PICKS = ["London", "New York", "Berlin", "Bengaluru", "Toronto", "Europe"];
const MAX_CV_BYTES = 10 * 1024 * 1024;
const DEFAULT_SUMMARY = "Fill in the steps above. Nothing is sent anywhere until you save.";

const CURRENCIES = ["USD", "EUR", "GBP", "INR", "CAD", "AUD", "NZD", "CHF", "SEK", "NOK", "DKK", "PLN", "CZK", "HUF",
  "RON", "TRY", "ILS", "AED", "SAR", "QAR", "EGP", "NGN", "KES", "GHS", "ZAR", "PKR", "BDT", "LKR", "SGD", "MYR",
  "IDR", "PHP", "THB", "VND", "JPY", "KRW", "CNY", "HKD", "TWD", "MXN", "BRL", "ARS", "CLP", "COP", "PEN", "UYU"];
const EURO = "AT BE HR EE FI FR DE GR IE IT LV LT LU NL PT SK SI ES".split(" ");
const COUNTRY_CURRENCY = {
  US: "USD", GB: "GBP", IN: "INR", CA: "CAD", AU: "AUD", NZ: "NZD", CH: "CHF", SE: "SEK", NO: "NOK", DK: "DKK",
  PL: "PLN", CZ: "CZK", HU: "HUF", RO: "RON", TR: "TRY", IL: "ILS", AE: "AED", SA: "SAR", QA: "QAR", EG: "EGP",
  NG: "NGN", KE: "KES", GH: "GHS", ZA: "ZAR", PK: "PKR", BD: "BDT", LK: "LKR", SG: "SGD", MY: "MYR", ID: "IDR",
  PH: "PHP", TH: "THB", VN: "VND", JP: "JPY", KR: "KRW", CN: "CNY", HK: "HKD", TW: "TWD", MX: "MXN", BR: "BRL",
  AR: "ARS", CL: "CLP", CO: "COP", PE: "PEN", UY: "UYU", ...Object.fromEntries(EURO.map((c) => [c, "EUR"])),
};

const state = {
  worldwide: false,
  locations: [],          // {name, type, detail, code}
  jobTypes: new Set(),
  schedule: null,
  shifts: new Set(),
  cv: null,               // {filename, stored_as, size, contact}
  authCountries: [],      // countries the person may already work in
  sponsorship: null,
  notice: null,
  currencyTouched: false, // once the person picks a currency, stop guessing it from locations
  mode: "cv",             // "cv" = match to CV, "title" = title & location only
  ai: null,               // last /api/ai/status answer
  results: [],            // jobs currently listed
  relatedTitles: [],      // extra titles searched alongside the typed one
  titleIdeas: [],         // suggestions not (yet) chosen
  titleBase: "",          // the typed title the current suggestions belong to
};

const TOKEN_KEY = "hfToken";
const storedToken = () => { try { return localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; } };
const hfHeaders = () => (storedToken() ? { "X-HF-Token": storedToken() } : {});

let LOCATIONS = [];

/* ---------- helpers ---------- */

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fold = (s) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
const sameLoc = (a, b) => a.name === b.name && a.type === b.type;
const fmtSize = (n) => (n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`);
const digits = (s) => (s.replace(/\D/g, "") ? Number(s.replace(/\D/g, "")) : null);

function listJoin(items) {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

function toast(message, bad = false) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.toggle("bad", bad);
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (el.hidden = true), 5000);
}

function setError(id, message) {
  $(`#${id}`).textContent = message || "";
}

const LOC_ICONS = {
  city: '<svg class="ico" viewBox="0 0 24 24"><path d="M4 20V9l5-3v14M9 20V4l7 3v13M16 20v-8l4 2v6M3 20h18"/></svg>',
  region: '<svg class="ico" viewBox="0 0 24 24"><path d="M3 6l6-2 6 2 6-2v14l-6 2-6-2-6 2z"/><path d="M9 4v14M15 6v14"/></svg>',
  country: '<svg class="ico" viewBox="0 0 24 24"><path d="M5 21V4M5 4h11l-2 4 2 4H5"/></svg>',
  custom: '<svg class="ico" viewBox="0 0 24 24"><path d="M12 21s-6.5-6-6.5-11a6.5 6.5 0 0 1 13 0c0 5-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.3"/></svg>',
};

/* ---------- reusable place picker (search box + chips + suggestions) ---------- */

function makePicker({ prefix, combo, input, list, chips, selected, types, allowCustom, showKind, onChange }) {
  let activeIndex = -1;
  let options = [];
  const GROUP_LABELS = { city: "Cities", region: "Regions", country: "Countries" };

  function renderChips() {
    chips.innerHTML = selected.map((l, i) => `
      <li class="chip">${esc(l.name)}${showKind && l.type !== "custom" ? `<span class="kind">${esc(l.type)}</span>` : ""}
        <button type="button" data-remove="${i}" aria-label="Remove ${esc(l.name)}">
          <svg class="ico" viewBox="0 0 24 24" style="width:14px;height:14px"><path d="M6 6l12 12M18 6 6 18"/></svg>
        </button></li>`).join("");
  }

  function add(loc) {
    if (!selected.some((l) => sameLoc(l, loc))) selected.push(loc);
    input.value = "";
    combo.classList.remove("invalid");
    renderChips();
    close();
    onChange();
  }

  function rank(loc, q) {
    const name = fold(loc.name);
    if (name === q) return 0;
    if (name.startsWith(q)) return 1;
    if (name.split(/[\s-]+/).some((w) => w.startsWith(q))) return 2;
    if (fold(loc.detail || "").startsWith(q)) return 3;  // "germany" also suggests German cities
    if (name.includes(q)) return 4;
    return -1;
  }

  function highlight(text, q) {
    const i = fold(text).indexOf(q);
    if (i < 0 || !q) return esc(text);
    return esc(text.slice(0, i)) + "<mark>" + esc(text.slice(i, i + q.length)) + "</mark>" + esc(text.slice(i + q.length));
  }

  function open() {
    const raw = input.value.trim();
    const q = fold(raw);
    if (!q) return close();

    const scored = LOCATIONS
      .filter((l) => types.includes(l.type) && !selected.some((s) => sameLoc(s, l)))
      .map((l) => [rank(l, q), l])
      .filter(([r]) => r >= 0)
      .sort((a, b) => a[0] - b[0] || a[1].name.localeCompare(b[1].name));

    const groups = Object.fromEntries(types.map((t) => [t, []]));
    for (const [, l] of scored) if (groups[l.type].length < 6) groups[l.type].push(l);

    options = [];
    let html = "";
    for (const type of types) {
      if (!groups[type].length) continue;
      if (types.length > 1) html += `<li class="group" role="presentation">${GROUP_LABELS[type]}</li>`;
      for (const l of groups[type]) {
        const i = options.push(l) - 1;
        html += `<li role="option" id="${prefix}-opt-${i}" data-index="${i}" aria-selected="false">
          <span class="loc-ico">${LOC_ICONS[l.type]}</span><span>${highlight(l.name, q)}</span>
          <span class="sub">${esc(l.type === "country" ? "Country" : l.detail || "")}</span></li>`;
      }
    }
    // Anywhere not in the list (a smaller town, say) can still be added as typed.
    if (allowCustom && !scored.some(([r]) => r === 0) && raw.length >= 2) {
      const i = options.push({ name: raw, type: "custom", detail: null, code: null }) - 1;
      html += `<li class="group" role="presentation">Not listed?</li>
        <li role="option" id="${prefix}-opt-${i}" data-index="${i}" aria-selected="false">
          <span class="loc-ico">${LOC_ICONS.custom}</span><span>Add “${esc(raw)}”</span></li>`;
    }

    list.innerHTML = html || `<li class="empty">No matches yet. Keep typing.</li>`;
    list.style.top = `${combo.offsetHeight + 8}px`;  // chips can wrap the box onto several lines
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
    setActive(options.length ? 0 : -1);
  }

  function close() {
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    activeIndex = -1;
  }

  function setActive(i) {
    activeIndex = i;
    list.querySelectorAll("[role=option]").forEach((o) => o.setAttribute("aria-selected", String(Number(o.dataset.index) === i)));
    if (i >= 0) {
      input.setAttribute("aria-activedescendant", `${prefix}-opt-${i}`);
      document.getElementById(`${prefix}-opt-${i}`)?.scrollIntoView({ block: "nearest" });
    }
  }

  input.addEventListener("input", open);
  input.addEventListener("focus", () => input.value && open());
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (list.hidden) return open();
      const n = options.length;
      if (n) setActive((activeIndex + (e.key === "ArrowDown" ? 1 : -1) + n) % n);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (!list.hidden && activeIndex >= 0) add(options[activeIndex]);
    } else if (e.key === "Escape") {
      close();
    } else if (e.key === "Backspace" && !input.value && selected.length) {
      selected.pop();
      renderChips();
      onChange();
    }
  });
  list.addEventListener("mousedown", (e) => e.preventDefault()); // keep focus in the input
  list.addEventListener("click", (e) => {
    const opt = e.target.closest("[role=option]");
    if (opt) add(options[Number(opt.dataset.index)]);
  });
  chips.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-remove]");
    if (!btn) return;
    selected.splice(Number(btn.dataset.remove), 1);
    renderChips();
    onChange();
    input.focus();
  });
  combo.addEventListener("click", () => input.focus());
  document.addEventListener("click", (e) => { if (!combo.parentElement.contains(e.target)) close(); });

  return { add, close, renderChips };
}

/* ---------- step 1: job title ---------- */

const titleInput = $("#job-title");
titleInput.addEventListener("input", () => {
  titleInput.removeAttribute("aria-invalid");
  setError("err-title");
  refresh();
  clearTimeout(titleInput.timer);
  titleInput.timer = setTimeout(loadTitleIdeas, 350);
});

/* related titles: suggested as you type, searched together with the typed title */

const PRESELECT = 4;
const sameTitle = (a, b) => a.trim().toLowerCase() === b.trim().toLowerCase();

async function loadTitleIdeas({ keepPicked = false } = {}) {
  const typed = titleInput.value.trim();
  if (typed.length < 3) {
    $("#title-ideas").hidden = true;
    return;
  }
  let suggestions = [];
  try {
    const res = await fetch(`/api/titles/suggest?q=${encodeURIComponent(typed)}`);
    suggestions = (await res.json()).suggestions || [];
  } catch {
    return;
  }
  if (!sameTitle(typed, titleInput.value)) return;  // the person kept typing
  const newBase = !sameTitle(typed, state.titleBase);
  if (newBase && !keepPicked) {
    // A different title: start over, pre-picking the closest few so the search is composite by default.
    // One seniority step ("Senior …") plus the best related roles; the rest stay one tap away.
    const seniority = suggestions.filter((s) => s.kind === "seniority").slice(0, 1);
    const related = suggestions.filter((s) => s.kind !== "seniority").slice(0, PRESELECT - seniority.length);
    state.relatedTitles.length = 0;
    state.relatedTitles.push(...[...seniority, ...related].map((s) => s.title));
  }
  state.titleBase = typed;
  state.titleIdeas = suggestions.filter((s) => !state.relatedTitles.some((t) => sameTitle(t, s.title)));
  renderTitleIdeas(suggestions);
}

function renderTitleIdeas(suggestions = []) {
  const counts = Object.fromEntries(suggestions.map((s) => [s.title.toLowerCase(), s.jobs]));
  const n = (t) => {
    const c = counts[t.toLowerCase()];
    return c ? `<span class="n">${c} open</span>` : "";
  };
  $("#title-picked").innerHTML = state.relatedTitles.map((t, i) =>
    `<button type="button" class="title-chip" data-unpick="${i}" aria-label="Stop searching for ${esc(t)}">
      ${esc(t)} ${n(t)}<span class="x" aria-hidden="true">×</span></button>`).join("");
  $("#title-more").innerHTML = state.titleIdeas.map((s, i) =>
    `<button type="button" class="title-chip idea" data-pick="${i}" aria-label="Also search for ${esc(s.title)}">
      + ${esc(s.title)} ${n(s.title)}</button>`).join("");
  $("#title-ideas").hidden = false;
  renderTitleIdeas.last = suggestions;
  refresh();
}

$("#title-picked").addEventListener("click", (e) => {
  const chip = e.target.closest("[data-unpick]");
  if (!chip) return;
  const [title] = state.relatedTitles.splice(Number(chip.dataset.unpick), 1);
  const known = (renderTitleIdeas.last || []).find((s) => sameTitle(s.title, title));
  state.titleIdeas.unshift(known || { title, jobs: 0 });
  renderTitleIdeas(renderTitleIdeas.last);
});
$("#title-more").addEventListener("click", (e) => {
  const chip = e.target.closest("[data-pick]");
  if (!chip) return;
  const [idea] = state.titleIdeas.splice(Number(chip.dataset.pick), 1);
  if (state.relatedTitles.length < 19) state.relatedTitles.push(idea.title);
  renderTitleIdeas(renderTitleIdeas.last);
});
$("#title-add").addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  e.preventDefault();
  const t = e.target.value.trim().replace(/\s+/g, " ");
  if (t.length >= 2 && !sameTitle(t, titleInput.value) && !state.relatedTitles.some((x) => sameTitle(x, t))) {
    state.relatedTitles.push(t.slice(0, 120));
    state.titleIdeas = state.titleIdeas.filter((s) => !sameTitle(s.title, t));
  }
  e.target.value = "";
  renderTitleIdeas(renderTitleIdeas.last);
});

/* ---------- step 2: location ---------- */

const locPicker = makePicker({
  prefix: "loc",
  combo: $("#combo"), input: $("#loc-input"), list: $("#loc-list"), chips: $("#loc-chips"),
  selected: state.locations,
  types: ["city", "region", "country"],
  allowCustom: true,
  showKind: true,
  onChange: () => {
    setError("err-where");
    renderQuickPicks();
    renderAuthQuick();
    guessCurrency();
    refresh();
  },
});

function setScope(worldwide) {
  state.worldwide = worldwide;
  $$(".segmented button").forEach((b) =>
    b.setAttribute("aria-checked", String((b.dataset.scope === "worldwide") === worldwide)));
  $("#places").hidden = worldwide;
  $("#worldwide-note").hidden = !worldwide;
  setError("err-where");
  $("#combo").classList.remove("invalid");
  locPicker.close();
  refresh();
}
$$(".segmented button").forEach((b) => b.addEventListener("click", () => setScope(b.dataset.scope === "worldwide")));

function renderQuickPicks() {
  const picks = QUICK_PICKS
    .map((name) => LOCATIONS.find((l) => l.name === name))
    .filter((l) => l && !state.locations.some((s) => sameLoc(s, l)));
  $("#quick-locs").innerHTML = picks.length
    ? `<span class="label">Popular:</span>` + picks.map((l) =>
        `<button type="button" data-quick="${esc(l.name)}">+ ${esc(l.name)}</button>`).join("")
    : "";
}
$("#quick-locs").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-quick]");
  if (btn) locPicker.add(LOCATIONS.find((l) => l.name === btn.dataset.quick));
});

/* ---------- step 3: job type ---------- */

$$("[data-type]").forEach((pill) => pill.addEventListener("click", () => {
  const t = pill.dataset.type;
  state.jobTypes.has(t) ? state.jobTypes.delete(t) : state.jobTypes.add(t);
  pill.setAttribute("aria-pressed", String(state.jobTypes.has(t)));
  setError("err-type");
  refresh();
}));

/* ---------- step 4: schedule ---------- */

function setSchedule(value) {
  state.schedule = value;
  $$("[data-schedule]").forEach((c) => c.setAttribute("aria-checked", String(c.dataset.schedule === value)));
  $("#shifts").hidden = value !== "fixed";
  setError("err-schedule");
  refresh();
}
$$("[data-schedule]").forEach((c) => c.addEventListener("click", () => setSchedule(c.dataset.schedule)));

$$("[data-shift]").forEach((pill) => pill.addEventListener("click", () => {
  const s = pill.dataset.shift;
  state.shifts.has(s) ? state.shifts.delete(s) : state.shifts.add(s);
  pill.setAttribute("aria-pressed", String(state.shifts.has(s)));
  refresh();
}));

/* ---------- step 5: CV ---------- */

const dropzone = $("#dropzone");
const cvInput = $("#cv-input");

function showCV(cv, { uploading = false } = {}) {
  dropzone.hidden = !!cv;
  $("#cv-file").hidden = !cv;
  $("#contact").hidden = !cv || uploading;
  if (!cv) return;
  $("#cv-name").textContent = cv.filename;
  $("#cv-size").textContent = uploading ? "Uploading…" : `${fmtSize(cv.size)} · ready`;
  $("#cv-progress").hidden = !uploading;
}

function fillContact(contact = {}) {
  for (const key of ["name", "email", "phone", "linkedin"]) {
    const input = $(`#c-${key}`);
    if (!input.value || input.dataset.fromCv === "1") {  // never overwrite what the person typed
      input.value = contact[key] || "";
      input.dataset.fromCv = "1";
    }
  }
}
$$("#contact input").forEach((i) => i.addEventListener("input", () => (i.dataset.fromCv = "0")));

function uploadCV(file) {
  setError("err-cv");
  dropzone.classList.remove("invalid");
  if (!file) return;
  if (!/\.(pdf|docx?|rtf|txt)$/i.test(file.name)) {
    return setError("err-cv", "That file type won't work. Try a PDF or Word document.");
  }
  if (file.size > MAX_CV_BYTES) {
    return setError("err-cv", "That file is over 10 MB. Try a smaller export of your CV.");
  }

  const previous = state.cv;
  showCV({ filename: file.name, size: file.size }, { uploading: true });
  const bar = $("#cv-progress span");
  bar.style.width = "0";

  const body = new FormData();
  body.append("file", file);
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/cv");
  xhr.upload.onprogress = (e) => { if (e.lengthComputable) bar.style.width = `${(e.loaded / e.total) * 100}%`; };
  xhr.onload = () => {
    let data = {};
    try { data = JSON.parse(xhr.responseText); } catch { /* handled below */ }
    if (xhr.status >= 200 && xhr.status < 300) {
      state.cv = data;
      showCV(data);
      fillContact(data.contact);
      const found = ["name", "email", "phone"].filter((k) => data.contact?.[k]).length;
      toast(found ? "CV attached. We filled in what we could find." : "CV attached. Add your contact details below.");
    } else {
      state.cv = previous;
      showCV(previous);
      setError("err-cv", data.detail || "We couldn't upload that file. Please try again.");
    }
    refresh();
  };
  xhr.onerror = () => {
    state.cv = previous;
    showCV(previous);
    setError("err-cv", "Upload failed. Is the app's server still running?");
  };
  xhr.send(body);
}

cvInput.addEventListener("change", () => { uploadCV(cvInput.files[0]); cvInput.value = ""; });
["dragenter", "dragover"].forEach((ev) => dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.remove("over"); }));
dropzone.addEventListener("drop", (e) => uploadCV(e.dataTransfer.files[0]));
$("#cv-replace").addEventListener("click", () => cvInput.click());
$("#cv-remove").addEventListener("click", () => {
  state.cv = null;
  showCV(null);
  refresh();
});

/* ---------- step 6: optional details ---------- */

const authPicker = makePicker({
  prefix: "auth",
  combo: $("#auth-combo"), input: $("#auth-input"), list: $("#auth-list"), chips: $("#auth-chips"),
  selected: state.authCountries,
  types: ["country"],
  allowCustom: false,
  showKind: false,
  onChange: () => { renderAuthQuick(); refresh(); },
});

/** One-click suggestions: the countries of the places chosen in step 2. */
function renderAuthQuick() {
  const codes = [...new Set(state.locations.map((l) => l.code).filter(Boolean))];
  const picks = codes
    .map((code) => LOCATIONS.find((l) => l.type === "country" && l.code === code))
    .filter((c) => c && !state.authCountries.some((a) => sameLoc(a, c)));
  $("#auth-quick").innerHTML = picks.length
    ? `<span class="label">From your locations:</span>` + picks.map((c) =>
        `<button type="button" data-auth="${esc(c.code)}">+ ${esc(c.name)}</button>`).join("")
    : "";
}
$("#auth-quick").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-auth]");
  if (btn) authPicker.add(LOCATIONS.find((l) => l.type === "country" && l.code === btn.dataset.auth));
});

/** Radio pills that can also be un-picked, since every question here is optional. */
function optionalRadio(attr, key, after = () => {}) {
  $$(`[data-${attr}]`).forEach((pill) => pill.addEventListener("click", () => {
    const value = pill.dataset[attr];
    state[key] = state[key] === value ? null : value;
    setRadio(attr, state[key]);
    after();
    refresh();
  }));
}
function setRadio(attr, value) {
  $$(`[data-${attr}]`).forEach((p) => p.setAttribute("aria-checked", String(p.dataset[attr] === value)));
}

optionalRadio("sponsor", "sponsorship");
optionalRadio("notice", "notice", () => {
  const picking = state.notice === "date";
  $("#start-date-wrap").hidden = !picking;
  setError("err-notice");
  if (picking) $("#start-date").focus();
});
$("#start-date").min = new Date().toISOString().slice(0, 10);
$("#start-date").addEventListener("change", () => { setError("err-notice"); refresh(); });

const currencySelect = $("#sal-currency");
currencySelect.innerHTML = CURRENCIES.map((c) => `<option value="${c}">${c}</option>`).join("");
currencySelect.addEventListener("change", () => { state.currencyTouched = true; });

function guessCurrency() {
  if (state.currencyTouched) return;
  const code = state.locations.find((l) => COUNTRY_CURRENCY[l.code])?.code;
  if (code) currencySelect.value = COUNTRY_CURRENCY[code];
}

function formatAmount(input) {
  const n = digits(input.value);
  input.value = n === null ? "" : n.toLocaleString();
}
for (const id of ["#sal-min", "#sal-max"]) {
  const input = $(id);
  input.addEventListener("input", () => { setError("err-salary"); refresh(); });
  input.addEventListener("blur", () => formatAmount(input));
}

const yearsInput = $("#years");
function setYears(n) {
  yearsInput.value = n === null ? "" : String(Math.max(0, Math.min(60, n)));
  $("#years-note").textContent = n === null ? "Leave blank to skip" : Number(yearsInput.value) === 1 ? "year" : "years";
  refresh();
}
yearsInput.addEventListener("input", () => setYears(digits(yearsInput.value)));
$("#yrs-up").addEventListener("click", () => setYears((digits(yearsInput.value) ?? -1) + 1));
$("#yrs-down").addEventListener("click", () => {
  const n = digits(yearsInput.value);
  setYears(n === null || n === 0 ? null : n - 1);
});

function collectDetails() {
  const min = digits($("#sal-min").value);
  const max = digits($("#sal-max").value);
  return {
    authorized_countries: state.authCountries,
    needs_sponsorship: state.sponsorship,
    salary: min === null && max === null ? null
      : { currency: currencySelect.value, min, max, period: $("#sal-period").value },
    notice: state.notice,
    start_date: state.notice === "date" ? $("#start-date").value || null : null,
    years_experience: digits(yearsInput.value),
  };
}

/* ---------- step 7: how to search ---------- */

function setMode(mode) {
  state.mode = mode;
  $$("[data-mode]").forEach((c) => c.setAttribute("aria-checked", String(c.dataset.mode === mode)));
  $("#ai-panel").hidden = mode !== "cv";
  if (mode === "cv" && !state.ai) loadAIStatus();
}
$$("[data-mode]").forEach((c) => c.addEventListener("click", () => setMode(c.dataset.mode)));

async function loadAIStatus() {
  const box = $("#ai-status");
  const text = $("#ai-status-text");
  box.className = "ai-status";
  text.textContent = "Checking Hugging Face…";
  let s;
  try {
    s = await (await fetch("/api/ai/status", { headers: hfHeaders() })).json();
  } catch {
    text.textContent = "Couldn't reach the app's server.";
    return;
  }
  state.ai = s;
  const model = s.llm.split("/").pop();
  const hasToken = s.token_origin !== "none" && !s.token_error;
  const spaceReady = s.space?.exists && s.space.stage === "RUNNING";

  $("#token-form").hidden = hasToken;
  $("#token-change").hidden = !hasToken || s.token_origin === "yours";
  $("#token-forget").hidden = !storedToken();
  $("#space-setup").hidden = !(hasToken && s.is_local && s.account?.pro && !s.space?.exists);

  if (s.token_error) {
    box.classList.add("warn");
    text.innerHTML = `${esc(s.token_error)}<span class="sub">Until then we'll use free keyword matching.</span>`;
  } else if (!hasToken) {
    box.classList.add("warn");
    text.innerHTML = `No Hugging Face token yet<span class="sub">Add one below to switch on AI matching. Without it, we use free keyword matching.</span>`;
  } else if (s.space?.exists) {
    box.classList.add("ok");
    const who = `${esc(s.account.name)}${s.account.pro ? " (PRO)" : ""}`;
    const stage = spaceReady ? "ready" : s.space.stage === "SLEEPING" ? "asleep, wakes up on your first search"
      : esc(String(s.space.stage || "starting").toLowerCase().replace(/_/g, " "));
    const via = s.token_origin === "yours" ? "your token" : esc(s.token_origin);
    text.innerHTML = `${esc(model)} on ZeroGPU: ${stage}<span class="sub">Signed in as ${who} via ${via}. Searches use ${esc(s.account.name)}'s own free ZeroGPU time.</span>`;
  } else {
    box.classList.add("warn");
    text.innerHTML = `Signed in as ${esc(s.account.name)}, but no ZeroGPU Space is set up yet<span class="sub">${
      s.account.pro ? "Set one up below (needs PRO, which you have)." : "Hosting one needs PRO. Tick the Inference backup, or we'll use keyword matching."}</span>`;
  }
}

$("#token-save").addEventListener("click", () => {
  const value = $("#hf-token").value.trim();
  if (!/^hf_[A-Za-z0-9]{20,}$/.test(value)) {
    toast("That doesn't look like a Hugging Face token (they start with hf_).", true);
    return;
  }
  try { localStorage.setItem(TOKEN_KEY, value); } catch { /* private window: works for this page only */ }
  $("#hf-token").value = "";
  state.ai = null;
  loadAIStatus();
});
$("#token-change").addEventListener("click", () => { $("#token-form").hidden = false; $("#hf-token").focus(); });
$("#token-forget").addEventListener("click", () => {
  try { localStorage.removeItem(TOKEN_KEY); } catch { /* nothing stored */ }
  state.ai = null;
  loadAIStatus();
  toast("Token removed from this browser.");
});
$("#space-setup").addEventListener("click", async () => {
  const btn = $("#space-setup");
  btn.disabled = true;
  btn.textContent = "Setting up… this takes a minute";
  try {
    const res = await fetch("/api/ai/space", { method: "POST", headers: hfHeaders() });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Setup failed.");
    toast("Your ZeroGPU Space is building. It'll be ready in a few minutes.");
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "Set up my ZeroGPU Space";
    state.ai = null;
    loadAIStatus();
  }
});

/* ---------- results ---------- */

const TYPE_TEXT = { full_time: "Full-time", part_time: "Part-time", contract: "Contract", internship: "Internship", temporary: "Temporary" };

function ago(iso) {
  if (!iso) return "";
  const days = Math.round((Date.now() - new Date(iso).getTime()) / 86400000);
  if (Number.isNaN(days) || days < 0) return "";
  return days === 0 ? "today" : days === 1 ? "yesterday" : days < 30 ? `${days} days ago` : `${Math.round(days / 30)} mo ago`;
}

function fitLabel(job) {
  if (job.method === "title") return "Title match";
  const s = job.score;
  return s >= 80 ? "Great fit" : s >= 60 ? "Good fit" : s >= 40 ? "Worth a look" : "Long shot";
}

function renderJobs(task) {
  const list = $("#job-list");
  const jobs = task.results || [];
  const b = task.backend || {};
  const how = { zerogpu: `ranked by ${state.ai?.llm?.split("/").pop() || "AI"} on ZeroGPU`,
                inference: "ranked by AI via HF Inference", keywords: "ranked by keywords from your CV",
                title: "ranked by title" }[b.method] || "";
  const m = task.meta || {};
  $("#results-meta").textContent =
    `${(m.total_jobs || 0).toLocaleString()} current jobs from ${m.sources || 0} sites in the directory · ` +
    `${(m.in_scope || 0).toLocaleString()} fit your location and job type · ${how}`;
  $("#backend-note").hidden = !b.note;
  $("#backend-note").textContent = b.note || "";

  if (!jobs.length) {
    list.innerHTML = `<li class="empty-state">No matching jobs right now. Try more locations, Worldwide, or a broader title.</li>`;
    return;
  }
  state.results = jobs;
  list.innerHTML = jobs.map((j, i) => {
    const tier = j.score >= 70 ? "" : j.score >= 45 ? "mid" : "low";
    const meta = [j.company, j.location, j.remote ? "Remote" : "", TYPE_TEXT[j.job_type] || "", ago(j.posted)]
      .filter(Boolean).map(esc).join(" · ");
    const gaps = (j.missing || []).length ? `<p class="job-gaps">Worth brushing up: ${j.missing.map(esc).join(", ")}</p>` : "";
    return `<li class="job" style="animation-delay:${Math.min(i, 12) * 40}ms">
      <div><div class="score ${tier}" style="--p:${j.score}"><b>${j.score}</b></div><span class="fit">${fitLabel(j)}</span></div>
      <div>
        <h3><a href="${esc(j.url)}" data-open-job="${i}">${esc(j.title)}</a></h3>
        <p class="job-meta">${meta}</p>
        ${j.matched_title && !sameTitle(j.matched_title, titleInput.value)
          ? `<p class="job-meta"><span class="badge matched">via “${esc(j.matched_title)}”</span></p>` : ""}
        ${j.reason ? `<p class="job-reason">${esc(j.reason)}</p>` : ""}
        ${gaps}
      </div>
      <div class="job-side">
        ${j.reviewed ? `<span class="badge">AI reviewed</span>` : ""}
        <span class="source">via ${esc(j.source)}</span>
        <button type="button" class="secondary" data-open-job="${i}">View job</button>
      </div>
    </li>`;
  }).join("");
}

async function startSearch(refreshJobs = false) {
  const section = $("#results");
  section.hidden = false;
  $("#search-progress").hidden = false;
  $("#backend-note").hidden = true;
  $("#job-list").innerHTML = "";
  $("#refresh-jobs").disabled = true;
  section.scrollIntoView({ behavior: "smooth", block: "start" });

  const bar = $("#search-progress .progress span");
  try {
    const res = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...hfHeaders() },
      body: JSON.stringify({ refresh: refreshJobs }),
    });
    const { id, detail } = await res.json();
    if (!res.ok) throw new Error(detail || "Couldn't start the search.");
    for (;;) {
      await new Promise((r) => setTimeout(r, 900));
      const task = await (await fetch(`/api/search/${id}`)).json();
      $("#search-message").textContent = task.message || "Working…";
      bar.style.width = `${Math.round((task.progress || 0) * 100)}%`;
      if (task.stage === "done") { renderJobs(task); break; }
      if (task.stage === "error") throw new Error(task.error);
    }
  } catch (err) {
    $("#job-list").innerHTML = `<li class="empty-state">${esc(err.message || "Search failed.")}</li>`;
  } finally {
    $("#search-progress").hidden = true;
    $("#refresh-jobs").disabled = false;
  }
}
$("#refresh-jobs").addEventListener("click", () => startSearch(true));

/* ---------- apply sheet: full job post, tailored documents, application page ---------- */

const sheet = $("#apply-dialog");
const ap = { job: null, details: null, engine: "zerogpu", formats: new Set(["pdf", "docx"]), busy: false };

$("#job-list").addEventListener("click", (e) => {
  const trigger = e.target.closest("[data-open-job]");
  if (!trigger) return;
  e.preventDefault();
  openApply(state.results[Number(trigger.dataset.openJob)]);
});

function resetApplySheet() {
  $("#ap-setup").hidden = false;
  $("#ap-progress").hidden = true;
  $("#ap-result").hidden = true;
  $("#ap-blocked").hidden = true;
  $("#ap-error").textContent = "";
  $("#ap-go").hidden = false;
  $("#ap-go").disabled = false;
}

async function openApply(job) {
  ap.job = job;
  ap.details = null;
  resetApplySheet();
  $("#ap-title").textContent = job.title;
  $("#ap-meta").textContent = [job.company, job.location, `via ${job.source}`].filter(Boolean).join(" · ");
  $("#ap-open").href = job.url;
  $("#ap-open").textContent = "Open job page ↗";
  $("#ap-desc").textContent = "Fetching the full job post…";
  $("#ap-desc-note").hidden = true;
  sheet.showModal();
  if (!state.ai) await loadAIStatus();
  describeEngines();

  try {
    const res = await fetch("/api/job/details", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: job.url, title: job.title, company: job.company,
                             location: job.location, description: job.description || "" }),
    });
    const d = await res.json();
    if (!res.ok) throw new Error(d.detail || "Couldn't load the job post.");
    if (ap.job !== job) return;  // the person already opened another job
    ap.details = d;
    $("#ap-desc").textContent = d.description || "This listing has no description text.";
    $("#ap-desc-note").hidden = !d.note;
    $("#ap-desc-note").textContent = d.note || "";
    if (d.company && !job.company) $("#ap-meta").textContent = [d.company, d.location, `via ${job.source}`].filter(Boolean).join(" · ");
    $("#ap-open").href = d.apply_url || job.url;
    if (d.apply_url && d.apply_url !== job.url) $("#ap-open").textContent = "Open application page ↗";
  } catch (err) {
    $("#ap-desc").textContent = job.description || "";
    $("#ap-desc-note").hidden = false;
    $("#ap-desc-note").textContent = err.message;
  }
}

function describeEngines() {
  const s = state.ai || {};
  const writer = s.writer || {};
  const model = (s.writer_model || "").split("/").pop();
  $("#ap-engine-free").textContent = writer.exists
    ? `${model} on your daily ZeroGPU time${writer.stage === "RUNNING" ? "" : " (starting up)"}`
    : "Not available: no writer Space set up";
  $("#ap-engine-paid").textContent = `${(s.inference_model || "").split("/").pop()} · your own HF credits, about 1–2¢`;
  const hasToken = s.token_origin && s.token_origin !== "none" && !s.token_error;
  $("#ap-who").textContent = hasToken
    ? `Billed to ${s.account?.name}'s Hugging Face account only (${s.token_origin === "yours" ? "your token" : s.token_origin}).`
    : "No Hugging Face token: we'll reformat your CV for ATS as it is, without tailoring or a cover letter. Add a token in step 7 for tailored documents.";
  if (!writer.exists && ap.engine === "zerogpu" && hasToken) setEngine("inference");
}

function setEngine(engine) {
  ap.engine = engine;
  $$("[data-engine]").forEach((c) => c.setAttribute("aria-checked", String(c.dataset.engine === engine)));
}
$$("[data-engine]").forEach((c) => c.addEventListener("click", () => setEngine(c.dataset.engine)));
$$("[data-format]").forEach((p) => p.addEventListener("click", () => {
  const f = p.dataset.format;
  if (ap.formats.has(f) && ap.formats.size === 1) return;  // keep at least one format
  ap.formats.has(f) ? ap.formats.delete(f) : ap.formats.add(f);
  p.setAttribute("aria-pressed", String(ap.formats.has(f)));
}));
$("#ap-close").addEventListener("click", () => sheet.close());
sheet.addEventListener("click", (e) => { if (e.target === sheet) sheet.close(); });  // click on the backdrop

$("#ap-go").addEventListener("click", async () => {
  if (ap.busy || !ap.job) return;
  ap.busy = true;
  $("#ap-error").textContent = "";
  $("#ap-setup").hidden = true;
  $("#ap-progress").hidden = false;
  $("#ap-go").disabled = true;
  const bar = $("#ap-progress .progress span");
  bar.style.width = "4%";
  const job = { ...ap.job, ...(ap.details || {}), url: ap.job.url };
  // Open the new tab now, while this click still counts as the person's own action; browsers block
  // tabs opened minutes later. It shows a waiting note, then turns into the application page.
  const pending = window.open("", "_blank");
  if (pending) {
    pending.opener = null;
    pending.document.write(`<!doctype html><meta charset="utf-8"><title>Preparing your application · Mr. Job Consultant</title>
      <link rel="icon" type="image/svg+xml" href="${location.origin}/static/icon.svg">
      <body style="margin:0;min-height:100vh;display:grid;place-items:center;background:#f5efe6;color:#2f2925;
      font:16px/1.6 system-ui,sans-serif;text-align:center;padding:24px">
      <div><p style="font-size:20px;font-weight:600;margin:0 0 8px">Preparing your documents…</p>
      <p style="margin:0;color:#7b6f63">This tab will open the application page for<br><strong>${esc(job.title)}</strong>
      as soon as your tailored resume is ready. Your files are in the job search tab.</p></div>`);
    pending.document.close();
  }
  try {
    const res = await fetch("/api/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...hfHeaders() },
      body: JSON.stringify({ job, formats: [...ap.formats], cover_letter: $("#ap-letter").checked, engine: ap.engine }),
    });
    const { id, detail } = await res.json();
    if (!res.ok) throw new Error(detail || "Couldn't start.");
    let task;
    for (;;) {
      await new Promise((r) => setTimeout(r, 1200));
      task = await (await fetch(`/api/apply/${id}`)).json();
      $("#ap-progress-msg").textContent = task.message || "Working…";
      bar.style.width = `${Math.max(4, Math.round((task.progress || 0) * 100))}%`;
      if (task.stage === "done" || task.stage === "error") break;
    }
    if (task.stage === "error") throw new Error(task.error);
    showApplyResult(task.result, pending);
  } catch (err) {
    if (pending && !pending.closed) pending.close();
    $("#ap-error").textContent = err.message || "Something went wrong.";
    $("#ap-setup").hidden = false;
    $("#ap-go").disabled = false;
  } finally {
    $("#ap-progress").hidden = true;
    ap.busy = false;
  }
});

function showApplyResult(r, pending) {
  const label = { resume: "Resume", cover_letter: "Cover letter" };
  $("#ap-files").innerHTML = r.files.map((f) =>
    `<li><a href="${esc(f.url)}" download="${esc(f.name)}">
      <svg class="ico" viewBox="0 0 24 24"><path d="M12 4v11M7.5 10.5 12 15l4.5-4.5"/><path d="M5 19h14"/></svg>
      ${label[f.kind] || f.kind}<span class="fmt">${esc(f.format.toUpperCase())}</span></a></li>`).join("");
  const missing = r.keywords?.missing || [];
  $("#ap-missing").hidden = !missing.length;
  $("#ap-missing-tags").innerHTML = missing.map((k) => `<span>${esc(k)}</span>`).join("");
  $("#ap-report-count").textContent = r.report.length;
  $("#ap-report-list").innerHTML = r.report.length
    ? r.report.map((x) => `<li>${esc(x)}</li>`).join("")
    : "<li>Nothing. Every line came straight from your CV or passed the checks.</li>";
  if (r.letter_requested && !r.letter_written && r.engine !== "none") {
    $("#ap-report-list").insertAdjacentHTML("afterbegin",
      "<li>No cover letter: none of the drafted sentences could be backed by your CV.</li>");
  }
  $("#ap-result").hidden = false;
  $("#ap-go").hidden = true;
  $("#ap-open").href = r.apply_url;
  $("#ap-open").textContent = "Open application page ↗";
  const who = r.account ? ` Written by ${r.model?.split("/").pop()} on ${r.account}'s account.` : "";
  toast(`Your documents are ready.${who}`);
  if (pending && !pending.closed) {
    pending.location.href = r.apply_url;  // the tab opened at click time becomes the application page
  } else {
    $("#ap-blocked").hidden = false;     // blocked, or the person closed it: the button still works
  }
}

/* ---------- summary, progress, save ---------- */

function refresh() {
  const title = titleInput.value.trim();
  const details = collectDetails();
  const done = {
    title: title.length >= 2,
    where: state.worldwide || state.locations.length > 0,
    type: state.jobTypes.size > 0,
    schedule: !!state.schedule,
    cv: !!state.cv?.stored_as,
    more: details.authorized_countries.length > 0 || !!details.needs_sponsorship || !!details.salary
      || !!details.notice || details.years_experience !== null,
    how: true,  // always has a choice (CV matching by default)
  };
  $$(".card").forEach((card, i) => card.classList.toggle("done", Object.values(done)[i]));

  const required = [done.title, done.where, done.type, done.schedule, done.cv];
  if (!required.some(Boolean)) {
    $("#summary").textContent = DEFAULT_SUMMARY;
    return;
  }
  const parts = [];
  const extra = state.relatedTitles.length;
  parts.push(done.title
    ? `<strong>${esc(title)}</strong>${extra ? ` + ${extra} related title${extra > 1 ? "s" : ""}` : " roles"}`
    : "Roles");
  if (done.type) parts.push(listJoin([...state.jobTypes].map((t) => TYPE_LABELS[t].toLowerCase())));
  if (state.worldwide) parts.push("anywhere in the world");
  else if (state.locations.length) parts.push(`in ${esc(listJoin(state.locations.map((l) => l.name)))}`);
  if (state.schedule === "flexible") parts.push("with a flexible schedule");
  if (state.schedule === "fixed") {
    parts.push(state.shifts.size
      ? `on ${listJoin([...state.shifts].map((s) => SHIFT_LABELS[s]))} shifts`
      : "on fixed hours");
  }
  const left = required.filter((d) => !d).length;
  $("#summary").innerHTML = parts.join(" · ") + (left ? `<br>${left} step${left > 1 ? "s" : ""} to go` : "");
}

function validate() {
  let first = null;
  const fail = (id, msg, el) => { setError(id, msg); first ??= el; };

  if (titleInput.value.trim().length < 2) {
    titleInput.setAttribute("aria-invalid", "true");
    fail("err-title", "Add the job title you're looking for.", titleInput);
  }
  if (!state.worldwide && !state.locations.length) {
    $("#combo").classList.add("invalid");
    fail("err-where", "Add at least one place, or choose Worldwide.", $("#loc-input"));
  }
  if (!state.jobTypes.size) fail("err-type", "Pick at least one kind of job.", $("[data-type]"));
  if (!state.schedule) fail("err-schedule", "Choose fixed or flexible.", $("[data-schedule]"));
  if (!state.cv?.stored_as) {
    dropzone.classList.add("invalid");
    fail("err-cv", "Attach your CV so we can apply for you.", dropzone);
  }
  const { salary } = collectDetails();
  if (salary && salary.min !== null && salary.max !== null && salary.min > salary.max) {
    fail("err-salary", "The “from” figure is higher than “up to”. Swap them?", $("#sal-min"));
  }
  if (state.notice === "date" && !$("#start-date").value) {
    fail("err-notice", "Pick the date you could start, or choose another option.", $("#start-date"));
  }
  if (first) {
    first.closest(".card").scrollIntoView({ behavior: "smooth", block: "center" });
    setTimeout(() => (first.focus?.({ preventScroll: true })), 300);
  }
  return !first;
}

$("#profile").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!validate()) return;

  const contact = {};
  for (const key of ["name", "email", "phone", "linkedin"]) contact[key] = $(`#c-${key}`).value.trim() || null;
  const payload = {
    job_title: titleInput.value.trim(),
    related_titles: state.relatedTitles.filter((t) => !sameTitle(t, titleInput.value)),
    worldwide: state.worldwide,
    locations: state.worldwide ? [] : state.locations,
    job_types: [...state.jobTypes],
    schedule: state.schedule,
    shifts: state.schedule === "fixed" ? [...state.shifts] : [],
    cv: state.cv,
    contact,
    details: collectDetails(),
    use_cv_matching: state.mode === "cv",
    allow_inference_backup: $("#allow-inference").checked,
  };

  const button = $("#save");
  button.disabled = true;
  try {
    const res = await fetch("/api/profile", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      const msg = Array.isArray(data.detail) ? data.detail.map((d) => d.msg.replace(/^Value error, /, "")).join(" ") : data.detail;
      throw new Error(msg || "Something went wrong while saving.");
    }
    toast("Saved. Looking for jobs now…");
  } catch (err) {
    toast(err.message || "Couldn't reach the app's server.", true);
    button.disabled = false;
    return;
  }
  button.disabled = false;
  startSearch();
});

/* ---------- start-up ---------- */

function restoreDetails(d = {}) {
  state.authCountries.push(...(d.authorized_countries || []));
  authPicker.renderChips();
  state.sponsorship = d.needs_sponsorship || null;
  setRadio("sponsor", state.sponsorship);
  state.notice = d.notice || null;
  setRadio("notice", state.notice);
  $("#start-date-wrap").hidden = state.notice !== "date";
  if (d.start_date) $("#start-date").value = d.start_date;
  if (d.salary) {
    state.currencyTouched = true;
    currencySelect.value = d.salary.currency;
    $("#sal-min").value = d.salary.min?.toLocaleString() ?? "";
    $("#sal-max").value = d.salary.max?.toLocaleString() ?? "";
    $("#sal-period").value = d.salary.period;
  }
  setYears(d.years_experience ?? null);
}

async function loadSaved() {
  const res = await fetch("/api/profile");
  const saved = res.ok ? await res.json() : {};
  if (!saved.job_title) return;

  titleInput.value = saved.job_title;
  state.relatedTitles.push(...(saved.related_titles || []));
  state.titleBase = saved.job_title;  // keep the saved choice of related titles
  loadTitleIdeas({ keepPicked: true });
  state.locations.push(...(saved.locations || []));
  locPicker.renderChips();
  setScope(!!saved.worldwide);
  for (const t of saved.job_types || []) {
    state.jobTypes.add(t);
    $(`[data-type="${t}"]`)?.setAttribute("aria-pressed", "true");
  }
  if (saved.schedule) setSchedule(saved.schedule);
  for (const s of saved.shifts || []) {
    state.shifts.add(s);
    $(`[data-shift="${s}"]`)?.setAttribute("aria-pressed", "true");
  }
  if (saved.cv) {
    state.cv = saved.cv;
    showCV(saved.cv);
    fillContact(saved.contact || saved.cv.contact);
    $$("#contact input").forEach((i) => (i.dataset.fromCv = "0"));
  }
  restoreDetails(saved.details);
  setMode(saved.use_cv_matching === false ? "title" : "cv");
  $("#allow-inference").checked = !!saved.allow_inference_backup;
  renderQuickPicks();
  renderAuthQuick();
  refresh();
}

(async () => {
  try {
    LOCATIONS = await (await fetch("/static/locations.json")).json();
  } catch {
    LOCATIONS = [];
  }
  renderQuickPicks();
  try { await loadSaved(); } catch { /* first visit, or server not ready */ }
  if (state.mode === "cv" && !state.ai) loadAIStatus();
})();
