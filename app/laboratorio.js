/* Vigilador app — módulo laboratorio.js: revisión de avistamientos y métricas
   de calibración (el "barrio aprendiendo": ground truth → precisión por zona). */

async function cargarLaboratorio() {
  try {
    const [lab, avis] = await Promise.all([
      fetchJson("/api/laboratorio"),
      fetchJson("/api/avistamientos?limite=15")]);
    const m = $("#lab-metricas");
    if (!m) return;
    const p = lab.precision;
    m.innerHTML =
      `<span class="badge">total ${lab.total}</span>
       <span class="badge badge-ok">revisados ${lab.revisados}</span>
       <span class="badge ${p === null ? "" : p >= 0.8 ? "badge-ok" : "badge-warn"}">precisión ${p === null ? "—" : (p * 100).toFixed(0) + "%"}</span>`;
    const zs = Object.entries(lab.por_zona || {});
    if (zs.length) {
      m.innerHTML += `<span class="sub" style="margin-left:10px">` +
        zs.map(([z, v]) => `${esc(z)}: ${v.correcto}✅ · ${v.falso_positivo}❌ · ${v.falso_negativo}➖`).join(" &nbsp;|&nbsp; ") +
        `</span>`;
    }
    $("#lab-lista").innerHTML = `<table><tr><th></th><th>#</th><th>Cam / Etiqueta</th><th>Hora</th><th>Veredicto</th><th>Revisión</th></tr>` +
      (avis.avistamientos || []).map((a) => {
        let v = null;
        try { v = a.veredicto ? JSON.parse(a.veredicto) : null; } catch (e) {}
        const t = a.inicio ? new Date(a.inicio * 1000).toLocaleString("es-AR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—";
        const estado = a.revision
          ? `<span class="badge ${a.revision === "correcto" ? "badge-ok" : "badge-warn"}">${esc(a.revision)}</span>`
          : "";
        const btns = `<button class="del" title="Correcto" onclick="revisarAvistamiento(${a.id},'correcto',this)">✅</button>
          <button class="del" title="Falso positivo (alertó de más)" onclick="revisarAvistamiento(${a.id},'falso_positivo',this)">❌</button>
          <button class="del" title="Falso negativo (debió alertar)" onclick="revisarAvistamiento(${a.id},'falso_negativo',this)">➖</button>`;
        const foto = a.foto
          ? `<img class="foto" title="Ver la foto (original + anotada)." src="${API}/api/avistamientos/${a.id}/foto" onclick="verFoto(${a.id})">`
          : `<div class="foto empty"></div>`;
        return `<tr><td>${foto}</td><td>#${a.id}</td><td>${esc(a.camara)} / ${esc(a.label)}</td>
          <td>${t}</td>
          <td>${v ? `<b>${esc(v.tipo || "")}</b> <small class="sub">${(v.confianza || 0).toFixed(2)} · ${esc(v._modelo || "")}</small>` : "—"}</td>
          <td>${estado} ${btns}</td></tr>`;
      }).join("") + "</table>";
  } catch (e) { toast(e.message, true); }
}

async function revisarAvistamiento(id, rev) {
  try {
    const res = await fetchJson(`/api/avistamientos/${id}/revision`, {
      method: "PUT", body: JSON.stringify({ revision: rev }) });
    toast(res.detalle || "revisión guardada");
    cargarLaboratorio();
  } catch (e) { toast(e.message, true); }
}
