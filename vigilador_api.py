#!/usr/bin/env python3
"""
Vigilador — API REST consumible por el sistema del barrio (stdlib, sin dependencias).
Contrato: el barrio consulta una patente → recibe avistamientos + foto, y asocia
la placa con los propietarios de SU base de datos.

Endpoints:
  GET /health                          estado del servicio
  GET /api/avistamientos?patente=X     avistamientos (filtros: camara, desde, hasta, limite)
  GET /api/avistamientos/recorrido?patente=X   cadena temporal (recorrido del vehículo)
  GET /api/avistamientos/<id>/foto     foto JPEG del avistamiento
  GET /api/placas                      placas registradas en el Vigilador
  GET /api/resumen?dias=N              conteos por día (dashboard del barrio)

Seguridad: si existe VIGILADOR_API_KEY en el .env del perfil, se exige
cabecera `X-API-Key`. Sin clave → servicio abierto solo en la LAN.
"""
import os, sys, json, sqlite3, argparse, time, re
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vigilador_db import DB, personas, add_persona, set_persona_avistamiento
from vigilador_db import placa_update, persona_update, persona_delete

HOST = "0.0.0.0"
PORT = 8788
START = time.time()
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vigilador_config.json")

def validar_config(cfg):
    """Valida la config antes de guardarla. Devuelve (ok, error)."""
    if not isinstance(cfg, dict):
        return False, "el cuerpo debe ser un objeto JSON"
    pol = cfg.get("politica")
    if pol is not None:
        if not isinstance(pol, dict):
            return False, "politica debe ser un objeto"
        z = pol.get("zonas_interes")
        if z is not None and not (isinstance(z, list) and all(isinstance(x, str) for x in z)):
            return False, "politica.zonas_interes debe ser una lista de strings"
        for k in ("night_start", "night_end", "offline_cooldown", "heartbeat_log"):
            if pol.get(k) is not None and not isinstance(pol[k], (int, float)):
                return False, f"politica.{k} debe ser numérico"
    con = cfg.get("conexion")
    if con is not None:
        if not isinstance(con, dict):
            return False, "conexion debe ser un objeto"
        for k in ("mqtt_host", "mqtt_user", "mqtt_topic", "frigate_api", "telegram_chat"):
            if con.get(k) is not None and not isinstance(con[k], str):
                return False, f"conexion.{k} debe ser string"
        if con.get("mqtt_port") is not None and not isinstance(con["mqtt_port"], (int, float)):
            return False, "conexion.mqtt_port debe ser numérico"
    pol = cfg.get("politica")
    if isinstance(pol, dict) and pol.get("momentos") is not None:
        momentos = pol["momentos"]
        if not isinstance(momentos, list):
            return False, "politica.momentos debe ser una lista"
        for m in momentos:
            if not isinstance(m, dict) or not str(m.get("nombre", "")).strip():
                return False, "cada momento necesita nombre"
            if not re.fullmatch(r"\d{2}:\d{2}", str(m.get("desde", ""))) or \
               not re.fullmatch(r"\d{2}:\d{2}", str(m.get("hasta", ""))):
                return False, f"momento '{m.get('nombre')}': desde/hasta deben ser HH:MM"
            if not isinstance(m.get("zonas"), list):
                return False, f"momento '{m.get('nombre')}': zonas debe ser una lista"
    vtp = pol.get("vision_tipo_prioridad")
    if vtp is not None:
        if not isinstance(vtp, dict):
            return False, "politica.vision_tipo_prioridad debe ser un objeto"
        for k, v in vtp.items():
            if str(v) not in ("baja", "media", "alta", "critica"):
                return False, f"vision_tipo_prioridad['{k}'] debe ser baja|media|alta|critica"
    vis = cfg.get("vision")
    if vis is not None:
        if not isinstance(vis, dict):
            return False, "vision debe ser un objeto"
        for k in ("proveedores", "zonas", "default"):
            if vis.get(k) is not None and not isinstance(vis[k], dict):
                return False, f"vision.{k} debe ser un objeto"
    return True, ""

def get_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {}

# ---------- especificación OpenAPI 3.0.3 (sirve en /openapi.json) ----------

# ---------- especificación OpenAPI 3.0.3 (sirve en /openapi.json) ----------
# Generada desde una tabla declarativa de rutas reales: cada endpoint que la API
# sirve queda documentado sin posibilidad de olvido (agregar ruta = agregar fila).

_OPENAPI_DEFS = [
    # (método, path, tag, resumen, params query, schema body)
    ("get", "/openapi.json", "sistema", "Especificación OpenAPI de toda la API", [], None),
    ("get", "/health", "sistema", "Estado + conteo de avistamientos", [], None),
    ("get", "/api/estado", "sistema", "Estado vivo del núcleo (contadores, detectores, momentos, zonas activas, ocupación, última alerta)", [], None),
    ("get", "/api/resumen", "sistema", "Conteos por día (dashboard)", ["dias"], None),
    ("get", "/api/avistamientos", "avistamientos", "Lista con filtros patente/cámara/rango", ["patente", "camara", "desde", "hasta", "limite"], None),
    ("get", "/api/avistamientos/recorrido", "avistamientos", "Cadena temporal de un vehículo por patente", ["patente"], None),
    ("get", "/api/avistamientos/{id}/foto", "avistamientos", "Foto JPEG del avistamiento", [], None),
    ("put", "/api/avistamientos/{id}/persona", "avistamientos", "Etiqueta el avistamiento con una persona (aprende en memoria)", [], "Etiqueta"),
    ("get", "/api/placas", "placas", "Lista de placas", [], None),
    ("post", "/api/placas", "placas", "Registra una placa", [], "Placa"),
    ("put", "/api/placas/{patente}", "placas", "Actualiza una placa", [], "Placa"),
    ("delete", "/api/placas/{patente}", "placas", "Elimina una placa", [], None),
    ("get", "/api/personas", "personas", "Registro de personas (siembra desde memoria)", [], None),
    ("post", "/api/personas", "personas", "Registra una persona", [], "Persona"),
    ("put", "/api/personas/{id}", "personas", "Actualiza una persona", [], "Persona"),
    ("delete", "/api/personas/{id}", "personas", "Elimina una persona", [], None),
    ("get", "/api/vision/zonas", "vision", "Asociación visión por zona (lista)", [], None),
    ("post", "/api/vision/zonas", "vision", "Asocia visión a una zona", [], "VisionZona"),
    ("put", "/api/vision/zonas/{zona}", "vision", "Actualiza la visión de una zona", [], "VisionZona"),
    ("delete", "/api/vision/zonas/{zona}", "vision", "Quita visión de una zona", [], None),
    ("get", "/api/vision/modelos", "vision", "CRUD de modelos de visión (proveedor+modelo, uno activo)", [], None),
    ("post", "/api/vision/modelos", "vision", "Agrega un modelo de visión", [], "VisionModelo"),
    ("put", "/api/vision/modelos/{idx}", "vision", "Actualiza un modelo (proveedor/modelo/activo)", [], "VisionModelo"),
    ("delete", "/api/vision/modelos/{idx}", "vision", "Elimina un modelo de visión", [], None),
    ("get", "/api/vision/prioridades", "vision", "Prioridad por tipo de visión (contexto)", [], None),
    ("post", "/api/vision/prioridades", "vision", "Agrega un tipo de visión con prioridad", [], "PrioridadTipo"),
    ("put", "/api/vision/prioridades/{tipo}", "vision", "Actualiza la prioridad de un tipo", [], "PrioridadTipo"),
    ("delete", "/api/vision/prioridades/{tipo}", "vision", "Elimina un tipo de visión", [], None),
    ("get", "/api/proveedores", "vision", "Proveedores de visión (keys enmascaradas)", [], None),
    ("post", "/api/proveedores", "vision", "Agrega un proveedor de visión", [], "Proveedor"),
    ("put", "/api/proveedores/{nombre}", "vision", "Actualiza un proveedor", [], "Proveedor"),
    ("delete", "/api/proveedores/{nombre}", "vision", "Elimina un proveedor", [], None),
    ("get", "/api/bots", "telegram", "Bots de Telegram (token solo como ✓/sin)", [], None),
    ("post", "/api/bots", "telegram", "Agrega un bot de Telegram", [], "Bot"),
    ("put", "/api/bots/{nombre}", "telegram", "Actualiza un bot", [], "Bot"),
    ("delete", "/api/bots/{nombre}", "telegram", "Elimina un bot", [], None),
    ("get", "/api/notificaciones", "telegram", "Reglas de notificación + bots", [], None),
    ("post", "/api/notificaciones", "telegram", "Guarda las reglas de notificación", [], "Reglas"),
    ("get", "/api/config", "config", "Configuración completa del núcleo", [], None),
    ("put", "/api/config", "config", "Actualiza la configuración (validada, recarga ≤60s)", [], "Config"),
    ("get", "/api/memoria", "memoria", "Búsqueda semántica en Qdrant", ["q", "limite"], None),
    ("get", "/api/memoria/config", "memoria", "Config del stack de memoria (embedder + vector store + health en vivo)", [], None),
    ("put", "/api/memoria/config", "memoria", "Actualiza el embedder (modelo/base_url del perfil)", [], "MemoriaConf"),
    ("get", "/api/logs", "logs", "Últimas líneas de un log (tipo: events|vigilador|mqtt_raw)", ["tipo", "lineas", "errores"], None),
    ("get", "/api/logs/bundle", "logs", "Bundle tar.gz de diagnóstico (logs + config + estado)", [], None),
    ("post", "/api/test-alerta", "telegram", "Envía alerta de prueba por la regla 'test'", [], None),
]

