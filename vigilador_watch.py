#!/usr/bin/env python3
"""
Vigilador — FASE 5: NÍšCLEO POTENTE.
- Foco diurno: SOLO personas en puertacalle (cochera reservada para la fase nocturna).
- TRACKER de ciclo de vida: cada objeto se sigue de aparición a desaparición
  (duración, zonas recorridas en orden, score máximo, merodeo, veredicto) y se
  cierra con registro estructurado en log + memoria.
- OCUPACIÍ“N de zonas (topics frigate/<zona>/<label>): el daemon sabe si una zona está activa.
- MOTOR DE POLÍTICA (evaluar_evento): fase × zona × objeto × severidad × merodeo ×
  nocturno × recurrencia × rostro → decisión {ignorar|registrar|alertar} + prioridad.
- RECURRENCIA: la memoria (Qdrant) es insumo de decisión — "el sodero pasó N veces".
- SYNC de configuración de Frigate: /api/config con diff y aviso de drift.
- Auditoría MQTT cruda completa (JSON), logs con rotación y vencimiento.
"""
import os, sys, json, time, struct, socket, uuid, logging, re, threading
import urllib.request, urllib.parse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vision_verdict import get_verdict
from vigilador_db import (insertar_avistamiento, actualizar_veredicto_avistamiento,
                          label_corregido_por_vision, add_persona, placa_add, placa_existe)
from vision_policy import zona_vision_activa

# ---------- base del Vigilador (DESACOPLADO de Hermes) ----------
# VIGILADOR_HOME: raíz del box. Si no se define, se deduce del propio directorio:
#   - corriendo en .../vigilador/workspace (perfil) → raíz = .../vigilador
#   - corriendo en /opt/vigilador (box standalone)  → raíz = /opt/vigilador
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ES_PROFILE = os.path.basename(_SCRIPT_DIR) == "workspace"
VIGILADOR_HOME = (os.environ.get("VIGILADOR_HOME")
                  or (os.path.dirname(_SCRIPT_DIR) if _ES_PROFILE else _SCRIPT_DIR))
WS = os.path.join(VIGILADOR_HOME, "workspace") if _ES_PROFILE else VIGILADOR_HOME
SNAP_DIR = os.path.join(WS, "snapshots")
LOG_FILE = os.path.join(WS, "events.log")

# --- conexiones: viven en vigilador_config.json (conexion) — SIN defaults hardcodeados ---
FRIGATE_API = ""
MQTT_HOST, MQTT_PORT = "", 1883
MQTT_USER, MQTT_PASS = "", os.environ.get("MQTT_PASS", "")   # user en config, pass en .env
MQTT_TOPIC = "frigate/#"
CLIENT_ID = "vigilador"

TELEGRAM_CHAT = ""   # viene de conexion.telegram_chat / bots[].chat_id

# ---- política ----
# Momentos: ventanas de tiempo con zonas propias (dia/noche...). El motor elige
# el momento activo y usa SUS zonas. DEFAULT_ZONAS es el respaldo si no hay momento.
DEFAULT_ZONAS = {"puertacalle"}
MOMENTOS = []
INTEREST_ZONES = DEFAULT_ZONAS.copy()   # zonas del momento activo (reflejo para logs/estado)
RANK = {"baja": 0, "media": 1, "alta": 2, "critica": 3}
VISION_TIPO_PRIORIDAD = {}   # tipo de veredicto de visión → prioridad (contexto)
ZONE_LABEL_RULES = {"puertacalle": {"person"}}   # se actualiza dinámicamente desde Frigate
OFFLINE_COOLDOWN = 300
NIGHT_START, NIGHT_END = 23, 7                   # persona nocturna = prioridad crítica
SEEN_TTL = 6 * 3600                              # dedupe de eventos (6 h)
ACTIVIDAD_TIMEOUT = 300                          # s sin updates => finalizar actividad
HEARTBEAT_LOG = 300

# ---- configuración externa (gobernada por API, recarga en caliente) ----
CONFIG_FILE = os.path.join(WS, "vigilador_config.json")
_config_mtime = 0
VISION_CFG = {"proveedores": {}, "zonas": {}, "default": {}}   # visión por zona (proveedor/modelo)

def cargar_config(inicio=False):
    """Carga vigilador_config.json. En arranque aplica también conexión;
    en recarga en caliente aplica solo política (conexión requiere reinicio)."""
    global INTEREST_ZONES, OFFLINE_COOLDOWN, HEARTBEAT_LOG, MOMENTOS
    global FRIGATE_API, MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASS, MQTT_TOPIC, TELEGRAM_CHAT
    global _config_mtime, VISION_CFG, TG_BOTS, TG_REGLAS, VISION_TIPO_PRIORIDAD
    global SOLAPADO_HABILITADO, SOLAPADO_VENTANA
    try:
        if not os.path.exists(CONFIG_FILE):
            return
        cfg = json.load(open(CONFIG_FILE))
        pol = cfg.get("politica", {})
        con = cfg.get("conexion", {})
        if inicio:
            if con.get("mqtt_host"): MQTT_HOST = con["mqtt_host"]
            if con.get("mqtt_port"): MQTT_PORT = int(con["mqtt_port"])
            if con.get("mqtt_user"): MQTT_USER = con["mqtt_user"]
            if con.get("mqtt_pass"): MQTT_PASS = con["mqtt_pass"]
            if not MQTT_PASS: MQTT_PASS = os.environ.get("MQTT_PASS", "")
            if con.get("mqtt_topic"): MQTT_TOPIC = con["mqtt_topic"]
            if con.get("frigate_api"): FRIGATE_API = con["frigate_api"].rstrip("/")
            if con.get("telegram_chat"): TELEGRAM_CHAT = str(con["telegram_chat"])
        if pol.get("momentos") is not None:
            MOMENTOS = [m for m in pol["momentos"] if isinstance(m, dict) and m.get("nombre")]
            if MOMENTOS:
                INTEREST_ZONES = zonas_activas()   # zonas del momento actual
        elif pol.get("zonas_interes"):  # respaldo legacy (sin momentos)
            INTEREST_ZONES = set(pol["zonas_interes"])
        if pol.get("offline_cooldown") is not None: OFFLINE_COOLDOWN = int(pol["offline_cooldown"])
        if pol.get("car_cooldown_s") is not None:
            global CAR_COOLDOWN_S
            CAR_COOLDOWN_S = int(pol["car_cooldown_s"])
        if pol.get("movimiento_min_px") is not None:
            global MOVIMIENTO_MIN_PX, MOVIMIENTO_MIN_PX_POR_LABEL
            v = pol["movimiento_min_px"]
            if isinstance(v, dict):
                MOVIMIENTO_MIN_PX_POR_LABEL = {str(k).lower(): int(x) for k, x in v.items()}
                MOVIMIENTO_MIN_PX = MOVIMIENTO_MIN_PX_POR_LABEL.get("car", 150)
            else:
                MOVIMIENTO_MIN_PX = int(v)
                MOVIMIENTO_MIN_PX_POR_LABEL = {"car": MOVIMIENTO_MIN_PX}
        vtp = pol.get("vision_tipo_prioridad")
        if isinstance(vtp, dict):
            VISION_TIPO_PRIORIDAD = {str(k).lower(): str(v) for k, v in vtp.items()
                                     if str(v) in RANK}
        if pol.get("heartbeat_log") is not None: HEARTBEAT_LOG = int(pol["heartbeat_log"])
        if isinstance(cfg.get("logs"), dict) and cfg["logs"].get("heartbeat_log") is not None:
            HEARTBEAT_LOG = int(cfg["logs"]["heartbeat_log"])
        if cfg.get("vision"):
            VISION_CFG = cfg["vision"]
        tg_cfg = cfg.get("telegram")
        if isinstance(tg_cfg, dict):
            TG_BOTS.clear()
            for b in tg_cfg.get("bots") or []:
                if isinstance(b, dict) and b.get("nombre"):
                    TG_BOTS[b["nombre"]] = {"token_env": b.get("token_env") or "TELEGRAM_BOT_TOKEN",
                                            "chat_id": b.get("chat_id") or TELEGRAM_CHAT,
                                            "habilitado": bool(b.get("habilitado", True))}
            TG_REGLAS.clear()
            for t, r in (tg_cfg.get("reglas") or {}).items():
                if isinstance(r, dict):
                    TG_REGLAS[t] = {"bots": r.get("bots") or [], "habilitado": bool(r.get("habilitado", True))}
            sol = tg_cfg.get("solapado") or {}
            if isinstance(sol, dict):
                SOLAPADO_HABILITADO = bool(sol.get("habilitado", True))
                try:
                    SOLAPADO_VENTANA = int(sol.get("ventana_s", 120))
                except Exception:
                    SOLAPADO_VENTANA = 120
        _config_mtime = os.path.getmtime(CONFIG_FILE)
        log.info("config cargada%s: momentos=%s · offline=%ss · visión zonas=%s",
                 " (arranque)" if inicio else " (recarga)",
                 {m.get("nombre"): m.get("zonas") for m in MOMENTOS} if MOMENTOS
                 else "legacy: " + str(sorted(INTEREST_ZONES)),
                 OFFLINE_COOLDOWN,
                 {z: v.get("habilitado") for z, v in (VISION_CFG.get("zonas") or {}).items()})
    except Exception as e:
        log.exception("cargar_config: %s", e)

def revisar_recarga_config():
    """Recarga en caliente si el archivo de config cambió (se chequea con cada stats)."""
    global _config_mtime
    try:
        if os.path.exists(CONFIG_FILE) and os.path.getmtime(CONFIG_FILE) != _config_mtime:
            cargar_config(inicio=False)
    except Exception as e:
        log.error("revisar_recarga_config: %s", e)

log = logging.getLogger("vigilador")

# ---------- logging ----------

