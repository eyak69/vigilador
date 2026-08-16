/* Vigilador app — módulo dashboard.js (corte de app.js). */
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