def _build_openapi():
    paths = {}
    for metodo, path, tag, resumen, params, body in _OPENAPI_DEFS:
        op = {"summary": resumen, "tags": [tag],
              "responses": {"200": {"description": "OK"},
                            "400": {"description": "JSON inválido / validación"},
                            "404": {"description": "No encontrado"}}}
        if params:
            op["parameters"] = [{"name": p, "in": "query", "required": False,
                                 "schema": {"type": "string"}} for p in params]
        if body:
            op["requestBody"] = {"content": {"application/json": {
                "schema": {"$ref": f"#/components/schemas/{body}"}}}}
        paths.setdefault(path, {})[metodo] = op
    return {
        "openapi": "3.0.3",
        "info": {"title": "Vigilador API", "version": "5.0.0",
                 "description": "API REST del Sistema Vigilador Hermes — núcleo consumible por la app web y el sistema del barrio. Swagger UI: https://petstore.swagger.io/?url=<host>:8788/openapi.json"},
        "servers": [],   # el handler de /openapi.json lo completa con el Host real
        "tags": [{"name": t} for t in ("sistema", "avistamientos", "placas", "personas", "vision", "telegram", "config", "memoria", "logs")],
        "paths": paths,
        "components": {"schemas": {
            "Placa": {"type": "object", "properties": {"patente": {"type": "string"}, "nombre": {"type": "string"}, "tipo": {"type": "string", "enum": ["vecino", "visita", "servicio", "desconocido"]}, "notas": {"type": "string"}}},
            "Persona": {"type": "object", "properties": {"nombre": {"type": "string"}, "tipo": {"type": "string"}, "notas": {"type": "string"}}},
            "Etiqueta": {"type": "object", "properties": {"persona": {"type": "string", "nullable": True, "description": "nombre o null para quitar"}}},
            "VisionZona": {"type": "object", "properties": {"zona": {"type": "string"}, "proveedor": {"type": "string"}, "modelo": {"type": "string"}, "habilitado": {"type": "boolean"}}},
            "PrioridadTipo": {"type": "object", "properties": {"tipo": {"type": "string"}, "prioridad": {"type": "string", "enum": ["baja", "media", "alta", "critica"]}}},
            "Proveedor": {"type": "object", "properties": {"nombre": {"type": "string"}, "tipo": {"type": "string", "enum": ["openai", "ollama"]}, "base_url": {"type": "string"}, "modelo": {"type": "string"}, "api_key": {"type": "string", "description": "va al .env, nunca se devuelve"}}},
            "Bot": {"type": "object", "properties": {"nombre": {"type": "string"}, "chat_id": {"type": "string"}, "token": {"type": "string", "description": "va al .env como TELEGRAM_<NOMBRE>_BOT_TOKEN"}, "habilitado": {"type": "boolean"}}},
            "Reglas": {"type": "object", "description": "tipo → {bots: [nombres], habilitado: bool}"},
            "MemoriaConf": {"type": "object", "properties": {"modelo": {"type": "string"}, "base_url": {"type": "string"}}},
            "Config": {"type": "object", "description": "conexion + telegram + politica + vision + logs"},
        }},
    }

OPENAPI_SPEC = _build_openapi()

def load_env():
    """Carga las variables del .env del perfil (si no están ya en el entorno)."""
    try:
        for line in open(ENV_PROFILE):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass

def con():
    c = sqlite3.connect(os.path.join(WS_DIR, DB))
    c.row_factory = sqlite3.Row
    return c

def avistamientos(patente=None, camara=None, desde=None, hasta=None, limite=50):
    q = "SELECT id, evento_id, camara, label, patente, patente_score, inicio, fin, duracion, zonas, veredicto, foto, persona, estado FROM avistamientos WHERE 1=1"
    params = []
    if patente:
        q += " AND patente = ?"
        params.append(patente.upper())
    if camara:
        q += " AND camara = ?"
        params.append(camara)
    if desde:
        q += " AND inicio >= ?"
        params.append(float(desde))
    if hasta:
        q += " AND inicio <= ?"
        params.append(float(hasta))
    q += " ORDER BY inicio DESC LIMIT ?"
    params.append(min(int(limite), 500))
    c = con()
    rows = c.execute(q, params).fetchall()
    c.close()
    return [dict(r) for r in rows]

def recorrido(patente):
    c = con()
    rows = c.execute("""SELECT camara, inicio, fin, duracion, zonas, foto
                        FROM avistamientos WHERE patente = ?
                        ORDER BY inicio""", (patente.upper(),)).fetchall()
    c.close()
    return [dict(r) for r in rows]

def resumen(dias=7):
    c = con()
    rows = c.execute("""SELECT date(inicio,'unixepoch','localtime') AS dia,
                        COUNT(*) AS n, COUNT(DISTINCT patente) AS patentes
                        FROM avistamientos
                        WHERE inicio >= ?
                        GROUP BY dia ORDER BY dia""",
                     (time.time() - int(dias) * 86400,)).fetchall()
    c.close()
    return [dict(r) for r in rows]

def placas():
    c = con()
    rows = c.execute("SELECT patente, nombre, tipo, notas FROM placas ORDER BY patente").fetchall()
    c.close()
    return [dict(r) for r in rows]

