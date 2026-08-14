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

/* ---------- DASHBOARD ---------- */
async function cargarEstado() {
  try {
    const [st, res, ult] = await Promise.all([
      fetchJson("/api/estado"),
      fetchJson("/api/resumen?dias=7"),
      fetchJson("/api/avistamientos?limite=8"),
    ]);
    $("#estado-ts").textContent = `actualizado ${st.ts || "—"} · uptime ${Math.round(st.uptime_s || 0)}s`;
    const c = st.contadores || {};
    $("#contadores").innerHTML = [
      ["Eventos", c.eventos || 0, ""],
      ["Alertas", c.alertas || 0, c.alertas > 0 ? "sev-alta" : ""],
      ["Visión", c.vision || 0, ""],
      ["Recurrentes", c.recurrentes || 0, ""],
      ["Actividades", c.actividades_fin || 0, ""],
      ["Errores", c.errores || 0, c.errores > 0 ? "sev-critica" : ""],
      ["Sup. log", st.updates_suprimidos || 0, ""],
    ].map(([l, n, cls]) => `<div class="card"><div class="n ${cls}">${n}</div><div class="l">${l}</div></div>`).join("");

    const zonasF = st.zonas_frigate || {};
    const act = st.cameras_actividad || {};
    $("#camaras").innerHTML = (st.cameras || [])
      .map((cam) => {
        const zs = zonasF[cam] || [];
        const a = act[cam];
        const dot = a
          ? (a.activa
              ? "<span class='badge badge-ok' title='en vivo'>🟢</span>"
              : "<span class='badge badge-warn' title='sin actividad'>⚪</span>")
          : "";
        const objs = a && a.objetos && a.objetos.length
          ? " · <b>" + a.objetos.map((o) => `${esc(o.label)}${o.zona ? "·" + esc(o.zona) : ""}${o.estacionado ? " 🅿️" : ""}`).join(", ") + "</b>"
          : "";
        return `<div class="cam"><span class="name">🎥 ${esc(cam)} ${dot}</span>
          <span class="zones">${zs.length ? zs.map((z) => `<span class="badge badge-ok">${esc(z)}</span>`).join(" ") : "sin zonas"}${objs}</span></div>`;
      }).join("") || "<p class='sub'>sin cámaras</p>";

    const oc = st.ocupacion || {};
    const zonas = Object.keys(oc);
    $("#ocupacion").innerHTML = zonas.length
      ? zonas.map((z) => `<div class="cam"><span class="name">📍 ${esc(z)}</span><span class="zones">${Object.entries(oc[z]).map(([l, a]) => `<span class="badge ${a ? "badge-warn" : ""}">${esc(l)} ${a ? "● activo" : "○"}</span>`).join(" ")}</span></div>`).join("")
      : "<p class='sub'>sin ocupación registrada</p>";

    // salud: bus, detectores, última alerta
    const dets = Object.entries(st.detectores || {})
      .map(([k, v]) => `<tr><td>${esc(k)}</td><td>${v.inference_speed != null ? v.inference_speed + " ms" : "—"}</td></tr>`).join("");
    const bus = st.mqtt_ultimo_mensaje_hace_s != null
      ? `<span class="${st.mqtt_ultimo_mensaje_hace_s > 120 ? "sev-critica" : "badge-ok"}">${st.mqtt_ultimo_mensaje_hace_s}s</span>`
      : "—";
    const ua = st.ultima_alerta;
    $("#salud").innerHTML = `<table>
      <tr><th>Bus MQTT (último msg)</th><td>${bus}</td></tr>
      <tr><th>Detectores</th><td>${dets ? `<table class="inner"><tr><th>detector</th><th>inferencia</th></tr>${dets}</table>` : "—"}</td></tr>
      <tr><th>Última alerta</th><td>${ua ? `${esc(ua.ts)} · <b>${esc(ua.camara)}</b> · ${esc(ua.label)} · ${esc(ua.prioridad)}${ua.veredicto ? " · 🧠 " + esc(ua.veredicto) : ""}${ua.patente ? " · 🚗 " + esc(ua.patente) : ""}` : "sin alertas aún"}</td></tr>
    </table>`;

    const dias = res.dias || [];
    $("#resumen").innerHTML = `<table><tr><th>Día</th><th>Avistamientos</th><th>Patentes</th></tr>` +
      dias.map((d) => `<tr><td>${esc(d.dia)}</td><td>${d.n}</td><td>${d.patentes}</td></tr>`).join("") + "</table>";

    const avs = ult.avistamientos || [];
    $("#ultimos").innerHTML = `<table><tr><th>ID</th><th>Foto</th><th>Hora</th><th>Cámara</th><th>Objeto</th><th>Patente</th><th>Duración</th><th>Zonas</th><th>Visión</th><th>Visión dice</th></tr>` +
      avs.map((a) => {
        _VERD[a.id] = a.veredicto || null;
        _ZONAS[a.id] = a.zonas || null;
        return `<tr>
          <td><span class="sub" title="ID del avistamiento.">#${a.id}</span></td>
          <td><img class="foto" title="Ver foto en grande." src="${API}/api/avistamientos/${a.id}/foto" onclick="verFoto(${a.id})"></td>
        <td>${fmtEpoch(a.inicio)}</td><td>${esc(a.camara)}</td><td>${esc(a.label)}</td>
        <td>${a.patente ? `<b>${esc(a.patente)}</b>` : "—"}</td><td>${a.duracion ? a.duracion + "s" : "—"}</td>
        <td>${esc(a.zonas)}</td><td>${a.veredicto ? "<span class='badge badge-ok'>👁</span>" : "<span class='sub'>—</span>"}</td>
        <td>${detalleVision(a.veredicto)}</td></tr>`;
      }).join("") + "</table>";
  } catch (e) {
    $("#estado-ts").textContent = "error: " + e.message;
  }
}

