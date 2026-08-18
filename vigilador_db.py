#!/usr/bin/env python3
"""
Vigilador — capa SQL de avistamientos (SQLite).
Complementa Qdrant (semántica) con búsquedas exactas/agregadas:
patentes, recorridos, conteos, actividad por cámara/hora.
Uso CLI:
  vigilador_db.py init
  vigilador_db.py patente ABC123
  vigilador_db.py recorrido ABC123
  vigilador_db.py hoy [YYYY-MM-DD]
  vigilador_db.py top [N]
  vigilador_db.py placa add ABC123 "Cristian" vecino
  vigilador_db.py placa list
  vigilador_db.py backfill <events.log>
"""
import os, sys, json, sqlite3, argparse, time, re
from datetime import datetime

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vigilador.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS avistamientos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evento_id TEXT UNIQUE,
    camara TEXT NOT NULL,
    label TEXT NOT NULL,
    patente TEXT,
    patente_score REAL,
    inicio REAL,
    fin REAL,
    duracion REAL,
    zonas TEXT,
    veredicto TEXT,
    prioridad TEXT,
    motivo_fin TEXT,
    foto TEXT,
    ts TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_av_patente ON avistamientos(patente);
CREATE INDEX IF NOT EXISTS idx_av_inicio ON avistamientos(inicio);
CREATE INDEX IF NOT EXISTS idx_av_camara ON avistamientos(camara);
CREATE INDEX IF NOT EXISTS idx_av_label ON avistamientos(label);
CREATE TABLE IF NOT EXISTS placas (
    patente TEXT PRIMARY KEY,
    nombre TEXT,
    tipo TEXT DEFAULT 'desconocido',
    notas TEXT,
    ts TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS personas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT UNIQUE NOT NULL,
    tipo TEXT DEFAULT 'familia',
    notas TEXT,
    ts TEXT DEFAULT (datetime('now'))
);
"""

def con():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=5000")
    return c

def init():
    c = con()
    c.executescript(SCHEMA)
    # migración: columna foto (si la base ya existía sin ella)
    cols = [r[1] for r in c.execute("PRAGMA table_info(avistamientos)").fetchall()]
    if "foto" not in cols:
        c.execute("ALTER TABLE avistamientos ADD COLUMN foto TEXT")
    if "persona" not in cols:
        c.execute("ALTER TABLE avistamientos ADD COLUMN persona TEXT")
        c.execute("CREATE INDEX IF NOT EXISTS idx_av_persona ON avistamientos(persona)")
    if "estado" not in cols:
        c.execute("ALTER TABLE avistamientos ADD COLUMN estado TEXT")
    c.commit()
    c.close()
    print(f"esquema listo en {DB}")

def label_corregido_por_vision(label, veredicto):
    """Corrige falsos ``person`` solo cuando visión descarta una persona.

    El label original de Frigate se conserva salvo que el tipo sea explícitamente
    animal o la descripción niegue una persona y, además, identifique un animal.
    """
    original = str(label or "").lower()
    if original != "person" or not isinstance(veredicto, dict) or veredicto.get("_error"):
        return original
    tipo = str(veredicto.get("tipo") or "").lower().strip()
    objetos = " ".join(str(x) for x in (veredicto.get("objetos") or []))
    texto = f"{tipo} {veredicto.get('descripcion') or ''} {objetos}".lower()
    contiene = lambda palabra: re.search(rf"(?<!\w){re.escape(palabra)}(?!\w)", texto) is not None
    mapa = (
        ("cat", ("gato", "gata", "felino", "cat")),
        ("dog", ("perro", "perra", "canino", "dog")),
        ("bird", ("pájaro", "pajaro", "ave", "bird")),
    )
    animal = next((destino for destino, palabras in mapa if any(contiene(p) for p in palabras)), None)
    if not animal and any(contiene(p) for p in ("animal", "mascota")):
        animal = "animal"
    tipo_animal = any(tipo == p for _, palabras in mapa for p in palabras) or tipo in ("animal", "mascota")
    niega_persona = any(p in texto for p in (
        "no se observa ninguna persona", "no se observa una persona", "no se observa persona",
        "no hay ninguna persona", "no hay persona", "ninguna persona", "sin persona",
    ))
    return animal if animal and (tipo_animal or niega_persona) else original

def actualizar_veredicto_avistamiento(evento_id, veredicto):
    """Guarda visión tardía y reclasifica el label estructural si corresponde."""
    c = con()
    try:
        row = c.execute("SELECT label FROM avistamientos WHERE evento_id=?", (str(evento_id),)).fetchone()
        if not row:
            return 0
        original = row["label"]
        corregido = label_corregido_por_vision(original, veredicto)
        guardado = dict(veredicto or {})
        if corregido != original:
            guardado.setdefault("label_original", original)
        cur = c.execute("UPDATE avistamientos SET veredicto=?, label=? WHERE evento_id=?",
                        (json.dumps(guardado, ensure_ascii=False), corregido, str(evento_id)))
        c.commit()
        return cur.rowcount
    finally:
        c.close()

def insertar_avistamiento(a):
    """a: dict con evento_id, camara, label, inicio, fin, duracion, zonas(list),
    patente, patente_score, veredicto, prioridad, motivo_fin, foto, persona, estado."""
    c = con()
    try:
        veredicto = dict(a.get("veredicto") or {})
        label_original = a.get("label")
        label = label_corregido_por_vision(label_original, veredicto)
        if label != label_original:
            veredicto.setdefault("label_original", label_original)
        c.execute("""INSERT OR IGNORE INTO avistamientos
            (evento_id, camara, label, patente, patente_score, inicio, fin, duracion,
             zonas, veredicto, prioridad, motivo_fin, foto, persona, estado)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (a.get("evento_id"), a.get("camara"), label,
             a.get("patente"), a.get("patente_score"),
             a.get("inicio"), a.get("fin"), a.get("duracion"),
             json.dumps(a.get("zonas") or [], ensure_ascii=False),
             json.dumps(veredicto, ensure_ascii=False) if veredicto else None,
             a.get("prioridad"), a.get("motivo_fin"), a.get("foto"), a.get("persona"),
             a.get("estado")))
        c.commit()
    finally:
        c.close()