def setup_logging():
    """Log persistente con rotación: logs/vigilador.log (+ consola)."""
    try:
        import logging.handlers
        log_dir = os.path.join(VIGILADOR_HOME, "logs")
        os.makedirs(log_dir, exist_ok=True)
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        fh = logging.handlers.RotatingFileHandler(
            os.path.join(log_dir, "vigilador.log"),
            maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        fh.setLevel(logging.INFO)
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        log.addHandler(fh)
        log.addHandler(sh)
        log.setLevel(logging.INFO)
        log.info("logging a archivo: %s", os.path.join(log_dir, "vigilador.log"))
    except Exception as e:
        print(f"[setup_logging] falló: {e}")

def setup_raw_log():
    """Auditoría cruda del bus MQTT: mqtt_raw-YYYYMMDD.log (JSONL por día).
    GUARDA EL JSON COMPLETO de cada mensaje (fidelidad total para debugging).
    Vencimiento: purga automática de archivos con más de 7 días al arrancar."""
    try:
        purge_raw_logs()
        log.info("auditoría MQTT cruda: %s (JSON completo, archivos diarios, purga >7 días)",
                 os.path.join(WS, "mqtt_raw-YYYYMMDD.log"))
    except Exception as e:
        log.exception("setup_raw_log falló: %s", e)

def purge_raw_logs():
    """Elimina archivos diarios del log crudo con más de 7 días (vencimiento)."""
    import glob
    for old in glob.glob(os.path.join(WS, "mqtt_raw-*.log")):
        try:
            if time.time() - os.path.getmtime(old) > 7 * 86400:
                os.remove(old)
                log.info("purga por vencimiento: %s", os.path.basename(old))
        except Exception:
            pass

def raw_log_publish(hdr, topic, payload):
    """Registra el mensaje MQTT con su JSON COMPLETO.
    Trabajo fino: los updates de frigate/events se guardan COMPLETOS cuando el
    estado del evento CAMBIA (zonas, label, severidad) o pasan 30s; los updates
    idénticos se suprimen (ruido de objetos estacionarios)."""
    global _raw_throttle_count
    if payload[:2] == b"\xff\xd8":
        _raw_write({"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                    "topic": topic, "retained": bool(hdr & 0x01),
                    "kind": "jpeg", "size": len(payload)})
        return
    txt = payload.decode("utf-8", errors="replace")
    try:
        obj = json.loads(txt)
    except Exception:
        _raw_write({"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                    "topic": topic, "retained": bool(hdr & 0x01), "text": txt[:500]})
        return
    entry = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
             "topic": topic, "retained": bool(hdr & 0x01), "json": obj}
    if topic == "frigate/events":
        a = obj.get("after") or obj.get("before") or {}
        eid = a.get("id")
        etype = obj.get("type")
        if eid and etype not in ("new", "end"):
            sig = (tuple(sorted(set((a.get("entered_zones") or []) + (a.get("current_zones") or [])))),
                   a.get("label"), a.get("max_severity"), bool(a.get("false_positive")))
            ahora = time.time()
            if _raw_last.get(eid) == sig and ahora - _raw_last_ts.get(eid, 0) < 30:
                _raw_throttle_count += 1
                return
            _raw_last[eid] = sig
            _raw_last_ts[eid] = ahora
        elif eid:
            _raw_last[eid] = None
        if len(_raw_last) > 1000:
            _raw_last.clear()
    _raw_write(entry)

def _raw_write(entry):
    try:
        day = datetime.now().strftime("%Y%m%d")
        with open(os.path.join(WS, f"mqtt_raw-{day}.log"), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        log.exception("raw_log falló: %s", e)

_raw_last = {}          # event_id -> firma de estado
_raw_last_ts = {}       # event_id -> última escritura (compuerta 30s)
_raw_throttle_count = 0 # updates suprimidos

# ---------- utilidades ----------

def load_env():
    env_file = os.path.join(VIGILADOR_HOME, ".env")
    if os.path.exists(env_file):
        for line in open(env_file):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

def write_log(entry):
    entry["ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        # rotación manual: events.log máx 5MB, 6 backups (≥ˆ35MB)
        try:
            if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 5_000_000:
                for i in range(6, 0, -1):
                    src = f"{LOG_FILE}.{i-1}" if i > 1 else LOG_FILE
                    dst = f"{LOG_FILE}.{i}"
                    if os.path.exists(src):
                        os.replace(src, dst)
        except Exception:
            pass
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        log.error("log write: %s", e)

# ---------- Telegram (bots + ruteo de notificaciones) ----------
# TG_BOTS: nombre -> {token_env, chat_id, habilitado} (config telegram.bots)
# TG_REGLAS: tipo_notificación -> {bots: [nombres], habilitado} (config telegram.reglas)
TG_BOTS = {}
TG_REGLAS = {}

def _read_env_var(key):
    v = os.environ.get(key or "", "")
    if not v:
        try:
            for line in open(os.path.join(VIGILADOR_HOME, ".env")):
                if line.startswith((key or "") + "="):
                    v = line.strip().split("=", 1)[1]
                    break
        except Exception:
            pass
    return v

def _tg_send_text(tok, chat, text, reply_to=None):
    url = f"https://api.telegram.org/bot{tok}/sendMessage"
    data = {"chat_id": chat, "text": text, "parse_mode": "HTML"}
    if reply_to:
        data["reply_to_message_id"] = reply_to
    body = urllib.parse.urlencode(data).encode()
    try:
        req = urllib.request.Request(url, data=body)
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read())
            return (resp.get("result") or {}).get("message_id") if resp.get("ok") else False
    except Exception as e:
        log.error("tg text: %s", e)
        return False

def _tg_send_photo(tok, chat, photo_path, caption, reply_to=None):
    boundary = "----Vigilador" + uuid.uuid4().hex
    with open(photo_path, "rb") as f:
        img = f.read()
    parts = []
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat}\r\n".encode())
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"parse_mode\"\r\n\r\nHTML\r\n".encode())
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption}\r\n".encode())
    if reply_to:
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"reply_to_message_id\"\r\n\r\n{reply_to}\r\n".encode())
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"snap.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n".encode())
    parts.append(img)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    url = f"https://api.telegram.org/bot{tok}/sendPhoto"
    req = urllib.request.Request(url, data=b"".join(parts), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            resp = json.loads(r.read())
            return (resp.get("result") or {}).get("message_id") if resp.get("ok") else False
    except Exception as e:
        log.error("tg photo: %s", e)
        return False

def tg_enviar(tipo, text, foto=None, reply_to=None):
    """Rutea una notificación según telegram.reglas: a qué bot(s) va (o a ninguno).
    Devuelve el message_id del primer bot que la recibió (o False). foto: ruta JPEG.
    reply_to: message_id para encadenar como respuesta (consolidación de paseo)."""
    regla = TG_REGLAS.get(tipo) or {}
    if regla and not regla.get("habilitado", True):
        log.info("notificación '%s': deshabilitada por regla", tipo)
        return False
    bots = [b for b in (regla.get("bots") or [])
            if b in TG_BOTS and TG_BOTS[b].get("habilitado", True)]
    if not bots:
        log.info("notificación '%s': sin bots en la regla — no se envía", tipo)
        return False
    enviado = False
    for nombre in bots:
        b = TG_BOTS[nombre]
        tok = _read_env_var(b.get("token_env") or "TELEGRAM_BOT_TOKEN")
        chat = b.get("chat_id") or TELEGRAM_CHAT
        if not tok or not chat:
            log.warning("bot '%s': sin token o chat_id", nombre)
            continue
        if foto and os.path.exists(foto):
            mid = _tg_send_photo(tok, chat, foto, text, reply_to=reply_to)
        else:
            mid = _tg_send_text(tok, chat, text, reply_to=reply_to)
        if mid:
            if not enviado:
                enviado = mid
    return enviado

# ---------- memoria propia (mem0) ----------
# Toda la config de memoria (llm/embedder/vector store) vive en vigilador_config.json
# → el box es autónomo: un solo archivo de configuración.

_mem = None
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
                                    "collection_name": vs.get("coleccion", "vigilador_eventos"),
                                    **({"api_key": _read_env_var(vs["api_key_env"])} if vs.get("api_key_env") else {})}},
        "version": "v1.1",
    }

def get_memory():
    global _mem
    if _mem is not None:
        return _mem
    try:
        from mem0 import Memory
        oss = _oss_mem0()
        if oss is None:
            # respaldo legacy: config.yaml del perfil (pre-desacople)
            import yaml
            cfg = yaml.safe_load(open(os.path.join(VIGILADOR_HOME, "config.yaml")))
            oss = cfg["memory"]["mem0"]["oss_config"]
            if isinstance(oss, str):
                oss = json.loads(oss)
        _mem = Memory.from_config(oss)
        coll = oss.get("vector_store", {}).get("config", {}).get("collection_name")
        log.info("mem0 inicializada (colección %s)", coll)
    except Exception as e:
        log.error("mem0 init: %s", e)
        _mem = None
    return _mem

def remember(fact):
    m = get_memory()
    if m is None:
        return
    try:
        # infer=False: guarda el texto crudo COMPLETO (veredicto, OCR...) sin reformatear
        m.add(fact, user_id="cristian", infer=False)
        log.info("memoria: %s", fact[:80])
    except Exception as e:
        log.error("mem0 add: %s", e)

# ---------- Frigate API ----------

def frigate_snapshot(event_id, dest):
    try:
        req = urllib.request.Request(f"{FRIGATE_API}/api/events/{event_id}/snapshot.jpg")
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
        with open(dest, "wb") as f:
            f.write(data)
        return dest if data[:2] == b"\xff\xd8" else None
    except Exception as e:
        log.error("snapshot api: %s", e)
        return None