/* ---------- AVISTAMIENTOS ---------- */
let _PERSONAS = [];
async function cargarPersonasCache() {
  try { const d = await fetchJson("/api/personas"); _PERSONAS = d.personas || []; } catch (_) {}
}
function tagSelect(a) {
  const opts = ['<option value="">—</option>']
    .concat(_PERSONAS.map((p) => `<option value="${esc(p.nombre)}" ${a.persona === p.nombre ? "selected" : ""}>${esc(p.nombre)}</option>`));
  return `<select class="tagsel" data-id="${a.id}" title="Etiqueta este avistamiento con una persona (la aprende en memoria)." onchange="etiquetarAvistamiento(${a.id}, this.value)">${opts.join("")}</select>`;
}
async function etiquetarAvistamiento(id, nombre) {
  try {
    await fetchJson(`/api/avistamientos/${id}/persona`, { method: "PUT", body: JSON.stringify({ persona: nombre }) });
    toast(nombre ? `avistamiento #${id} → ${nombre}` : `etiqueta quitada #${id}`);
    cargarAvistamientos();
  } catch (e) { toast(e.message, true); }
}
async function cargarAvistamientos() {
  const p = $("#f-patente").value.trim();
  const c = $("#f-camara").value;
  const l = $("#f-limite").value || 30;
  try {
    const q = new URLSearchParams({ limite: l });
    if (p) q.set("patente", p);
    if (c) q.set("camara", c);
    const d = await fetchJson("/api/avistamientos?" + q);
    const avs = d.avistamientos || [];
    let recorridoHtml = "";
    if (p) {
      try {
        const r = await fetchJson(`/api/avistamientos/recorrido?patente=${encodeURIComponent(p)}`);
        const cadena = r.recorrido || [];
        if (cadena.length) {
          recorridoHtml = `<div class="panel"><h2>🚗 Recorrido de ${esc(p.toUpperCase())}</h2>` +
            `<div class="cam">${cadena.map((s) => `<span class="zones">${fmtEpoch(s.inicio)} · <b>${esc(s.camara)}</b> (${s.duracion ? s.duracion + "s" : "?"})</span>`).join(" → ")}</div></div>`;
        }
      } catch (_) {}
    }
    $("#av-result").innerHTML = recorridoHtml + (avs.length
      ? `<table><tr><th>ID</th><th>Foto</th><th>Hora</th><th>Cámara</th><th>Objeto</th><th>Patente</th><th>Persona</th><th>Estado</th><th>Duración</th><th>Zonas</th><th>Visión</th><th>Veredicto</th><th>Visión dice</th></tr>` +
        avs.map((a) => {
          _VERD[a.id] = a.veredicto || null;
          _ZONAS[a.id] = a.zonas || null;
          let v = "";
          try { const vj = JSON.parse(a.veredicto); if (vj?.tipo) v = `${vj.tipo} (${vj.confianza})`; } catch (_) { v = a.veredicto || ""; }
          const est = a.estado === "detenido" ? "<span class='badge badge-warn'>🅿️ detenido</span>"
                    : a.estado === "en_movimiento" ? "<span class='badge'>🏃 en movimiento</span>" : "—";
          return `<tr>
            <td><span class="sub" title="ID del avistamiento (para referenciarlo puntual).">#${a.id}</span></td>
            <td>${a.foto ? `<img class="foto" title="Ver foto en grande." src="${API}/api/avistamientos/${a.id}/foto" onclick="verFoto(${a.id})">` : `<div class="foto empty"></div>`}</td>
            <td>${fmtEpoch(a.inicio)}</td><td>${esc(a.camara)}</td><td>${esc(a.label)}</td>
            <td>${a.patente ? `<b>${esc(a.patente)}</b>` : "—"}</td>
            <td>${a.label === "person" ? tagSelect(a) : esc(a.persona || "—")}</td>
            <td>${est}</td>
            <td>${a.duracion ? a.duracion + "s" : "—"}</td>
            <td>${esc(a.zonas)}</td>
            <td>${a.veredicto ? "<span class='badge badge-ok' title='La visión analizó la foto y ayudó a decidir'>👁 visión</span>" : "<span class='sub'>—</span>"}</td>
            <td>${esc(v)}</td>
            <td>${detalleVision(a.veredicto)}</td></tr>`;
        }).join("") + "</table>"
      : "<p class='sub'>sin resultados</p>");
  } catch (e) { toast(e.message, true); }
}
function detalleVision(raw) {
  if (!raw) return "<span class='sub'>—</span>";
  try {
    const vj = JSON.parse(raw);
    const desc = (vj.descripcion || "").replace(/\s+/g, " ").trim();
    if (!desc) return "<span class='sub'>—</span>";
    const titulo = `${vj.tipo || "otro"} (${vj.confianza}) · ${desc}`.slice(0, 300);
    return `<span class="badge" title="${esc(titulo)}">📝</span>`;
  } catch (_) { return "<span class='sub'>—</span>"; }
}
bind("#form-av", "submit", (e) => { e.preventDefault(); cargarAvistamientos(); });
setInterval(() => { if ($("#tab-avistamientos").classList.contains("active")) cargarAvistamientos(); }, 30000);

