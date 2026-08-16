/* Vigilador app — módulo placas.js (corte de app.js). */
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