def recuperar_eventos_activos():
    """Al arrancar, recupera de Frigate los eventos EN CURSO (end_time null):
    un corte de red o reinicio del daemon no pierde el avistamiento ni la
    patente — la actividad vuelve al tracker y se finaliza cuando termine."""
    try:
        req = urllib.request.Request(f"{FRIGATE_API}/api/events?limit=200")
        with urllib.request.urlopen(req, timeout=15) as r:
            evs = json.loads(r.read()) or []
    except Exception as ex:
        log.warning("recuperación de eventos activos: %s", ex)
        return
    n = 0
    for e in evs:
        if not isinstance(e, dict) or e.get("end_time") or not e.get("id"):
            continue
        if e.get("label") not in ("person", "car", "dog", "cat"):
            continue
        eid = str(e["id"])
        if eid in actividades:
            continue
        start = e.get("start_time")
        if not start:
            continue
        cam = e.get("camera") or "calle"
        zonas = list(dict.fromkeys((e.get("zones") or []) + (e.get("current_zones") or [])))
        nueva_actividad(eid, cam, e.get("label"), start, zonas)
        # patente acumulada del evento (el LPR la reporta en data)
        try:
            data = e.get("data") or {}
            pl = data.get("recognized_license_plate")
            if pl:
                p, s = _parse_placa(pl)
                actividades[eid]["patente"] = p
                actividades[eid]["patente_score"] = s
        except Exception:
            pass
        # foto si Frigate ya capturó una
        if e.get("has_snapshot"):
            try:
                dest = os.path.join(SNAP_DIR, cam, f"rec_{int(time.time())}_{eid[:8]}.jpg")
                path = frigate_snapshot(eid, dest)
                if path:
                    actividades[eid]["foto"] = path
            except Exception:
                pass
        n += 1
    if n:
        log.info("recuperados %d eventos en curso de Frigate (corte/reinicio sin pérdida)", n)

def modelo_activo():
    """El modelo de visión ACTIVO (CRUD vision.modelos): el único con activo=true.
    Compatible con el viejo vision.default si aún existe."""
    try:
        for m in VISION_CFG.get("modelos") or []:
            if m.get("activo"):
                return m
        d = VISION_CFG.get("default")
        if isinstance(d, dict) and d.get("proveedor"):
            return d
    except Exception:
        pass
    return None

def precalentar_vision():
    """Al arrancar, carga los modelos LOCALES de visión (keep_alive -1):
    la primera llamada de un evento NO paga la recarga de ~40 s —
    el veredicto entra en la ventana de la alerta desde el primer evento."""
    try:
        provs = (VISION_CFG.get("proveedores") or {})
        zonas = (VISION_CFG.get("zonas") or {})
        vistos = set()
        for z, zc in zonas.items():
            prov = zc.get("proveedor") or (modelo_activo() or {}).get("proveedor")
            modelo = zc.get("modelo") or (modelo_activo() or {}).get("modelo")
            pc = provs.get(prov) or {}
            clave = (pc.get("tipo"), pc.get("base_url"), modelo)
            if clave in vistos or pc.get("tipo") != "ollama" or not modelo:
                continue
            vistos.add(clave)
            try:
                body = json.dumps({"model": modelo, "prompt": "precalentamiento",
                                   "stream": False, "keep_alive": -1}).encode()
                req = urllib.request.Request(pc["base_url"].rstrip("/") + "/api/generate",
                                             data=body, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=180) as r:
                    json.loads(r.read())
                log.info("modelo local precalentado y residente: %s", modelo)
            except Exception as e:
                log.warning("precalentamiento %s: %s", modelo, e)
        # memoria: inicializar mem0 + calentar el embedder local (nomic en Ollama)
        try:
            m = get_memory()
            if m:
                m.search("precalentamiento", filters={"user_id": "cristian"}, limit=1)
                log.info("mem0 precalentado (embedder local listo)")
        except Exception as e:
            log.warning("precalentamiento memoria: %s", e)
    except Exception:
        pass

# ---------- sync de configuración de Frigate (aprendizaje continuo) ----------

synced_config = None  # {camera: {...}} — None = aún sin sync (primer arranque)
_last_available = None  # estado previo de frigate/available

def fetch_frigate_config():
    """GET /api/config → normaliza a lo que le importa al Vigilador (cámaras + zonas)."""
    try:
        req = urllib.request.Request(f"{FRIGATE_API}/api/config")
        cfg = json.loads(urllib.request.urlopen(req, timeout=15).read())
        out = {}
        for name, cam in (cfg.get("cameras") or {}).items():
            out[name] = {
                "enabled": bool(cam.get("enabled", True)),
                "detect": bool((cam.get("detect") or {}).get("enabled", True)),
                "zonas": {},
            }
            for zn, z in (cam.get("zones") or {}).items():
                out[name]["zonas"][zn] = {
                    "friendly_name": z.get("friendly_name"),
                    "objetos": list(z.get("objects") or []),
                    "loitering_s": z.get("loitering_time"),
                }
        return out
    except Exception as e:
        log.exception("fetch config frigate: %s", e)
        return None

def diff_config(old, new):
    """Detecta cámaras/zonas nuevas, eliminadas y cambios de objetos/loitering/enabled."""
    cambios = []
    if old is None:
        total_z = sum(len(c["zonas"]) for c in new.values())
        return [f"sync inicial: {len(new)} cámaras, {total_z} zonas"]
    for cam in sorted(set(new) - set(old)):
        cambios.append(f"cámara NUEVA: {cam}")
    for cam in sorted(set(old) - set(new)):
        cambios.append(f"cámara ELIMINADA: {cam}")
    for cam in sorted(set(old) & set(new)):
        nz, oz = set(new[cam]["zonas"]), set(old[cam]["zonas"])
        for zn in sorted(nz - oz):
            cambios.append(f"zona NUEVA en {cam}: {zn} ({new[cam]['zonas'][zn].get('friendly_name')})")
        for zn in sorted(oz - nz):
            cambios.append(f"zona ELIMINADA de {cam}: {zn}")
        for zn in sorted(nz & oz):
            n, o = new[cam]["zonas"][zn], old[cam]["zonas"][zn]
            if n.get("objetos") != o.get("objetos"):
                cambios.append(f"zona {cam}/{zn}: objetos {o.get('objetos')} → {n.get('objetos')}")
            if n.get("loitering_s") != o.get("loitering_s"):
                cambios.append(f"zona {cam}/{zn}: loitering {o.get('loitering_s')} → {n.get('loitering_s')}s")
        if old[cam].get("enabled") != new[cam].get("enabled"):
            cambios.append(f"cámara {cam}: {'deshabilitada' if not new[cam]['enabled'] else 'habilitada'}")
    return cambios

def actualizar_reglas_zonas():
    """Reglas de objetos por zona DINÍMICAS (leídas de Frigate, no hardcodeadas)."""
    global ZONE_LABEL_RULES
    reglas = {}
    if synced_config:
        for cam, c in synced_config.items():
            for zn, z in c["zonas"].items():
                if zn in zonas_activas() and z.get("objetos"):
                    reglas[zn] = set(z["objetos"])
    if reglas != ZONE_LABEL_RULES:
        ZONE_LABEL_RULES = reglas
        log.info("reglas de zona dinámicas (desde Frigate): %s", ZONE_LABEL_RULES)

def sync_frigate_config(reason="arranque", log_unchanged=False):
    """Trae la config de Frigate, diffea contra la caché, registra y alerta drift."""
    global synced_config
    nuevo = fetch_frigate_config()
    if nuevo is None:
        return
    cambios = diff_config(synced_config, nuevo)
    if cambios:
        for c in cambios:
            write_log({"type": "config_sync", "reason": reason, "cambio": c})
            log.info("config sync (%s): %s", reason, c)
        drift = [c for c in cambios
                 if "ELIMINADA" in c or "NUEVA" in c or any(z in c for z in INTEREST_ZONES)]
        if drift:
            try:
                tg_enviar("drift", f"⚠ï¸ <b>Vigilador</b> · cambios en Frigate ({reason}):\n"
                          + "\n".join("• " + d for d in drift[:6]))
            except Exception as e:
                log.exception("alerta drift: %s", e)
    elif log_unchanged:
        write_log({"type": "config_sync", "reason": reason, "cambio": "sin cambios"})
    synced_config = nuevo
    actualizar_reglas_zonas()

# ---------- MQTT puro ----------

def enc_len(n):
    out = b""
    while True:
        d = n % 128
        n //= 128
        if n > 0:
            d |= 0x80
        out += bytes([d])
        if n == 0:
            return out

def recv_exact(s, n):
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socket cerrado")
        buf += chunk
    return buf

def read_packet(s):
    hdr = recv_exact(s, 1)[0]
    mult, rl = 1, 0
    while True:
        b = recv_exact(s, 1)[0]
        rl += (b & 0x7F) * mult
        if not (b & 0x80):
            break
        mult *= 128
    return hdr, recv_exact(s, rl) if rl else b""

# ---------- estado ----------

seen = {}            # event_id -> ts (alertas enviadas)
vision_seen = {}     # event_id -> ts (visión ya aplicada)
vision_seen_retry = {}  # event_id -> ts (reintento de visión con modelo frío)
memory_seen = {}     # event_id -> ts (memoria ya guardada)
face_cache = {}      # event_id -> nombre de rostro reconocido
patente_cache = {}   # event_id -> patente reconocida (tracked_object_update)
last_offline = {}    # cam -> ts
last_heartbeat = 0
retained_seen = 0
actividades = {}     # eid -> actividad (tracker de ciclo de vida)
ocupacion = {}       # zona -> {label -> {activo, ts}}
contadores = {"eventos": 0, "alertas": 0, "vision": 0, "recurrentes": 0,
              "errores": 0, "actividades_fin": 0}