/* ---------- PLACAS (CRUD) ---------- */
let _EDIT_PLACA = null;
async function cargarPlacas() {
  try {
    const d = await fetchJson("/api/placas");
    const ps = d.placas || [];
    $("#placas-list").innerHTML = ps.length
      ? `<table><tr><th>Patente</th><th>Nombre</th><th>Tipo</th><th>Notas</th><th></th></tr>` +
        ps.map((p) => `<tr><td><b>${esc(p.patente)}</b></td><td>${esc(p.nombre)}</td><td>${esc(p.tipo)}</td>
          <td>${esc(p.notas)}</td>
          <td><button class="del" title="Editar esta placa." onclick="editarPlaca('${esc(p.patente)}','${esc(p.nombre || "")}','${esc(p.tipo)}','${esc(p.notas || "")}')">✏️</button>
              <button class="del" title="Eliminar esta placa (requiere confirmación)." onclick="eliminarPlaca('${esc(p.patente)}')">✕</button></td></tr>`).join("") + "</table>"
      : "<p class='sub'>sin placas registradas</p>";
  } catch (e) { toast(e.message, true); }
}
function editarPlaca(pat, nombre, tipo, notas) {
  _EDIT_PLACA = pat;
  $("#p-patente").value = pat; $("#p-patente").disabled = true;
  $("#p-nombre").value = nombre; $("#p-tipo").value = tipo; $("#p-notas").value = notas;
  $("#form-placa button[type=submit]").textContent = "Actualizar";
  $("#btn-placa-cancel").classList.remove("hidden");
}
bind("#btn-placa-cancel", "click", () => {
  _EDIT_PLACA = null; $("#p-patente").disabled = false; $("#form-placa").reset();
  $("#form-placa button[type=submit]").textContent = "Registrar";
  $("#btn-placa-cancel").classList.add("hidden");
});
bind("#form-placa", "submit", async (e) => {
  e.preventDefault();
  const body = { patente: $("#p-patente").value.trim(), nombre: $("#p-nombre").value.trim(),
    tipo: $("#p-tipo").value, notas: $("#p-notas").value.trim() };
  try {
    if (_EDIT_PLACA) {
      await fetchJson(`/api/placas/${encodeURIComponent(_EDIT_PLACA)}`, { method: "PUT", body: JSON.stringify({ nombre: body.nombre, tipo: body.tipo, notas: body.notas }) });
      toast(`placa ${_EDIT_PLACA} actualizada`);
    } else {
      await fetchJson("/api/placas", { method: "POST", body: JSON.stringify(body) });
      toast(`placa ${body.patente.toUpperCase()} registrada`);
    }
    _EDIT_PLACA = null; $("#p-patente").disabled = false; $("#form-placa").reset();
    $("#form-placa button[type=submit]").textContent = "Registrar";
    $("#btn-placa-cancel").classList.add("hidden");
    cargarPlacas();
  } catch (err) { toast(err.message, true); }
});
async function eliminarPlaca(pat) {
  if (!confirm(`¿Eliminar la placa ${pat}?`)) return;
  try {
    await fetchJson("/api/placas/" + encodeURIComponent(pat), { method: "DELETE" });
    toast(`placa ${pat} eliminada`);
    cargarPlacas();
  } catch (e) { toast(e.message, true); }
}

/* ---------- PERSONAS (CRUD) ---------- */
let _EDIT_PERSONA = null;
async function cargarPersonas() {
  try {
    const d = await fetchJson("/api/personas");
    _PERSONAS = d.personas || [];
    $("#personas-list").innerHTML = _PERSONAS.length
      ? `<table><tr><th>Nombre</th><th>Tipo</th><th>Avistamientos</th><th>Notas</th><th></th></tr>` +
        _PERSONAS.map((p) => `<tr><td><b>${esc(p.nombre)}</b></td><td>${esc(p.tipo)}</td><td>${p.avistamientos}</td><td>${esc(p.notas)}</td>
          <td><button class="del" title="Editar esta persona." onclick="editarPersona(${p.id},'${esc(p.nombre)}','${esc(p.tipo)}','${esc(p.notas || "")}')">✏️</button>
              <button class="del" title="Eliminar esta persona (requiere confirmación)." onclick="eliminarPersona(${p.id},'${esc(p.nombre)}')">✕</button></td></tr>`).join("") + "</table>"
      : "<p class='sub'>sin personas registradas</p>";
  } catch (e) { toast(e.message, true); }
}
function editarPersona(id, nombre, tipo, notas) {
  _EDIT_PERSONA = id;
  $("#per-nombre").value = nombre; $("#per-tipo").value = tipo; $("#per-notas").value = notas;
  $("#form-persona button[type=submit]").textContent = "Actualizar";
  $("#btn-persona-cancel").classList.remove("hidden");
}
bind("#btn-persona-cancel", "click", () => {
  _EDIT_PERSONA = null; $("#form-persona").reset();
  $("#form-persona button[type=submit]").textContent = "Registrar";
  $("#btn-persona-cancel").classList.add("hidden");
});
bind("#form-persona", "submit", async (e) => {
  e.preventDefault();
  const body = { nombre: $("#per-nombre").value.trim(), tipo: $("#per-tipo").value, notas: $("#per-notas").value.trim() };
  try {
    if (_EDIT_PERSONA) {
      await fetchJson(`/api/personas/${_EDIT_PERSONA}`, { method: "PUT", body: JSON.stringify(body) });
      toast("persona actualizada");
    } else {
      await fetchJson("/api/personas", { method: "POST", body: JSON.stringify(body) });
      toast("persona registrada");
    }
    _EDIT_PERSONA = null; $("#form-persona").reset();
    $("#form-persona button[type=submit]").textContent = "Registrar";
    $("#btn-persona-cancel").classList.add("hidden");
    cargarPersonas();
  } catch (err) { toast(err.message, true); }
});
async function eliminarPersona(id, nombre) {
  if (!confirm(`¿Eliminar a ${nombre} del registro?`)) return;
  try {
    await fetchJson(`/api/personas/${id}`, { method: "DELETE" });
    toast(`${nombre} eliminado del registro`);
    cargarPersonas();
  } catch (e) { toast(e.message, true); }
}

