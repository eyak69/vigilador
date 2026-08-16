/* Vigilador app — módulo perfiles.js (corte de app.js). */
/* ---------- arranque ---------- */
cargarEstado();
cargarPersonasCache();
aplicarAyuda();
setInterval(cargarEstado, 30000);


// --- PERFILES DE ZONA & SYNC FRIGATE ---

function renderPerfilesList() {
  const el = $("#perfiles-list");
  if (!el) return;
  const entries = Object.entries(_PERFILES);
  if (!entries.length) {
    el.innerHTML = '<p class="sub">No hay perfiles creados. Crea uno abajo para asignar contexto a tus zonas.</p>';
    return;
  }
  el.innerHTML = `<table><tr><th>ID</th><th>Nombre</th><th>Prompt Contexto</th><th>Modelo Override</th><th></th></tr>` +
    entries.map(([pid, p]) => `<tr>
      <td><b>${esc(pid)}</b></td>
      <td>${esc(p.nombre || "")}</td>
      <td><small style="color:var(--text-dim);">${esc(p.prompt || "(sin prompt)")}</small></td>
      <td>${esc(p.modelo || "(activo global)")}</td>
      <td>
        <button class="del" title="Editar este perfil" onclick="editarPerfil('${esc(pid)}')">✏️</button>
        <button class="del" title="Eliminar este perfil" onclick="eliminarPerfil('${esc(pid)}')">✕</button>
      </td>
    </tr>`).join("") + "</table>";
}

function editarPerfil(pid) {
  const p = _PERFILES[pid];
  if (!p) return;
  $("#pf-id").value = p.id || pid;
  $("#pf-id").disabled = true;
  $("#pf-nombre").value = p.nombre || "";
  $("#pf-prompt").value = p.prompt || "";
  $("#pf-modelo").value = p.modelo || "";
  $("#btn-perfil-cancel")?.classList.remove("hidden");
}

function cancelarEdicionPerfil() {
  $("#pf-id").value = "";
  $("#pf-id").disabled = false;
  $("#pf-nombre").value = "";
  $("#pf-prompt").value = "";
  $("#pf-modelo").value = "";
  $("#btn-perfil-cancel")?.classList.add("hidden");
}

async function eliminarPerfil(pid) {
  if (!confirm(`Eliminar el perfil '${pid}'? Las zonas asignadas volverán a sin perfil.`)) return;
  try {
    const res = await fetchJson(`/api/vision/perfiles/${encodeURIComponent(pid)}`, { method: "DELETE" });
    toast(res.detalle || "Perfil eliminado");
    cargarVisionConfig();
  } catch (err) { toast(err.message, true); }
}

async function syncZonasFrigate() {
  try {
    toast("Sincronizando zonas con Frigate API...");
    const res = await fetchJson("/api/zonas/sync", { method: "POST" });
    toast(res.detalle || "Zonas sincronizadas");
    cargarVisionConfig();
  } catch (err) { toast(err.message, true); }
}

// Refresca la lista de perfiles Y la tabla "Visión por zona" (tras crear/editar/eliminar/sync).
async function cargarVisionConfig() {
  try {
    const d = await fetchJson("/api/vision/perfiles");
    _PERFILES = d.perfiles || {};
    renderPerfilesList();
    if (typeof renderVisionZonas === "function") renderVisionZonas(_VZONAS);
  } catch (e) { toast(e.message, true); }
}

// Event Listeners para Formulario Perfil
document.addEventListener("DOMContentLoaded", () => {
  const formP = $("#form-perfil");
  if (formP) {
    formP.addEventListener("submit", async (e) => {
      e.preventDefault();
      const pid = $("#pf-id").value.trim().toLowerCase();
      const isEdit = $("#pf-id").disabled;
      const body = {
        id: pid,
        nombre: $("#pf-nombre").value.trim(),
        prompt: $("#pf-prompt").value.trim(),
        modelo: $("#pf-modelo").value.trim()
      };
      try {
        const method = isEdit ? "PUT" : "POST";
        const url = isEdit ? `/api/vision/perfiles/${encodeURIComponent(pid)}` : "/api/vision/perfiles";
        const res = await fetchJson(url, { method, body: JSON.stringify(body) });
        toast(res.detalle || "Perfil guardado");
        cancelarEdicionPerfil();
        cargarVisionConfig();
      } catch (err) { toast(err.message, true); }
    });
  }
  $("#btn-perfil-cancel")?.addEventListener("click", cancelarEdicionPerfil);
});
