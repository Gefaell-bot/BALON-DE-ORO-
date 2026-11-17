// Balón de Oro Analytics — cliente del API REST (FastAPI + MongoDB).
const $ = (s, el = document) => el.querySelector(s);
const state = { season: null, sort: "score", view: "table", compare: new Set(), leaderboard: [], meta: null, picked: null };
const charts = {};
const PALETTE = ["#F2D58A", "#7FB7E6", "#E58FA0", "#6CC6A4", "#C9B6F2", "#EFE8D8"];
const COMPONENT_LABELS = { ga: "Goles + asist.", titles: "Títulos", market: "Valor", ucl: "Champions", ga90: "G+A por 90", win_rate: "Victorias" };
const POSITION_ES = { Attack: "Delantero", Midfield: "Centrocampista", Defender: "Defensa", Goalkeeper: "Portero" };
const SUB_ES = {
  "Centre-Forward": "delantero centro", "Second Striker": "mediapunta", "Left Winger": "extremo izquierdo",
  "Right Winger": "extremo derecho", "Attacking Midfield": "mediapunta", "Central Midfield": "mediocentro",
  "Defensive Midfield": "pivote", "Left Midfield": "interior izquierdo", "Right Midfield": "interior derecho",
  "Centre-Back": "central", "Left-Back": "lateral izquierdo", "Right-Back": "lateral derecho", "Goalkeeper": "portero",
};
const nf = new Intl.NumberFormat("es-ES");

// ------------------------------------------------------------------ utilidades
async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(errorText(body, res.status)), { status: res.status, body });
  return body;
}
function errorText(body, status) {
  const d = body.detail;
  if (Array.isArray(d)) return d.map(e => `${e.loc.at(-1)}: ${e.msg}`).join(" · ");
  if (d && d.error) {
    const rule = JSON.stringify(d.rules || "").match(/"propertyName":"([^"]+)"/);
    return `${d.error}${rule ? ` (campo ${rule[1]})` : ""}.`;
  }
  return typeof d === "string" ? d : `Error ${status}`;
}
const money = v => v >= 1e6 ? `${nf.format(Math.round(v / 1e6))} M€` : v > 0 ? `${nf.format(Math.round(v / 1e3))} mil €` : "—";
const pct = p => p == null ? "—" : p >= 0.1 ? `${Math.round(p * 100)} %` : p >= 0.001 ? `${(p * 100).toFixed(1)} %` : "<0,1 %";
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const initials = n => n.split(/\s+/).map(w => w[0]).slice(0, 2).join("");
function portrait(p, size) {
  const img = p.image_url && !p.image_url.includes("default.jpg")
    ? `<img src="${esc(p.image_url)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">` : "";
  const bg = img ? ` style="--photo:url('${esc(p.image_url)}')"` : "";
  return `<span class="portrait ${size}"${bg} aria-hidden="true">${img || esc(initials(p.name))}</span>`;
}
const ordinal = n => `${n}.º`;
function toast(msg, isError = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("error", isError);
  t.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove("show"), 5200);
}

// ------------------------------------------------------------------ carga
async function loadMeta() {
  const m = await api(state.season == null ? "/api/meta" : `/api/meta?season=${state.season}`);
  state.meta = m;
  if (state.season == null) {
    state.season = m.season;
    $("#season").innerHTML = m.seasons.map(s =>
      `<option value="${s.value}">Temporada ${s.label}</option>`).join("");
  }
  $("#season").value = state.season;
  fill("#f-league", m.leagues.map(l => [l.id, `${l.name}${l.country ? ` (${l.country})` : ""}`]), "Todas");
  fill("#f-position", m.positions.map(p => [p, POSITION_ES[p] || p]), "Todas");
  fill("#f-club", m.clubs.map(c => [c.id, c.name]), "Todos");
  fill("#m-comp", [...m.leagues.map(l => [l.id, l.name]), ["CL", "Champions League"], ["EL", "Europa League"],
    ["FIWC", "Mundial"], ["CDR", "Copa del Rey"], ["FAC", "FA Cup"]]);
  $("#f-age-min").placeholder = `desde ${m.age.min}`;
  $("#f-age-max").placeholder = `hasta ${m.age.max}`;
  $("#stage-year").textContent = state.season + 1;
}
function fill(sel, pairs, anyLabel) {
  const el = $(sel), cur = el.value;
  el.innerHTML = (anyLabel ? `<option value="">${anyLabel}</option>` : "") +
    pairs.map(([v, t]) => `<option value="${esc(v)}">${esc(t)}</option>`).join("");
  if ([...el.options].some(o => o.value === cur)) el.value = cur;
}

