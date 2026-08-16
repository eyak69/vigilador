/* Vigilador app — módulo config-politica.js (corte de app.js). */
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

