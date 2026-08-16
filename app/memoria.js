/* Vigilador app — módulo memoria.js (corte de app.js). */
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