function filterQuery() {
  const f = new FormData($("#filters"));
  const q = new URLSearchParams({ season: state.season, sort: state.sort, limit: 50 });
  for (const k of ["league", "position", "club", "age_min", "age_max"]) if (f.get(k)) q.set(k, f.get(k));
  return q;
}

async function loadRankings({ animate = false } = {}) {
  let r;
  try {
    r = await api(`/api/rankings?${filterQuery()}`);
  } catch (e) {
    toast(`No se pudo cargar el ranking: ${e.message}`, true);
    return;
  }
  state.leaderboard = r.leaderboard;
  renderPodium(r.leaderboard.slice(0, 3), animate);
  renderFacts(r.summary);
  renderTable(r.leaderboard);
  renderCards(r.leaderboard);
  renderTiers(r.tiers);
  renderPromesas(r.promesas);
  $("#rank-empty").hidden = r.leaderboard.length > 0;
  $("#ranking-count").textContent = r.summary.candidates
    ? `${nf.format(r.summary.candidates)} candidatos con al menos 1.200 minutos. Se muestran los 50 primeros.` : "";
  const ids = state.compare.size ? [...state.compare] : r.leaderboard.slice(0, 5).map(p => p.player_id);
  loadCompare(ids);
}

// ------------------------------------------------------------------ hero
function renderPodium(top, animate) {
  const ol = $("#podium");
  const sortByMl = state.sort === "ml";
  const gala = state.season + 1;
  $("#stage-sub").textContent = top.some(p => p.ballon_dor_rank)
    ? `La gala de ${gala} ya se celebró: el distintivo dorado marca el podio real. El modelo no vio esta temporada al entrenar.`
    : `Candidatos ordenados por ${sortByMl ? "la probabilidad que estima el modelo" : "rendimiento, títulos, Champions y valor de mercado"} de la temporada ${state.meta.season_label}.`;
  ol.innerHTML = top.map((p, i) => `
    <li class="medal">
      <button type="button" data-player="${p.player_id}" aria-label="${ordinal(i + 1)} ${esc(p.name)}, ver ficha">
        <div class="ring">${portrait(p, "")}<span class="place" aria-hidden="true">${i + 1}</span></div>
        <h2>${esc(p.name)}</h2>
        <p class="club">${esc(p.club.name)}${p.ballon_dor_rank ? ` <span class="real-badge">${ordinal(p.ballon_dor_rank)} real</span>` : ""}</p>
        <div class="engraved">${sortByMl ? pct(p.ml?.probability) : p.bdo.score.toFixed(1)}</div>
        <div class="engraved-label">${sortByMl ? "probabilidad de ganar" : `puntos · ${pct(p.ml?.probability)} de probabilidad`}</div>
        <div class="pedestal" aria-hidden="true"></div>
      </button>
    </li>`).join("");
  if (animate) { ol.classList.remove("reveal"); void ol.offsetWidth; ol.classList.add("reveal"); }
}
function renderFacts(s) {
  if (!s.candidates) { $("#stage-facts").innerHTML = ""; return; }
  $("#stage-facts").innerHTML = [
    ["Candidatos analizados", nf.format(s.candidates)],
    ["Goles entre todos", nf.format(s.goals)],
    ["Asistencias", nf.format(s.assists)],
    ["Puntuación más alta", s.max_score?.toFixed(1)],
  ].map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("");
}

