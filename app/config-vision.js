/* Vigilador app — módulo config-vision.js (corte de app.js). */
/* ---------- VISIÓN POR ZONA + PROVEEDORES (CRUD) ---------- */
let _PROVEEDORES = [];
let _EDIT_PROV = null;
let _VZONAS = {};   // últimas zonas de visión (para redibujar tras cambios de perfiles)
async function cargarVision() {
  try {
    const [vzD, provD, estD, perfD] = await Promise.all([
      fetchJson("/api/vision/zonas"), fetchJson("/api/proveedores"),
      fetchJson("/api/estado"), fetchJson("/api/vision/perfiles")]);
    const zonas = vzD.zonas || {};
    _VZONAS = zonas;
    _PERFILES = perfD.perfiles || {};
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
    renderPerfilesList();
    renderVisionZonas(zonas);
    $("#prov-list").innerHTML = `<table><tr><th>Proveedor</th><th>Tipo</th><th>Base URL</th><th>API key</th><th></th></tr>` +
      _PROVEEDORES.map((p) => `<tr><td><b>${esc(p.nombre)}</b></td><td>${esc(p.tipo)}</td>
        <td>${esc(p.base_url)}</td>
        <td>${p.api_key_configurada ? "<span class='badge badge-ok'>✓</span>" : "<span class='badge badge-warn'>sin key</span>"}</td>
        <td><button class="del" title="Editar este proveedor." onclick="editarProveedor('${esc(p.nombre)}','${esc(p.tipo)}','${esc(p.base_url || "")}')">✏️</button>
            <td><button class="del" title="Eliminar este proveedor (requiere confirmación)." onclick="eliminarProveedor('${esc(p.nombre)}')">✕</button></td></tr>`).join("") + "</table>";
            renderVisionDefault();
            } catch (e) { toast(e.message, true); }
            }
function renderVisionZonas(zonas) {
  /* Sin perfil no hay visión: el perfil asigna explícitamente el ojo a la zona. */
  const el = $("#vision-zonas");
  if (!el) return;
  el.innerHTML = `<table><tr><th>Zona</th><th>Perfil de Zona</th><th>Habilitada</th><th>Reescalar</th><th></th></tr>` +
    Object.entries(zonas || {}).map(([z, v]) => `<tr>
      <td><b>${esc(z)}</b></td>
      <td>
        <select class="vz-perfil" data-zona="${esc(z)}" style="max-width:170px;" onchange="actualizarEstadoVisionFila(this)">
          <option value="">(sin perfil = sin visión)</option>
          ${Object.entries(_PERFILES).map(([pid, p]) => `<option value="${esc(pid)}" ${v.perfil_id === pid ? "selected" : ""}>${esc(p.nombre || pid)}</option>`).join("")}
        </select>
      </td>
      <td><input type="checkbox" class="vz-hab" data-zona="${esc(z)}" title="Habilita visión únicamente cuando esta zona tiene un perfil asignado." ${v.habilitado && v.perfil_id ? "checked" : ""} ${v.perfil_id ? "" : "disabled"}></td>
      <td><input type="checkbox" class="vz-res" data-zona="${esc(z)}" title="Reescala la imagen (máx 640px) antes de enviarla al modelo: ~2x más rápido en GPU chicas." ${v.reescalar ? "checked" : ""}></td>
      <td><button class="del" title="Guarda SOLO esta zona (checks)." onclick="guardarVisionZona('${esc(z)}')">💾</button>
          <button class="del" title="Quita la visión de esta zona (no elimina la zona de Frigate)." onclick="eliminarVisionZona('${esc(z)}')">✕</button></td>
    </tr>`).join("") + "</table>";
}
function actualizarEstadoVisionFila(sel) {
  const hab = sel.closest("tr")?.querySelector(".vz-hab");
  if (!hab) return;
  hab.disabled = !sel.value;
  if (!sel.value) hab.checked = false;
}
async function guardarVisionZona(z) {
  const fila = document.querySelector(`[data-zona="${CSS.escape(z)}"]`)?.closest("tr");
  if (!fila) return;
  const perfilId = fila.querySelector(".vz-perfil")?.value || "";
  if (fila.querySelector(".vz-hab").checked && !perfilId) {
    toast("Para habilitar visión debe asignar un perfil", true);
    return;
  }
  try {
    await fetchJson(`/api/vision/zonas/${encodeURIComponent(z)}`, { method: "PUT", body: JSON.stringify({
      habilitado: fila.querySelector(".vz-hab").checked,
      reescalar: fila.querySelector(".vz-res").checked,
      perfil_id: perfilId }) });
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
      zona, habilitado: false }) });
    toast(`zona ${zona} agregada sin visión; asigne un perfil para habilitarla`);
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

