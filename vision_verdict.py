#!/usr/bin/env python3
"""
Vigilador — módulo de VISIÍ“N.
Analiza el snapshot con un modelo de visión configurable por PROVEEDOR:
- "openai": cualquier API compatible OpenAI (OpenRouter, NVIDIA, OpenAI...) vía chat/completions.
- "ollama": modelo local (llava, llama3.2-vision...) vía /api/chat de Ollama.
Extrae MÍXIMO contexto: descripción física, OCR de texto/logos, objetos, vehículo,
y veredicto estructurado. Ante fallo del proveedor configurado, usa la cadena de
respaldo gratuita de OpenRouter (MODELS).
"""
import os, re, json, base64, time, urllib.request

# cadena de respaldo (gratis, OpenRouter) si el proveedor configurado falla
MODELS = [
    "nvidia/nemotron-nano-12b-v2-vl:free",          # probado y operativo
    "google/gemma-4-26b-a4b-it:free",               # backup (a veces 429)
]
DEFAULT_BASE = "https://openrouter.ai/api/v1"

PROMPT = """Eres el analista visual del Vigilador, un sistema de seguridad residencial. 
Esta foto fue tomada por una cámara en la zona 'puertacalle' (la puerta de calle de una casa).
Extrae el MAXIMO contexto posible de la persona visible:

1. descripcion: aspecto físico detallado (edad aprox, sexo, ropa, colores, calzado, accesorios).
2. ocr: TODO texto legible en la ropa/uniforme/mameluco, logos, vehículo, cajas o paquetes (ej: "Coca-Cola", "PedidosYa", "MercadoLibre"). Si no hay texto, lista vacía.
3. objetos: qué lleva en las manos o carga (caja de gaseosas, casco, mochila, paquetes, carrito...).
4. vehiculo: si se ve una moto, bicicleta o auto cerca de la persona (o "no visible").
5. tipo: clasifica la visita en uno de: 'sodero/repartidor' (uniforme o carga de delivery), 'visita' (alguien que llama a la puerta), 'vecino', 'desconocido', 'otro'.
6. confianza: 0.0 a 1.0 sobre tu clasificación.
7. sospechoso: true si algo te parece fuera de lugar (merodeo, cara cubierta, actitud rara).
8. peligro: PUNTO CRITICO. ¿La persona lleva un ARMA u objeto peligroso visible (cuchillo, machete, espada, pistola, palo, bate, objeto largo o metálico con filo o punta en la mano)? Si SÍ: {"arma": true, "tipo": "cuchillo"/"pistola"/"otro", "descripcion": "cómo y dónde lo lleva"}. Si NO: {"arma": false, "tipo": null, "descripcion": ""}. Rigor: un objeto largo en la mano NO es un paquete si tiene filo o punta.

Responde SOLO con JSON válido, sin texto extra, con esta estructura exacta:
{"tipo": "...", "descripcion": "...", "ocr": ["..."], "objetos": ["..."], "vehiculo": "...", "confianza": 0.0, "sospechoso": false, "peligro": {"arma": false, "tipo": null, "descripcion": ""}}"""


def _env_dir():
    return os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes/profiles/vigilador"))

def _read_env(key):
    """Lee una variable del .env del perfil (o del entorno del proceso)."""
    try:
        for line in open(os.path.join(_env_dir(), ".env")):
            if line.startswith(key + "="):
                return line.strip().split("=", 1)[1]
    except Exception:
        pass
    return os.environ.get(key, "")

def _load_key():
    return _read_env("OPENROUTER_API_KEY")

def _extract_json(text):
    """Extrae el primer JSON válido del texto (tolera fences de markdown y prosa)."""
    if not text:
        return None
    text = re.sub(r"```(?:json)?", "", text)
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i+1])
                except Exception:
                    return None
    return None

def _llamar_openai(base_url, modelo, key, prompt, img_b64):
    """chat/completions estilo OpenAI con imagen en data URL."""
    body = {
        "model": modelo,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
        ]}],
        "max_tokens": 500,
        "temperature": 0.1,
    }
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read().decode())
    return resp["choices"][0]["message"]["content"]

