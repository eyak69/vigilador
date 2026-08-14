#!/usr/bin/env python3
"""Backfill retroactivo: visión + memoria para los snapshots de personas de hoy."""
import os, sys, json, glob, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vision_verdict import get_verdict

HERMES_HOME = "/home/hermes/.hermes/profiles/vigilador"
SNAP = os.path.join(HERMES_HOME, "workspace", "snapshots")

def load_mem():
    import yaml
    from mem0 import Memory
    cfg = yaml.safe_load(open(os.path.join(HERMES_HOME, "config.yaml")))
    oss = cfg["memory"]["mem0"]["oss_config"]
    if isinstance(oss, str):
        oss = json.loads(oss)
    return Memory.from_config(oss)

mem = load_mem()
files = sorted(glob.glob(os.path.join(SNAP, "*", "*person*.jpg")))
print(f"snapshots de persona: {len(files)}")
for f in files:
    cam = f.split("/")[-2]
    base = os.path.basename(f).replace(".jpg", "")
    ts = base[:15]  # YYYYMMDD_HHMMSS
    try:
        fecha = f"{ts[6:8]}/{ts[4:6]} {ts[9:11]}:{ts[11:13]}:{ts[13:15]}"
    except Exception:
        fecha = ts
    verdict = get_verdict(f, f"cámara {cam}, snapshot de persona, {fecha}")
    clean = {k: v for k, v in verdict.items() if not k.startswith("_")}
    fact = (f"{fecha}: persona en cámara {cam} | visión: {json.dumps(clean, ensure_ascii=False)}")
    try:
        mem.add(fact, user_id="cristian", infer=False)
        print(f"[OK] {fecha} {cam} -> {verdict.get('tipo')} conf={verdict.get('confianza')}")
    except Exception as e:
        print(f"[ERR] {fecha} {cam}: {e}")
    time.sleep(1)
print("backfill completado")
