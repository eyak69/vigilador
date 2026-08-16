/* Vigilador app — módulo personas.js (corte de app.js). */
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