// ------------------------------------------------------------------ ranking
function renderTable(rows) {
  $("#rank-body").innerHTML = rows.map((p, i) => `
    <tr data-player="${p.player_id}">
      <td class="num pos">${i + 1}</td>
      <td><div class="who">${portrait(p, "sm")}<div>
        <button type="button" data-player="${p.player_id}"><strong>${esc(p.name)}${p.ballon_dor_rank ? `<span class="real-badge" title="Podio real del Balón de Oro">${ordinal(p.ballon_dor_rank)}</span>` : ""}</strong></button>
        <small>${esc(p.club.name)}, ${p.age.toFixed(0)} años</small></div></div></td>
      <td class="num">${p.performance.goals}</td>
      <td class="num">${p.performance.assists}</td>
      <td class="num hide-sm">${p.performance.ga_per90.toFixed(2)}</td>
      <td class="num hide-sm">${p.ucl?.ga ?? 0}</td>
      <td class="hide-md"><div class="titles-mini" title="${esc(p.titles.map(t => t.name).join(", "))}">${esc(p.titles.map(t => t.name).join(", ")) || "—"}</div></td>
      <td class="num hide-sm">${money(p.market_value_eur)}</td>
      <td class="num">${pct(p.ml?.probability)}</td>
      <td class="num score-cell">${p.bdo.score.toFixed(1)}</td>
      <td class="num"><input type="checkbox" class="cmp" value="${p.player_id}" aria-label="Comparar a ${esc(p.name)}" ${state.compare.has(p.player_id) ? "checked" : ""}></td>
    </tr>`).join("");
}
function renderCards(rows) {
  $("#cards-view").innerHTML = rows.slice(0, 24).map((p, i) => `
    <button type="button" class="card" data-player="${p.player_id}" aria-label="${ordinal(i + 1)} ${esc(p.name)}, ver ficha">
      <div class="card-top">${portrait(p, "md")}<div><strong>${esc(p.name)}</strong><br><small class="hint">${esc(p.club.name)}</small></div><span class="pos">${i + 1}</span></div>
      <div class="meter" aria-hidden="true"><i style="width:${p.bdo.score}%"></i></div>
      <dl><div><dt>Puntos</dt><dd>${p.bdo.score.toFixed(1)}</dd></div><div><dt>Goles</dt><dd>${p.performance.goals}</dd></div>
      <div><dt>Asist.</dt><dd>${p.performance.assists}</dd></div><div><dt>Prob.</dt><dd>${pct(p.ml?.probability)}</dd></div></dl>
    </button>`).join("");
}
function renderTiers(tiers) {
  $("#tiers").innerHTML = tiers.filter(t => t.label !== "Resto" || t.count).map((t, i) => `
    <li class="tier-row ${i === 0 ? "top" : ""}">
      <b>${esc(t.label)}</b><span>${nf.format(t.count)} jugadores</span>
      <p>${t.label === "Resto" ? `Edad media ${t.avg_age.toFixed(1)} años` : t.top.map(x => esc(x.name)).join(", ")}</p>
    </li>`).join("");
}
function renderPromesas(list) {
  $("#promesas").innerHTML = list.length ? list.map(p => `
    <li><button type="button" data-player="${p.player_id}">${portrait(p, "sm")}
      <span><strong>${esc(p.name)}</strong><small>${p.age.toFixed(0)} años, ${esc(p.club.name)}</small></span>
      <span class="p-score">${p.bdo.score.toFixed(1)}</span></button></li>`).join("")
    : `<li class="hint">No hay jugadores sub-21 con estos filtros.</li>`;
}