def patente(p):
    c = con()
    rows = c.execute("""SELECT camara, label, patente, datetime(inicio,'unixepoch','localtime') AS desde,
                        datetime(fin,'unixepoch','localtime') AS hasta, duracion, zonas
                        FROM avistamientos WHERE patente = ? ORDER BY inicio""", (p.upper(),)).fetchall()
    c.close()
    print(f"Avistamientos de la patente {p.upper()}: {len(rows)}")
    for r in rows:
        print(f"  {r['desde']} → {r['hasta']} | {r['camara']} | {r['label']} | {r['duracion']}s | zonas {r['zonas']}")

def recorrido(p):
    c = con()
    rows = c.execute("""SELECT camara, datetime(inicio,'unixepoch','localtime') AS desde,
                        datetime(fin,'unixepoch','localtime') AS hasta, duracion, zonas
                        FROM avistamientos WHERE patente = ? ORDER BY inicio""", (p.upper(),)).fetchall()
    c.close()
    if not rows:
        print(f"Sin registro de la patente {p.upper()}")
        return
    print(f"Recorrido de {p.upper()}:")
    for r in rows:
        print(f"  {r['desde']}  {r['camara']:8s} {r['duracion']:6.1f}s  zonas: {r['zonas']}")

def hoy(fecha=None):
    c = con()
    if fecha:
        rows = c.execute("""SELECT camara, label, patente, datetime(inicio,'unixepoch','localtime') AS desde,
                            duracion, zonas FROM avistamientos
                            WHERE date(inicio,'unixepoch','localtime') = ? ORDER BY inicio""", (fecha,)).fetchall()
        titulo = f"Avistamientos del {fecha}"
    else:
        rows = c.execute("""SELECT camara, label, patente, datetime(inicio,'unixepoch','localtime') AS desde,
                            duracion, zonas FROM avistamientos
                            WHERE date(inicio,'unixepoch','localtime') = date('now','localtime')
                            ORDER BY inicio""").fetchall()
        titulo = "Avistamientos de hoy"
    c.close()
    print(f"{titulo}: {len(rows)}")
    for r in rows:
        pat = f" | patente {r['patente']}" if r["patente"] else ""
        print(f"  {r['desde']} | {r['camara']:8s} | {r['label']}{pat} | {r['duracion']}s")

def top(n=10):
    c = con()
    rows = c.execute("""SELECT patente, COUNT(*) AS n, GROUP_CONCAT(DISTINCT camara) AS cams
                        FROM avistamientos WHERE patente IS NOT NULL
                        GROUP BY patente ORDER BY n DESC LIMIT ?""", (n,)).fetchall()
    c.close()
    print(f"Patentes más vistas (top {n}):")
    for r in rows:
        print(f"  {r['patente']:10s} x{r['n']:3d}  cámaras: {r['cams']}")

def placa_existe(pat):
    c = con()
    n = c.execute("SELECT COUNT(*) FROM placas WHERE patente=?", (pat.upper(),)).fetchone()[0]
    c.close()
    return n > 0

def placa_add(pat, nombre, tipo="desconocido", notas=""):
    c = con()
    c.execute("INSERT OR REPLACE INTO placas (patente, nombre, tipo, notas) VALUES (?,?,?,?)",
              (pat.upper(), nombre, tipo, notas))
    c.commit()
    c.close()
    print(f"placa {pat.upper()} → {nombre} ({tipo})")

# ---------- registro de PERSONAS (identidad por nombre) ----------

def _normalizar_nombre(nombre):
    """Frigate a veces emite el rostro como \"['Cristian', 0.86]\" (string serializado).
    Extrae SOLO el nombre; si no es ese formato, devuelve el texto limpio."""
    n = (nombre or "").strip()
    if n.startswith("[") and n.endswith("]"):
        try:
            import ast
            parsed = ast.literal_eval(n)
            if isinstance(parsed, list) and parsed:
                return str(parsed[0]).strip()
        except Exception:
            pass
    return n