INICIO = time.time()
ultima_alerta = None      # última alerta enviada (para el dashboard)
ultimo_mensaje_mqtt = 0   # ts del último mensaje del bus
detectores = {}           # nombre -> inference_speed (desde frigate/stats)
CAM_ACTIVIDAD = {}        # cámara -> {activa, motion, objetos} (frigate/camera_activity)
SOLAPADO_HABILITADO = True    # consolidación de paseo (cámaras pegadas) — config telegram.solapado
SOLAPADO_VENTANA = 120        # segundos: misma persona en otra cámara dentro de la ventana = mismo paseo
CAR_COOLDOWN_S = 180          # segundos: mismo auto re-rastreado en la misma cámara = repetido (sin avistamiento)
MOVIMIENTO_MIN_PX = 150        # desplazamiento mínimo del centroide (px) para considerar movimiento REAL en autos
MOVIMIENTO_MIN_PX_POR_LABEL = {"car": 150, "person": 0, "dog": 0, "cat": 0}  # 0 = off (fantasmas por etiqueta)
ultimo_car_por_cam = {}       # camara -> ts del último car procesado (dedup de re-rastreos)
PASEO_ACTIVO = None           # {ts, clave, msg_id} — hilo de mensajes del paseo en curso
ALERTAS_MSG = {}              # eid -> (msg_id, tipo_regla) — para el refuerzo tardío de visión

def escribir_estado():
    """Publica el estado vivo del daemon en estado.json (lo consume la API para la UI)."""
    try:
        estado = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "uptime_s": round(time.time() - INICIO, 1),
            "contadores": contadores,
            "ultima_alerta": ultima_alerta,
            "detectores": detectores,
            "cameras_actividad": CAM_ACTIVIDAD,
            "mqtt_ultimo_mensaje_hace_s": round(time.time() - ultimo_mensaje_mqtt, 1) if ultimo_mensaje_mqtt else None,
            "momentos": [{"nombre": m.get("nombre"), "desde": m.get("desde"), "hasta": m.get("hasta"),
                          "zonas": m.get("zonas") or [], "nocturno": bool(m.get("nocturno"))}
                         for m in MOMENTOS],
            "momento_activo": (momento_activo() or {}).get("nombre"),
            "zonas_activas": sorted(zonas_activas()),
            "zonas_interes": sorted(zonas_activas()),
            "reglas_zonas": {k: sorted(v) for k, v in ZONE_LABEL_RULES.items()},
            "vision_config": {z: {"proveedor": v.get("proveedor"), "modelo": v.get("modelo"),
                                  "habilitado": v.get("habilitado")}
                              for z, v in (VISION_CFG.get("zonas") or {}).items()},
            "cameras": sorted(synced_config.keys()) if synced_config else [],
            "zonas_frigate": {cam: sorted(c["zonas"].keys()) for cam, c in (synced_config or {}).items()},
            "ocupacion": {z: {l: v["activo"] for l, v in ls.items()} for z, ls in ocupacion.items()},
            "mqtt_available": _last_available,
            "updates_suprimidos": _raw_throttle_count,
        }
        with open(os.path.join(WS, "estado.json"), "w") as f:
            json.dump(estado, f, ensure_ascii=False, indent=1)
    except Exception as e:
        log.error("estado: %s", e)

def is_night():
    """'Nocturno' lo define el momento activo (flag nocturno), no una hora fija."""
    m = momento_activo()
    return bool(m and m.get("nocturno"))

def momento_activo():
    """Momento (ventana de tiempo con zonas) que contiene 'ahora'. None si ninguno."""
    try:
        now = datetime.now().strftime("%H:%M")
        for m in MOMENTOS:
            d = m.get("desde") or ""
            h = m.get("hasta") or ""
            if not d or not h:
                continue
            if d <= h:
                if d <= now < h:
                    return m
            else:  # cruza medianoche (ej. 23:00 → 07:00)
                if now >= d or now < h:
                    return m
    except Exception:
        pass
    return None

def zonas_activas():
    """Zonas de interés del momento actual (política dinámica por ventana de tiempo)."""
    m = momento_activo()
    if m and m.get("zonas"):
        return set(m["zonas"])
    return DEFAULT_ZONAS

def prune_seen():
    now = time.time()
    for d in (seen, vision_seen, memory_seen, face_cache, patente_cache):
        stale = [eid for eid, ts in d.items() if now - ts > SEEN_TTL]
        for eid in stale:
            del d[eid]

def fmt_ts(epoch):
    try:
        return datetime.fromtimestamp(epoch).strftime("%d/%m %H:%M:%S")
    except Exception:
        return str(epoch)

def esc(s):
    """Escapa HTML para captions con parse_mode=HTML."""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

# ================= NÍšCLEO: tracker + política + ocupación =================

def nueva_actividad(eid, cam, label, start, zonas):
    actividades[eid] = {
        "id": eid, "cam": cam, "label": label,
        "inicio": start, "ultimo": time.time(), "fin": None,
        "duracion": None, "zonas_orden": list(zonas), "max_score": 0,
        "severidad": None, "merodeo": False, "veredicto": None,
        "rostro": None, "patente": None, "patente_score": None,
        "estado": None,   # en_movimiento | detenido (desde stationary de Frigate)
        "terminada": False,
    }

def actualizar_actividad(eid, ev, zonas, score, severity, loitering):
    a = actividades.get(eid)
    if not a:
        return
    a["ultimo"] = time.time()
    for z in zonas:
        if z not in a["zonas_orden"]:
            a["zonas_orden"].append(z)
    a["max_score"] = max(a.get("max_score", 0), score or 0)
    if severity:
        a["severidad"] = severity
    if loitering:
        a["merodeo"] = True
    # DESPLAZAMIENTO REAL del centroide del box (la idea del dueño: los reflejos
    # hacen 'moverse' al estacionado; si el centroide no se desplaza, no hay
    # movimiento real). El box [x1,y1,x2,y2] viene en cada update del evento.
    box = ev.get("box") if isinstance(ev, dict) else None
    if isinstance(box, (list, tuple)) and len(box) == 4:
        cx = (box[0] + box[2]) / 2.0
        cy = (box[1] + box[3]) / 2.0
        if a.get("_cx0") is None:
            a["_cx0"], a["_cy0"], a["_disp"] = cx, cy, 0.0
        else:
            d = ((cx - a["_cx0"]) ** 2 + (cy - a["_cy0"]) ** 2) ** 0.5
            if d > a.get("_disp", 0):
                a["_disp"] = d

def finalizar_actividad(eid, motivo="end"):
    """Cierra la actividad con su historia estructurada (log + memoria)."""
    a = actividades.get(eid)
    if not a or a["terminada"]:
        return
    a["terminada"] = True
    # FANTASMA de persona: sin NINGUNA zona recorrida + desplazamiento mínimo
    # (sombra/rama que el detector "mueve" 2 min) → no es un avistamiento real.
    # El visitante parado en la puerta SIEMPRE entra a una zona → no se toca.
    if a.get("label") == "person" and not (a.get("zonas_orden") or []):
        umb_f = MOVIMIENTO_MIN_PX_POR_LABEL.get("person", 0)
        disp_f = a.get("_disp") or 0
        if umb_f > 0 and disp_f < umb_f:
            write_log({"type": "event_fantasma_persona", "id": str(eid)[:12], "camera": a.get("cam"),
                       "disp_px": round(disp_f, 1), "umbral": umb_f})
            actividades.pop(eid, None)
            return
    a["fin"] = time.time()
    if a["inicio"]:
        a["duracion"] = round(a["fin"] - a["inicio"], 1)
    contadores["actividades_fin"] += 1
    write_log({"type": "actividad", "id": str(eid)[:12], "cam": a["cam"], "label": a["label"],
               "inicio": a["inicio"], "duracion": a["duracion"], "zonas": a["zonas_orden"],
               "max_score": round(a["max_score"], 2), "severidad": a["severidad"],
               "merodeo": a["merodeo"], "veredicto": a["veredicto"], "rostro": a["rostro"],
               "patente": a.get("patente"), "motivo_fin": motivo})
    # La foto del AVISTAMIENTO prefiere la LIMPIA de la API (sin recuadro de
    # debug — el cliente ya sabe qué es). Si la que hay es la del MQTT (anotada
    # con el box de Frigate), intentamos reemplazarla por el snapshot oficial.
    if a.get("foto") and "_mqtt" in a["foto"] and a.get("id"):
        try:
            dest = os.path.join(SNAP_DIR, a["cam"], f"oficial_{str(a['id'])[:8]}_{int(time.time())}.jpg")
            path = frigate_snapshot(a["id"], dest)
            if path:
                a["foto"] = path
        except Exception:
            pass
    # capa SQL: avistamiento SOLO si hay FOTO (regla del dueño: sin foto, no hay evento).
    # Fuentes de foto: MQTT adjuntada durante el evento, o el snapshot oficial
    # de la API con el id del evento (retenido 3 días).
    # Además: confianza BAJA (< 0.55) = ruido (ej. el auto estático fantasma) →
    # no ensucia la app, solo auditoría por log.
    if a.get("max_score") is not None and a["max_score"] < 0.55:
        write_log({"type": "avistamiento_baja_confianza", "id": str(eid)[:12], "cam": a["cam"],
                   "label": a["label"], "score": a["max_score"], "inicio": a["inicio"]})
        actividades.pop(eid, None)
        return
    if not a.get("foto") and a.get("id"):
        try:
            dest = os.path.join(SNAP_DIR, a["cam"], f"fin_{str(a['id'])[:8]}_{int(time.time())}.jpg")
            path = frigate_snapshot(a["id"], dest)
            if path:
                a["foto"] = path
        except Exception:
            pass
    if not a.get("foto"):
        write_log({"type": "avistamiento_sin_foto", "id": str(eid)[:12], "cam": a["cam"],
                   "label": a["label"], "inicio": a["inicio"], "duracion": a["duracion"]})
    else:
        # auto-identidad: si Frigate reconoció un rostro (nombre), el avistamiento
        # queda etiquetado y la persona pasa al registro
        persona = None
        if a.get("rostro"):
            persona = str(a["rostro"])
            try:
                add_persona(persona, "familia")
            except Exception:
                pass
        try:
            # las zonas del avistamiento = SOLO las activas en el momento:
            # una zona nocturna (cocheranocturna) NO figura en un evento diurno.
            mo = momento_activo()
            mz = set(mo.get("zonas") or []) if mo else None
            zonas_final = (a.get("zonas_orden") or []) if not mz else [z for z in (a.get("zonas_orden") or []) if z in mz]
            insertar_avistamiento({
                "evento_id": eid, "camara": a["cam"], "label": a["label"],
                "patente": a.get("patente"), "patente_score": a.get("patente_score"),
                "inicio": a["inicio"], "fin": a["fin"], "duracion": round(a["duracion"], 1),
                "zonas": zonas_final, "veredicto": a.get("veredicto"),
                "prioridad": a.get("prioridad"), "motivo_fin": motivo,
                "foto": a.get("foto"), "persona": a.get("persona"),
                "estado": a.get("estado")})
        except Exception as e:
            contadores["errores"] += 1
            log.error("sql avistamiento: %s", e)
    # memoria: solo si es persona o hubo veredicto (el resto es ruido para las preguntas)
    if a["label"] == "person" or a["veredicto"]:
        fact = f"{fmt_ts(a['inicio'])}: {a['label']} en {a['cam']}"
        if a["zonas_orden"]:
            fact += f", recorrió {a['zonas_orden']}"
        if a["duracion"]:
            fact += f" ({a['duracion']}s)"
        if a["veredicto"]:
            clean = {k: v for k, v in a["veredicto"].items() if not k.startswith("_")}
            fact += f" | visión: {json.dumps(clean, ensure_ascii=False)}"
        if a["rostro"]:
            fact += f" | rostro: {a['rostro']}"
        fact += f" (score {a['max_score']}, severidad {a['severidad']})"
        threading.Thread(target=remember, args=(fact,), daemon=True).start()
    actividades.pop(eid, None)