// ------------------------------------------------------------------ comparativa
function chartDefaults() {
  Chart.defaults.color = "#8E96B3";
  Chart.defaults.font.family = "'Instrument Sans', system-ui, sans-serif";
  Chart.defaults.borderColor = "rgba(242,213,138,.10)";
  Chart.defaults.plugins.legend.labels.usePointStyle = true;
  Chart.defaults.plugins.tooltip.backgroundColor = "#16214A";
  Chart.defaults.plugins.tooltip.borderColor = "#C9A24A";
  Chart.defaults.plugins.tooltip.borderWidth = 1;
}
function draw(id, config) {
  charts[id]?.destroy();
  charts[id] = new Chart(document.getElementById(id), config);
}
async function loadCompare(ids) {
  if (!ids.length || !window.Chart) return;
  const { players, monthly } = await api(`/api/compare?season=${state.season}&ids=${ids.join(",")}`);
  const order = ids.map(id => players.find(p => p.player_id === id)).filter(Boolean);
  const color = i => PALETTE[i % PALETTE.length];
  const keys = Object.keys(COMPONENT_LABELS);
  draw("chart-radar", {
    type: "radar",
    data: { labels: keys.map(k => COMPONENT_LABELS[k]),
      datasets: order.map((p, i) => ({ label: p.name, data: keys.map(k => +(p.bdo.components[k] * 100).toFixed(1)),
        borderColor: color(i), backgroundColor: color(i) + "22", pointBackgroundColor: color(i), borderWidth: 2 })) },
    options: { maintainAspectRatio: false, scales: { r: { min: 0, max: 100, ticks: { display: false, stepSize: 25 },
      angleLines: { color: "rgba(242,213,138,.12)" }, grid: { color: "rgba(242,213,138,.10)" },
      pointLabels: { color: "#EFE8D8", font: { size: 12 } } } }, plugins: { legend: { position: "bottom" } } },
  });
  const months = [...new Set(monthly.map(m => m.month.slice(0, 7)))].sort();
  const label = m => new Date(m + "-01").toLocaleDateString("es-ES", { month: "short", year: "2-digit" });
  draw("chart-line", {
    type: "line",
    data: { labels: months.map(label), datasets: order.map((p, i) => {
      let last = 0;
      const byMonth = Object.fromEntries(monthly.filter(m => m.player_id === p.player_id).map(m => [m.month.slice(0, 7), m.cum_ga]));
      return { label: p.name, data: months.map(m => (last = byMonth[m] ?? last)), borderColor: color(i),
        backgroundColor: color(i), tension: .3, pointRadius: 2, borderWidth: 2 };
    }) },
    options: { maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      scales: { y: { beginAtZero: true, title: { display: true, text: "G+A acumulados" } } }, plugins: { legend: { position: "bottom" } } },
  });
  const metrics = [["Goles", p => p.performance.goals], ["Asistencias", p => p.performance.assists],
    ["G+A Champions", p => p.ucl?.ga ?? 0], ["Puntos de títulos", p => p.title_points]];
  draw("chart-bars", {
    type: "bar",
    data: { labels: metrics.map(m => m[0]), datasets: order.map((p, i) => ({ label: p.name, data: metrics.map(m => m[1](p)),
      backgroundColor: color(i), borderRadius: 4 })) },
    options: { maintainAspectRatio: false, scales: { y: { beginAtZero: true } }, plugins: { legend: { position: "bottom" } } },
  });
}