def personas():
    c = con()
    rows = c.execute("""SELECT p.id, p.nombre, p.tipo, p.notas,
                        (SELECT COUNT(*) FROM avistamientos a WHERE a.persona = p.nombre) AS avistamientos
                        FROM personas p ORDER BY p.nombre""").fetchall()
    c.close()
    return [dict(r) for r in rows]

def add_persona(nombre, tipo="familia", notas=""):
    c = con()
    c.execute("INSERT OR IGNORE INTO personas (nombre, tipo, notas) VALUES (?,?,?)",
              (_normalizar_nombre(nombre), tipo, notas))
    c.commit()
    c.close()

def set_persona_avistamiento(av_id, nombre):
    """Etiqueta un avistamiento con un nombre de persona (None para quitar)."""
    c = con()
    if nombre:
        add_persona(nombre, "familia")
        c.execute("UPDATE avistamientos SET persona = ? WHERE id = ?", (_normalizar_nombre(nombre).strip(), int(av_id)))
    else:
        c.execute("UPDATE avistamientos SET persona = NULL WHERE id = ?", (int(av_id),))
    c.commit()
    c.close()

def placa_update(pat, nombre=None, tipo=None, notas=None):
    c = con()
    cur = c.execute("UPDATE placas SET nombre=COALESCE(?,nombre), tipo=COALESCE(?,tipo), notas=COALESCE(?,notas) WHERE patente=?",
                    (nombre, tipo, notas, pat.upper()))
    c.commit()
    c.close()
    return cur.rowcount

def persona_update(pid, nombre=None, tipo=None, notas=None):
    c = con()
    cur = c.execute("UPDATE personas SET nombre=COALESCE(?,nombre), tipo=COALESCE(?,tipo), notas=COALESCE(?,notas) WHERE id=?",
                    (nombre, tipo, notas, int(pid)))
    c.commit()
    c.close()
    return cur.rowcount

def persona_delete(pid):
    c = con()
    cur = c.execute("DELETE FROM personas WHERE id=?", (int(pid),))
    c.commit()
    c.close()
    return cur.rowcount

def placa_list():
    c = con()
    rows = c.execute("SELECT patente, nombre, tipo, notas FROM placas ORDER BY patente").fetchall()
    c.close()
    print(f"Placas registradas: {len(rows)}")
    for r in rows:
        print(f"  {r['patente']:10s} {r['nombre']:15s} {r['tipo']}  {r['notas']}")

def backfill(events_log):
    """Replay de activities del events.log hacia SQL (historia previa)."""
    n = 0
    c = con()
    for line in open(events_log):
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("type") != "actividad":
            continue
        c.execute("""INSERT OR IGNORE INTO avistamientos
                     (evento_id, camara, label, inicio, fin, duracion, zonas, veredicto, motivo_fin)
                     VALUES (?,?,?,?,?,?,?,?,?)""",
                  (d.get("id"), d.get("cam"), d.get("label"), d.get("inicio"),
                   None, d.get("duracion"), json.dumps(d.get("zonas") or [], ensure_ascii=False),
                   json.dumps(d.get("veredicto"), ensure_ascii=False) if d.get("veredicto") else None,
                   d.get("motivo_fin")))
        n += 1
    c.commit()
    c.close()
    print(f"backfill: {n} actividades importadas a {DB}")

def main():
    ap = argparse.ArgumentParser(description="Vigilador SQL — avistamientos y patentes")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("init")
    p = sub.add_parser("patente"); p.add_argument("patente")
    p = sub.add_parser("recorrido"); p.add_argument("patente")
    p = sub.add_parser("hoy"); p.add_argument("fecha", nargs="?")
    p = sub.add_parser("top"); p.add_argument("n", nargs="?", type=int, default=10)
    p = sub.add_parser("backfill"); p.add_argument("events_log")
    p = sub.add_parser("placa"); p.add_argument("sub"); p.add_argument("args", nargs="*")
    args = ap.parse_args()
    if not args.cmd:
        ap.print_help(); return
    if args.cmd == "init":
        init()
    elif args.cmd == "patente":
        patente(args.patente)
    elif args.cmd == "recorrido":
        recorrido(args.patente)
    elif args.cmd == "hoy":
        hoy(args.fecha)
    elif args.cmd == "top":
        top(args.n)
    elif args.cmd == "backfill":
        backfill(args.events_log)
    elif args.cmd == "placa":
        if args.sub == "add" and len(args.args) >= 2:
            placa_add(args.args[0], args.args[1],
                      args.args[2] if len(args.args) > 2 else "desconocido",
                      " ".join(args.args[3:]) if len(args.args) > 3 else "")
        elif args.sub == "list":
            placa_list()
        else:
            print("uso: placa add PATENTE NOMBRE [tipo] | placa list")

if __name__ == "__main__":
    main()

