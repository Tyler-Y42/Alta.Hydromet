#!/usr/bin/env python3
"""Copy PhenoCam daily summaries for the Sagehen Hydromet page.

PhenoCam's archive CSVs (phenocam.nau.edu/data/archive/...) send no CORS header, so the
Sagehen page (github.com/Tyler-Y42/Sagehen.Hydromet) can't read them from a browser. This
rides along with the Alta county job, which a Cloudflare cron triggers every 15 minutes, and
writes one small JSON to the data branch: <out>/sagehen/phenocam.json.

PhenoCam updates these files about once a day, so the download is skipped unless a file's
Last-Modified changed. Columns kept per day: gcc_90 (greenness, the PhenoCam standard;
null when flagged as an outlier), the ROI mean blue chromatic coordinate (used for snow on
the meadow cameras), and the image count.
"""
import argparse, csv, datetime, io, json, os, urllib.request

SITES = [  # (camera, ROI file stem, description)
    ("sagehen", "sagehen_EN_1000", "evergreen canopy, above canopy view"),
    ("sagehen2", "sagehen2_GR_1000", "fen meadow"),
    ("sagehen3", "sagehen3_GR_1001", "fen meadow, second view"),
]
BASE = "https://phenocam.nau.edu/data/archive/{site}/ROI/{stem}_1day.csv"
UA = {"User-Agent": "Sagehen.Hydromet data copy (github.com/Tyler-Y42/Sagehen.Hydromet)"}


def get(url, method="GET"):
    req = urllib.request.Request(url, headers=UA, method=method)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.headers.get("Last-Modified", ""), (r.read() if method == "GET" else b"")


def num(x, d=5):
    try:
        v = float(x)
        return round(v, d)
    except (TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    path = os.path.join(a.out, "sagehen", "phenocam.json")
    old = {}
    if os.path.exists(path):
        with open(path) as f:
            old = json.load(f)
    out = {"source": "PhenoCam Network, phenocam.nau.edu (Kirchner lab cameras at Sagehen)", "sites": {}}
    changed = False
    for site, stem, desc in SITES:
        url = BASE.format(site=site, stem=stem)
        prev = old.get("sites", {}).get(site)
        try:
            lm, _ = get(url, "HEAD")
            if prev and prev.get("modified") == lm:
                out["sites"][site] = prev
                continue
            lm, body = get(url)
        except Exception as e:  # keep the last good copy
            print(f"{site}: {e}")
            if prev:
                out["sites"][site] = prev
            continue
        rows = csv.DictReader(l for l in io.StringIO(body.decode("utf-8", "replace")) if not l.startswith("#"))
        d, g90, bcc, n = [], [], [], []
        for r in rows:
            rm, gm, bm = num(r.get("r_mean")), num(r.get("g_mean")), num(r.get("b_mean"))
            d.append(r["date"])
            g90.append(None if r.get("outlierflag_gcc_90") == "1" else num(r.get("gcc_90")))
            bcc.append(round(bm / (rm + gm + bm), 4) if None not in (rm, gm, bm) and rm + gm + bm > 0 else None)
            n.append(int(r["image_count"]) if (r.get("image_count") or "").isdigit() else 0)
        out["sites"][site] = {"roi": stem, "desc": desc, "modified": lm, "d": d, "g90": g90, "bcc": bcc, "n": n}
        changed = True
        print(f"{site}: {len(d)} days to {d[-1] if d else None}")
    if not changed and old:
        print("phenocam: unchanged")
        return
    out["updated_utc"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(out, f, separators=(",", ":"))


if __name__ == "__main__":
    main()
