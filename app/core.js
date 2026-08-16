/* Vigilador app — módulo core.js (corte de app.js). */
let _PERFILES = {};
/* Vigilador App — consume la API del núcleo (mismo origen). */
"use strict";

const $ = (s) => document.querySelector(s);
const API = "";

async function fetchJson(url, opts = {}) {
  const r = await fetch(API + url, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
  return d;
}

function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function fmtEpoch(e) {
  if (!e) return "—";
  const d = new Date(e * 1000);
  return d.toLocaleString("es-AR", { dateStyle: "short", timeStyle: "medium" });
}
function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (err ? " err" : "");
  clearTimeout(toast._h);
  toast._h = setTimeout(() => (t.className = "toast hidden"), 3500);
}

/* ---------- ayuda "?" en controles estáticos ----------
   Cada input/select con title recibe un icono "?" visible; el tooltip
   nativo explica el parámetro al pasar el mouse. Se omite dentro de
   filas en grid (.row2) y tablas para no romper el layout. */
function aplicarAyuda() {
  document.querySelectorAll("input, select").forEach((el) => {
    if (!el.title || el.closest(".row2") || el.closest("table")) return;
    const h = document.createElement("span");
    h.className = "help"; h.title = el.title; h.textContent = "?";
    el.insertAdjacentElement("afterend", h);
  });
}

/* ---------- tabs ---------- */
document.querySelectorAll(".tab").forEach((b) =>
  b.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === b));
    document.querySelectorAll(".tabpage").forEach((p) =>
      p.classList.toggle("active", p.id === "tab-" + b.dataset.tab));
    if (b.dataset.tab === "avistamientos") cargarAvistamientos();
    if (b.dataset.tab === "placas") cargarPlacas();
    if (b.dataset.tab === "personas") cargarPersonas();
    if (b.dataset.tab === "config") { cargarConfig(); cargarVision(); cargarTelegram(); cargarMemoriaConf(); }
    if (b.dataset.tab === "memoria") cargarMemoria();
    if (b.dataset.tab === "laboratorio") cargarLaboratorio();
    if (b.dataset.tab === "logs") cargarLogs();
  }));

/* ---------- foto modal ---------- */
const _VERD = {};   // id avistamiento -> veredicto (string) — evita comillas en onclick
const _ZONAS = {};  // id avistamiento -> zonas recorridas (string JSON)
let _VER_FOTO_ID = null;
async function verFoto(id) {
  if (!id) return;
  _VER_FOTO_ID = id;
  $("#modal-img").src = `${API}/api/avistamientos/${id}/foto`;
  $("#modal-cap").textContent = `Avistamiento #${id}`;
  // la segunda foto: la detección ANOTADA de Frigate (recuadro del motor)
  const wrap = $("#modal-annot-wrap");
  wrap.classList.add("hidden");
  fetchJson(`/api/avistamientos/${id}/box`).then((d) => {
    if (d && d.mqtt) {
      $("#modal-img-annot").src = d.mqtt;
      wrap.classList.remove("hidden");
    }
  }).catch(() => {});
  const box = $("#modal-verdict");
  let html = "";
  try {
    const zonas = _ZONAS[id] ? JSON.parse(_ZONAS[id]) : [];
    if (zonas.length) html += `<p class="sub">🚶 ${esc(zonas.join(" → "))}</p>`;
  } catch (_) {}
  try {
    const vj = _VERD[id] ? JSON.parse(_VERD[id]) : null;
    if (vj && (vj.tipo || vj.descripcion)) {
      const tipo = vj.tipo || "otro";
      const conf = vj.confianza != null ? ` (${vj.confianza})` : "";
      const desc = (vj.descripcion || "").replace(/\s+/g, " ").trim();
      const ocr = (vj.ocr || []).filter(Boolean).join(", ");
      let html2 = `<div class="badge badge-ok">👁 ${esc(tipo)}${esc(conf)}</div>`;
      if (vj.sospechoso) html2 += ` <span class="badge badge-warn">⚠️ sospechoso</span>`;
      if (desc) html2 += `<p class="sub">📝 ${esc(desc)}</p>`;
      if (ocr) html2 += `<p class="sub">🔠 OCR: ${esc(ocr)}</p>`;
      box.innerHTML = html + html2;
    } else {
      box.innerHTML = "<p class='sub'>sin veredicto de visión</p>";
    }
  } catch (_) { box.innerHTML = "<p class='sub'>sin veredicto de visión</p>"; }
  $("#modal").classList.remove("hidden");
}
function bind(id, evt, fn) {
  /* Arranque defensivo: si un elemento falta, no mata la app — el resto sigue. */
  const el = document.querySelector(id);
  if (el) el.addEventListener(evt, fn);
}
/* sub-tabs de Configuración (agrupados por proximidad de uso) */
bind("#cfg-tabs", "click", (e) => {
  const b = e.target.closest("button.st");
  if (!b) return;
  document.querySelectorAll("#cfg-tabs .st").forEach((x) => x.classList.toggle("active", x === b));
  document.querySelectorAll("#tab-config .subpage").forEach((x) => x.classList.toggle("hidden", x.id !== b.dataset.st));
});
bind("#modal-close", "click", () => $("#modal").classList.add("hidden"));
bind("#modal", "click", (e) => { if (e.target.id === "modal") $("#modal").classList.add("hidden"); });