/* ---------- VISIÓN POR ZONA + PROVEEDORES (CRUD) ---------- */
let _PROVEEDORES = [];
let _EDIT_PROV = null;
async function cargarVision() {
  try {
    const [vzD, provD, estD] = await Promise.all([
      fetchJson("/api/vision/zonas"), fetchJson("/api/proveedores"),
      fetchJson("/api/estado")]);
    const zonas = vzD.zonas || {};
    _PROVEEDORES = provD.proveedores || [];
    const provNames = _PROVEEDORES.map((p) => p.nombre);
    // combo de zonas: las reales de Frigate (sync de config) sin las ya configuradas
    const conocidas = new Set();
    Object.values(estD.zonas_frigate || {}).forEach((zs) => zs.forEach((z) => conocidas.add(z)));
    (estD.zonas_interes || []).forEach((z) => conocidas.add(z));
    const disponibles = [...conocidas].filter((z) => !(z in zonas)).sort();
    const sel = $("#vz-nombre");
    if (disponibles.length) {
      sel.innerHTML = disponibles.map((z) => `<option value="${esc(z)}">${esc(z)}</option>`).join("");
      sel.disabled = false;
    } else {
      sel.innerHTML = '<option value="">(todas las zonas ya configuradas)</option>';
      sel.disabled = true;
    }
    $("#vision-zonas").innerHTML = `<table><tr><th>Zona</th><th>Habilitada</th><th>Reescalar</th><th></th></tr>` +
      Object.entries(zonas).map(([z, v]) => `<tr>
        <td><b>${esc(z)}</b></td>
        <td><input type="checkbox" class="vz-hab" data-zona="${esc(z)}" title="Habilita la visión en esta zona: cada evento de persona se analiza con el modelo único de la app." ${v.habilitado ? "checked" : ""}></td>
        <td><input type="checkbox" class="vz-res" data-zona="${esc(z)}" title="Reescala la imagen (máx 640px) antes de enviarla al modelo: ~2x más rápido en GPU chicas." ${v.reescalar ? "checked" : ""}></td>
        <td><button class="del" title="Guarda SOLO esta zona (checks)." onclick="guardarVisionZona('${esc(z)}')">💾</button>
            <button class="del" title="Quita la visión de esta zona (no elimina la zona de Frigate)." onclick="eliminarVisionZona('${esc(z)}')">✕</button></td>
      </tr>`).join("") + "</table>";
    $("#prov-list").innerHTML = `<table><tr><th>Proveedor</th><th>Tipo</th><th>Base URL</th><th>API key</th><th></th></tr>` +
      _PROVEEDORES.map((p) => `<tr><td><b>${esc(p.nombre)}</b></td><td>${esc(p.tipo)}</td>
        <td>${esc(p.base_url)}</td>
        <td>${p.api_key_configurada ? "<span class='badge badge-ok'>✓</span>" : "<span class='badge badge-warn'>sin key</span>"}</td>
        <td><button class="del" title="Editar este proveedor." onclick="editarProveedor('${esc(p.nombre)}','${esc(p.tipo)}','${esc(p.base_url || "")}')">✏️</button>
            <td><button class="del" title="Eliminar este proveedor (requiere confirmación)." onclick="eliminarProveedor('${esc(p.nombre)}')">✕</button></td></tr>`).join("") + "</table>";
            renderVisionDefault();
            } catch (e) { toast(e.message, true); }
            }
async function guardarVisionZona(z) {
  const fila = document.querySelector(`[data-zona="${CSS.escape(z)}"]`)?.closest("tr");
  if (!fila) return;
  try {
    await fetchJson(`/api/vision/zonas/${encodeURIComponent(z)}`, { method: "PUT", body: JSON.stringify({
      habilitado: fila.querySelector(".vz-hab").checked,
      reescalar: fila.querySelector(".vz-res").checked }) });
    toast(`zona ${z} guardada (≤60s)`);
  } catch (err) { toast(err.message, true); }
}
function renderVisionDefault() {
  /* CRUD de modelos de visión: proveedor + modelo + activo (uno solo). */
  const sel = $("#md-proveedor");
  if (sel) sel.innerHTML = _PROVEEDORES.map((p) => `<option value="${esc(p.nombre)}">${esc(p.nombre)}</option>`).join("");
  fetchJson("/api/vision/modelos").then((d) => {
    const mods = d.modelos || [];
    $("#modelos-list").innerHTML = `<table><tr><th>Proveedor</th><th>Modelo</th><th>Activo</th><th></th></tr>` +
      mods.map((m, i) => `<tr>
        <td><b>${esc(m.proveedor)}</b></td>
        <td>${esc(m.modelo)}</td>
        <td>${m.activo ? "<span class='badge badge-ok'>⭐ activo</span>"
                       : `<button class="del" title="Poner este modelo como predeterminado (el único activo)." onclick="setModeloActivo(${i})">activar</button>`}</td>
        <td><button class="del" title="Eliminar este modelo." onclick="eliminarModelo(${i})">✕</button></td>
      </tr>`).join("") + "</table>";
  }).catch((e) => toast(e.message, true));
}
async function setModeloActivo(i) {
  try {
    await fetchJson(`/api/vision/modelos/${i}`, { method: "PUT", body: JSON.stringify({ activo: true }) });
    toast("modelo activo actualizado (≤60s)");
    renderVisionDefault();
  } catch (err) { toast(err.message, true); }
}
async function eliminarModelo(i) {
  try {
    await fetchJson(`/api/vision/modelos/${i}`, { method: "DELETE" });
    toast("modelo eliminado (≤60s)");
    renderVisionDefault();
  } catch (err) { toast(err.message, true); }
}
bind("#form-modelo", "submit", async (e) => {
  e.preventDefault();
  try {
    await fetchJson("/api/vision/modelos", { method: "POST", body: JSON.stringify({
      proveedor: $("#md-proveedor").value, modelo: $("#md-modelo").value.trim() }) });
    toast("modelo agregado (≤60s)");
    $("#md-modelo").value = "";
    renderVisionDefault();
  } catch (err) { toast(err.message, true); }
});
bind("#form-vision-zona", "submit", async (e) => {
  e.preventDefault();
  const zona = $("#vz-nombre").value.trim();
  if (!zona) return;
  try {
    await fetchJson("/api/vision/zonas", { method: "POST", body: JSON.stringify({
      zona, habilitado: true }) });
    toast(`zona ${zona} agregada a visión`);
    $("#vz-nombre").value = "";
    cargarVision();
  } catch (err) { toast(err.message, true); }
});
function editarProveedor(nombre, tipo, base) {
  _EDIT_PROV = nombre;
  $("#pv-nombre").value = nombre; $("#pv-nombre").disabled = true;
  $("#pv-tipo").value = tipo; $("#pv-base").value = base; $("#pv-key").value = "";
  $("#form-prov button[type=submit]").textContent = "Actualizar";
  $("#btn-prov-cancel").classList.remove("hidden");
}
bind("#btn-prov-cancel", "click", () => {
  _EDIT_PROV = null; $("#pv-nombre").disabled = false; $("#form-prov").reset();
  $("#form-prov button[type=submit]").textContent = "Guardar proveedor";
  $("#btn-prov-cancel").classList.add("hidden");
});
async function eliminarProveedor(nombre) {
  if (!confirm(`¿Eliminar el proveedor ${nombre}?`)) return;
  try {
    await fetchJson(`/api/proveedores/${encodeURIComponent(nombre)}`, { method: "DELETE" });
    toast(`proveedor ${nombre} eliminado`);
    cargarVision();
  } catch (e) { toast(e.message, true); }
}
async function eliminarVisionZona(zona) {
  if (!confirm(`¿Quitar visión de la zona ${zona}?`)) return;
  try {
    await fetchJson(`/api/vision/zonas/${encodeURIComponent(zona)}`, { method: "DELETE" });
    toast(`visión de ${zona} eliminada`);
    cargarVision();
  } catch (e) { toast(e.message, true); }
}
bind("#form-prov", "submit", async (e) => {
  e.preventDefault();
  const body = {
    nombre: $("#pv-nombre").value.trim(), tipo: $("#pv-tipo").value,
    base_url: $("#pv-base").value.trim(),
    api_key: $("#pv-key").value.trim() || null };
  try {
    if (_EDIT_PROV) {
      await fetchJson(`/api/proveedores/${encodeURIComponent(_EDIT_PROV)}`, { method: "PUT", body: JSON.stringify(body) });
      toast("proveedor actualizado");
    } else {
      await fetchJson("/api/proveedores", { method: "POST", body: JSON.stringify(body) });
      toast("proveedor guardado");
    }
    _EDIT_PROV = null; $("#pv-nombre").disabled = false; $("#form-prov").reset();
    $("#form-prov button[type=submit]").textContent = "Guardar proveedor";
    $("#btn-prov-cancel").classList.add("hidden");
    cargarVision();
  } catch (err) { toast(err.message, true); }
});

