#!/usr/bin/env python3
"""Fetch Salt Lake County Flood Control stream gauges (OneRain Contrail portal at
rain-flow.saltlakecounty.gov) into monthly JSON files the dashboard can read.

Why this exists: the portal sends no CORS headers and only serves data to a guest
session (the login redirect hands one out automatically), so a browser page on
another origin cannot read it directly. This script runs on a GitHub Actions
schedule and writes plain JSON that is served from raw.githubusercontent.com.

Output layout (relative to --out):
    county/manifest.json
    county/<key>/<YYYY-MM>.json   {"key", "units", "t": [epoch seconds UTC], "v": [values]}
Months are UTC calendar months. Values are stored exactly as the portal reports them
(no smoothing, no gap filling); timestamps are the portal's report times.

Usage:
    python fetch_county.py --out data                 # refresh the last 3 days' months
    python fetch_county.py --out data --since 2016-10 # backfill from a month
Standard library only.
"""
import argparse
import datetime as dt
import http.cookiejar
import json
import os
import sys
import time
import urllib.parse
import urllib.request

HOST = "https://rain-flow.saltlakecounty.gov"

# key: (label, county gauge number, contrail site_id, site uuid, device_id, device uuid, units)
SERIES = {
    "lcc_tanners_q": ("Little Cottonwood Creek @ Tanners Flat, discharge", 220, 111,
                      "356eff27-b9e1-4585-bb4f-cd946469a294", 3, "4ee002b8-afa2-4d83-ad9e-9ffee95ca81d", "cfs"),
    "lcc_tanners_tw": ("Little Cottonwood Creek @ Tanners Flat, water temperature", 220, 111,
                       "356eff27-b9e1-4585-bb4f-cd946469a294", 4, "d99e2549-6fa7-4b73-a047-206083397c70", "F"),
    "bcc_mouth_q": ("Big Cottonwood Creek @ Canyon Mouth, discharge", 320, 67,
                    "7b550c74-37e9-4af7-8d6a-3b6afe73013c", 2, "a18d9df7-3902-40e4-b509-a5be73a0540f", "cfs"),
    "lcc_crestwood_q": ("Little Cottonwood Creek @ Crestwood Park, discharge", 240, 68,
                        "13f042c2-8e64-4209-85b2-f9c2fb431045", 2, "88c22adb-4abe-46fb-9fea-b7a684689e81", "cfs"),
    "lcc_300w_q": ("Little Cottonwood Creek @ 300 West, discharge", 290, 80,
                   "f4ca7320-4f72-4552-b822-cb53e0fde0eb", 2, "03cfc591-bccc-4253-8125-c666cc81ba01", "cfs"),
}

UA = "Alta-Hydromet-dashboard/1.0 (github.com/Tyler-Y42/Alta.Hydromet)"


def make_opener():
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.addheaders = [("User-Agent", UA)]
    # Any page redirects through /login/, which issues a guest session cookie.
    op.open(HOST + "/", timeout=60).read()
    return op


def fetch_range(op, site_id, device_id, start_local, end_local, tries=4):
    """Raw points between two US/Mountain wall-clock times, as [(epoch_s, value)]."""
    q = urllib.parse.urlencode({
        "method": "sensorDetails", "site_id": site_id, "device_id": device_id,
        "site": site_id, "device": device_id, "range": "custom", "bin": 3600,
        "time_zone": "US/Mountain",
        "data_start": start_local.strftime("%Y-%m-%d %H:%M:%S"),
        "data_end": end_local.strftime("%Y-%m-%d %H:%M:%S"),
    })
    url = HOST + "/export/flot/?" + q
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"X-Requested-With": "XMLHttpRequest"})
            body = op.open(req, timeout=120).read()
            d = json.loads(body)
            # First element is the sensor series; later elements are flood thresholds.
            pts = d[0]["data"] if d else []
            return [(int(t // 1000), v) for t, v in pts if v is not None]
        except Exception as e:  # network hiccup or a login page instead of JSON
            last = e
            time.sleep(3 * (i + 1))
            op = make_opener()
    raise RuntimeError(f"site {site_id} device {device_id}: {last}")


def month_iter(first, last):
    y, m = first
    while (y, m) <= last:
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def write_month(out, key, units, y, m, pts):
    path = os.path.join(out, "county", key, f"{y:04d}-{m:02d}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pts = sorted(set(pts))
    with open(path, "w") as f:
        json.dump({"key": key, "units": units, "t": [p[0] for p in pts], "v": [p[1] for p in pts]},
                  f, separators=(",", ":"))
    return len(pts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data")
    ap.add_argument("--since", help="backfill from YYYY-MM (UTC month)")
    a = ap.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    if a.since:
        y, m = map(int, a.since.split("-"))
        first = (y, m)
    else:
        back = now - dt.timedelta(days=3)  # late or revised reports land inside this window
        first = (back.year, back.month)
    last = (now.year, now.month)

    op = make_opener()
    man_path = os.path.join(a.out, "county", "manifest.json")
    manifest = {"series": {}}
    if os.path.exists(man_path):
        with open(man_path) as f:
            manifest = json.load(f)

    failures = []
    for key, (label, gauge, sid, suuid, did, duuid, units) in SERIES.items():
        info = manifest["series"].get(key, {"months": []})
        months = set(info.get("months", []))
        for y, m in month_iter(first, last):
            # Ask in Mountain wall-clock time with a day of padding either side, then
            # keep only points inside the UTC month so neighbouring files never overlap.
            m0 = dt.datetime(y, m, 1, tzinfo=dt.timezone.utc)
            m1 = dt.datetime(y + (m == 12), 1 if m == 12 else m + 1, 1, tzinfo=dt.timezone.utc)
            s_local = (m0 - dt.timedelta(days=1)).replace(tzinfo=None)
            e_local = (m1 + dt.timedelta(days=1)).replace(tzinfo=None)
            try:
                pts = fetch_range(op, sid, did, s_local, e_local)
            except Exception as e:
                failures.append(str(e))
                continue
            pts = [p for p in pts if m0.timestamp() <= p[0] < m1.timestamp()]
            tag = f"{y:04d}-{m:02d}"
            if pts:
                n = write_month(a.out, key, units, y, m, pts)
                months.add(tag)
                print(f"{key} {tag}: {n} points")
        info.update({
            "label": label, "gauge": gauge, "units": units,
            "source": f"{HOST}/sensor/?site_id={sid}&site={suuid}&device_id={did}&device={duuid}",
            "months": sorted(months),
        })
        manifest["series"][key] = info

    manifest["updated_utc"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest["portal"] = HOST
    manifest["failures"] = failures
    os.makedirs(os.path.dirname(man_path), exist_ok=True)
    with open(man_path, "w") as f:
        json.dump(manifest, f, indent=1)
    if failures:
        print("FAILURES:", *failures, sep="\n  ", file=sys.stderr)
    # Fail the run only if nothing at all came back, so one bad gauge doesn't block the rest.
    if len(failures) >= len(SERIES) * len(list(month_iter(first, last))):
        sys.exit(1)


if __name__ == "__main__":
    main()