// ------------------------------------------------------------------ ficha
async function openPlayer(id) {
  const dlg = $("#player-dialog");
  let d;
  try { d = await api(`/api/players/${id}?season=${state.season}`); }
  catch (e) { toast(e.message, true); return; }
  const p = d.player;
  $("#pd-portrait").outerHTML = portrait(p, "lg").replace('class="portrait lg"', 'class="portrait lg" id="pd-portrait"');
  $("#pd-name").textContent = p.name;
  const sub = SUB_ES[p.sub_position] || p.sub_position;
  $("#pd-meta").textContent = `${POSITION_ES[p.position] || p.position}${sub && sub !== "portero" ? ` (${sub})` : ""}, ${p.club.name}, ${p.league.name}. ${p.age.toFixed(0)} años, ${p.nationality || "nacionalidad desconocida"}.`;
  $("#pd-badges").innerHTML = [
    p.bdo.tier, p.ml ? `${ordinal(p.ml.rank)} según el modelo` : null,
    p.ballon_dor_rank ? `${ordinal(p.ballon_dor_rank)} en la gala real` : null, money(p.market_value_eur),
  ].filter(Boolean).map(b => `<span>${esc(b)}</span>`).join("");
  $("#pd-score").innerHTML = `<b>${p.bdo.score.toFixed(1)}</b><small>puntos, ${pct(p.ml?.probability)} de probabilidad</small>`;
  $("#pd-components").innerHTML = Object.entries(COMPONENT_LABELS).map(([k, l]) => {
    const v = p.bdo.components[k] * 100;
    return `<li><span>${l}</span><span class="meter" aria-hidden="true"><i style="width:${v}%"></i></span><span>${v.toFixed(0)}</span></li>`;
  }).join("");
  const s = p.performance;
  $("#pd-stats").innerHTML = [["Partidos", s.apps], ["Minutos", nf.format(s.minutes)], ["Goles", s.goals], ["Asistencias", s.assists],
    ["G+A por 90", s.ga_per90.toFixed(2)], ["Victorias", pct(s.win_rate)], ["Champions G+A", p.ucl?.ga ?? 0], ["Internacionalidades", p.national?.caps ?? 0]]
    .map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("");
  $("#pd-titles").innerHTML = p.titles.length ? p.titles.map(t => `<li>${esc(t.name)}</li>`).join("") : `<li class="none">Sin títulos esta temporada</li>`;
  $("#pd-matches").innerHTML = d.matches.slice(-8).reverse().map(m => `
    <tr><td>${new Date(m.date).toLocaleDateString("es-ES")}</td><td>${esc(m.competition_id)}</td><td class="num">${m.goals}</td>
    <td class="num">${m.assists}</td><td class="num">${m.minutes}</td><td class="res-${m.result}">${{ W: "Victoria", D: "Empate", L: "Derrota" }[m.result] || "—"}</td></tr>`).join("")
    || `<tr><td colspan="6" class="hint">Sin partidos registrados en esta temporada.</td></tr>`;
  dlg.showModal();
  const dates = d.matches.map(m => new Date(m.date).toLocaleDateString("es-ES", { day: "2-digit", month: "short" }));
  draw("pd-form", {
    data: { labels: dates, datasets: [
      { type: "line", label: "G+A acumulados", data: d.matches.map(m => m.cum_ga), borderColor: "#F2D58A", backgroundColor: "#F2D58A", pointRadius: 0, tension: .25, yAxisID: "y" },
      { type: "bar", label: "Media últimos 5", data: d.matches.map(m => m.form5), backgroundColor: "rgba(127,183,230,.5)", yAxisID: "y1" }] },
    options: { maintainAspectRatio: false, scales: { x: { ticks: { maxTicksLimit: 8 } }, y: { beginAtZero: true }, y1: { position: "right", beginAtZero: true, grid: { display: false } } },
      plugins: { legend: { position: "bottom" } } },
  });
  draw("pd-value", {
    type: "line",
    data: { labels: d.market_values.map(v => new Date(v.date).toLocaleDateString("es-ES", { month: "short", year: "numeric" })), datasets: [{ label: "Valor (M€)",
      data: d.market_values.map(v => v.value_eur / 1e6), borderColor: "#C9A24A", backgroundColor: "rgba(201,162,74,.18)", fill: true, pointRadius: 0, stepped: true }] },
    options: { maintainAspectRatio: false, scales: { x: { ticks: { maxTicksLimit: 8 } } }, plugins: { legend: { display: false } } },
  });
}