/* ---------- TELEGRAM: bots (CRUD) + reglas de notificación ---------- */
let _REGLAS = {};
let _BOTS = [];
let _EDIT_BOT = null;
const TIPOS_NOTIF = ["alerta_critica", "alerta_alta", "alerta_media", "alerta_baja", "offline", "drift", "test"];
const RULES_META = {
  alerta_critica: ["🚨🚨", "Persona NOCTURNA en zona vigilada, o veredicto 'sospechoso' de la visión"],
  alerta_alta: ["🚨", "Severidad 'alert' de Frigate o merodeo (loitering en la zona)"],
  alerta_media: ["⚡", "Evento normal en zona de interés (ej. persona en puertacalle de día)"],
  alerta_baja: ["ℹ️", "Persona conocida por rostro, o visita recurrente"],
  offline: ["⚠️", "Detector o cámara de Frigate se cae (cooldown 5 min)"],
  drift: ["🔄", "Cambios en la configuración de Frigate (cámara/zona nueva, eliminada, objetos)"],
  test: ["🧪", "Botón 'Probar alerta' de la app"],
};
const GRUPOS_NOTIF = {
  "🔔 Alertas de eventos": ["alerta_critica", "alerta_alta", "alerta_media", "alerta_baja"],
  "🛠️ Sistema": ["offline", "drift", "test"],
};
let _VTP = {};
let _TG_CARGADO = false;
async function cargarTelegram() {
  _TG_CARGADO = false;
  try {
    const [d, cfgD, prioD] = await Promise.all([
      fetchJson("/api/notificaciones"), fetchJson("/api/config"), fetchJson("/api/vision/prioridades")]);
    _REGLAS = d.reglas || {};
    _BOTS = d.bots || [];
    _VTP = prioD.prioridades || {};
    $("#bots-list").innerHTML = `<table><tr><th>Bot</th><th>chat_id</th><th>Token</th><th>Habilitado</th><th></th></tr>` +
      _BOTS.map((b) => `<tr><td><b>${esc(b.nombre)}</b></td><td>${esc(b.chat_id)}</td>
        <td>${b.token_configurado ? "<span class='badge badge-ok'>✓</span>" : "<span class='badge badge-warn'>sin token</span>"}</td>
        <td>${b.habilitado ? "✅" : "⛔"}</td>
        <td><button class="del" title="Editar este bot." onclick="editarBot('${esc(b.nombre)}','${esc(b.chat_id || "")}',${b.habilitado})">✏️</button>
            <button class="del" title="Eliminar este bot (requiere confirmación)." onclick="eliminarBot('${esc(b.nombre)}')">✕</button></td></tr>`).join("") + "</table>";
    const nombres = _BOTS.map((b) => b.nombre);
    $("#reglas-list").innerHTML = Object.entries(GRUPOS_NOTIF).map(([grupo, tipos]) =>
      `<h4>${grupo}</h4><table><tr><th>Regla (cuándo se dispara)</th><th>Activa</th><th>Bots</th></tr>` +
      tipos.map((t) => {
        const r = _REGLAS[t] || { bots: [], habilitado: true };
        const [emoji, desc] = RULES_META[t] || ["", ""];
        const nombre = t.startsWith("alerta_") ? t.replace("alerta_", "") : t;
        return `<tr>
          <td><b>${emoji} ${esc(nombre)}</b><br><small class="sub">${esc(desc)}</small></td>
          <td><input type="checkbox" class="nt-hab" data-tipo="${esc(t)}" title="Activa o desactiva esta regla. Desactivada = esta notificación no se evalúa." ${r.habilitado === false ? "" : "checked"}></td>
          <td><div class="chips">` +
            (nombres.length ? nombres.map((n) =>
              `<button type="button" class="chip ${(r.bots || []).includes(n) ? "chip-on" : ""}" title="Chip: encendido = esta notificación va a ese bot. Un toque lo enciende o apaga." onclick="toggleReglaBot('${esc(t)}','${esc(n)}',this)">${esc(n)}</button>`).join("")
              : "<small class='sub'>sin bots creados</small>") + `</div></td>
        </tr>`;
      }).join("") + "</table>").join("");
    $("#vision-prioridad-list").innerHTML = `<table><tr><th>Tipo de visión</th><th>Prioridad de alerta</th><th></th></tr>` +
      Object.entries(_VTP).sort(([a], [b]) => a.localeCompare(b)).map(([t, p]) => `<tr><td><b>${esc(t)}</b></td>
        <td><select class="vtp-sel" data-tipo="${esc(t)}" title="Prioridad de alerta para este tipo de visión. Cambia al seleccionar (se guarda solo).">
          ${["baja", "media", "alta", "critica"].map((x) => `<option value="${x}" ${p === x ? "selected" : ""}>${esc(x)}</option>`).join("")}
        </select></td>
        <td><button class="del" title="Elimina este tipo de visión (requiere confirmación)." onclick="eliminarVTP('${esc(t)}')">✕</button></td></tr>`).join("") + "</table>";
    _TG_CARGADO = true;
  } catch (e) { toast(e.message, true); }
}
async function toggleReglaBot(tipo, bot, el) {
  const r = _REGLAS[tipo] = _REGLAS[tipo] || { bots: [], habilitado: true };
  const i = r.bots.indexOf(bot);
  if (i >= 0) r.bots.splice(i, 1); else r.bots.push(bot);
  el.classList.toggle("chip-on");
  await guardarReglas();
}
async function guardarReglas() {
  if (!_TG_CARGADO) { toast("reglas aún cargando — espere", true); return; }
  try {
    const d = await fetchJson("/api/config");
    const tg = d.config.telegram = d.config.telegram || {};
    /* MERGE: nunca reemplaza las reglas enteras con estado parcial de la UI
       (evita borrar reglas por un clic antes de que cargue la pestaña). */
    tg.reglas = { ...(tg.reglas || {}), ..._REGLAS };
    await fetchJson("/api/config", { method: "PUT", body: JSON.stringify(d.config) });
    toast("reglas de notificación guardadas");
  } catch (e) { toast(e.message, true); }
}
document.addEventListener("change", async (e) => {
  const el = e.target;
  if (!el.classList) return;
  if (el.classList.contains("nt-hab")) {
    const t = el.dataset.tipo;
    const r = _REGLAS[t] = _REGLAS[t] || { bots: [], habilitado: true };
    r.habilitado = el.checked;
    await guardarReglas();
    return;
  }
  if (el.classList.contains("vtp-sel")) {
    const t = el.dataset.tipo;
    try {
      await fetchJson(`/api/vision/prioridades/${encodeURIComponent(t)}`, { method: "PUT", body: JSON.stringify({ prioridad: el.value }) });
      toast(`prioridad '${t}' → ${el.value} (≤60s)`);
    } catch (err) { toast(err.message, true); }
  }
});
async function eliminarVTP(tipo) {
  if (!confirm(`¿Eliminar la prioridad del tipo '${tipo}'?`)) return;
  try {
    await fetchJson(`/api/vision/prioridades/${encodeURIComponent(tipo)}`, { method: "DELETE" });
    toast(`tipo '${tipo}' eliminado`);
    cargarTelegram();
  } catch (e) { toast(e.message, true); }
}
bind("#form-vtp", "submit", async (e) => {
  e.preventDefault();
  const tipo = $("#vtp-tipo").value.trim();
  if (!tipo) return;
  try {
    await fetchJson("/api/vision/prioridades", { method: "POST", body: JSON.stringify({ tipo, prioridad: $("#vtp-prioridad").value }) });
    toast(`tipo '${tipo}' agregado (≤60s)`);
    $("#vtp-tipo").value = "";
    cargarTelegram();
  } catch (err) { toast(err.message, true); }
});
function editarBot(nombre, chat, hab) {
  _EDIT_BOT = nombre;
  $("#bt-nombre").value = nombre; $("#bt-nombre").disabled = true;
  $("#bt-chat").value = chat; $("#bt-token").value = ""; $("#bt-habilitado").checked = hab;
  $("#form-bot button[type=submit]").textContent = "Actualizar";
  $("#btn-bot-cancel").classList.remove("hidden");
}
bind("#btn-bot-cancel", "click", () => {
  _EDIT_BOT = null; $("#bt-nombre").disabled = false; $("#form-bot").reset();
  $("#bt-habilitado").checked = true;
  $("#form-bot button[type=submit]").textContent = "Agregar bot";
  $("#btn-bot-cancel").classList.add("hidden");
});
bind("#form-bot", "submit", async (e) => {
  e.preventDefault();
  const nombre = $("#bt-nombre").value.trim();
  if (!nombre) return;
  const body = { nombre, chat_id: $("#bt-chat").value.trim(), habilitado: $("#bt-habilitado").checked };
  try {
    if (_EDIT_BOT) {
      body.token = $("#bt-token").value.trim() || null;
      await fetchJson(`/api/bots/${encodeURIComponent(_EDIT_BOT)}`, { method: "PUT", body: JSON.stringify(body) });
      toast(`bot ${_EDIT_BOT} actualizado`);
    } else {
      body.token = $("#bt-token").value.trim() || null;
      await fetchJson("/api/bots", { method: "POST", body: JSON.stringify(body) });
      toast(`bot ${nombre} agregado`);
    }
    _EDIT_BOT = null; $("#bt-nombre").disabled = false; $("#form-bot").reset();
    $("#bt-habilitado").checked = true;
    $("#form-bot button[type=submit]").textContent = "Agregar bot";
    $("#btn-bot-cancel").classList.add("hidden");
    cargarTelegram();
  } catch (err) { toast(err.message, true); }
});
async function eliminarBot(nombre) {
  if (!confirm(`¿Eliminar el bot ${nombre}?`)) return;
  try {
    await fetchJson(`/api/bots/${encodeURIComponent(nombre)}`, { method: "DELETE" });
    toast(`bot ${nombre} eliminado`);
    cargarTelegram();
  } catch (e) { toast(e.message, true); }
}