def finalizar_actividades_stale():
    """Cierra actividades sin updates en ACTIVIDAD_TIMEOUT (objeto que se fue sin 'end')."""
    now = time.time()
    for eid in list(actividades.keys()):
        a = actividades[eid]
        if not a["terminada"] and now - a["ultimo"] > ACTIVIDAD_TIMEOUT:
            finalizar_actividad(eid, "timeout")

def zonas_conocidas():
    zs = set()
    if synced_config:
        for c in synced_config.values():
            zs.update(c["zonas"].keys())
    return zs

def manejar_ocupacion(topic, payload):
    """frigate/<zona>/<label>[/active] — ocupación de zonas."""
    partes = topic.split("/")
    if len(partes) < 3:
        return
    zona, label = partes[1], partes[2]
    txt = payload.decode(errors="replace").strip()
    activo = txt.lower() in ("on", "1", "true", "active")
    if zona not in ocupacion:
        ocupacion[zona] = {}
    prev = ocupacion[zona].get(label)
    ocupacion[zona][label] = {"activo": activo, "ts": time.time()}
    if prev is None or prev.get("activo") != activo:
        write_log({"type": "ocupacion", "zona": zona, "label": label, "activo": activo})

def buscar_recurrencia(cam, label, tipo=None):
    """¿Visita recurrente? Cuenta coincidencias del mismo objeto+cámara en memoria."""
    m = get_memory()
    if m is None:
        return None
    try:
        q = f"{label} en cámara {cam}"
        if tipo:
            q += f" {tipo}"
        r = m.search(q, filters={"user_id": "cristian"}, limit=12)
        n = 0
        for item in r.get("results", []):
            mem_txt = str(item.get("memory", ""))
            if cam in mem_txt and label in mem_txt:
                n += 1
        if n >= 2:
            contadores["recurrentes"] += 1
            return {"n": n}
    except Exception as e:
        contadores["errores"] += 1
        log.error("recurrencia: %s", e)
    return None

def evaluar_evento(eid, cam, label, hit, score, severity, loitering, night,
                   verdict, face_name, recurrente):
    """MOTOR DE POLÍTICA: decide acción y prioridad.
    Devuelve {"accion": ignorar|registrar|ya_alerta|alertar, "prioridad", "razones"}."""
    if not hit:
        return {"accion": "registrar", "razon": "fuera de zonas de interés"}
    # REGLAS DE ZONA (desde Frigate): cada zona admite ciertos labels.
    # Una zona car-only (cochera) NO bloquea un evento de persona que también
    # pase por una zona person (cocheranocturna): solo se ignora si NINGUNA
    # zona del hit admite el label.
    permitidas = [z for z in hit
                  if ZONE_LABEL_RULES.get(z) is None or label in ZONE_LABEL_RULES[z]]
    if not permitidas:
        return {"accion": "ignorar", "razon": f"ninguna zona admite {label}",
                "prioridad": None}
    if eid in seen:
        return {"accion": "ya_alerta", "razon": "ya alertado"}
    prioridad = "media"
    razones = []
    # PISO DE CONFIANZA: una detección de confianza baja (ej. el auto estático
    # detectado a 0.51 — camioneta lejana en zona de exclusión) NO alerta.
    # Los eventos reales (autos/personas cerca) pasan de 0.7-0.9 holgados.
    if score is not None and score < 0.55:
        return {"accion": "registrar", "razon": f"confianza baja ({score:.2f} < 0.55)", "prioridad": "baja"}
    if severity == "alert":
        prioridad = "alta"
        razones.append("severidad alert")
    if loitering:
        prioridad = "alta"
        razones.append("merodeo")
    if night and label == "person":
        prioridad = "critica"
        razones.append("persona nocturna")
    if verdict and verdict.get("sospechoso"):
        prioridad = "critica"
        razones.append("sospechoso (visión)")
    if face_name:
        prioridad = "baja"
        razones.append(f"conocido: {face_name}")
    elif recurrente:
        prioridad = "media"
        razones.append(f"recurrente x{recurrente.get('n', 1)}")
    # visión por CONTEXTO: el tipo de veredicto ajusta la prioridad (configurable).
    # Las reglas duras (noche/sospechoso → crítica) no se bajan; el merodeo no baja de alta.
    if verdict and isinstance(verdict, dict) and verdict.get("tipo"):
        t = str(verdict["tipo"]).lower()
        mapeo = VISION_TIPO_PRIORIDAD.get(t)
        if mapeo and prioridad != "critica":
            if loitering and RANK.get(mapeo, 1) < RANK.get("alta", 2):
                mapeo = "alta"
            prioridad = mapeo
            razones.append(f"contexto visión '{t}' → {prioridad}")
    return {"accion": "alertar", "prioridad": prioridad, "razones": razones}

# ================= proceso de eventos =================

def _es_patente_valida(s):
    """Patente argentina: ABC123 (vieja) o AB123CD (Mercosur). Frigate a veces
    emite el NOMBRE DE LA CÍMARA en el campo plate cuando no leyó nada."""
    import re
    return bool(re.match(r"^[A-Z]{2,3}\d{3}[A-Z]{0,2}$", (s or "").upper()))

def _parse_placa(v):
    """Frigate emite la placa como ["AB123CD", score] (lista), como string
    serializado "['AB123CD', 0.94]" o como texto. Devuelve (patente, score).
    Devuelve (None, None) si el valor no parece una patente real."""
    if isinstance(v, list) and v:
        try:
            p = str(v[0]); s = v[1] if len(v) > 1 else None
            return (p, s) if _es_patente_valida(p) else (None, None)
        except Exception:
            return (None, None)
    if isinstance(v, str) and v.startswith("["):
        try:
            import ast
            parsed = ast.literal_eval(v)
            if isinstance(parsed, list) and parsed:
                p = str(parsed[0]); s = parsed[1] if len(parsed) > 1 else None
                return (p, s) if _es_patente_valida(p) else (None, None)
        except Exception:
            pass
    p = str(v)
    return (p, None) if _es_patente_valida(p) else (None, None)