// ------------------------------------------------------------------ escritura
function setupPlayerSearch() {
  const input = $("#m-player"), list = $("#m-suggest");
  let timer, results = [];
  input.addEventListener("input", () => {
    clearTimeout(timer);
    state.picked = null;
    $("#m-player-pick").textContent = "";
    if (input.value.trim().length < 2) { list.hidden = true; return; }
    timer = setTimeout(async () => {
      results = await api(`/api/players/search?season=${state.season}&q=${encodeURIComponent(input.value.trim())}`).catch(() => []);
      list.innerHTML = results.map((p, i) => `<li role="option" id="opt-${i}" data-i="${i}">${portrait(p, "sm")}${esc(p.name)}<small>${esc(p.club?.name || "")}</small></li>`).join("")
        || `<li class="hint">Ningún candidato con ese nombre en ${state.meta.season_label}.</li>`;
      list.hidden = false;
    }, 220);
  });
  const pick = p => {
    state.picked = p;
    input.value = p.name;
    list.hidden = true;
    $("#m-player-pick").textContent = `Seleccionado: ${p.name} (${p.club?.name || "sin club"})`;
  };
  list.addEventListener("mousedown", e => { const li = e.target.closest("li[data-i]"); if (li) pick(results[+li.dataset.i]); });
  input.addEventListener("keydown", e => {
    const opts = [...list.querySelectorAll("li[data-i]")];
    if (list.hidden || !opts.length) return;
    let i = opts.findIndex(o => o.getAttribute("aria-selected") === "true");
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      i = (i + (e.key === "ArrowDown" ? 1 : -1) + opts.length) % opts.length;
      opts.forEach((o, j) => o.setAttribute("aria-selected", j === i));
      input.setAttribute("aria-activedescendant", opts[i].id);
    } else if (e.key === "Enter" && i >= 0) { e.preventDefault(); pick(results[i]); }
    else if (e.key === "Escape") list.hidden = true;
  });
  input.addEventListener("blur", () => setTimeout(() => (list.hidden = true), 150));
}

async function submitMatch(e) {
  e.preventDefault();
  const err = $("#match-error"), btn = e.submitter;
  err.textContent = "";
  if (!state.picked) { err.textContent = "Elige un jugador de la lista de sugerencias."; $("#m-player").focus(); return; }
  if (!$("#m-date").value) { err.textContent = "Indica la fecha del partido."; $("#m-date").focus(); return; }
  const body = {
    player_id: state.picked.player_id, date: `${$("#m-date").value}T20:00:00Z`, competition_id: $("#m-comp").value,
    goals: +$("#m-goals").value, assists: +$("#m-assists").value, minutes: +$("#m-minutes").value,
    yellow: +$("#m-yellow").value, red: 0, result: new FormData(e.target).get("result"),
  };
  btn.disabled = true;
  try {
    const r = await api("/api/matches", { method: "POST", body: JSON.stringify(body) });
    const p = r.player;
    toast(`Partido registrado. ${p.name} suma ${p.performance.goals} goles y ${p.performance.assists} asistencias; probabilidad ${pct(p.ml?.probability)}.`);
    if (r.season === state.season) loadRankings();
  } catch (ex) {
    err.textContent = ex.message;
  } finally { btn.disabled = false; }
}

async function submitStats(e) {
  e.preventDefault();
  const err = $("#stats-error"), btn = e.submitter;
  err.textContent = "";
  if (!state.picked) { err.textContent = "Primero elige un jugador en el formulario de partido."; $("#m-player").focus(); return; }
  const body = {};
  if ($("#s-value").value) body.market_value_eur = +$("#s-value").value * 1e6;
  if ($("#s-title").value) {
    let [id, name, w] = $("#s-title").value.split("|");
    if (id === "LEAGUE") { // la liga del propio jugador, para que cuente como título de liga top-5
      const lb = state.leaderboard.find(x => x.player_id === state.picked.player_id);
      id = lb?.league.id || id; name = lb?.league.name || name;
    }
    body.add_title = { competition_id: id, name, weight: +w };
  }
  if (!Object.keys(body).length) { err.textContent = "Indica un valor de mercado o un título."; return; }
  btn.disabled = true;
  try {
    const r = await api(`/api/players/${state.picked.player_id}/seasons/${state.season}`, { method: "PATCH", body: JSON.stringify(body) });
    toast(`Cambios guardados. ${r.name}: ${money(r.market_value_eur)}, ${r.titles.length} títulos, probabilidad ${pct(r.ml?.probability)}.`);
    loadRankings();
  } catch (ex) {
    err.textContent = ex.status === 404 ? `${state.picked.name} no tiene datos en ${state.meta.season_label}.` : ex.message;
  } finally { btn.disabled = false; }
}