/* ---------- MEMORIA: CRUD de componentes (editar sí, borrar NO) ---------- */
async function cargarMemoriaConf() {
  try {
    const d = await fetchJson("/api/memoria/config");
    const l = d.llm || {}, e = d.embedder || {}, v = d.vector_store || {}, h = d.health || {};
    const badge = (ok) => ok
      ? '<span class="badge badge-ok">✓ OK</span>'
      : '<span class="badge badge-warn">✗ caído</span>';
    $("#memoria-conf").innerHTML = `
      <table><tr><th>Componente</th><th>Proveedor</th><th>Configuración</th><th></th></tr>
      <tr><td><b>Embedder</b><br><small class="sub">vectoriza cada hecho de memoria</small></td><td>${esc(e.provider || "—")}</td>
        <td><input class="mc-inp" data-c="emb" data-f="modelo" title="Modelo de embedding local (ej. nomic-embed-text). Cambiarlo invalida los vectores viejos si cambian las dimensiones." value="${esc(e.modelo || "")}" placeholder="modelo" style="width:190px">
            <input class="mc-inp" data-c="emb" data-f="base_url" title="URL del servidor de embeddings." value="${esc(e.base_url || "")}" placeholder="base_url" style="width:250px">
            <input class="mc-inp" data-c="emb" data-f="dims" title="Dimensiones del vector (nomic = 768)." value="${esc(e.dims ?? "")}" placeholder="dims" style="width:70px"></td>
        <td><button class="btn-test" title="Guarda SOLO este componente." onclick="guardarMemoriaComp('emb')">💾</button></td></tr>
      <tr><td><b>Vector store</b><br><small class="sub">donde viven los vectores</small></td><td>${esc(v.provider || "—")}</td>
        <td><input class="mc-inp" data-c="vs" data-f="url" title="URL de Qdrant. Cambiarla apunta la memoria a otro servidor." value="${esc(v.url || "")}" placeholder="url" style="width:230px">
            <input class="mc-inp" data-c="vs" data-f="coleccion" title="Colección de vectores. Cambiarla huerfaniza la memoria existente." value="${esc(v.coleccion || "")}" placeholder="colección" style="width:160px">
            <input class="mc-inp" data-c="vs" data-f="api_key" type="password" title="API key de Qdrant (opcional — seguridad del vector store). Va al .env como QDRANT_API_KEY, nunca al JSON." value="" placeholder="API key (opcional)" style="width:180px"></td>
        <td><button class="btn-test" title="Guarda SOLO este componente (incluye la API key si la escribió)." onclick="guardarMemoriaComp('vs')">💾</button></td></tr>
      <tr><td colspan="4" class="sub">Salud en vivo: Ollama ${badge(h.ollama)} · Qdrant ${badge(h.qdrant)} · ${v.puntos != null ? v.puntos + " puntos en la colección" : ""} · los cambios se aplican al reiniciar el daemon</td></tr>
    </table>`;
  } catch (e) { toast(e.message, true); }
}
async function guardarMemoriaComp(tipo) {
  const fila = [...document.querySelectorAll(".mc-inp")].filter((i) => i.dataset.c === tipo);
  const val = (f) => (fila.find((i) => i.dataset.f === f) || {}).value || "";
  const body = {};
  if (tipo === "llm") { body.llm_modelo = val("modelo"); body.llm_base_url = val("base_url"); }
  if (tipo === "emb") { body.modelo = val("modelo"); body.base_url = val("base_url"); body.dims = val("dims"); }
  if (tipo === "vs") { body.vs_url = val("url"); body.vs_coleccion = val("coleccion"); body.vs_api_key = val("api_key"); }
  try {
    const r = await fetchJson("/api/memoria/config", { method: "PUT", body: JSON.stringify(body) });
    toast(r.detalle || "guardado");
    cargarMemoriaConf();
  } catch (err) { toast(err.message, true); }
}