def process_event(ev, etype="update"):
    global PASEO_ACTIVO
    """Evento de frigate/events (estado 'after').
    Núcleo: tracker de ciclo de vida → visión (solo personas en puertacalle, diurno)
    → recurrencia → motor de política → alerta."""
    eid = ev.get("id")
    cam = ev.get("camera")
    label = ev.get("label")
    if not eid or not cam or not label:
        return
    contadores["eventos"] += 1
    if ev.get("false_positive"):
        write_log({"type": "event_fp", "id": str(eid)[:12], "camera": cam, "label": label})
        return

    entered = set(ev.get("entered_zones") or [])
    current = set(ev.get("current_zones") or [])
    zones = entered | current
    hit = zones & zonas_activas()   # zonas del momento activo (día/noche dinámico)
    score = ev.get("score") or ev.get("top_score")
    start = ev.get("start_time")
    severity = ev.get("max_severity")
    loitering = bool(ev.get("pending_loitering") or ev.get("loitering"))
    night = is_night()

    # --- filtro de ESTACIONADOS: un auto/animal que llega YA quieto (stationary,
    #     sin actividad previa en esta sesión) no genera evento ni avistamiento.
    #     La entrada en movimiento sí se registra; lo estático no reporta. ---
    if ev.get("stationary") and label in ("car", "dog", "cat") and eid not in actividades:
        write_log({"type": "event_estacionado", "id": str(eid)[:12], "camera": cam, "label": label})
        return

    # --- tracker de ciclo de vida ---
    if etype == "new" or eid not in actividades:
        # DEDUP de autos re-rastreados: Frigate re-trackea el mismo vehículo
        # (jitter del estacionado o la camioneta de la calle) → eventos nuevos
        # seguidos. Dentro del cooldown → repetido: solo log, sin avistamiento.
        if label == "car" and cam:
            prev = ultimo_car_por_cam.get(cam)
            if prev is not None and start - prev < CAR_COOLDOWN_S:
                write_log({"type": "event_car_repetido", "id": str(eid)[:12], "camera": cam,
                           "hace_s": round(start - prev, 1)})
                return
            ultimo_car_por_cam[cam] = start
            if len(ultimo_car_por_cam) > 100:
                for k in [k for k, v in ultimo_car_por_cam.items() if time.time() - v > 3600]:
                    ultimo_car_por_cam.pop(k, None)
        nueva_actividad(eid, cam, label, start, zones)
    else:
        actualizar_actividad(eid, ev, zones, score, severity, loitering)
    actividades[eid]["estado"] = "detenido" if ev.get("stationary") else "en_movimiento"

    if etype == "end":
        finalizar_actividad(eid, "end")
        return  # la alerta ya se emitió en su momento

    # snapshot por API (solo la primera vez por evento; SÍ busca aunque ya se haya
    # alertado — Frigate puede publicar has_snapshot=true después de la alerta)
    path = None
    if (ev.get("has_snapshot") and eid not in vision_seen
            and not (eid in actividades and actividades[eid].get("foto"))):
        sub = os.path.join(SNAP_DIR, cam)
        os.makedirs(sub, exist_ok=True)
        dest = os.path.join(sub, f"{time.strftime('%Y%m%d_%H%M%S')}_{label}.jpg")
        path = frigate_snapshot(eid, dest)
        if path and eid in actividades:
            actividades[eid]["foto"] = path

    # --- VISIÍ“N: asociada a ZONA (config vision.zonas.<zona>.habilitado) ---
    # Cada zona puede usar un proveedor/modelo distinto (o estar deshabilitada),
    # y un filtro de labels (vision.zonas.<zona>.labels, default ["person"]).
    verdict = None
    zonas_cfg = VISION_CFG.get("zonas") or {}
    perfiles_cfg = VISION_CFG.get("perfiles") or {}
    zonas_vision = [z for z in hit
                    if zona_vision_activa(zonas_cfg.get(z, {}), perfiles_cfg, label)]
    do_vision = bool(zonas_vision)

    # --- RECONOCIMIENTO FACIAL (cuando Frigate lo emita) ---
    face_name = None
    sub = ev.get("sub_label")
    if sub:
        face_name = str(sub)
        # Frigate a veces serializa el atributo como string "['Nombre', score]"
        if face_name.startswith("["):
            try:
                import ast
                parsed = ast.literal_eval(face_name)
                if isinstance(parsed, list) and parsed:
                    face_name = str(parsed[0])
            except Exception:
                pass
    else:
        attrs = (ev.get("data") or {}).get("attributes") or []
        for a in attrs:
            if isinstance(a, dict) and a.get("face"):
                fv = a["face"]
                # Frigate emite face como ["Nombre", score], "Nombre" o el
                # string serializado "['Nombre', score]" (caso visto en patio)
                if isinstance(fv, list) and fv:
                    face_name = str(fv[0])
                elif isinstance(fv, str) and fv.startswith("["):
                    try:
                        import ast
                        parsed = ast.literal_eval(fv)
                        if isinstance(parsed, list) and parsed:
                            face_name = str(parsed[0])
                        else:
                            face_name = fv
                    except Exception:
                        face_name = fv
                else:
                    face_name = str(fv)
    if not face_name:
        face_name = face_cache.get(eid)

    # --- PATENTE (reconocimiento de placas de Frigate, cuando esté activo) ---
    patente = None
    patente_score = None
    pl = ev.get("recognized_license_plate")
    if pl:
        patente, patente_score = _parse_placa(pl)
    if not patente:
        snap = ev.get("snapshot")
        if isinstance(snap, dict) and snap.get("recognized_license_plate"):
            patente, ps = _parse_placa(snap["recognized_license_plate"])
            patente_score = patente_score or ps
    if not patente:
        d = ev.get("data")
        if isinstance(d, dict) and d.get("recognized_license_plate"):
            patente, ps = _parse_placa(d["recognized_license_plate"])
            patente_score = patente_score or ps
    if not patente:
        pv = patente_cache.get(eid)
        if pv:
            patente, ps = _parse_placa(pv)
            patente_score = patente_score or ps
    if patente and eid in actividades:
        actividades[eid]["patente"] = patente
        actividades[eid]["patente_score"] = patente_score
    # AUTO-PLACA: una patente NUEVA leída por el LPR se auto-registra en el
    # apartado Placas (tipo desconocido) — el dueño la clasifica después.
    if patente:
        try:
            if not placa_existe(patente):
                placa_add(patente, "", "desconocido", "auto (LPR)")
                log.info("placa auto-registrada (LPR): %s", patente)
        except Exception:
            pass

    # La foto puede venir del MQTT (adjuntada a la actividad) aunque Frigate no
    # mande has_snapshot en el evento MQTT (campo que solo existe en la API REST)
    if not path and eid in actividades and actividades[eid].get("foto"):
        path = actividades[eid]["foto"]

    if path and do_vision and eid not in vision_seen:
        vision_seen[eid] = time.time()
        if len(vision_seen) > 1000:
            prune_seen()
        contadores["vision"] += 1
        # proveedor/modelo de la zona (o default) según config
        zc = (VISION_CFG.get("zonas") or {}).get(zonas_vision[0], {}) if zonas_vision else {}
        # PERFIL de la zona: prompt (contexto) + modelo override (si el perfil lo define)
        perfil = None
        pid = zc.get("perfil_id")
        if pid:
            perfil = (VISION_CFG.get("perfiles") or {}).get(pid)
        perfil_prompt = (perfil or {}).get("prompt") or ""
        perfil_modelo = ((perfil or {}).get("modelo") or "").strip()
        act = modelo_activo() or {}
        prov = zc.get("proveedor") or act.get("proveedor")
        modelo = zc.get("modelo") or act.get("modelo")
        if perfil_modelo:
            if "/" in perfil_modelo:          # formato "proveedor/modelo" → ambos
                prov, modelo = perfil_modelo.split("/", 1)
            else:                              # nombre pelado → proveedor del activo
                modelo = perfil_modelo
        pc = (VISION_CFG.get("proveedores") or {}).get(prov) or None
        contexto = f"cámara {cam}, zona {sorted(hit) if hit else 'sin zona'}, {time.strftime('%d/%m %H:%M')}"
        # REESCALAR la imagen antes de mandarla (config vision.zonas.<zona>.reescalar):
        # las GPU chicas procesan ~2x más rápido una foto de 576-640px
        if zc.get("reescalar") and path:
            try:
                from PIL import Image
                im = Image.open(path)
                if im.width > 640:
                    r = 640.0 / im.width
                    im2 = im.resize((640, max(1, int(im.height * r))))
                    tmp = f"{path}.res640.jpg"
                    im2.convert("RGB").save(tmp, "JPEG", quality=85)
                    path = tmp
            except Exception:
                pass
        # --- VISIÍ“N con TOPE de 40 s en hilo: la alerta espera al veredicto para
        #     salir COMPLETA en un solo mensaje (decisión del dueño, 13/08).
        #     Cobertura: modelo caliente ~11 s; recarga del modelo ~38 s.
        #     Si el proveedor tarda/falla, la alerta sale igual y el veredicto
        #     llega como REFUERZO (caso límite). ---
        _vr = {}

        def _analizar():
            try:
                _vr["v"] = get_verdict(path, contexto, proveedor=pc, modelo=modelo, perfil_prompt=perfil_prompt)
            except Exception as e:
                _vr["err"] = str(e)
            _vr["done"] = True

        hilo_v = threading.Thread(target=_analizar, daemon=True)
        hilo_v.start()
        hilo_v.join(timeout=40)
        if _vr.get("done"):
            verdict = _vr.get("v")
            if _vr.get("err"):
                contadores["errores"] += 1
                log.error("vision: %s", _vr["err"])
            if verdict:
                verdict["_zona"] = sorted(zonas_vision)
                write_log({"type": "vision", "id": str(eid)[:12], "camera": cam, "label": label,
                           "zonas": sorted(hit), "proveedor": prov, "modelo": modelo, "veredicto": verdict})
        else:
            # la visión sigue en segundo plano → REFUERZO tardío
            def _refuerzo():
                try:
                    hilo_v.join(timeout=90)
                    v = _vr.get("v")
                    # modelo frío (primer uso tras el idle): reintenta una vez
                    if v and isinstance(v, dict) and v.get("_error") and eid not in vision_seen_retry:
                        vision_seen_retry[eid] = time.time()
                        time.sleep(5)
                        try:
                            v2 = get_verdict(path, contexto, proveedor=pc, modelo=modelo, perfil_prompt=perfil_prompt)
                            if v2 and isinstance(v2, dict) and not v2.get("_error"):
                                v = v2
                        except Exception:
                            pass
                    if not v:
                        return
                    # fallo de proveedor: no mandar mensajes con texto de error
                    if isinstance(v, dict) and v.get("_error"):
                        return
                    if eid in actividades:
                        actividades[eid]["veredicto"] = v
                        actividades[eid]["label"] = label_corregido_por_vision(
                            actividades[eid].get("label"), v)
                    else:
                        # actividad ya cerrada: el veredicto tardío se guarda en SQL
                        try:
                            actualizar_veredicto_avistamiento(eid, v)
                        except Exception:
                            pass
                    write_log({"type": "vision_tardia", "id": str(eid)[:12], "camera": cam,
                               "label": label, "veredicto": v})
                    par = ALERTAS_MSG.get(eid)
                    if par:
                        mid, tipo_regla = par
                        txt = f"🧠 <b>{esc(v.get('tipo') or 'desconocido')}</b> ({v.get('confianza', 0):.2f})"
                        if v.get("sospechoso"):
                            txt += "\n⚠ï¸ <b>SOSPECHOSO</b>"
                        if v.get("descripcion"):
                            txt += f"\n📝 {esc(v['descripcion'][:160])}"
                        tg_enviar(tipo_regla, txt, reply_to=mid)
                except Exception:
                    pass
            threading.Thread(target=_refuerzo, daemon=True).start()
            verdict = None

    # guardar veredicto/rostro en la actividad
    if eid in actividades:
        if verdict:
            actividades[eid]["veredicto"] = verdict
        if face_name:
            actividades[eid]["rostro"] = face_name

    # --- RECURRENCIA: la memoria como insumo de decisión ---
    recurrente = None
    if do_vision and verdict:
        # recurrencia en HILO con tope 3 s: la falta de datos NO frena la alerta
        _rec = {}

        def _buscar_recurrencia():
            try:
                _rec["r"] = buscar_recurrencia(cam, label, verdict.get("tipo"))
            except Exception:
                pass
            _rec["done"] = True

        _h_rec = threading.Thread(target=_buscar_recurrencia, daemon=True)
        _h_rec.start()
        _h_rec.join(timeout=3)
        if _rec.get("done"):
            recurrente = _rec.get("r")

    # --- MEMORIA temprana (veredicto, una vez por evento) ---
    if do_vision and eid not in memory_seen:
        memory_seen[eid] = time.time()
        if len(memory_seen) > 1000:
            prune_seen()
        fact = f"{fmt_ts(start)}: {label} en cámara {cam}"
        if hit:
            fact += f", zona {sorted(hit)}"
        if verdict:
            clean = {k: v for k, v in verdict.items() if not k.startswith("_")}
            fact += f" | visión: {json.dumps(clean, ensure_ascii=False)}"
        if face_name:
            fact += f" | rostro: {face_name}"
        fact += f" (confianza detección {score if score else 'n/d'}, severidad {severity})"
        # memoria FIRE-AND-FORGET: nunca bloquea el flujo del evento
        threading.Thread(target=remember, args=(fact,), daemon=True).start()

    # --- CORRECCIÍ“N por visión: si el veredicto descarta una persona y reconoce
    #     un animal, corrige también el label estructural del avistamiento. ---
    label_vision = label_corregido_por_vision(label, verdict)
    es_animal = label_vision != str(label or "").lower()
    if es_animal:
        if eid in actividades:
            actividades[eid]["label"] = label_vision
        write_log({"type": "vision_correccion", "id": str(eid)[:12], "camera": cam,
                   "label": label, "label_corregido": label_vision,
                   "detalle": ((verdict or {}).get("descripcion") or "")[:120]})

    # --- MOTOR DE POLÍTICA ---
    decision = evaluar_evento(eid, cam, label, hit, score, severity, loitering, night,
                              verdict, face_name, recurrente)
    if es_animal:
        decision = {"accion": "registrar", "razon": "animal (corrección por visión)"}
    # AUTO ESTACIONADO NO ALERTA: un objeto 'detenido' (parado) que entra a una
    # zona (ej. el auto en la cochera) NO es un evento de seguridad — solo
    # auditoría. El estacionado en movimiento previo ya alertó en su momento.
    if decision.get("accion") == "alertar" and actividades.get(eid, {}).get("estado") == "detenido":
        decision = {"accion": "registrar", "razon": "estacionado (sin alerta)"}
        write_log({"type": "event_estacionado_sin_alerta", "id": str(eid)[:12],
                   "camera": cam, "label": label, "zonas": sorted(hit)})
    # MOVIMIENTO FALSO por REFLEJOS/FANTASMAS: Frigate dice 'en_movimiento' pero
    # el centroide del box no se desplazó (el auto parado cuyo reflejo baila, o
    # un track fantasma). Umbral POR ETIQUETA (config movimiento_min_px):
    # 0 = apagado (personas paradas en la puerta SÍ son eventos reales).
    umbral_px = MOVIMIENTO_MIN_PX_POR_LABEL.get(label, 0)
    if decision.get("accion") == "alertar" and umbral_px > 0:
        disp = actividades.get(eid, {}).get("_disp") or 0
        if disp < umbral_px:
            decision = {"accion": "registrar",
                        "razon": f"movimiento falso (desplazamiento {disp:.0f}px < {umbral_px}px)"}
            write_log({"type": "event_movimiento_falso", "id": str(eid)[:12], "camera": cam,
                       "label": label, "disp_px": round(disp, 1), "umbral": umbral_px})

    if decision["accion"] == "ignorar":
        write_log({"type": "event_zona_filtrado", "id": str(eid)[:12], "camera": cam,
                   "label": label, "razon": decision["razon"]})
        return
    if decision["accion"] == "ya_alerta":
        write_log({"type": "event_dup", "id": str(eid)[:12], "camera": cam, "label": label})
        return
    if decision["accion"] == "registrar":
        write_log({"type": "event_fuera_zona", "id": str(eid)[:12], "camera": cam, "label": label,
                   "zones": sorted(zones), "score": score, "veredicto": verdict})
        return

    # --- ALERTA ---
    global ultima_alerta
    seen[eid] = time.time()
    if len(seen) > 500:
        prune_seen()
    contadores["alertas"] += 1
    ultima_alerta = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "camara": cam, "label": label,
                     "prioridad": decision["prioridad"],
                     "veredicto": (verdict or {}).get("tipo") if verdict else None,
                     "patente": patente if patente else None}
    prioridad = decision["prioridad"]
    emoji = {"critica": "🚨🚨 CRÍTICA", "alta": "🚨 ALTA",
             "media": "⚡ MEDIA", "baja": "ℹ️ INFO"}.get(prioridad, prioridad)
    tags = [emoji]
    if loitering:
        tags.append("merodeo")
    if night and label == "person":
        tags.append("nocturno")
    if recurrente:
        tags.append(f"recurrente x{recurrente['n']}")
    for r in decision["razones"]:
        if r not in tags:
            tags.append(r)

    cap = f"🔎 <b>{esc(cam)}</b> · {esc(label)} · {fmt_ts(start)}"
    if verdict and verdict.get("_arma"):
        cap = f"⚠ï¸ <b>POSIBLE ARMA</b> ({esc(verdict['_arma'])})\n" + cap
    if hit:
        cap += f" · <i>{esc(','.join(sorted(hit)))}</i>"
    if tags:
        cap += "\n" + " · ".join(tags)
    if score:
        cap += f"\nconfianza {score:.2f}"
    if verdict:
        line = []
        if verdict.get("_error"):
            line.append("sin veredicto (proveedor)")   # no filtrar textos de error al cliente
        elif verdict.get("tipo"):
            line.append(f"{esc(verdict['tipo'])} ({verdict.get('confianza', 0):.2f})")
        if verdict.get("objetos"):
            line.append("📦 " + esc(", ".join(verdict["objetos"][:3])))
        if verdict.get("vehiculo") and verdict["vehiculo"] not in ("no visible",):
            line.append("🚗 " + esc(verdict["vehiculo"]))
        if verdict.get("ocr"):
            line.append("🔤 " + esc(", ".join(verdict["ocr"][:3])))
        if line:
            cap += "\n🧠 " + " · ".join(line)
        if verdict.get("sospechoso"):
            cap += "\n⚠ï¸ <b>SOSPECHOSO</b>"
        if verdict.get("descripcion"):
            cap += "\n📝 " + esc(verdict["descripcion"][:160])
    if face_name:
        cap += f"\n🧑 {esc(face_name)}"

    sent_photo = False
    # --- SOLAPADO de cámaras: misma persona en otra cámara dentro de la ventana
    #     = mismo paseo → la alerta se encadena como respuesta del primer mensaje ---
    reply_to = None
    clave = face_name or ("person" if label == "person" else label)
    if SOLAPADO_HABILITADO and label == "person" and PASEO_ACTIVO:
        p = PASEO_ACTIVO
        if p.get("clave") == clave and time.time() - p.get("ts", 0) <= SOLAPADO_VENTANA:
            reply_to = p.get("msg_id")
            p["ts"] = time.time()
        else:
            PASEO_ACTIVO = None
    if path:
        sent_photo = tg_enviar(f"alerta_{prioridad}", cap, foto=path, reply_to=reply_to)
        if sent_photo and not reply_to and PASEO_ACTIVO is None:
            PASEO_ACTIVO = {"ts": time.time(), "clave": clave, "msg_id": sent_photo}
        if sent_photo:
            ALERTAS_MSG[eid] = (sent_photo, f"alerta_{prioridad}")
            if len(ALERTAS_MSG) > 500:
                try:
                    ALERTAS_MSG.pop(next(iter(ALERTAS_MSG)))
                except Exception:
                    pass
        if sent_photo:
            write_log({"type": "alerta_foto", "id": str(eid)[:12], "camera": cam, "label": label,
                       "zonas": sorted(hit), "prioridad": prioridad, "severity": severity,
                       "loitering": loitering, "path": path, "veredicto": verdict,
                       "recurrente": recurrente})
    if not sent_photo:
        mid_txt = tg_enviar(f"alerta_{prioridad}", cap)
        if mid_txt:
            ALERTAS_MSG[eid] = (mid_txt, f"alerta_{prioridad}")
            if len(ALERTAS_MSG) > 500:
                try:
                    ALERTAS_MSG.pop(next(iter(ALERTAS_MSG)))
                except Exception:
                    pass
        write_log({"type": "alerta_texto", "id": str(eid)[:12], "camera": cam, "label": label,
                   "zonas": sorted(hit), "prioridad": prioridad, "severity": severity,
                   "loitering": loitering, "veredicto": verdict, "recurrente": recurrente})