// ------------------------------------------------------------------ modelo
async function loadHistory() {
  const h = await api("/api/history");
  const m = h.metrics?.logistic_regression;
  if (m) {
    $("#model-sub").textContent = `Regresión logística validada temporada a temporada: para cada gala, el modelo se entrenó sin ver ese año. ${h.per_season.length} galas evaluadas (2013 a 2025, sin 2020).`;
    $("#model-metrics").innerHTML = [["Ganador acertado en el 1.er puesto", pct(m.winner_top1)], ["Ganador entre los 3 primeros", pct(m.winner_top3)],
      ["Ganador entre los 10 primeros", pct(m.winner_top10)], ["Podio real dentro del top 10", pct(m.podium_recall_top10)]]
      .map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("");
  }
  const byYear = {};
  h.podiums.forEach(p => ((byYear[p.award_year] ??= [])[p.rank - 1] = p));
  const perSeason = Object.fromEntries(h.per_season.map(s => [s.season, s]));
  $("#history-body").innerHTML = Object.keys(byYear).sort((a, b) => b - a).map(y => {
    const pod = byYear[y], r = perSeason[y - 1]?.winner_pred_rank;
    const cls = r === 1 ? "hit" : r <= 3 ? "near" : "miss";
    return `<tr><td>${y}</td>${pod.map(p => `<td>${esc(p?.name ?? "—")}</td>`).join("")}
      <td class="num">${r ? `<span class="rank-pill ${cls}">${ordinal(r)}</span>` : "—"}</td></tr>`;
  }).join("");
}

// ------------------------------------------------------------------ eventos
function bind() {
  $("#season").addEventListener("change", async e => {
    state.season = +e.target.value;
    state.compare.clear();
    await loadMeta();
    loadRankings({ animate: true });
  });
  $("#filters").addEventListener("change", e => {
    if (e.target.name === "sort") state.sort = e.target.value;
    loadRankings({ animate: e.target.name === "sort" });
  });
  $("#filters").addEventListener("reset", () => setTimeout(() => { state.sort = "score"; loadRankings(); }));
  $("#filters").addEventListener("submit", e => e.preventDefault());
  document.querySelectorAll("input[name=view]").forEach(r => r.addEventListener("change", e => {
    $("#table-view").hidden = e.target.value !== "table";
    $("#cards-view").hidden = e.target.value !== "cards";
  }));
  document.addEventListener("click", e => {
    if (e.target.matches(".cmp")) {
      const id = +e.target.value;
      if (e.target.checked) {
        if (state.compare.size >= 5) { e.target.checked = false; toast("Puedes comparar hasta cinco jugadores. Desmarca uno primero.", true); return; }
        state.compare.add(id);
      } else state.compare.delete(id);
      loadCompare(state.compare.size ? [...state.compare] : state.leaderboard.slice(0, 5).map(p => p.player_id));
      return;
    }
    const target = e.target.closest("[data-player]");
    if (target && !e.target.closest("#player-dialog")) openPlayer(+target.dataset.player);
  });
  $("#pd-close").addEventListener("click", () => $("#player-dialog").close());
  $("#player-dialog").addEventListener("click", e => { if (e.target.id === "player-dialog") e.target.close(); });
  $("#match-form").addEventListener("submit", submitMatch);
  $("#stats-form").addEventListener("submit", submitStats);
  setupPlayerSearch();
  $("#m-date").value = new Date().toISOString().slice(0, 10);
}

(async function init() {
  bind();
  try {
    await loadMeta();
  } catch (e) {
    $("#stage-sub").textContent = `No hay conexión con la API: ${e.message}. Arranca el backend con "python -m uvicorn backend.main:app".`;
    return;
  }
  if (window.Chart) chartDefaults();
  else window.addEventListener("load", chartDefaults);
  await loadRankings({ animate: true });
  loadHistory();
})();
