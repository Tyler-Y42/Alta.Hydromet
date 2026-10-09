#!/usr/bin/env python3
"""Hourly MRMS radar precipitation over the upper Little Cottonwood watershed.

Reads the IEM archive of the NOAA MRMS one hour radar QPE (p1h, a 0.01 degree
CONUS PNG, one per hour, valid at the end of the hour) and stores, per hour:
    b   watershed mean, mm x 100
    p   value at named points, mm x 100 (the 0.01 degree pixel holding the point)
MRMS handles widespread light rain much better than our 30 dBZ reflectivity
floor, so the page uses it for amounts. It runs about an hour behind real time
and is still low in snow.

Output: mrms/<YYYY-MM>.json (UTC months) and mrms/manifest.json.
Usage:
    python fetch_mrms.py --out data                 # hours since the last stored one (max 3 days)
    python fetch_mrms.py --out data --since 2026-09-01
Requires Pillow.
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import io
import json
import os
import time
import urllib.error
import urllib.request

from PIL import Image

UA = "Alta-Hydromet-dashboard/1.0 (github.com/Tyler-Y42/Alta.Hydromet)"
X0, Y0, D = -129.995, 54.995, 0.01  # center of the upper left pixel, pixel size (degrees)
BASIN = [[-111.5982,40.578],[-111.5988,40.5844],[-111.6081,40.5865],[-111.6093,40.5977],[-111.6144,40.6016],[-111.626,40.5983],[-111.6461,40.6007],[-111.6528,40.595],[-111.6847,40.5885],[-111.7059,40.593],[-111.7162,40.5906],[-111.7223,40.5937],[-111.7281,40.588],[-111.7358,40.5855],[-111.7468,40.5859],[-111.7513,40.5822],[-111.7664,40.5801],[-111.7733,40.582],[-111.7806,40.58],[-111.7938,40.5809],[-111.7987,40.5793],[-111.8005,40.5748],[-111.7981,40.5708],[-111.7405,40.5556],[-111.7316,40.5449],[-111.7316,40.538],[-111.7352,40.5357],[-111.7351,40.5327],[-111.7204,40.5252],[-111.7131,40.5327],[-111.6982,40.5357],[-111.6847,40.5313],[-111.6758,40.5328],[-111.6726,40.5378],[-111.664,40.5413],[-111.6614,40.55],[-111.6501,40.5513],[-111.6378,40.5681],[-111.6057,40.5645],[-111.602,40.5696],[-111.6024,40.5751],[-111.5982,40.578]]
POINTS = {"albion": (40.5850, -111.6180), "alta": (40.5905, -111.6380), "snowbird": (40.5640, -111.6550), "tanners": (40.5700, -111.7007)}


def lut(i):
    """p1h palette index to mm (IEM legend); 255 is missing."""
    if i >= 255:
        return None
    if i <= 100:
        return i * 0.25
    if i <= 180:
        return 25 + (i - 100) * 1.25
    return 125 + (i - 180) * 5.0


def in_poly(lon, lat, ring):
    ins, j = False, len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            ins = not ins
        j = i
    return ins


def pix(lat, lon):
    return int(round((lon - X0) / D)), int(round((Y0 - lat) / D))


C0, R0 = pix(40.62, -111.81)
C1, R1 = pix(40.51, -111.59)
MASK = [(c, r) for c in range(C0, C1 + 1) for r in range(R0, R1 + 1) if in_poly(X0 + c * D, Y0 - r * D, BASIN)]
PT_PIX = {k: pix(la, lo) for k, (la, lo) in POINTS.items()}


def fetch_hour(t, tries=3):
    """t: hour end (UTC). Returns a row, 'missing' if IEM has no file, or None on error."""
    url = f"https://mesonet.agron.iastate.edu/archive/data/{t:%Y/%m/%d}/GIS/mrms/p1h_{t:%Y%m%d%H}00.png"
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            raw = urllib.request.urlopen(req, timeout=90).read()
            im = Image.open(io.BytesIO(raw))
            if im.mode != "P" or im.size != (7000, 3500):
                return None
            box = im.crop((C0, R0, C1 + 1, R1 + 1)).load()
            vals = [lut(box[c - C0, r - R0]) for (c, r) in MASK]
            good = [v for v in vals if v is not None]
            pts = {}
            for key, (c, r) in PT_PIX.items():
                v = lut(box[c - C0, r - R0])
                pts[key] = None if v is None else int(round(v * 100))
            return {"t": int(t.timestamp()), "b": int(round(sum(good) / len(good) * 100)) if good else None, "p": pts}
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return "missing"
            time.sleep(3 * (k + 1))
        except Exception:
            time.sleep(3 * (k + 1))
    return None


def load_month(out, tag):
    path = os.path.join(out, "mrms", f"{tag}.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        d = json.load(f)
    return {d["t"][i]: {"t": d["t"][i], "b": d["b"][i], "p": {k: d["p"][k][i] for k in POINTS}} for i in range(len(d["t"]))}


def save_month(out, tag, rows):
    ts = sorted(rows)
    d = {"t": ts, "b": [rows[t]["b"] for t in ts], "p": {k: [rows[t]["p"][k] for t in ts] for k in POINTS},
         "units": "mm x 100 in the hour ending at t", "product": "MRMS RadarOnly QPE 01H via IEM", "basin_pixels": len(MASK)}
    os.makedirs(os.path.join(out, "mrms"), exist_ok=True)
    with open(os.path.join(out, "mrms", f"{tag}.json"), "w") as f:
        json.dump(d, f, separators=(",", ":"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data")
    ap.add_argument("--since", help="backfill from YYYY-MM-DD (UTC)")
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    now = dt.datetime.now(dt.timezone.utc)
    end = now.replace(minute=0, second=0, microsecond=0)
    man_path = os.path.join(a.out, "mrms", "manifest.json")
    man = {}
    if os.path.exists(man_path):
        with open(man_path) as f:
            man = json.load(f)
    if a.since:
        start = dt.datetime.fromisoformat(a.since).replace(tzinfo=dt.timezone.utc)
    elif man.get("last_utc"):
        start = max(dt.datetime.fromisoformat(man["last_utc"].replace("Z", "+00:00")) - dt.timedelta(hours=2), end - dt.timedelta(days=3))
    else:
        start = end - dt.timedelta(days=2)
    hours, t = [], start
    while t <= end:
        hours.append(t)
        t += dt.timedelta(hours=1)
    months = {}
    for h in hours:
        tag = h.strftime("%Y-%m")
        if tag not in months:
            months[tag] = load_month(a.out, tag)
    todo = [h for h in hours if int(h.timestamp()) not in months[h.strftime("%Y-%m")]]
    print(f"{len(hours)} hours in range, {len(todo)} to fetch")
    got = 0
    with cf.ThreadPoolExecutor(a.workers) as ex:
        for h, res in zip(todo, ex.map(fetch_hour, todo)):
            if isinstance(res, dict):
                months[h.strftime("%Y-%m")][res["t"]] = res
                got += 1
    for tag, rows in months.items():
        if rows:
            save_month(a.out, tag, rows)
    all_months = sorted(set(man.get("months", [])) | {k for k, v in months.items() if v})
    last = max((max(v) for v in months.values() if v), default=None)
    man.update({"months": all_months, "updated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "last_utc": dt.datetime.fromtimestamp(last, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if last else man.get("last_utc"),
                "points": {k: list(v) for k, v in POINTS.items()}, "basin_pixels": len(MASK),
                "source": "https://mesonet.agron.iastate.edu/docs/mrms/"})
    os.makedirs(os.path.dirname(man_path), exist_ok=True)
    with open(man_path, "w") as f:
        json.dump(man, f, indent=1)
    print(f"fetched {got}/{len(todo)}")


if __name__ == "__main__":
    main()
