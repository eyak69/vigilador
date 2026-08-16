/* Vigilador app — módulo config-memoria.js (corte de app.js). */
/* ---------- MEMORIA: CRUD de componentes (editar sí, borrar NO) ---------- */
async function cargarMemoriaConf() {
  try {
    const d = await fetchJson("/api/memoria/config");
    const l = d.llm || {}, e = d.embedder || {}, v = d.vector_store || {}, h = d.health || {};
    const badge = (ok) => ok
      ? '<span class="badge badge-ok">✓ OK</span>'
      : '<span class="badge badge-warn">✗ caído</span>';
    $("#memoria-conf").innerHTML = `
      <table><tr><th>Componente</th><th>Proveedor</th><th>Configuración</th><th></th></tr>
      <tr><td><b>Embedder</b><br><small class="sub">vectoriza cada hecho de memoria</small></td><td>${esc(e.provider || "—")}</td>
        <td><input class="mc-inp" data-c="emb" data-f="modelo" title="Modelo de embedding local (ej. nomic-embed-text). Cambiarlo invalida los vectores viejos si cambian las dimensiones." value="${esc(e.modelo || "")}" placeholder="modelo" style="width:190px">
            <input class="mc-inp" data-c="emb" data-f="base_url" title="URL del servidor de embeddings." value="${esc(e.base_url || "")}" placeholder="base_url" style="width:250px">
            <input class="mc-inp" data-c="emb" data-f="dims" title="Dimensiones del vector (nomic = 768)." value="${esc(e.dims ?? "")}" placeholder="dims" style="width:70px"></td>
        <td><button class="btn-test" title="Guarda SOLO este componente." onclick="guardarMemoriaComp('emb')">💾</button></td></tr>
      <tr><td><b>Vector store</b><br><small class="sub">donde viven los vectores</small></td><td>${esc(v.provider || "—")}</td>
        <td><input class="mc-inp" data-c="vs" data-f="url" title="URL de Qdrant. Cambiarla apunta la memoria a otro servidor." value="${esc(v.url || "")}" placeholder="url" style="width:230px">
            <input class="mc-inp" data-c="vs" data-f="coleccion" title="Colección de vectores. Cambiarla huerfaniza la memoria existente." value="${esc(v.coleccion || "")}" placeholder="colección" style="width:160px">
            <input class="mc-inp" data-c="vs" data-f="api_key" type="password" title="API key de Qdrant (opcional — seguridad del vector store). Va al .env como QDRANT_API_KEY, nunca al JSON." value="" placeholder="API key (opcional)" style="width:180px"></td>
        <td><button class="btn-test" title="Guarda SOLO este componente (incluye la API key si la escribió)." onclick="guardarMemoriaComp('vs')">💾</button></td></tr>
      <tr><td colspan="4" class="sub">Salud en vivo: Ollama ${badge(h.ollama)} · Qdrant ${badge(h.qdrant)} · ${v.puntos != null ? v.puntos + " puntos en la colección" : ""} · los cambios se aplican al reiniciar el daemon</td></tr>
    </table>`;
  } catch (e) { toast(e.message, true); }
}
async function guardarMemoriaComp(tipo) {
  const fila = [...document.querySelectorAll(".mc-inp")].filter((i) => i.dataset.c === tipo);
  const val = (f) => (fila.find((i) => i.dataset.f === f) || {}).value || "";
  const body = {};
  if (tipo === "llm") { body.llm_modelo = val("modelo"); body.llm_base_url = val("base_url"); }
  if (tipo === "emb") { body.modelo = val("modelo"); body.base_url = val("base_url"); body.dims = val("dims"); }
  if (tipo === "vs") { body.vs_url = val("url"); body.vs_coleccion = val("coleccion"); body.vs_api_key = val("api_key"); }
  try {
    const r = await fetchJson("/api/memoria/config", { method: "PUT", body: JSON.stringify(body) });
    toast(r.detalle || "guardado");
    cargarMemoriaConf();
  } catch (err) { toast(err.message, true); }
}

