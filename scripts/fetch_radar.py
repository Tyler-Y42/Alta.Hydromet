#!/usr/bin/env python3
"""Radar time series over the upper Little Cottonwood watershed from the IEM NEXRAD mosaic.

For each 5 min mosaic frame this reads one map tile (z10, x194, y385, which holds the whole
watershed above the canyon mouth except a 16 pixel sliver), decodes the colours back to
reflectivity with the n0q palette, and stores:
    r   watershed mean rain rate, mm/h x 1000 (Z = 200 R^1.6, echoes below 30 dBZ = 0, 53 dBZ cap)
    mx  watershed maximum reflectivity, dBZ (null when no echo)
    ll  [lat, lon] of that maximum
    p   reflectivity at named points (max of a 5 x 5 pixel box, about +/- 230 m)
    n   number of watershed pixels at 35 dBZ or more (one mosaic cell covers about 17 tile pixels)
Ground clutter: on 19 dry days (Sep 8 to Oct 8 2026, SNOTEL 0) the watershed maximum sat on the
same ridge pixels in 794 frames. Pixels within 4 of those spots (CLUTTER) are left out of every
field. Month files carry mask_v; frames stored under an older mask are refetched when in range.
Output: radar/<YYYY-MM>.json (UTC months) and radar/manifest.json. The page reads these
instead of pulling hundreds of tiles itself, which keeps load on IEM low.

Usage:
    python fetch_radar.py --out data              # frames since the last stored one (max 3 days)
    python fetch_radar.py --out data --since 2026-09-08
Requires Pillow.
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import io
import json
import math
import os
import time
import urllib.request

from PIL import Image

Z, TX, TY = 10, 194, 385
FLOOR, CAP = 30.0, 53.0
UA = "Alta-Hydromet-dashboard/1.0 (github.com/Tyler-Y42/Alta.Hydromet)"
PAL_HEX = ("00000085718f85728f86738d87758b87768b887789897987897a878a7b858b7d848b7e848c7f828d81808d82808e837e8f847c8f857c90877b91887991"
           "8979928b77938d759691539894579b975b9d9a60a09d64a3a068a5a36da8a671aaa976adac7ab0af7eb2b283b7b88cbabb90bdbe94bfc199c2c49dc4c7a2"
           "c7caa6cacdaaccd0afd2d4b4cfd2b4c9ccb4c6c9b4c3c7b4c0c4b4bdc1b4b9beb4b6bbb4b3b9b4b0b6b4adb3b4aab0b4a4abb4a0a8b49da5b49aa2b497a0"
           "b4949db4919ab4949bb59098b48c95b38892b2808cb07c89af7886ae7483ac7080ab6c7daa6779a96376a85f73a75b70a6576da44f67a24b64a14761a043"
           "5e9f415b9e4361a24568a6486faa4a76ae4d7db24f84b6518bbb5699c3599fc75ba6cb5eadcf60b4d462bbd865c2dc67c9e06ad0e46fd6e868d6d759d6b3"
           "52d6a24bd69043d67e3cd66d35d65b11d51811d11710cd1710c81610c4160fbc150fb7140eb3140eaf130eab130da6120da2120d9e110c99110c95100c91"
           "100b880f0b840e0a800e0a7c0d0a770d09730c096f0c096b0b08660b08620a095e09327308467d085b88076f9207849d0698a806adb205c1bd05d6c704ea"
           "d204ffe200ffd800ffd300ffce00ffc900ffc400ffc000ffbb00ffb600ffb100ffac00ffa700ffa200ff9900ff9400ff8f00ff8a00ff8500ff8000ff0000"
           "f80000f10000ea0000e30000d50000cd0000c60000bf0000b80000b10000aa0000a300009b00009400008d00007f0000780000710000fffffffff5ffffea"
           "ffffdfffffd4ffffc9ffffbeffffb3ffff9dffff92ffff75fffc6bfdf960faf656f7f34bf4f040f1ed36efea2bece720e9e10be3b200ffac00fca400f79b"
           "00f49300ef8800ea8300e87900e27200dd6900db05ecf005ebf005eaf005dde005dce005dbe005cdd005ccd004bdc004bcc004bbc004aeb004adb0049ea"
           "0049da0049ca0038e90038d90038c90037e80037d80036f70036e70036d70025f60025e60024f50024e50024d50023f40023e40023d40013030012f30012"
           "020011f20011e203a67b53a66b53a65b53a64b53a63b53a62b5")
BASIN = [[-111.5982,40.578],[-111.5988,40.5844],[-111.6081,40.5865],[-111.6093,40.5977],[-111.6144,40.6016],[-111.626,40.5983],[-111.6461,40.6007],[-111.6528,40.595],[-111.6847,40.5885],[-111.7059,40.593],[-111.7162,40.5906],[-111.7223,40.5937],[-111.7281,40.588],[-111.7358,40.5855],[-111.7468,40.5859],[-111.7513,40.5822],[-111.7664,40.5801],[-111.7733,40.582],[-111.7806,40.58],[-111.7938,40.5809],[-111.7987,40.5793],[-111.8005,40.5748],[-111.7981,40.5708],[-111.7405,40.5556],[-111.7316,40.5449],[-111.7316,40.538],[-111.7352,40.5357],[-111.7351,40.5327],[-111.7204,40.5252],[-111.7131,40.5327],[-111.6982,40.5357],[-111.6847,40.5313],[-111.6758,40.5328],[-111.6726,40.5378],[-111.664,40.5413],[-111.6614,40.55],[-111.6501,40.5513],[-111.6378,40.5681],[-111.6057,40.5645],[-111.602,40.5696],[-111.6024,40.5751],[-111.5982,40.578]]
CLUTTER = [(93, 117), (43, 155), (51, 174), (94, 117), (46, 170), (53, 117), (47, 160), (51, 169), (96, 116), (100, 115),
           (54, 155), (51, 160), (47, 169), (48, 165), (46, 160), (51, 150)]
CLUTTER_R = 4
MASK_V = 2
POINTS = {"albion": (40.5850, -111.6180), "alta": (40.5905, -111.6380), "snowbird": (40.5640, -111.6550), "tanners": (40.5700, -111.7007)}

PAL = [tuple(int(PAL_HEX[i * 6 + k * 2:i * 6 + k * 2 + 2], 16) for k in range(3)) for i in range(256)]
COLOR_IDX = {}
for i, c in enumerate(PAL):
    if i and c not in COLOR_IDX:
        COLOR_IDX[c] = i


def rgb_to_idx(c):
    if c in COLOR_IDX:
        return COLOR_IDX[c]
    best, bd = 0, 1e9
    for i in range(1, 256):
        p = PAL[i]
        d = (p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2 + (p[2] - c[2]) ** 2
        if d < bd:
            best, bd = i, d
    COLOR_IDX[c] = best if bd < 300 else 0
    return COLOR_IDX[c]


def dbz_of(i):
    return i * 0.5 - 32.5 if i > 0 else None


def rate(dbz):
    if dbz is None or dbz < FLOOR:
        return 0.0
    return (10 ** (min(dbz, CAP) / 10) / 200) ** (1 / 1.6)


def pix_latlon(px, py):
    n = 2 ** Z
    x, y = TX + (px + 0.5) / 256, TY + (py + 0.5) / 256
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n)))), x / n * 360 - 180


def tile_xy(lat, lon):
    n = 2 ** Z
    la = math.radians(lat)
    return (lon + 180) / 360 * n, (1 - math.log(math.tan(la) + 1 / math.cos(la)) / math.pi) / 2 * n


def in_poly(lon, lat, ring):
    ins, j = False, len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            ins = not ins
        j = i
    return ins


def clutter(px, py):
    return any((px - cx) ** 2 + (py - cy) ** 2 <= CLUTTER_R ** 2 for cx, cy in CLUTTER)


BASIN_PIX = [(px, py) for py in range(256) for px in range(256) if in_poly(pix_latlon(px, py)[1], pix_latlon(px, py)[0], BASIN)]
MASK = [q for q in BASIN_PIX if not clutter(*q)]
PT_PIX = {}
for k, (la, lo) in POINTS.items():
    x, y = tile_xy(la, lo)
    PT_PIX[k] = (int((x - TX) * 256), int((y - TY) * 256))


def stamp(t):
    return t.strftime("%Y%m%d%H%M")


def fetch_frame(t, tries=4):
    url = f"https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/ridge::USCOMP-N0Q-{stamp(t)}/{Z}/{TX}/{TY}.png"
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            im = Image.open(io.BytesIO(urllib.request.urlopen(req, timeout=60).read())).convert("RGBA")
            px = im.load()
            idx = {}

            def at(x, y):
                key = (x, y)
                if key not in idx:
                    r, g, b, a = px[x, y]
                    idx[key] = 0 if a < 128 else rgb_to_idx((r, g, b))
                return idx[key]

            s, mx, ll, n35 = 0.0, None, None, 0
            for (x, y) in MASK:
                d = dbz_of(at(x, y))
                s += rate(d)
                if d is not None and d >= 35:
                    n35 += 1
                if d is not None and (mx is None or d > mx):
                    mx, ll = d, pix_latlon(x, y)
            pts = {}
            for key, (cx, cy) in PT_PIX.items():
                m = None
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        X, Y = cx + dx, cy + dy
                        if 0 <= X < 256 and 0 <= Y < 256:
                            d = dbz_of(at(X, Y))
                            if d is not None and (m is None or d > m):
                                m = d
                pts[key] = m
            return {"t": int(t.timestamp()), "r": int(round(s / len(MASK) * 1000)), "mx": mx,
                    "ll": [round(ll[0], 4), round(ll[1], 4)] if ll else None, "p": pts, "n": n35, "v": MASK_V}
        except Exception:
            time.sleep(2 * (k + 1))
    return None


def load_month(out, tag):
    path = os.path.join(out, "radar", f"{tag}.json")
    if os.path.exists(path):
        with open(path) as f:
            d = json.load(f)
        n = d.get("n") or [None] * len(d["t"])
        v = d.get("v") or [1] * len(d["t"])
        return {d["t"][i]: {"t": d["t"][i], "r": d["r"][i], "mx": d["mx"][i], "ll": d["ll"][i], "n": n[i], "v": v[i],
                            "p": {k: d["p"][k][i] for k in POINTS}} for i in range(len(d["t"]))}
    return {}


def save_month(out, tag, rows):
    ts = sorted(rows)
    d = {"t": ts, "r": [rows[t]["r"] for t in ts], "mx": [rows[t]["mx"] for t in ts], "ll": [rows[t]["ll"] for t in ts],
         "n": [rows[t].get("n") for t in ts], "v": [rows[t].get("v", 1) for t in ts],
         "p": {k: [rows[t]["p"][k] for t in ts] for k in POINTS},
         "floor_dbz": FLOOR, "tile": [Z, TX, TY], "basin_pixels": len(MASK), "clutter_pixels": len(BASIN_PIX) - len(MASK), "mask_v": MASK_V}
    os.makedirs(os.path.join(out, "radar"), exist_ok=True)
    with open(os.path.join(out, "radar", f"{tag}.json"), "w") as f:
        json.dump(d, f, separators=(",", ":"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data")
    ap.add_argument("--since", help="backfill from YYYY-MM-DD (UTC)")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    now = dt.datetime.now(dt.timezone.utc)
    end = (now - dt.timedelta(minutes=8)).replace(second=0, microsecond=0)
    end -= dt.timedelta(minutes=end.minute % 5)
    man_path = os.path.join(a.out, "radar", "manifest.json")
    man = {}
    if os.path.exists(man_path):
        with open(man_path) as f:
            man = json.load(f)
    if a.since:
        start = dt.datetime.fromisoformat(a.since).replace(tzinfo=dt.timezone.utc)
    elif man.get("last_utc"):
        start = dt.datetime.fromisoformat(man["last_utc"].replace("Z", "+00:00")) - dt.timedelta(minutes=30)
        start = max(start, end - dt.timedelta(days=3))
    else:
        start = end - dt.timedelta(days=1)
    # also retry frames that failed recently
    frames, t = [], start
    while t <= end:
        frames.append(t)
        t += dt.timedelta(minutes=5)
    months = {}
    for f in frames:
        tag = f.strftime("%Y-%m")
        if tag not in months:
            months[tag] = load_month(a.out, tag)
    def stored(f):
        row = months[f.strftime("%Y-%m")].get(int(f.timestamp()))
        return row is not None and row.get("v", 1) >= MASK_V
    todo = [f for f in frames if not stored(f)]
    print(f"{len(frames)} frames in range, {len(todo)} to fetch")
    got = 0
    with cf.ThreadPoolExecutor(a.workers) as ex:
        for f, res in zip(todo, ex.map(fetch_frame, todo)):
            if res:
                months[f.strftime("%Y-%m")][res["t"]] = res
                got += 1
    for tag, rows in months.items():
        if rows:
            save_month(a.out, tag, rows)
    all_months = sorted(set(man.get("months", [])) | {k for k, v in months.items() if v})
    last = max((max(v) for v in months.values() if v), default=None)
    man.update({"months": all_months, "updated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "last_utc": dt.datetime.fromtimestamp(last, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if last else man.get("last_utc"),
                "points": {k: list(v) for k, v in POINTS.items()}, "floor_dbz": FLOOR, "mask_v": MASK_V,
                "clutter": {"tile": [Z, TX, TY], "centers_px": CLUTTER, "radius_px": CLUTTER_R},
                "source": "https://mesonet.agron.iastate.edu/docs/nexrad_mosaic/"})
    os.makedirs(os.path.dirname(man_path), exist_ok=True)
    with open(man_path, "w") as f:
        json.dump(man, f, indent=1)
    print(f"fetched {got}/{len(todo)}")


if __name__ == "__main__":
    main()
