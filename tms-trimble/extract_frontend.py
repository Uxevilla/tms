#!/usr/bin/env python3
"""Extrae los 3 archivos del frontend desde la respuesta del otro agente."""
import os
import re

text = open("/tmp/delegation_reply.txt", encoding="utf-8").read()
if "REPLY from other agent:" in text:
    text = text.split("REPLY from other agent:", 1)[1]

pattern = re.compile(r"===FILE:([\w.-]+)===\n(.*?)(?=\n===FILE:|\Z)", re.DOTALL)
files = {}
for m in pattern.finditer(text):
    files[m.group(1)] = m.group(2)

# recortar la nota final del agente (no forma parte del CSS)
for name in list(files):
    idx = files[name].find("\nNota:")
    if idx != -1:
        files[name] = files[name][:idx]
    files[name] = files[name].strip("\n") + "\n"

out = "/root/tms-trimble/frontend"
os.makedirs(out, exist_ok=True)
for name, content in files.items():
    path = os.path.join(out, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"{name}: {len(content)} chars, {content.count(chr(10)) + 1} lineas")

missing = {"index.html", "app.js", "style.css"} - set(files)
if missing:
    print("FALTAN:", missing)
else:
    print("OK: los 3 archivos extraidos")
