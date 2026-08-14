#!/usr/bin/env python3
"""
Vigilador — FASE 1: APRENDIZAJE.
Escucha pasiva de frigate/#: mapea el árbol de topics, frecuencias, tipos de payload.
NO alerta, NO escribe memoria. Solo observa y registra.
"""
import os, sys, json, time, struct, socket, re
from collections import defaultdict

MQTT_HOST, MQTT_PORT = "192.168.1.4", 1883
MQTT_USER, MQTT_PASS = "mosquito", "ojoseco6971"
MQTT_TOPIC = "frigate/#"
WINDOW = 75  # segundos de estudio

OUT = os.path.expanduser("~/.hermes/profiles/vigilador/workspace/mqtt_study.jsonl")

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
            raise ConnectionError("cerrado")
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

def main():
    s = socket.create_connection((MQTT_HOST, MQTT_PORT), timeout=10)
    s.settimeout(1.0)
    cid = b"vigilador-estudio"
    user, pwd = MQTT_USER.encode(), MQTT_PASS.encode()
    vh = struct.pack(">H", 4) + b"MQTT" + bytes([4, 0xC2]) + struct.pack(">H", 60)
    pl = struct.pack(">H", len(cid)) + cid
    pl += struct.pack(">H", len(user)) + user + struct.pack(">H", len(pwd)) + pwd
    s.sendall(b"\x10" + enc_len(len(vh) + len(pl)) + vh + pl)
    t0 = time.time()
    while time.time() - t0 < 5:
        try:
            hdr, body = read_packet(s)
            if hdr >> 4 == 2:
                if body[1] != 0:
                    print("CONNACK rechazado:", body[1]); return
                break
        except socket.timeout:
            continue
    tb = MQTT_TOPIC.encode()
    sub = b"\x82" + enc_len(2 + 2 + len(tb) + 1) + b"\x00\x01" + struct.pack(">H", len(tb)) + tb + b"\x00"
    s.sendall(sub)
    print(f"[*] Estudiando '{MQTT_TOPIC}' durante {WINDOW}s — sin alertas, solo observación")
    start = time.time()
    count = 0
    f = open(OUT, "a")
    last_ping = time.time()
    while time.time() - start < WINDOW:
        try:
            hdr, body = read_packet(s)
        except socket.timeout:
            if time.time() - last_ping > 25:
                s.sendall(b"\xc0\x00"); last_ping = time.time()
            continue
        except ConnectionError:
            print("ERROR: conexión cerrada"); return
        if hdr >> 4 == 3:
            tl = struct.unpack(">H", body[:2])[0]
            topic = body[2:2+tl].decode(errors="replace")
            payload = body[2+tl:]
            rec = {"t": time.time() - start, "topic": topic}
            if payload[:2] == b"\xff\xd8":
                rec["kind"] = "jpeg"; rec["size"] = len(payload)
            else:
                txt = payload.decode("utf-8", errors="replace")
                try:
                    rec["kind"] = "json"; rec["data"] = json.loads(txt)
                except Exception:
                    rec["kind"] = "text"; rec["text"] = txt
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            count += 1
    f.close()
    print(f"[Fin] {count} mensajes en {WINDOW}s → {OUT}")

if __name__ == "__main__":
    main()
