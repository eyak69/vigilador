# Vigilador Hermes

Sistema de vigilancia barrial autónomo: **Frigate + MQTT + visión IA + memoria + API REST + app web**.

## Arquitectura

| Pieza | Archivo | Rol |
|---|---|---|
| Daemon | `vigilador_watch.py` | Escucha MQTT (`frigate/#`), política de momentos/zonas, alertas Telegram, visión, memoria, LPR |
| API | `vigilador_api.py` | REST (`:8788`), spec OpenAPI en `/openapi.json`, sirve la app web en `/app` |
| DB | `vigilador_db.py` | SQLite (avistamientos, placas, personas) |
| Visión | `vision_verdict.py` | Proveedores (Ollama local / OpenAI-compatible), veredictos con detección de armas |
| App | `app/` | UI sin build step (index.html + app.js + style.css) |

## Conceptos clave

- **Modelos de visión (CRUD)**: varios `{proveedor, modelo, activo}` — UNO solo activo (⭐), el que usa toda la app.
- **Momentos**: ventanas de tiempo que acotan qué zonas tienen resultado (una zona nocturna no figura de día).
- **Anti-fantasma**: desplazamiento mínimo del centroide por etiqueta (`movimiento_min_px`) — reflejos y sombras no generan avistamientos.
- **Credenciales**: solo en el `.env` del perfil (nunca en el JSON ni en el repo).

## Requisitos

- Frigate (MQTT + API), Ollama local o proveedor OpenAI-compatible, Qdrant (opcional, memoria).
- Python 3.12+ · sin dependencias externas para el núcleo (la memoria usa mem0, opcional).

## Arranque

```bash
cp vigilador_config.example.json vigilador_config.json   # o dejar que se deduzca
python3 vigilador_api.py --port 8788 &                   # API + app web
python3 vigilador_watch.py &                             # daemon
```

La config se autogenera con defaults seguros; todo lo externo (IPs, URLs, claves) vive en `vigilador_config.json` y `.env`.
