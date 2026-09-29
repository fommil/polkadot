#!/usr/bin/env python3
import re
import struct

# these are the locations of the power/gnd straps, read them from
# runs/wokwi/final/lef/*.lef
# the left edge of the first `VPWR` strap
# and from there to the right edge of the first `VGND` strap
#
# we don't automate this because it would introduce a circular dependency
STRAP0, PAIR = 18.28, 4.9

PX = 0.5
NAME = "art"

rows = [l.rstrip("\n") for l in open(f"{NAME}.txt") if l.strip()]
h, w = len(rows), len(rows[0])
assert all(len(r) == w for r in rows)

bad = [(r, c) for r in range(h - 1) for c in range(w - 1)
       if rows[r][c] == rows[r + 1][c + 1] != rows[r][c + 1] == rows[r + 1][c]]
for r, c in bad:
    print(f"{NAME}.txt: corner-only contact at line {r + 1}, column {c + 1}")
if bad:
    raise SystemExit(1)
W, H = w * PX, h * PX

MARGIN = 5.0
HALO = 10.0
tiles = re.search(r'^\s*tiles:\s*"(\S+)"', open("info.yaml").read(), re.M)[1]
size = re.search(rf'^{tiles}:\s*"0 0 (\S+) (\S+)"',
                 open("tt/tech/sky130A/tile_sizes.yaml").read(), re.M)
TW, TH = float(size[1]), float(size[2])
print(f"tile {tiles} {TW} x {TH}, art {W} x {H}")
# met4 straps must reach the bottom edge (precheck pin_check), so the art sits
# between VPWR/VGND pairs. Offset and pair width taken from the hardened LEF.
PITCH = float(re.search(r'"FP_PDN_VPITCH":\s*([\d.]+)',
                        open("src/config.json").read())[1])

GAP = PITCH - PAIR
if W > GAP - 2:
    raise SystemExit(f"art width {W} does not fit strap gap {GAP:.2f}")
print("suggested values for src/config.json")
for k in range(int((TW - STRAP0 - PAIR) // PITCH)):
    x = STRAP0 + PAIR + k * PITCH + (GAP - W) / 2
    print(f"  bottom, strap gap {k}:")
    print(f'    "location": [{x:.2f}, {MARGIN:.2f}]')
    print(f'    "FP_MACRO_VERTICAL_HALO": {HALO:g},')

rects = []
for r, line in enumerate(rows):
    y = (h - 1 - r) * PX
    c = 0
    while c < w:
        if line[c] == "#":
            s = c
            while c < w and line[c] == "#":
                c += 1
            rects.append((s * PX, y, c * PX, y + PX))
        else:
            c += 1

def real8(v):
    if v == 0:
        return bytes(8)
    e = 64
    while v < 1 / 16:
        v *= 16
        e -= 1
    while v >= 1:
        v /= 16
        e += 1
    return struct.pack(">Q", (e << 56) | round(v * 2**56))

def rec(kind, dtype, data=b""):
    if len(data) % 2:
        data += b"\0"
    return struct.pack(">HBB", 4 + len(data), kind, dtype) + data

def i2(*v):
    return struct.pack(f">{len(v)}h", *v)

def boundary(x0, y0, x1, y1, layer, datatype):
    nm = [round(v * 1000) for v in (x0, y0, x1, y1)]
    x0, y0, x1, y1 = nm
    xy = struct.pack(">10i", x0, y0, x1, y0, x1, y1, x0, y1, x0, y0)
    return (rec(0x08, 0) + rec(0x0D, 2, i2(layer)) + rec(0x0E, 2, i2(datatype))
            + rec(0x10, 3, xy) + rec(0x11, 0))

ts = i2(*([2026, 1, 1, 0, 0, 0] * 2))
gds = (rec(0x00, 2, i2(600)) + rec(0x01, 2, ts) + rec(0x02, 6, NAME.encode())
       + rec(0x03, 5, real8(1e-3) + real8(1e-9))
       + rec(0x05, 2, ts) + rec(0x06, 6, NAME.encode())
       + b"".join(boundary(*r, 71, 20) for r in rects)
       + boundary(0, 0, W, H, 235, 4)
       + rec(0x07, 0) + rec(0x04, 0))
with open(f"{NAME}.gds", "wb") as f:
    f.write(gds)

# with open(f"{NAME}.svg", "w") as f:
#     f.write(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">\n'
#             f'<rect width="{W}" height="{H}" fill="black"/>\n')
#     for x0, y0, x1, y1 in rects:
#         f.write(f'<rect x="{x0}" y="{H - y1}" width="{x1 - x0}" height="{y1 - y0}" fill="gold"/>\n')
#     f.write("</svg>\n")

with open(f"{NAME}.v", "w") as f:
    f.write(f"`default_nettype none\n\nmodule {NAME} ();\nendmodule\n")

with open(f"{NAME}.lef", "w") as f:
    f.write(f"""VERSION 5.7 ;
BUSBITCHARS "[]" ;
DIVIDERCHAR "/" ;
MACRO {NAME}
  CLASS BLOCK ;
  FOREIGN {NAME} ;
  ORIGIN 0 0 ;
  SIZE {W:.3f} BY {H:.3f} ;
  OBS
    LAYER met4 ;
      RECT 0.000 0.000 {W:.3f} {H:.3f} ;
  END
END {NAME}
END LIBRARY
""")