# ---------- fase A: logs, bundle, memoria semántica, alerta de prueba ----------

WS_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ES_PROFILE = os.path.basename(_SCRIPT_DIR) == "workspace"
VIGILADOR_HOME = (os.environ.get("VIGILADOR_HOME")
                  or (os.path.dirname(_SCRIPT_DIR) if _ES_PROFILE else _SCRIPT_DIR))

def archivos_log():
    import glob
    mqtt = sorted(glob.glob(os.path.join(WS_DIR, "mqtt_raw-*.log")), key=os.path.getmtime)
    return {
        "vigilador": os.path.join(VIGILADOR_HOME, "logs", "vigilador.log"),
        "events": os.path.join(WS_DIR, "events.log"),
        "mqtt_raw": mqtt[-1] if mqtt else None,
    }

def tail_lines(path, n):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            block = 8192
            pos = size
            out = b""
            while pos > 0 and out.count(b"\n") <= n:
                pos = max(0, pos - block)
                f.seek(pos)
                out = f.read(size - pos)
        return out.decode("utf-8", errors="replace").splitlines()[-n:]
    except Exception:
        return []

def bundle_diagnostico():
    import tarfile, io
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for nombre, path in archivos_log().items():
            if path and os.path.exists(path):
                tar.add(path, arcname=nombre)
        for nombre in ("vigilador_config.json", "estado.json"):
            p = os.path.join(WS_DIR, nombre)
            if os.path.exists(p):
                tar.add(p, arcname=nombre)
    return buf.getvalue()

_mem = None
def get_memoria():
    global _mem
    if _mem is not None:
        return _mem
    try:
        from mem0 import Memory
        oss = _oss_mem0()
        if oss is None:
            import yaml
            cfg = yaml.safe_load(open(os.path.join(VIGILADOR_HOME, "config.yaml")))
            oss = cfg["memory"]["mem0"]["oss_config"]
            if isinstance(oss, str):
                oss = json.loads(oss)
        _mem = Memory.from_config(oss)
    except Exception as e:
        print(f"[memoria] no disponible: {e}", file=sys.stderr)
        _mem = None
    return _mem

def buscar_memoria(q, limite=10):
    m = get_memoria()
    if m is None:
        return None
    r = m.search(q, filters={"user_id": "cristian"}, limit=int(limite))
    return r.get("results", [])

# ---------- config de MEMORIA (embedding) ----------
CONFIG_YAML = os.path.join(VIGILADOR_HOME, "config.yaml")   # solo respaldo legacy

def _oss_mem0():
    """Construye la config de mem0 desde vigilador_config.json (sección memoria)."""
    try:
        cfg = json.load(open(CONFIG_FILE))
    except Exception:
        cfg = {}
    mem = cfg.get("memoria")
    if not isinstance(mem, dict):
        return None
    llm = mem.get("llm") or {}
    emb = mem.get("embedder") or {}
    vs = mem.get("vector_store") or {}
    return {
        "llm": {"provider": llm.get("provider", "ollama"),
                "config": {"model": llm.get("modelo", "gemma3:4b"),
                           "ollama_base_url": llm.get("base_url", "http://localhost:11434")}},
        "embedder": {"provider": emb.get("provider", "ollama"),
                     "config": {"model": emb.get("modelo", "nomic-embed-text"),
                                "ollama_base_url": emb.get("base_url", "http://localhost:11434"),
                                "embedding_dims": emb.get("dims", 768)}},
        "vector_store": {"provider": vs.get("provider", "qdrant"),
                         "config": {"url": vs.get("url", "http://localhost:6333"),
                                    "collection_name": vs.get("coleccion", "vigilador_eventos")}},
        "version": "v1.1",
    }

def memoria_config():
    """Config del stack de memoria (embedder + vector store) con health en vivo."""
    oss = _oss_mem0() or {}
    emb = oss.get("embedder", {}) or {}
    vs = oss.get("vector_store", {}) or {}
    emb_cfg = emb.get("config", {}) or {}
    vs_cfg = vs.get("config", {}) or {}
    base = emb_cfg.get("ollama_base_url", "")
    ollama_ok, qdrant_ok, puntos = False, False, None
    try:
        import urllib.request
        with urllib.request.urlopen(base + "/api/tags", timeout=5) as r:
            tags = json.loads(r.read()).get("models", [])
            ollama_ok = any(t.get("name", "").startswith(emb_cfg.get("model", "")) for t in tags)
    except Exception:
        pass
    try:
        with urllib.request.urlopen((vs_cfg.get("url", "http://localhost:6333") +
                                     "/collections/" + vs_cfg.get("collection_name", "")), timeout=5) as r:
            puntos = json.loads(r.read()).get("result", {}).get("points_count")
            qdrant_ok = True
    except Exception:
        pass
    return {
        "llm": {"provider": (oss.get("llm", {}) or {}).get("provider"),
                "modelo": (oss.get("llm", {}) or {}).get("config", {}).get("model"),
                "base_url": (oss.get("llm", {}) or {}).get("config", {}).get("ollama_base_url")},
        "embedder": {"provider": emb.get("provider"), "modelo": emb_cfg.get("model"),
                     "base_url": base, "dims": emb_cfg.get("embedding_dims")},
        "vector_store": {"provider": vs.get("provider"), "url": vs_cfg.get("url"),
                         "coleccion": vs_cfg.get("collection_name"), "puntos": puntos},
        "health": {"ollama": ollama_ok, "qdrant": qdrant_ok},
    }

def memoria_config_save(d):
    """Actualiza la memoria (llm/embedder/vector store) en vigilador_config.json.
    Edición por componente — sin borrado: el stack es infraestructura."""
    modelo = (d.get("modelo") or "").strip()
    base = (d.get("base_url") or "").strip()
    if base and not modelo:
        return False, "falta modelo del embedder"
    if modelo and not base:
        return False, "falta base_url del embedder"
    if base and not base.startswith("http"):
        return False, "base_url debe ser http(s)"
    try:
        cfg = json.load(open(CONFIG_FILE))
    except Exception:
        cfg = {}
    mem = cfg.setdefault("memoria", {})
    # LLM (editable, sin borrado — es infraestructura del stack)
    llm_modelo = (d.get("llm_modelo") or "").strip()
    llm_base = (d.get("llm_base_url") or "").strip()
    if llm_modelo or llm_base:
        llmc = mem.setdefault("llm", {})
        if llm_modelo:
            llmc["modelo"] = llm_modelo
        if llm_base:
            if not llm_base.startswith("http"):
                return False, "llm base_url debe ser http(s)"
            llmc["base_url"] = llm_base
    emb = mem.setdefault("embedder", {})
    if modelo:
        emb["modelo"] = modelo
    if base:
        emb["base_url"] = base
    dims = (d.get("dims") or "").strip()
    if dims:
        try:
            emb["dims"] = int(dims)
        except Exception:
            return False, "dims debe ser numérico"
    vs_url = (d.get("vs_url") or "").strip()
    vs_coll = (d.get("vs_coleccion") or "").strip()
    vs_key = (d.get("vs_api_key") or "").strip()
    if vs_url or vs_coll or vs_key:
        vs = mem.setdefault("vector_store", {})
        if vs_url:
            if not vs_url.startswith("http"):
                return False, "vs_url debe ser http(s)"
            vs["url"] = vs_url
        if vs_coll:
            vs["coleccion"] = vs_coll
        if vs_key:
            # la API key de Qdrant va al .env (seguridad opcional del vector store)
            vs["api_key_env"] = "QDRANT_API_KEY"
            lines = []
            if os.path.exists(ENV_PROFILE):
                lines = [l for l in open(ENV_PROFILE).read().splitlines() if not l.startswith("QDRANT_API_KEY=")]
            lines.append(f"QDRANT_API_KEY={vs_key}")
            with open(ENV_PROFILE, "w") as f:
                f.write("\n".join(lines) + "\n")
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return False, f"no pude escribir: {e}"
    return True, "memoria guardada — el daemon la toma al reiniciar"