def _llamar_ollama(base_url, modelo, prompt, img_b64):
    """Ollama (sin key). Formato CLÍSICO universal: content como string +
    images aparte (compatible con Ollama viejas y nuevas). Reintenta sin
    keep_alive si la versión no lo soporta; fallback a /api/generate
    (moondream y similares)."""
    url = base_url.rstrip("/") + "/api/chat"
    body = {
        "model": modelo,
        "messages": [{"role": "user", "content": prompt, "images": [img_b64]}],
        "stream": False,
        "keep_alive": -1,   # el modelo NUNCA se descarga (igual que OLLAMA_KEEP_ALIVE=-1 del dueño)
        "options": {"temperature": 0.1, "num_predict": 400},
    }
    for intento in (body, {k: v for k, v in body.items() if k != "keep_alive"}):
        try:
            req = urllib.request.Request(url, data=json.dumps(intento).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as r:
                resp = json.loads(r.read().decode())
            return resp.get("message", {}).get("content", "")
        except urllib.error.HTTPError as e:
            if e.code not in (400, 404):
                raise
    # fallback: /api/generate (prompt + images) — moondream y similares
    body2 = {"model": modelo, "prompt": prompt, "images": [img_b64],
             "stream": False, "keep_alive": -1,
             "options": {"temperature": 0.1, "num_predict": 400}}
    req2 = urllib.request.Request(base_url.rstrip("/") + "/api/generate",
                                  data=json.dumps(body2).encode(),
                                  headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req2, timeout=180) as r2:
        resp2 = json.loads(r2.read().decode())
    return resp2.get("response", "")

def _procesar(modelo, content, err_contexto=""):
    verdict = _extract_json(content)
    if verdict:
        # normalizar PELIGRO (arma): string serializado o dict; arma → sospechoso
        pel = verdict.get("peligro")
        if isinstance(pel, str):
            try:
                pel = json.loads(pel)
            except Exception:
                pel = None
        if isinstance(pel, dict) and pel.get("arma"):
            verdict["sospechoso"] = True
            verdict["_arma"] = pel.get("tipo") or "arma"
            if not verdict.get("descripcion") and pel.get("descripcion"):
                verdict["descripcion"] = pel["descripcion"]
        verdict["_modelo"] = modelo
        return verdict
    crudo = (content or "").strip()
    # si la respuesta parece JSON/código con parseo fallido, no volcar el crudo
    if crudo.startswith("{") or crudo.startswith("[") or "```" in crudo:
        desc = "respuesta del modelo sin formato estructurado"
    else:
        desc = crudo[:500]
    return {"tipo": "otro", "descripcion": desc, "ocr": [], "objetos": [],
            "vehiculo": "no visible", "confianza": 0.0, "sospechoso": False,
            "_modelo": modelo, "_sin_json": True, "_error": err_contexto}

def get_verdict(image_path, contexto="", proveedor=None, modelo=None, perfil_prompt=""):
    """Devuelve dict con el veredicto. Nunca lanza: ante fallo total, 'desconocido'.
    proveedor: dict {tipo: openai|ollama, base_url, api_key_env, modelo} de la config.
    modelo: override del nombre de modelo.
    perfil_prompt: contexto semántico del perfil de la zona."""
    try:
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
    except Exception as e:
        return {"tipo": "desconocido", "descripcion": f"no pude leer la foto: {e}", "ocr": [],
                "objetos": [], "vehiculo": "no visible", "confianza": 0.0, "sospechoso": False, "_error": str(e)}

    prompt = PROMPT
    if perfil_prompt:
        prompt += f"\nContexto especifico de esta zona: {perfil_prompt}"
    if contexto:
        prompt += f"\nContexto adicional: {contexto}"

    # --- proveedor configurado (asociación zona → visión) ---
    if proveedor:
        tipo = (proveedor.get("tipo") or "openai").lower()
        base = (proveedor.get("base_url") or DEFAULT_BASE).rstrip("/")
        m = modelo or proveedor.get("modelo") or MODELS[0]
        key = _read_env(proveedor.get("api_key_env")) if proveedor.get("api_key_env") else ""
        try:
            if tipo == "ollama":
                content = _llamar_ollama(base, m, prompt, img_b64)
            else:
                content = _llamar_openai(base, m, key, prompt, img_b64)
            return _procesar(m, content)
        except Exception as e:
            print(f"[vision] proveedor {m} falló: {str(e)[:120]} — probando respaldo", flush=True)
            time.sleep(2)
            # caer a la cadena de respaldo gratuita (openrouter) con el mismo tipo openai
            if tipo == "openai" and key:
                for fm in MODELS:
                    try:
                        content = _llamar_openai(DEFAULT_BASE, fm, _load_key(), prompt, img_b64)
                        return _procesar(fm, content)
                    except Exception as e2:
                        print(f"[vision] respaldo {fm} falló: {str(e2)[:120]}", flush=True)
                        time.sleep(3)
            return {"tipo": "desconocido", "descripcion": f"proveedor falló: {str(e)[:120]}", "ocr": [],
                    "objetos": [], "vehiculo": "no visible", "confianza": 0.0, "sospechoso": False,
                    "_error": str(e)[:120], "_modelo": m}

    # --- comportamiento por defecto (openrouter gratis) ---
    key = _load_key()
    if not key:
        return {"tipo": "desconocido", "descripcion": "sin OPENROUTER_API_KEY", "ocr": [], "objetos": [],
                "vehiculo": "no visible", "confianza": 0.0, "sospechoso": False, "_error": "no key"}
    for m in MODELS:
        try:
            content = _llamar_openai(DEFAULT_BASE, m, key, prompt, img_b64)
            return _procesar(m, content)
        except Exception as e:
            print(f"[vision] {m} falló: {str(e)[:120]}", flush=True)
            time.sleep(3)
    return {"tipo": "desconocido", "descripcion": "todos los modelos de visión fallaron", "ocr": [],
            "objetos": [], "vehiculo": "no visible", "confianza": 0.0, "sospechoso": False, "_error": "todos fallaron"}

if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "/home/hermes/workspace/frigate_095107_calle_person.jpg"
    ctx = sys.argv[2] if len(sys.argv) > 2 else ""
    print(json.dumps(get_verdict(path, ctx), ensure_ascii=False, indent=2))