# ---------- bus MQTT ----------

def handle_publish(hdr, topic, payload):
    global last_heartbeat, retained_seen, _last_available, ultimo_mensaje_mqtt
    ultimo_mensaje_mqtt = time.time()
    raw_log_publish(hdr, topic, payload)

    # señal de vida de Frigate (se procesa incluso si es retenido)
    if topic == "frigate/available":
        st = payload.decode(errors="replace").strip()
        if st == "online" and _last_available == "offline":
            sync_frigate_config("reload de Frigate", log_unchanged=True)
        _last_available = st
        return

    # mensajes retenidos = estado previo, no eventos vivos
    if hdr & 0x01:
        retained_seen += 1
        return

    # --- frigate/events (JSON {type, before, after}) ---
    if topic == "frigate/events":
        try:
            msg = json.loads(payload.decode("utf-8", errors="replace"))
        except Exception:
            return
        ev = msg.get("after") or msg.get("before") or msg
        process_event(ev, etype=msg.get("type", "update"))
        return

    # --- ocupación de zonas (frigate/<zona>/<label>[/active]) ---
    m = re.match(r"^frigate/([^/]+)/([^/]+)(?:/active)?$", topic)
    if m and m.group(1) in zonas_conocidas():
        manejar_ocupacion(topic, payload)
        return

    # --- snapshots MQTT: guardar, no alertar (la alerta lleva el de la API) ---
    m = re.match(r"^frigate/([^/]+)/([^/]+)/snapshot$", topic)
    if m:
        cam, label = m.group(1), m.group(2)
        if payload[:2] != b"\xff\xd8":
            return
        sub = os.path.join(SNAP_DIR, cam)
        os.makedirs(sub, exist_ok=True)
        path = os.path.join(sub, f"{time.strftime('%Y%m%d_%H%M%S')}_{label}_mqtt.jpg")
        try:
            with open(path, "wb") as f:
                f.write(payload)
            write_log({"type": "snapshot", "camera": cam, "label": label, "path": path})
            # adjuntar a la actividad activa de esa cámara/label (la más reciente sin foto):
            # así el avistamiento nace con foto aunque Frigate purgue el evento después
            mejor = None
            for a in actividades.values():
                if a.get("cam") == cam and a.get("label") == label and not a.get("foto"):
                    if mejor is None or a.get("inicio", 0) > mejor.get("inicio", 0):
                        mejor = a
            if mejor:
                mejor["foto"] = path
        except Exception as e:
            log.error("snapshot save: %s", e)
        return

    # --- stats + heartbeat con telemetría ---
    if topic == "frigate/stats":
        try:
            st = json.loads(payload.decode())
            svc = st.get("service", {})
            free = ((svc.get("storage", {}).get("/media/frigate/recordings", {}) or {}).get("free", 0))
            write_log({"type": "stats", "version": svc.get("version"), "uptime": svc.get("uptime"),
                       "detection_fps": st.get("detection_fps"), "storage_free_mb": round(free, 1)})
            detectores.clear()
            for k, v in (st.get("detectors") or {}).items():
                detectores[k] = {"inference_speed": v.get("inference_speed")}
            revisar_recarga_config()  # recarga en caliente si cambió la config (≥ˆ60s)
            escribir_estado()  # estado vivo para la API/UI
            if time.time() - last_heartbeat > HEARTBEAT_LOG:
                c = contadores
                log.info("heartbeat OK · det_fps=%.1f · free=%.0f MB · E:%d A:%d V:%d Rec:%d Act:%d err:%d sup:%d",
                         st.get("detection_fps") or 0, free,
                         c["eventos"], c["alertas"], c["vision"], c["recurrentes"],
                         c["actividades_fin"], c["errores"], _raw_throttle_count)
                last_heartbeat = time.time()
                finalizar_actividades_stale()
                sync_frigate_config("poll periódico")
        except Exception:
            pass
        return

    # --- camera_activity: estado vivo por cámara (agenda MQTT fase 2) ---
    # payload: {cámara: {motion: bool, objects: [{label, current_zones, stationary...}]}}
    if topic == "frigate/camera_activity":
        try:
            act = json.loads(payload.decode())
            CAM_ACTIVIDAD.clear()
            for nombre, info in act.items():
                if isinstance(info, dict):
                    objs = info.get("objects") or []
                    CAM_ACTIVIDAD[nombre] = {
                        "activa": bool(info.get("motion")) or len(objs) > 0,
                        "motion": bool(info.get("motion")),
                        "objetos": [{"label": o.get("label"),
                                     "zona": (o.get("current_zones") or [None])[0],
                                     "estacionado": bool(o.get("stationary"))}
                                    for o in objs[:5]],
                    }
            escribir_estado()
        except Exception:
            pass
        return

    # --- reviews: registro (sin alerta para no duplicar) ---
    if topic == "frigate/reviews":
        try:
            rv = json.loads(payload.decode())
            write_log({"type": "review", "id": str(rv.get("id"))[:12], "camera": rv.get("camera"),
                       "severity": rv.get("severity"), "zones": (rv.get("data") or {}).get("zones")})
        except Exception:
            pass
        return

    # --- tracked_object_update: metadatos del objeto (reconocimiento facial/patentes) ---
    if topic == "frigate/tracked_object_update":
        try:
            tou = json.loads(payload.decode())
            eid = tou.get("id")
            write_log({"type": "tracked_update", "id": str(eid)[:12] if eid else None, "data": tou})
            if eid:
                # --- mensaje LPR dedicado: {type: "lpr", plate: "AB855EB", score} ---
                if tou.get("type") == "lpr" and _es_patente_valida(str(tou.get("plate") or "")):
                    patente_cache[eid] = str(tou["plate"])
                    log.info("patente LPR para evento %s: %s (score %.2f)",
                             str(eid)[:12], tou["plate"], tou.get("score") or 0)
                else:
                    def find_face(o):
                        if isinstance(o, dict):
                            for k, v in o.items():
                                if k in ("face", "recognized_face", "sub_label") and isinstance(v, str) and v:
                                    return v
                                r = find_face(v)
                                if r:
                                    return r
                        elif isinstance(o, list):
                            for it in o:
                                r = find_face(it)
                                if r:
                                    return r
                        return None
                    f = find_face(tou)
                    if f:
                        face_cache[eid] = f
                        log.info("rostro reconocido para evento %s: %s", str(eid)[:12], f)
                    # patente reconocida (el topic lleva recognized_license_plate)
                    def find_plate(o):
                        if isinstance(o, dict):
                            for k, v in o.items():
                                if k == "recognized_license_plate" and v:
                                    return v
                                r = find_plate(v)
                                if r:
                                    return r
                        elif isinstance(o, list):
                            for it in o:
                                r = find_plate(it)
                                if r:
                                    return r
                        return None
                    lp = find_plate(tou)
                    if lp:
                        patente_cache[eid] = _parse_placa(lp)[0]
                        log.info("patente reconocida para evento %s: %s", str(eid)[:12], lp)
        except Exception:
            pass
        return

    # --- estado de detección por cámara ---
    m = re.match(r"^frigate/([^/]+)/status/detect$", topic)
    if m:
        cam = m.group(1)
        status = payload.decode(errors="replace").strip()
        now = time.time()
        if status == "offline" and now - last_offline.get(cam, 0) > OFFLINE_COOLDOWN:
            last_offline[cam] = now
            tg_enviar("offline", f"⚠ï¸ <b>{cam}</b>: detector OFFLINE ({time.strftime('%H:%M:%S')})")
            threading.Thread(target=remember, args=(f"{time.strftime('%Y-%m-%d %H:%M:%S')}: detector de {cam} quedó offline",), daemon=True).start()
        write_log({"type": "detect_status", "camera": cam, "status": status})
        return

