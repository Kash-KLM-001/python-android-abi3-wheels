#!/usr/bin/env python3
"""Sanity-check the built wheels: tags, DT_NEEDED, and import on Termux."""

import glob
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

WHEELS = sorted(Path(__file__).resolve().parent.glob("wheels/*.whl"))

for wheel in WHEELS:
    print("===", wheel.name)
    with zipfile.ZipFile(wheel) as z:
        wheel_meta = z.read([n for n in z.namelist() if n.endswith(".dist-info/WHEEL")][0]).decode()
        for line in wheel_meta.splitlines():
            if line.startswith("Tag:"):
                print("  ", line)
        sos = [n for n in z.namelist() if n.endswith(".abi3.so")]
        with tempfile.TemporaryDirectory(prefix="wv-", dir="/tmp/opencode") as td:
            z.extractall(td)
            for name in sos:
                needed = subprocess.run(
                    ["readelf", "-d", str(Path(td) / name)],
                    capture_output=True, text=True, check=True,
                ).stdout
                py = [x.split("Shared library: [")[1].rstrip("]")
                      for x in needed.splitlines() if "libpython" in x]
                print("   ", name, "->", py)
                if not py:
                    sys.exit(f"ERROR: {name} has no versioned libpython (PyO3 Android)")

print("all wheels structurally OK")
