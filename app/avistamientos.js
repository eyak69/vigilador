/* Vigilador app — módulo avistamientos.js (corte de app.js). */
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

