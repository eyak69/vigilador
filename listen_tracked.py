#!/usr/bin/env python3
"""Escucha corta del topic frigate/tracked_object_update (metadatos/rostros)."""
import json, time, struct, socket

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
        c = s.recv(n - len(buf))
        if not c:
            raise ConnectionError()
        buf += c
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

s = socket.create_connection(("192.168.1.4", 1883), timeout=10)
s.settimeout(1.0)
cid, user, pwd = b"vigilador-escucha", b"mosquito", b"ojoseco6971"
vh = struct.pack(">H", 4) + b"MQTT" + bytes([4, 0xC2]) + struct.pack(">H", 60)
pl = struct.pack(">H", len(cid)) + cid
pl += struct.pack(">H", len(user)) + user + struct.pack(">H", len(pwd)) + pwd
s.sendall(b"\x10" + enc_len(len(vh) + len(pl)) + vh + pl)
t0 = time.time()
while time.time() - t0 < 5:
    try:
        hdr, body = read_packet(s)
        if hdr >> 4 == 2:
            break
    except socket.timeout:
        continue
tb = b"frigate/tracked_object_update"
sub = b"\x82" + enc_len(2 + 2 + len(tb) + 1) + b"\x00\x01" + struct.pack(">H", len(tb)) + tb + b"\x00"
s.sendall(sub)
print(f"[*] escuchando {tb.decode()} por 45s...", flush=True)
start, count = time.time(), 0
last_ping = time.time()
while time.time() - start < 45:
    try:
        hdr, body = read_packet(s)
    except socket.timeout:
        if time.time() - last_ping > 25:
            s.sendall(b"\xc0\x00")
            last_ping = time.time()
        continue
    if hdr >> 4 == 3:
        tl = struct.unpack(">H", body[:2])[0]
        topic = body[2:2+tl].decode()
        payload = body[2+tl:]
        count += 1
        try:
            d = json.loads(payload.decode())
            print(f"[{time.strftime('%H:%M:%S')}] {topic}")
            print("  keys:", sorted(d.keys()))
            print("  ", json.dumps(d, ensure_ascii=False)[:400])
        except Exception:
            print(f"[{time.strftime('%H:%M:%S')}] {topic} (no-json, {len(payload)}B)")
print(f"[fin] {count} mensajes")
