"""Show wide context around API base URL definitions + dump module exports."""
import os
import re

PROBE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".tmp", "probe")
APP_CHUNK = os.path.join(PROBE, "_app-9b429b5c0d4ac058.js")

body = open(APP_CHUNK, encoding="utf-8", errors="replace").read()
pos = body.find("sitern/api/")
print("=== context -1200..+1800 around sitern/api/ ===")
print(body[max(0, pos - 1200) : pos + 1800])
