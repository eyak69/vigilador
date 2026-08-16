/* Vigilador app — módulo config-telegram.js (corte de app.js). */
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

