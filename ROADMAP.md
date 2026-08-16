# Vigilador — Ruta de avance

Última actualización: 14/08/2026.

## Hecho (sesión 14/08)

### UI / App
- Modal de avistamiento con **2 fotos**: la original (limpia) + la **anotada de Frigate** (recuadro y etiqueta del motor, foto MQTT más cercana al evento). Se eliminó el check "Ver detección" y el overlay rojo propio (decisión del dueño).
- CRUD de **modelos de visión** en el tab IA: varios modelos `{proveedor, modelo, activo}`, UNO solo ⭐ activo; proveedores separados (solo conexión). Al activar uno, los demás pasan a false.
- Tabs de Configuración por proximidad + grupos (Proveedores primero).
- Columnas con ID visible, "Visión dice", veredicto + recorrido en el modal.
- Guardado explícito por fila.

### Núcleo
- Anti-fantasma por etiqueta (`movimiento_min_px`: car 150, person 60) + cooldown de autos (180 s) + estacionado sin alerta.
- Momentos acotan zonas (el momento activo filtra las zonas del avistamiento).
- Un solo mensaje de alerta (foto + veredicto, tope 40 s); veredicto tardío va al SQL.
- Detección de armas: campo `peligro` → alerta CRÍTICA.

### Visión (el ojo)
- **gemini / gemini-flash-lite-latest → ⭐ ACTIVO** (decisión del dueño): ve el cuchillo de la #482 (37,9 s en cola; ~2,6 s caliente). Confirmado en vivo en la config.
- nvidia / meta/llama-3.2-11b-vision-instruct: usable (4-14 s), hoy NEGÓ el cuchillo que Gemini confirmó. Queda inactivo.
- pc-cristian / qwen2.5vl:3b: inactivo, respaldo local.
- **DESCARTADO: nvidia/nemotron-nano-12b-v2-vl:free** — inventó cuchillos en #477 y #482 (falsos positivos CRÍTICOS). No debe quedar en la línea de visión ni en respaldos.

### API / Datos
- `/openapi.json` vivo (29 rutas), página 22 de WikiJS regenerada desde la spec.
- Página 21 de WikiJS ("Sistema Vigilador Hermes") reescrita con el estado actual.
- Repo **github.com/eyak69/vigilador** (main, 3 commits): código + app + README, auditado sin secretos (PAT en `.env` local del perfil vigilador, remote con x-access-token).

### Infra Hermes
- **NVIDIA_API_KEY (regalo) cargada** en Hermes. Principal = `nvidia / deepseek-ai/deepseek-v4-flash-0731` (gratis, 10-25 s con cola); fallback: deepseek pago + cadena openrouter.
- Modelos NVIDIA verificados: `deepseek-ai/deepseek-v4-flash-0731` (tool-calls OK), `meta/llama-3.2-11b-vision-instruct` (visión OK). **`nvidia/llama-3.3-nemotron-super-49b-v1` NO soporta tool-calls** (cuelga a agentes) — solo chat.
- Antigravity/agente externo: usar `deepseek-ai/deepseek-v4-flash-0731` + base URL `https://integrate.api.nvidia.com/v1`; el alias corto `deepseek-v4-flash` está dado de baja (410). No marcar "soporta imagen" en modelos de texto.
- **Samba**: share `vigilador-hermes` en 192.168.1.6 (\\192.168.1.6\vigilador-hermes, usuario cristian, lectura) — el código entero accesible desde Windows.

## Pendiente / Próximos pasos

1. **Perfiles de zona (diseño acordado 14/08, pendiente de implementar)**:
   - **ZONA**: id = nombre en Frigate (único, no se puede duplicar) · camara · momentos · visión habilitado/reescalar · anti-fantasma · `perfil_id` (1 sola perfil por zona).
   - **PERFIL ZONA** (CRUD en app): id · nombre · **prompt** (contexto de la zona) · **modelo** (del CRUD; vacío = hereda el ⭐ activo). Un perfil puede estar en VARIAS zonas (1:N).
   - **BASE PROMPT fijo** (en código): el contrato de salida JSON (campos tipo/descripcion/ocr/objetos/vehiculo/confianza/sospechoso/peligro) — lo que la app entiende. El prompt del perfil se SUMA como contexto (p. ej. muro: "por aquí se sube un ladrón, no entra el sodero").
   - **Zonas**: entran automáticas como ahora + botón "sync zonas" (manual, desde Frigate).
   - Regla del dueño: guardar antes de actuar; pensamos juntos; NO tocar código sin confirmación.
2. **Probar alerta en vivo con gemini-flash-lite activo**: tiempos reales del circuito MQTT → visión → Telegram (un solo mensaje, tope 40 s).
3. **Verificación de armas**: decidir si se agrega un segundo ojo (11b de NVIDIA) como verificador por zona con `peligro` — el dueño tenía pendiente "modelo grande" para armas.
4. **Actualizar WikiJS** (página 21): registrar gemini-flash-lite como activo y el descarte del nemotron-nano-12b-vl.
5. **Subir el ROADMAP** al repo eyak69/vigilador (o mantener local — decisión del dueño).
6. **Productización** (plan del dueño): UI wizard-first, multi-tenant, capa SQL, box local con privacidad como venta. Regla: todo lo cargado = CRUD completo; box autónomo con config editable desde la app.

## Reglas del dueño (recordatorio)

- Un solo daemon (duplicados = alertas dobles); gateway deshabilitado.
- Todo evento se loguea; avistamiento solo con foto; sin foto → `avistamiento_sin_foto`.
- Credenciales solo en `.env`; todo lo externo en config editable desde la app.
- Respuestas sobrias, sin adornos.