def enviar_alerta_prueba():
    """Envía la alerta de prueba usando el ruteo: regla 'test' → bot(s) habilitados."""
    cfg = get_config()
    reglas = (cfg.get("telegram") or {}).get("reglas") or {}
    bots_cfg = (cfg.get("telegram") or {}).get("bots") or []
    regla = reglas.get("test") or {}
    if regla and not regla.get("habilitado", True):
        return False, "regla 'test' deshabilitada"
    nombres = [b for b in (regla.get("bots") or [])]
    destinos = [b for b in bots_cfg if (b.get("nombre") in nombres or not nombres) and b.get("habilitado", True)]
    if not destinos:
        destinos = [b for b in bots_cfg if b.get("habilitado", True)]
    if not destinos:
        return False, "no hay bots habilitados en la config"
    b = destinos[0]
    tok = os.environ.get(b.get("token_env") or "", "")
    if not tok:
        # leer del .env del perfil
        for line in open(ENV_PROFILE):
            if line.startswith((b.get("token_env") or "TELEGRAM_BOT_TOKEN") + "="):
                tok = line.strip().split("=", 1)[1]
                break
    chat = b.get("chat_id")
    if not tok or not chat:
        return False, "el bot no tiene token o chat_id"
    body = urllib.parse.urlencode({
        "chat_id": chat, "parse_mode": "HTML",
        "text": "🧪 <b>Vigilador</b> · alerta de prueba — sistema OK\n"
                f"({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})"}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage", data=body)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            ok = json.loads(r.read()).get("ok", False)
        return ok, "enviada por bot '" + b.get("nombre", "?") + "'" if ok else "Telegram rechazó el envío"
    except Exception as e:
        return False, str(e)

# ---------- bots de Telegram + reglas de notificación (CRUD) ----------

def _env_val(key):
    """Lee una variable del .env del perfil (o del entorno del proceso)."""
    try:
        with open(os.path.join(VIGILADOR_HOME, ".env")) as f:
            for line in f:
                if line.startswith(key + "="):
                    return line.strip().split("=", 1)[1]
    except Exception:
        pass
    return os.environ.get(key, "")

def bots_telegram():
    cfg = get_config()
    out = []
    for b in (cfg.get("telegram") or {}).get("bots") or []:
        env = b.get("token_env") or ""
        out.append({"nombre": b.get("nombre"), "token_env": env,
                    "chat_id": b.get("chat_id"),
                    "habilitado": bool(b.get("habilitado", True)),
                    "token_configurado": bool(env and (os.environ.get(env) or _env_contiene(env)))})
    return out

def _env_contiene(key):
    try:
        for line in open(ENV_PROFILE):
            if line.startswith(key + "=") and line.strip().split("=", 1)[1]:
                return True
    except Exception:
        pass
    return False

def guardar_bot(nombre, chat_id, token=None, habilitado=True):
    cfg = get_config()
    tg = cfg.setdefault("telegram", {})
    bots = tg.setdefault("bots", [])
    viejo = next((b for b in bots if b.get("nombre") == nombre), None)
    env = (viejo or {}).get("token_env") or f"TELEGRAM_{nombre.upper().replace('-', '_')}_BOT_TOKEN"
    entry = {"nombre": nombre, "token_env": env, "chat_id": chat_id or "", "habilitado": bool(habilitado)}
    for i, b in enumerate(bots):
        if b.get("nombre") == nombre:
            bots[i] = entry
            break
    else:
        bots.append(entry)
    guardar_config(cfg)
    if token:
        lines = [l for l in open(ENV_PROFILE).read().splitlines() if not l.startswith(env + "=")] \
                if os.path.exists(ENV_PROFILE) else []
        lines.append(f"{env}={token}")
        open(ENV_PROFILE, "w").write("\n".join(lines) + "\n")
        os.environ[env] = token
    return True, "bot guardado (token en .env)"

def bot_delete(nombre):
    cfg = get_config()
    tg = cfg.get("telegram") or {}
    bots = tg.get("bots") or []
    antes = len(bots)
    tg["bots"] = [b for b in bots if b.get("nombre") != nombre]
    if len(tg["bots"]) == antes:
        return False, "bot no existe"
    guardar_config(cfg)
    return True, "bot eliminado (el token queda en .env)"

def notificaciones():
    cfg = get_config()
    return {"reglas": (cfg.get("telegram") or {}).get("reglas") or {}, "bots": bots_telegram(),
            "solapado": (cfg.get("telegram") or {}).get("solapado") or {"habilitado": True, "ventana_s": 120}}

# ---------- prioridad por tipo de visión (CRUD) ----------

def vision_prioridades():
    cfg = get_config()
    return {"prioridades": (cfg.get("politica") or {}).get("vision_tipo_prioridad") or {}}

def vision_prioridad_add(tipo, prioridad):
    if not tipo or not isinstance(tipo, str):
        return False, "falta tipo"
    t = tipo.strip()   # se conserva como se escribe (el daemon normaliza a minúsculas al cargar)
    prioridad = str(prioridad or "").lower()
    if prioridad not in ("baja", "media", "alta", "critica"):
        return False, "prioridad debe ser baja|media|alta|critica"
    cfg = get_config()
    pol = cfg.setdefault("politica", {})
    vtp = pol.setdefault("vision_tipo_prioridad", {})
    if t in vtp:
        return False, f"'{t}' ya existe (usá PUT para actualizar)"
    vtp[t] = prioridad
    guardar_config(cfg)
    return True, f"prioridad '{t}' → {prioridad} agregada"

def vision_prioridad_update(tipo, prioridad):
    prioridad = str(prioridad or "").lower()
    if prioridad not in ("baja", "media", "alta", "critica"):
        return False, "prioridad debe ser baja|media|alta|critica"
    cfg = get_config()
    vtp = (cfg.get("politica") or {}).get("vision_tipo_prioridad") or {}
    t = tipo.strip()   # se conserva como se escribe
    if t not in vtp:
        return False, f"'{t}' no existe"
    vtp[t] = prioridad
    guardar_config(cfg)
    return True, f"prioridad '{t}' → {prioridad}"

def vision_prioridad_delete(tipo):
    cfg = get_config()
    pol = cfg.get("politica") or {}
    vtp = pol.get("vision_tipo_prioridad")
    t = tipo.strip()   # se conserva como se escribe
    if not vtp or t not in vtp:
        return False, f"'{t}' no existe"
    del vtp[t]
    guardar_config(cfg)
    return True, f"prioridad '{t}' eliminada"

def guardar_notificaciones(reglas, solapado=None):
    if not isinstance(reglas, dict):
        return False, "reglas debe ser un objeto"
    for tipo, r in reglas.items():
        if not isinstance(r, dict):
            return False, f"regla '{tipo}' debe ser objeto"
        if not isinstance(r.get("bots"), list):
            return False, f"regla '{tipo}': bots debe ser lista"
    if solapado is not None and not isinstance(solapado, dict):
        return False, "solapado debe ser un objeto"
    cfg = get_config()
    cfg.setdefault("telegram", {})["reglas"] = reglas
    if solapado is not None:
        cfg["telegram"]["solapado"] = {
            "habilitado": bool(solapado.get("habilitado", True)),
            "ventana_s": max(0, int(solapado.get("ventana_s", 120) or 120)),
        }
    guardar_config(cfg)
    return True, "reglas de notificación guardadas"

# ---------- registro de personas (identidad) ----------

_seeded_personas = False

def seed_personas():
    """Siembra el registro con los nombres que ya existen en la memoria
    (registros históricos con 'persona: Cristian' / 'persona: Cristina')."""
    global _seeded_personas
    if _seeded_personas:
        return
    _seeded_personas = True
    try:
        if personas():
            return
        m = get_memoria()
        if m is None:
            return
        nombres = set()
        for q in ("persona: Cristian", "persona: Cristina", "persona detectada"):
            r = m.search(q, filters={"user_id": "cristian"}, limit=15)
            for item in r.get("results", []):
                for nombre in re.findall(r"persona:\s*([A-Za-zÁ-Úá-úÉéÍíÓóÚú]+)", str(item.get("memory", ""))):
                    if len(nombre) > 1:
                        nombres.add(nombre)
        for n in sorted(nombres):
            add_persona(n, "familia")
        if nombres:
            print(f"[personas] sembradas desde memoria: {sorted(nombres)}", file=sys.stderr)
    except Exception as e:
        print(f"[personas] seed falló: {e}", file=sys.stderr)

def etiquetar_persona(av_id, nombre):
    """Etiqueta un avistamiento con un nombre y lo aprende en memoria."""
    set_persona_avistamiento(av_id, nombre)
    if nombre:
        c = con()
        row = c.execute("SELECT camara, label, datetime(inicio,'unixepoch','localtime') AS d FROM avistamientos WHERE id = ?",
                        (int(av_id),)).fetchone()
        c.close()
        if row:
            fact = f"{row['d']}: {row['label']} en {row['camara']} identificada como {nombre.strip()} (etiquetado por el usuario)"
            m = get_memoria()
            if m is not None:
                try:
                    m.add(fact, user_id="cristian", infer=False)
                except Exception as e:
                    print(f"[personas] memoria: {e}", file=sys.stderr)

# ---------- proveedores de visión (keys en .env, nunca en el JSON) ----------

ENV_PROFILE = os.path.join(VIGILADOR_HOME, ".env")

def proveedores_vision():
    """Lista proveedores con estado de key (nunca expone el valor)."""
    cfg = get_config()
    provs = (cfg.get("vision") or {}).get("proveedores") or {}
    out = []
    for nombre, p in provs.items():
        out.append({
            "nombre": nombre,
            "tipo": p.get("tipo"),
            "base_url": p.get("base_url"),
            "api_key_configurada": bool(p.get("api_key_env") and _env_val(p["api_key_env"])),
            "api_key_env": p.get("api_key_env"),
        })
    return out

def guardar_proveedor(nombre, tipo, base_url, api_key=None):
    """Registra/actualiza un proveedor (SIN modelo: el modelo vive en
    vision.default — 'Modelo de visión único'). La key se escribe en el .env
    del perfil como VISION_<NOMBRE>_API_KEY; la config guarda solo la referencia."""
    nombre = (nombre or "").strip()
    if not nombre:
        return False, "falta nombre"
    cfg = get_config()
    vis = cfg.setdefault("vision", {})
    provs = vis.setdefault("proveedores", {})
    if not base_url:
        return False, "falta base_url (toda IP/URL externa vive en la config, no en el código)"
    env_var = f"VISION_{nombre.upper().replace('-', '_')}_API_KEY"
    provs[nombre] = {
        "tipo": tipo or "openai",
        "base_url": base_url,
        "api_key_env": env_var if tipo != "ollama" else "",
    }
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    if api_key:
        # escribir la key al .env del perfil (nunca al JSON)
        lines = []
        if os.path.exists(ENV_PROFILE):
            lines = [l for l in open(ENV_PROFILE).read().splitlines() if not l.startswith(env_var + "=")]
        lines.append(f"{env_var}={api_key}")
        with open(ENV_PROFILE, "w") as f:
            f.write("\n".join(lines) + "\n")
        os.environ[env_var] = api_key
    return True, "guardado (key en .env, recarga ≤60s)"

def guardar_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

def proveedor_delete(nombre):
    cfg = get_config()
    provs = (cfg.get("vision") or {}).get("proveedores") or {}
    if nombre not in provs:
        return False, "proveedor no existe"
    del provs[nombre]
    guardar_config(cfg)
    return True, "proveedor eliminado (la key queda en el .env)"

# --- CRUD de visión por zona (config vision.zonas) ---

def vision_zonas_list():
    cfg = get_config()
    return {"zonas": (cfg.get("vision") or {}).get("zonas") or {},
            "proveedores": proveedores_vision()}

def vision_modelos():
    cfg = get_config()
    return {"modelos": (cfg.get("vision") or {}).get("modelos") or []}

def vision_modelo_add(proveedor, modelo):
    cfg = get_config()
    vis = cfg.setdefault("vision", {})
    mods = vis.setdefault("modelos", [])
    if not proveedor or not modelo:
        return False, "faltan proveedor y modelo"
    # el PRIMERO cargado queda activo si no hay ninguno activo
    mods.append({"proveedor": proveedor.strip(), "modelo": modelo.strip(),
                 "activo": not any(m.get("activo") for m in mods)})
    guardar_config(cfg)
    return True, "modelo agregado (≤60s)"

def vision_modelo_update(idx, proveedor=None, modelo=None, activo=None):
    cfg = get_config()
    mods = (cfg.get("vision") or {}).get("modelos") or []
    if not (0 <= idx < len(mods)):
        return False, "modelo no existe"
    m = mods[idx]
    if proveedor is not None:
        m["proveedor"] = proveedor.strip()
    if modelo is not None:
        m["modelo"] = modelo.strip()
    if activo:
        for i, x in enumerate(mods):
            x["activo"] = (i == idx)   # UNO solo activo
    guardar_config(cfg)
    return True, "modelo actualizado (≤60s)"

def vision_modelo_delete(idx):
    cfg = get_config()
    mods = (cfg.get("vision") or {}).get("modelos") or []
    if not (0 <= idx < len(mods)):
        return False, "modelo no existe"
    era_activo = mods[idx].get("activo")
    del mods[idx]
    if era_activo and mods and not any(m.get("activo") for m in mods):
        mods[0]["activo"] = True   # nunca sin modelo activo
    guardar_config(cfg)
    return True, "modelo eliminado (≤60s)"

def vision_zona_add(zona, habilitado=True):
    cfg = get_config()
    vis = cfg.setdefault("vision", {})
    zs = vis.setdefault("zonas", {})
    if zona in zs:
        return False, "la zona ya existe (usá PUT)"
    zs[zona] = {"habilitado": bool(habilitado), "labels": ["person"],
                "reescalar": False}   # el modelo ÚNICO (vision.default) gobierna
    guardar_config(cfg)
    return True, "zona agregada a visión (recarga ≤60s)"

def vision_zona_update(zona, proveedor=None, modelo=None, habilitado=None, reescalar=None):
    cfg = get_config()
    zs = (cfg.get("vision") or {}).get("zonas") or {}
    if zona not in zs:
        return False, "zona no existe"
    if proveedor is not None:
        zs[zona]["proveedor"] = proveedor
    else:
        zs[zona].pop("proveedor", None)   # modelo ÚNICO: sin proveedor propio → usa vision.default
    if modelo is not None:
        zs[zona]["modelo"] = modelo
    else:
        zs[zona].pop("modelo", None)
    if habilitado is not None:
        zs[zona]["habilitado"] = bool(habilitado)
    if reescalar is not None:
        zs[zona]["reescalar"] = bool(reescalar)
    guardar_config(cfg)
    return True, "zona actualizada (recarga ≤60s)"

def vision_zona_delete(zona):
    cfg = get_config()
    zs = (cfg.get("vision") or {}).get("zonas")
    if not zs or zona not in zs:
        return False, "zona no existe"
    del zs[zona]
    guardar_config(cfg)
    return True, "zona eliminada de visión (recarga ≤60s)"

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "X-API-Key, Content-Type")

    def _auth_ok(self):
        key = os.environ.get("VIGILADOR_API_KEY", "")
        if not key:
            return True
        return self.headers.get("X-API-Key") == key

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _photo(self, path):
        if not path or not os.path.exists(path):
            self._json({"error": "foto no disponible"}, 404)
            return
        try:
            with open(path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _serve_static(self, name):
        """Sirve archivos de la app web (carpeta app/ del proyecto).
        Con Cache-Control: no-store — el navegador SIEMPRE pide fresco
        (evita dashboards muertos por JS cacheado)."""
        import mimetypes
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app")
        fp = os.path.join(base, name)
        if not os.path.exists(fp) or not os.path.isfile(fp):
            self._json({"error": "archivo no encontrado"}, 404)
            return
        ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
        with open(fp, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return None
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def do_PUT(self):
        if not self._auth_ok():
            self._json({"error": "no autorizado"}, 401)
            return
        u = urlparse(self.path)
        try:
            d = self._read_body()
        except Exception:
            self._json({"error": "JSON inválido"}, 400)
            return
        d = d or {}
        m = re.match(r"^/api/avistamientos/(\d+)/persona$", u.path)
        if m:
            nombre = ((d).get("persona") or "").strip() or None
            etiquetar_persona(int(m.group(1)), nombre)
            self._json({"ok": True, "avistamiento_id": int(m.group(1)), "persona": nombre})
            return
        m = re.match(r"^/api/placas/([^/]+)$", u.path)
        if m:
            pat = urllib.parse.unquote(m.group(1))
            filas = placa_update(pat, d.get("nombre"), d.get("tipo"), d.get("notas"))
            self._json({"ok": filas > 0, "patente": pat.upper()}, 404 if not filas else 200)
            return
        m = re.match(r"^/api/personas/(\d+)$", u.path)
        if m:
            filas = persona_update(int(m.group(1)), d.get("nombre"), d.get("tipo"), d.get("notas"))
            self._json({"ok": filas > 0, "persona_id": int(m.group(1))}, 404 if not filas else 200)
            return
        m = re.match(r"^/api/proveedores/([^/]+)$", u.path)
        if m:
            nombre = urllib.parse.unquote(m.group(1))
            ok, msg = guardar_proveedor(nombre, d.get("tipo"), d.get("base_url"), d.get("api_key"))
            self._json({"ok": ok, "detalle": msg}, 200 if ok else 400)
            return
        m = re.match(r"^/api/vision/zonas/([^/]+)$", u.path)
        if m:
            zona = urllib.parse.unquote(m.group(1))
            ok, msg = vision_zona_update(zona, d.get("proveedor"), d.get("modelo"), d.get("habilitado"), d.get("reescalar"))
            self._json({"ok": ok, "detalle": msg}, 200 if ok else 400)
            return
        m = re.match(r"^/api/vision/modelos/(\d+)$", u.path)
        if m:
            ok, msg = vision_modelo_update(int(m.group(1)), d.get("proveedor"), d.get("modelo"), d.get("activo"))
            self._json({"ok": ok, "detalle": msg}, 200 if ok else 400)
            return
        m = re.match(r"^/api/vision/prioridades/([^/]+)$", u.path)
        if m:
            tipo = urllib.parse.unquote(m.group(1))
            ok, msg = vision_prioridad_update(tipo, d.get("prioridad"))
            self._json({"ok": ok, "detalle": msg}, 200 if ok else 400)
            return

        if u.path == "/api/memoria/config":
            ok, msg = memoria_config_save(d)
            self._json({"ok": ok, "detalle": msg}, 200 if ok else 400)
            return
        m = re.match(r"^/api/bots/([^/]+)$", u.path)
        if m:
            nombre = urllib.parse.unquote(m.group(1))
            ok, msg = guardar_bot(nombre, d.get("chat_id"), d.get("token"), d.get("habilitado", True))
            self._json({"ok": ok, "detalle": msg}, 200 if ok else 400)
            return
        if u.path != "/api/config":
            self._json({"error": "ruta no encontrada"}, 404)
            return
        try:
            cfg = d   # el body ya se leyó arriba (evita doble lectura que colgaba)
        except Exception:
            self._json({"error": "JSON inválido"}, 400)
            return
        ok, err = validar_config(cfg)
        if not ok:
            self._json({"error": err}, 400)
            return
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        self._json({"ok": True, "recarga": "aplicada por el daemon en ≤60s (conexión requiere reinicio)"})

    def do_POST(self):
        if not self._auth_ok():
            self._json({"error": "no autorizado"}, 401)
            return
        u = urlparse(self.path)
        if u.path == "/api/test-alerta":
            ok, msg = enviar_alerta_prueba()
            self._json({"ok": ok, "detalle": msg}, 200 if ok else 502)
            return
        if u.path == "/api/personas":
            try:
                d = self._read_body()
            except Exception:
                self._json({"error": "JSON inválido"}, 400)
                return
            nombre = (d or {}).get("nombre", "").strip()
            if not nombre:
                self._json({"error": "falta nombre"}, 400)
                return
            add_persona(nombre, (d or {}).get("tipo", "familia"), (d or {}).get("notas", ""))
            self._json({"ok": True, "nombre": nombre.strip()})
            return
        if u.path == "/api/proveedores":
            try:
                d = self._read_body()
            except Exception:
                self._json({"error": "JSON inválido"}, 400)
                return
            ok, msg = guardar_proveedor((d or {}).get("nombre"), (d or {}).get("tipo"),
                                        (d or {}).get("base_url"), (d or {}).get("api_key"))
            self._json({"ok": ok, "detalle": msg}, 200 if ok else 400)
            return
        if u.path == "/api/bots":
            try:
                d = self._read_body()
            except Exception:
                self._json({"error": "JSON inválido"}, 400)
                return
            nombre = ((d or {}).get("nombre") or "").strip()
            if not nombre:
                self._json({"error": "falta nombre"}, 400)
                return
            ok, msg = guardar_bot(nombre, (d or {}).get("chat_id"), (d or {}).get("token"),
                                  (d or {}).get("habilitado", True))
            self._json({"ok": ok, "detalle": msg}, 200 if ok else 400)
            return
        if u.path == "/api/notificaciones":
            try:
                d = self._read_body()
            except Exception:
                self._json({"error": "JSON inválido"}, 400)
                return
            ok, msg = guardar_notificaciones((d or {}).get("reglas"), (d or {}).get("solapado"))
            self._json({"ok": ok, "detalle": msg}, 200 if ok else 400)
            return
        if u.path == "/api/vision/prioridades":
            try:
                d = self._read_body()
            except Exception:
                self._json({"error": "JSON inválido"}, 400)
                return
            ok, msg = vision_prioridad_add((d or {}).get("tipo"), (d or {}).get("prioridad"))
            self._json({"ok": ok, "detalle": msg}, 200 if ok else 400)
            return
        if u.path == "/api/vision/zonas":
            try:
                d = self._read_body()
            except Exception:
                self._json({"error": "JSON inválido"}, 400)
                return
            zona = ((d or {}).get("zona") or "").strip()
            if not zona:
                self._json({"error": "falta zona"}, 400)
                return
            ok, msg = vision_zona_add(zona, True)
            self._json({"ok": ok, "detalle": msg}, 200 if ok else 400)
            return
        if u.path == "/api/vision/modelos":
            try:
                d = self._read_body()
            except Exception:
                self._json({"error": "JSON inválido"}, 400)
                return
            ok, msg = vision_modelo_add((d or {}).get("proveedor"), (d or {}).get("modelo"))
            self._json({"ok": ok, "detalle": msg}, 200 if ok else 400)
            return
        if u.path == "/api/placas":
            try:
                d = self._read_body()
            except Exception:
                self._json({"error": "JSON inválido"}, 400)
                return
            pat = (d or {}).get("patente", "").strip().upper()
            if not pat:
                self._json({"error": "falta patente"}, 400)
                return
            c = con()
            c.execute("INSERT OR REPLACE INTO placas (patente, nombre, tipo, notas) VALUES (?,?,?,?)",
                      (pat, (d or {}).get("nombre", ""), (d or {}).get("tipo", "desconocido"),
                       (d or {}).get("notas", "")))
            c.commit()
            c.close()
            self._json({"ok": True, "patente": pat})
            return
        self._json({"error": "ruta no encontrada"}, 404)

    def do_DELETE(self):
        if not self._auth_ok():
            self._json({"error": "no autorizado"}, 401)
            return
        u = urlparse(self.path)
        m = re.match(r"^/api/placas/([^/]+)$", u.path)
        if m:
            pat = urllib.parse.unquote(m.group(1)).upper()
            c = con()
            cur = c.execute("DELETE FROM placas WHERE patente = ?", (pat,))
            c.commit()
            c.close()
            self._json({"ok": cur.rowcount > 0, "patente": pat}, 404 if not cur.rowcount else 200)
            return
        m = re.match(r"^/api/personas/(\d+)$", u.path)
        if m:
            filas = persona_delete(int(m.group(1)))
            self._json({"ok": filas > 0, "persona_id": int(m.group(1))}, 404 if not filas else 200)
            return
        m = re.match(r"^/api/proveedores/([^/]+)$", u.path)
        if m:
            nombre = urllib.parse.unquote(m.group(1))
            ok, msg = proveedor_delete(nombre)
            self._json({"ok": ok, "detalle": msg}, 200 if ok else 400)
            return
        m = re.match(r"^/api/vision/zonas/([^/]+)$", u.path)
        if m:
            zona = urllib.parse.unquote(m.group(1))
            ok, msg = vision_zona_delete(zona)
            self._json({"ok": ok, "detalle": msg}, 200 if ok else 400)
            return
        m = re.match(r"^/api/vision/modelos/(\d+)$", u.path)
        if m:
            ok, msg = vision_modelo_delete(int(m.group(1)))
            self._json({"ok": ok, "detalle": msg}, 200 if ok else 400)
            return
        m = re.match(r"^/api/vision/prioridades/([^/]+)$", u.path)
        if m:
            tipo = urllib.parse.unquote(m.group(1))
            ok, msg = vision_prioridad_delete(tipo)
            self._json({"ok": ok, "detalle": msg}, 200 if ok else 400)
            return
        m = re.match(r"^/api/bots/([^/]+)$", u.path)
        if m:
            nombre = urllib.parse.unquote(m.group(1))
            ok, msg = bot_delete(nombre)
            self._json({"ok": ok, "detalle": msg}, 200 if ok else 400)
            return
        self._json({"error": "ruta no encontrada"}, 404)

    def do_GET(self):
        if not self._auth_ok():
            self._json({"error": "no autorizado"}, 401)
            return
        u = urlparse(self.path)
        path = u.path
        q = parse_qs(u.query)

        # --- app web (estática, carpeta app/) ---
        if path in ("/", "/app", "/app/", "/app/index.html"):
            self._serve_static("index.html")
            return
        if path in ("/swagger", "/swagger/"):
            # Swagger UI local (mismo origen → sin mixed-content; el CDN va por https)
            self._serve_static("swagger.html")
            return
        m = re.match(r"^/app/([A-Za-z0-9_\-\.]+)$", path)
        if m:
            self._serve_static(m.group(1))
            return

        if path == "/openapi.json":
            spec = json.loads(json.dumps(OPENAPI_SPEC))
            host = self.headers.get("Host", "localhost:8788")
            spec["servers"] = [{"url": f"http://{host}", "description": "Box Vigilador (LAN)"}]
            self._json(spec)
            return

        if path == "/health":
            c = con()
            try:
                n = c.execute("SELECT COUNT(*) FROM avistamientos").fetchone()[0]
            except Exception:
                n = 0
            c.close()
            self._json({"ok": True, "servicio": "vigilador-api", "uptime_s": round(time.time() - START, 1),
                        "avistamientos": n})
            return

        if path == "/api/estado":
            est = os.path.join(os.path.dirname(os.path.abspath(__file__)), "estado.json")
            if os.path.exists(est):
                try:
                    with open(est) as f:
                        self._json(json.load(f))
                    return
                except Exception as e:
                    self._json({"error": str(e)}, 500)
                    return
            self._json({"error": "estado no disponible aún"}, 404)
            return

        if path == "/api/avistamientos":
            self._json({"avistamientos": avistamientos(
                patente=q.get("patente", [None])[0],
                camara=q.get("camara", [None])[0],
                desde=q.get("desde", [None])[0],
                hasta=q.get("hasta", [None])[0],
                limite=q.get("limite", [50])[0])})
            return

        if path == "/api/avistamientos/recorrido":
            pat = q.get("patente", [None])[0]
            if not pat:
                self._json({"error": "falta patente"}, 400)
                return
            self._json({"patente": pat.upper(), "recorrido": recorrido(pat)})
            return

        m = re.match(r"^/api/avistamientos/(\d+)/foto$", path)
        if m:
            c = con()
            row = c.execute("SELECT foto FROM avistamientos WHERE id = ?", (int(m.group(1)),)).fetchone()
            c.close()
            self._photo(row["foto"] if row else None)
            return

        m = re.match(r"^/api/avistamientos/(\d+)/box$", path)
        if m:
            # box del evento + la foto MQTT ANOTADA (el recuadro y la etiqueta
            # dibujados por Frigate) — la más cercana en tiempo al evento
            c = con()
            row = c.execute("SELECT evento_id, camara, inicio, duracion FROM avistamientos WHERE id = ?", (int(m.group(1)),)).fetchone()
            c.close()
            if not row:
                self._json({"box": None, "detalle": "no existe"}, 404)
                return
            mqtt = None
            try:
                dirm = os.path.join(os.path.dirname(os.path.abspath(__file__)), "snapshots", row["camara"])
                import glob as _g
                cands = [f for f in _g.glob(os.path.join(dirm, "*_mqtt.jpg")) if os.path.isfile(f)]
                fin = (row["inicio"] or 0) + (row["duracion"] or 0)
                best, bd = None, 10**12
                for f in cands:
                    try:
                        ts = datetime.strptime(os.path.basename(f)[:15], "%Y%m%d_%H%M%S").timestamp()
                    except Exception:
                        continue
                    d0 = abs(ts - (row["inicio"] or 0))
                    d1 = abs(ts - fin)
                    dist = min(d0, d1)
                    if dist < bd:
                        best, bd = f, dist
                if best:
                    mqtt = f"/api/avistamientos/{int(m.group(1))}/mqtt-foto"
            except Exception:
                pass
            try:
                cfg = get_config()
                fapi = (cfg.get("conexion") or {}).get("frigate_api", "http://localhost:5000")
                req = urllib.request.Request(f"{fapi}/api/events/{urllib.parse.quote(row['evento_id'])}")
                with urllib.request.urlopen(req, timeout=10) as r:
                    ev = json.loads(r.read())
                box = (ev.get("data") or {}).get("box")
                self._json({"box": box if isinstance(box, list) and len(box) == 4 else None,
                            "mqtt": mqtt,
                            "thumb": f"/api/avistamientos/{int(m.group(1))}/thumb"})
            except Exception as e:
                self._json({"box": None, "detalle": str(e), "mqtt": mqtt})
            return

        m = re.match(r"^/api/avistamientos/(\d+)/mqtt-foto$", path)
        if m:
            # la foto MQTT ANOTADA (recuadro + etiqueta de Frigate) del evento
            c = con()
            row = c.execute("SELECT camara, inicio, duracion FROM avistamientos WHERE id = ?", (int(m.group(1)),)).fetchone()
            c.close()
            if not row:
                self._json({"error": "no existe"}, 404)
                return
            dirm = os.path.join(os.path.dirname(os.path.abspath(__file__)), "snapshots", row["camara"])
            import glob as _g
            cands = [f for f in _g.glob(os.path.join(dirm, "*_mqtt.jpg")) if os.path.isfile(f)]
            fin = (row["inicio"] or 0) + (row["duracion"] or 0)
            best, bd = None, 10**12
            for f in cands:
                try:
                    ts = datetime.strptime(os.path.basename(f)[:15], "%Y%m%d_%H%M%S").timestamp()
                except Exception:
                    continue
                dist = min(abs(ts - (row["inicio"] or 0)), abs(ts - fin))
                if dist < bd:
                    best, bd = f, dist
            self._photo(best)
            return

        m = re.match(r"^/api/avistamientos/(\d+)/snap$", path)
        if m:
            # snapshot OFICIAL de Frigate = la REGION de detección — el box se
            # dibuja NORMALIZADO a esta imagen (alineación exacta)
            c = con()
            row = c.execute("SELECT evento_id FROM avistamientos WHERE id = ?", (int(m.group(1)),)).fetchone()
            c.close()
            if not row:
                self._json({"error": "no existe"}, 404)
                return
            try:
                cfg = get_config()
                fapi = (cfg.get("conexion") or {}).get("frigate_api", "http://localhost:5000")
                req = urllib.request.Request(f"{fapi}/api/events/{urllib.parse.quote(row['evento_id'])}/snapshot.jpg")
                with urllib.request.urlopen(req, timeout=10) as r:
                    body = r.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(body)))
                self._cors()
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                self._json({"error": str(e)}, 502)
            return

        m = re.match(r"^/api/avistamientos/(\d+)/thumb$", path)
        if m:
            # thumbnail de Frigate (la detección ANOTADA — recuadro del motor)
            c = con()
            row = c.execute("SELECT evento_id FROM avistamientos WHERE id = ?", (int(m.group(1)),)).fetchone()
            c.close()
            if not row:
                self._json({"error": "no existe"}, 404)
                return
            try:
                cfg = get_config()
                fapi = (cfg.get("conexion") or {}).get("frigate_api", "http://localhost:5000")
                req = urllib.request.Request(f"{fapi}/api/events/{urllib.parse.quote(row['evento_id'])}/thumbnail.jpg")
                with urllib.request.urlopen(req, timeout=10) as r:
                    body = r.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(body)))
                self._cors()
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                self._json({"error": str(e)}, 502)
            return

        if path == "/api/placas":
            self._json({"placas": placas()})
            return

        if path == "/api/personas":
            seed_personas()
            self._json({"personas": personas()})
            return

        if path == "/api/proveedores":
            self._json({"proveedores": proveedores_vision()})
            return

        if path == "/api/vision/zonas":
            self._json(vision_zonas_list(), 200)
            return
        if path == "/api/vision/modelos":
            self._json(vision_modelos(), 200)
            return

        if path == "/api/bots":
            self._json({"bots": bots_telegram()})
            return

        if path == "/api/notificaciones":
            self._json(notificaciones())
            return

        if path == "/api/vision/prioridades":
            self._json(vision_prioridades())
            return

        if path == "/api/config":
            self._json({"config": get_config(), "recarga": "aplicada por el daemon en ≤60s"})
            return

        if path == "/api/resumen":
            self._json({"dias": resumen(q.get("dias", [7])[0])})
            return

        # --- fase A: logs en vivo / bundle ---
        if path == "/api/logs":
            tipo = q.get("tipo", ["events"])[0]
            n = min(int(q.get("lineas", [100])[0]), 500)
            solo_errores = q.get("errores", ["0"])[0] == "1"
            fp = archivos_log().get(tipo)
            if not fp or not os.path.exists(fp):
                self._json({"error": f"log '{tipo}' no disponible"}, 404)
                return
            lines = tail_lines(fp, n)
            if solo_errores:
                lines = [l for l in lines if "ERROR" in l or "Traceback" in l]
            self._json({"tipo": tipo, "archivo": fp, "lineas": len(lines), "contenido": lines})
            return

        if path == "/api/logs/bundle":
            data = bundle_diagnostico()
            self.send_response(200)
            self.send_header("Content-Type", "application/gzip")
            self.send_header("Content-Disposition", 'attachment; filename="vigilador-diagnostico.tar.gz"')
            self.send_header("Content-Length", str(len(data)))
            self._cors()
            self.end_headers()
            self.wfile.write(data)
            return

        # --- fase A: memoria semántica ---
        if path == "/api/memoria/config":
            self._json(memoria_config())
            return
        if path == "/api/memoria":
            query = q.get("q", [""])[0].strip()
            if not query:
                self._json({"error": "falta parámetro q"}, 400)
                return
            res = buscar_memoria(query, q.get("limite", [10])[0])
            if res is None:
                self._json({"error": "memoria no disponible (mem0)"}, 503)
                return
            self._json({"consulta": query, "resultados": [
                {"memoria": r.get("memory", ""), "score": round(r.get("score", 0), 4),
                 "created_at": (r.get("metadata") or {}).get("created_at")}
                for r in res]})
            return

        self._json({"error": "ruta no encontrada"}, 404)

def main():
    ap = argparse.ArgumentParser(description="Vigilador API REST")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--host", default=HOST)
    args = ap.parse_args()
    load_env()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Vigilador API en http://{args.host}:{args.port} "
          f"({'con API key' if os.environ.get('VIGILADOR_API_KEY') else 'abierta en LAN'})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("API detenida")

if __name__ == "__main__":
    main()