/* ---------- CONFIG (momentos + globales) ---------- */
let _MOMENTOS = [];
let _ZONAS_CONOCIDAS = [];
let _MOM_CARGADO = false;
async function cargarConfig() {
  _MOM_CARGADO = false;
  try {
    const [cfgD, estD] = await Promise.all([fetchJson("/api/config"), fetchJson("/api/estado")]);
    const cfg = cfgD.config || {};
    const pol = cfg.politica || {};
    _MOMENTOS = pol.momentos || [];
    _ZONAS_CONOCIDAS = [...new Set([
      ...(estD.zonas_activas || []),
      ...Object.values(estD.zonas_frigate || {}).flat(),
    ])].sort();
    $("#c-offline").value = pol.offline_cooldown ?? "";
    $("#c-heartbeat").value = (cfg.logs || {}).heartbeat_log ?? pol.heartbeat_log ?? "";
    $("#c-car-cooldown").value = pol.car_cooldown_s ?? "";
    const mvd = pol.movimiento_min_px;
    const mvo = (typeof mvd === "object" && mvd) ? mvd : { car: mvd || 0 };
    $("#c-mov-car").value = mvo.car ?? "";
    $("#c-mov-person").value = mvo.person ?? "";
    $("#c-mov-dog").value = mvo.dog ?? "";
    $("#c-mqtt-host").value = (cfg.conexion || {}).mqtt_host || "";
    $("#c-mqtt-port").value = (cfg.conexion || {}).mqtt_port ?? "";
    $("#c-frigate").value = (cfg.conexion || {}).frigate_api || "";
    $("#c-tg").value = (cfg.conexion || {}).telegram_chat || "";
    renderMomentos();
    $("#config-msg").textContent = "";
    _MOM_CARGADO = true;
  } catch (e) { toast(e.message, true); }
}
function renderMomentos() {
  $("#momentos-list").innerHTML = `<table><tr><th>Momento</th><th>Desde</th><th>Hasta</th><th>Nocturno</th><th>Zonas</th><th></th></tr>` +
    _MOMENTOS.map((m) => `<tr>
      <td><input class="mz-nombre" data-momento="${esc(m.nombre)}" title="Nombre del momento (editable; se guarda con 💾)." value="${esc(m.nombre)}" style="min-width:90px"></td>
      <td><input class="mz-desde" type="time" data-momento="${esc(m.nombre)}" title="Hora de inicio de la ventana." value="${esc(m.desde)}"></td>
      <td><input class="mz-hasta" type="time" data-momento="${esc(m.nombre)}" title="Hora de fin de la ventana." value="${esc(m.hasta)}"></td>
      <td><input type="checkbox" class="mz-nocturno" data-momento="${esc(m.nombre)}" title="Nocturno: las personas de este momento son prioridad CRÍTICA." ${m.nocturno ? "checked" : ""}></td>
      <td><div class="chips">` +
        (_ZONAS_CONOCIDAS.length ? _ZONAS_CONOCIDAS.map((z) =>
          `<button type="button" class="chip ${(m.zonas || []).includes(z) ? "chip-on" : ""}" title="Zona que vigila este momento (pastilla clicable; se guarda con 💾)." onclick="toggleMomentoZona('${esc(m.nombre)}','${esc(z)}',this)">${esc(z)}</button>`).join("")
          : "<small class='sub'>sin zonas de Frigate</small>") + `</div></td>
      <td><button class="del" onclick="guardarMomento('${esc(m.nombre)}')" title="Guardar SOLO este momento (nada se guarda por tecla).">💾</button>
          <button class="del" onclick="eliminarMomento('${esc(m.nombre)}')" title="Eliminar este momento (requiere confirmación).">✕</button></td>
    </tr>`).join("") + "</table>";
}
async function guardarMomento(nombre) {
  /* Guardado EXPLÍCITO por registro: lee la fila y persiste solo ese momento. */
  const tr = document.querySelector(`.mz-nombre[data-momento="${CSS.escape ? CSS.escape(nombre) : nombre}"]`)?.closest("tr");
  if (!tr) return;
  const m = _MOMENTOS.find((x) => x.nombre === nombre);
  if (!m) return;
  m.nombre = (tr.querySelector(".mz-nombre").value.trim() || m.nombre);
  m.desde = tr.querySelector(".mz-desde").value || m.desde;
  m.hasta = tr.querySelector(".mz-hasta").value || m.hasta;
  m.nocturno = tr.querySelector(".mz-nocturno").checked;
  /* m.zonas ya se mantiene al día con los chips (toggleMomentoZona) */
  await guardarMomentos();
  renderMomentos();
  toast(`momento ${m.nombre} guardado (≤60s)`);
}
function toggleMomentoZona(nombre, zona, el) {
  const m = _MOMENTOS.find((x) => x.nombre === nombre);
  if (!m) return;
  m.zonas = m.zonas || [];
  const i = m.zonas.indexOf(zona);
  if (i >= 0) m.zonas.splice(i, 1); else m.zonas.push(zona);
  el.classList.toggle("chip-on");
}
async function guardarMomentos() {
  if (!_MOM_CARGADO) { toast("momentos aún cargando — espere", true); return; }
  try {
    const d = await fetchJson("/api/config");
    d.config.politica = d.config.politica || {};
    d.config.politica.momentos = _MOMENTOS;
    await fetchJson("/api/config", { method: "PUT", body: JSON.stringify(d.config) });
    toast("momentos guardados (≤60s)");
  } catch (e) { toast(e.message, true); }
}
async function eliminarMomento(nombre) {
  if (!confirm(`¿Eliminar el momento ${nombre}?`)) return;
  _MOMENTOS = _MOMENTOS.filter((m) => m.nombre !== nombre);
  await guardarMomentos();
  renderMomentos();
}
bind("#form-momento", "submit", async (e) => {
  e.preventDefault();
  const nombre = $("#m-nombre").value.trim();
  if (!nombre) return;
  if (_MOMENTOS.some((m) => m.nombre === nombre)) { toast("ese momento ya existe", true); return; }
  _MOMENTOS.push({ nombre, desde: $("#m-desde").value || "00:00", hasta: $("#m-hasta").value || "23:59",
                   zonas: [], nocturno: $("#m-nocturno").checked });
  await guardarMomentos();
  renderMomentos();
  $("#m-nombre").value = ""; $("#m-nocturno").checked = false;
});
bind("#form-config", "submit", async (e) => {
  e.preventDefault();
  try {
    const actual = await fetchJson("/api/config");
    const cfg = { ...actual.config };
    cfg.politica = cfg.politica || {};
    cfg.politica.momentos = _MOMENTOS;
    cfg.politica.offline_cooldown = parseInt($("#c-offline").value) || 300;
    cfg.politica.car_cooldown_s = parseInt($("#c-car-cooldown").value) || 0;
    cfg.politica.movimiento_min_px = {
      car: parseInt($("#c-mov-car").value) || 0,
      person: parseInt($("#c-mov-person").value) || 0,
      dog: parseInt($("#c-mov-dog").value) || 0,
      cat: 0,
    };
    cfg.logs = { ...(cfg.logs || {}), heartbeat_log: parseInt($("#c-heartbeat").value) || 300 };
    await fetchJson("/api/config", { method: "PUT", body: JSON.stringify(cfg) });
    $("#config-msg").className = "msg";
    $("#config-msg").textContent = "guardada — el daemon la aplica en ≤60 s sin reiniciar";
    toast("configuración guardada");
  } catch (err) {
    $("#config-msg").className = "msg err";
    $("#config-msg").textContent = err.message;
  }
});