# ---------- bucle principal ----------

def run():
    global retained_seen
    while True:
        try:
            s = socket.create_connection((MQTT_HOST, MQTT_PORT), timeout=10)
            s.settimeout(1.0)
            cid = CLIENT_ID.encode()
            vh = struct.pack(">H", 4) + b"MQTT" + bytes([4, 0xC2]) + struct.pack(">H", 60)
            pl = struct.pack(">H", len(cid)) + cid
            pl += struct.pack(">H", len(MQTT_USER.encode())) + MQTT_USER.encode()
            pl += struct.pack(">H", len(MQTT_PASS.encode())) + MQTT_PASS.encode()
            s.sendall(b"\x10" + enc_len(len(vh) + len(pl)) + vh + pl)
            ok = False
            t0 = time.time()
            while time.time() - t0 < 5:
                try:
                    hdr, body = read_packet(s)
                    if hdr >> 4 == 2:
                        ok = body[1] == 0
                        break
                except socket.timeout:
                    continue
            if not ok:
                log.error("CONNACK rechazado")
                s.close()
                time.sleep(10)
                continue
            tb = MQTT_TOPIC.encode()
            sub = b"\x82" + enc_len(2 + 2 + len(tb) + 1) + b"\x00\x01" + struct.pack(">H", len(tb)) + tb + b"\x00"
            s.sendall(sub)
            log.info("Vigilador FASE 5 conectado · zonas activas: %s · momento: %s · arranque silencioso",
                     sorted(zonas_activas()), (momento_activo() or {}).get("nombre"))
            sync_frigate_config("arranque", log_unchanged=True)
            recuperar_eventos_activos()
            threading.Thread(target=precalentar_vision, daemon=True).start()
            last_ping = time.time()
            while True:
                try:
                    hdr, body = read_packet(s)
                except socket.timeout:
                    if time.time() - last_ping > 25:
                        s.sendall(b"\xc0\x00")
                        last_ping = time.time()
                    continue
                ptype = hdr >> 4
                if ptype == 3:
                    tl = struct.unpack(">H", body[:2])[0]
                    topic = body[2:2+tl].decode(errors="replace")
                    payload = body[2+tl:]
                    try:
                        handle_publish(hdr, topic, payload)
                    except Exception as e:
                        contadores["errores"] += 1
                        log.exception("handle %s: %s", topic, e)
        except (ConnectionError, OSError) as e:
            log.exception("conexión caída (%s) — reconectando en 10s", e)
        except KeyboardInterrupt:
            log.info("Vigilador detenido. Retenidos ignorados: %d", retained_seen)
            return
        except Exception as e:
            contadores["errores"] += 1
            log.exception("error inesperado en el bucle: %s", e)
        time.sleep(10)

if __name__ == "__main__":
    os.makedirs(SNAP_DIR, exist_ok=True)
    load_env()
    setup_logging()
    setup_raw_log()
    cargar_config(inicio=True)
    log.info("Vigilador FASE 5 iniciado · eventos=%s · log=%s", LOG_FILE,
             os.path.join(VIGILADOR_HOME, "logs", "vigilador.log"))
    run()

