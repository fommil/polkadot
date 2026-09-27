#!/usr/bin/env python3
# fmax per STA corner: 1000 / (CLOCK_PERIOD - setup WS) MHz
import glob
import json
from pathlib import Path

root = Path(__file__).resolve().parent
period = json.loads((root / "src/config.json").read_text())["CLOCK_PERIOD"]

results = {}
for rpt in glob.glob(str(root / "runs/wokwi/*-openroad-stapostpnr/summary.rpt")):
    for line in Path(rpt).read_text().splitlines():
        cols = [c.strip() for c in line.split("│")]
        if len(cols) <= 8 or cols[1] == "Overall":
            continue
        try:
            ws = float(cols[7])
        except ValueError:
            continue
        results[cols[1]] = (ws, 1000 / (period - ws))

for corner, (ws, mhz) in results.items():
    print(f"{corner:<20} {ws:9.4f} ns {mhz:7.2f} MHz")

if results:
    mhz = [m for _, m in results.values()]
    print(f"fmax range: {min(mhz):.2f} – {max(mhz):.2f} MHz")


