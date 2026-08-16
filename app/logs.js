/* Vigilador app — módulo logs.js (corte de app.js). */
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