/* ---------- MEMORIA (búsqueda semántica) ---------- */
async function cargarMemoria() {
  const q = $("#m-q").value.trim();
  if (!q) {
    $("#mem-result").innerHTML = "<p class='sub'>buscá en la memoria del Vigilador — ej: \"¿a qué hora pasó el sodero?\"</p>";
    return;
  }
  try {
    const d = await fetchJson(`/api/memoria?q=${encodeURIComponent(q)}&limite=${$("#m-limite").value || 10}`);
    const rs = d.resultados || [];
    $("#mem-result").innerHTML = rs.length
      ? `<table><tr><th>Score</th><th>Memoria</th></tr>` +
        rs.map((r) => `<tr><td>${(r.score * 100).toFixed(0)}%</td><td>${esc(r.memoria)}</td></tr>`).join("") + "</table>"
      : "<p class='sub'>sin resultados</p>";
  } catch (e) { $("#mem-result").innerHTML = `<p class='msg err'>${esc(e.message)}</p>`; }
}
bind("#form-mem", "submit", (e) => { e.preventDefault(); cargarMemoria(); });

/* ---------- LOGS ---------- */
async function cargarLogs() {
  try {
    const tipo = $("#l-tipo").value;
    const n = $("#l-lineas").value || 120;
    const errs = $("#l-errores").checked ? "1" : "0";
    const d = await fetchJson(`/api/logs?tipo=${tipo}&lineas=${n}&errores=${errs}`);
    $("#logs-info").textContent = `${d.archivo} · ${d.lineas} líneas`;
    $("#logs-out").textContent = d.contenido.join("\n") || "(vacío)";
  } catch (e) { $("#logs-out").textContent = "error: " + e.message; }
}
bind("#form-logs", "submit", (e) => { e.preventDefault(); cargarLogs(); });
setInterval(() => { if ($("#tab-logs").classList.contains("active")) cargarLogs(); }, 20000);

/* ---------- alerta de prueba ---------- */
bind("#btn-test-alerta", "click", async () => {
  try {
    const d = await fetchJson("/api/test-alerta", { method: "POST" });
    toast(d.ok ? "alerta de prueba enviada a Telegram" : "falló: " + d.detalle, !d.ok);
  } catch (e) { toast(e.message, true); }
});

/* ---------- arranque ---------- */
cargarEstado();
cargarPersonasCache();
aplicarAyuda();
setInterval(cargarEstado, 30000);
