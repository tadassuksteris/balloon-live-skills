#!/usr/bin/env python3
"""Load the Balloon Live IGC files of a competition flight into one cleaned data model.

    data = load(paths, qnh=None, competition_zone=None, utc_offset_h=None, decl_digits=None,
                decl_alt_unit=None, pilots_csv=None, prefer=None, primary="longest")

    data["flights"]        {flight key: {"qnh", "source", "files", "header_values"}}
    data["files"]          one record per file: header facts, status, flight results, warnings,
                           "counts" (the file that stands for its pilot), and "track"
    data["markers"]        every marker drop, tagged with file, pilot, file_status, counts
    data["declarations"]   every goal declaration, expanded to a point, tagged the same way, with
                           "declared_from": where and how high the pilot was when declaring
    data["warnings"], data["unreadable"], data["skipped"], data["roster"]
    data["competition_zone"], data["utc_offset_h"]   as given

    counting(data)            {pilot: the file record that counts, or None}; every pilot appears
    markers_of(data, record)  declarations_of(data, record)     the events of one file
    reference_position(x)     a lat/lon that stands for a point, a file record or the loaded data
    to_grid(point, zone)      (easting, northing) of a point on one grid
    local_time(when, record)  the file's local time of an event ("HH:MM:SS"); local_datetime() for the datetime

Rules, fixed here so every report and measurement agrees:
- Latitude/longitude is the truth of a point. UTM is shown in the point's own zone and
  distances are measured on a grid both points share (geo.grid_distance).
- Altitude is QNH-corrected barometric; one QNH per flight. A fix or marker without
  barometric altitude shows its own GPS altitude as measured, labelled "gps". Nothing is
  interpolated or adjusted, and vertical speed is never taken across a change of source.
- Files are grouped into flights by name (references/records.md, File names), else by
  header date. A second file holding the same recording is ignored and said.
- When a pilot has several files with a flight in them, one counts: the longest by default,
  or the first started when the rules say so (`primary="first_started"`). It is always
  noted, and `prefer={pilot: file name}` overrides it.
- Digit references (declarations) are read on `competition_zone` when the competition or
  task fixes a grid, else on the zone of the position where they were declared. With a
  competition zone, markers and declarations also carry their coordinates on that grid
  (`grid_zone`, `grid_easting`, `grid_northing`, `grid_utm`).
"""
import csv
import math
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bl_igc import clean_text, parse_file  # noqa: E402
from geo import expand_declaration, format_utm, grid_distance, latlon_to_utm, qnh_altitude, utm_band  # noqa: E402

MAX_H_SPEED = 40.0    # m/s; a fix faster than this from BOTH neighbours is a position spike
MAX_V_SPEED = 15.0    # m/s; same rule for altitude
CLEARANCE = 25.0      # m above ground that counts as airborne
MIN_SECONDS = 300     # shorter recordings are tests
MAX_FILE_BYTES = 50_000_000
FLOWN = ("flight", "partial")


def epoch(when):
    """Seconds since 1970 from an ISO time (no offset means UTC, 'Z' accepted), a datetime, or a number."""
    if isinstance(when, (int, float)):
        return float(when)
    t = when if isinstance(when, datetime) else datetime.fromisoformat(str(when).replace("Z", "+00:00"))
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).timestamp()


def _fresh(p):
    return p.get("_at") == (p["lat"], p["lon"])  # the stored UTM still belongs to this lat/lon


def point_distance(a, b):
    """Horizontal metres between two points of the model (anything with lat/lon)."""
    if _fresh(a) and _fresh(b) and a["zone"] == b["zone"] and (a["lat"] >= 0) == (b["lat"] >= 0):
        return math.hypot(b["easting"] - a["easting"], b["northing"] - a["northing"])
    return grid_distance(a["lat"], a["lon"], b["lat"], b["lon"])


def with_utm(point):
    """Add the point's own-zone UTM fields to a dict that has lat/lon."""
    e, n, zone, band = latlon_to_utm(point["lat"], point["lon"])
    point.update(easting=e, northing=n, zone=zone, utm_zone=f"{zone}{band}", utm=f"{zone}{band} {e:.0f} {n:.0f}",
                 _at=(point["lat"], point["lon"]))
    return point


def to_grid(point, zone, northern=None):
    """(easting, northing) of a point on the given grid zone, whatever zone the point lies in."""
    if northern is None:
        northern = point["lat"] >= 0
    if _fresh(point) and point["zone"] == zone and (point["lat"] >= 0) == northern:
        return point["easting"], point["northing"]
    return latlon_to_utm(point["lat"], point["lon"], zone, northern)[:2]


def with_grid(point, zone):
    """Add the coordinates on the competition grid (`grid_zone`, `grid_easting`, `grid_northing`, `grid_utm`)."""
    if zone is None or point.get("lat") is None:
        return point
    e, n = to_grid(point, zone)
    point.update(grid_zone=zone, grid_easting=e, grid_northing=n, grid_utm=f"{zone}{utm_band(point['lat'])} {e:.0f} {n:.0f}")
    return point


def local_datetime(when, record=None, utc_shift_s=None):
    """The event as an aware datetime in the file's local time. `when` is anything epoch() reads, or a point.
    The offset is `utc_shift_s`, else the record's; without either it is UTC."""
    if isinstance(when, dict):
        when = when["t"] if "t" in when else when["time_utc"]
    shift = utc_shift_s if utc_shift_s is not None else (record or {}).get("utc_shift_s") or 0
    return datetime.fromtimestamp(epoch(when), timezone(timedelta(seconds=shift)))


def local_time(when, record=None, utc_shift_s=None, fmt="%H:%M:%S"):
    return local_datetime(when, record, utc_shift_s).strftime(fmt)


def reference_position(near):
    """(lat, lon) to complete shortened digits against: a point's own position, the middle fix of a file record's
    track, or the median of those over every file of the loaded data. None when nothing has a position."""
    if near is None:
        return None
    if isinstance(near, dict) and "files" in near:
        near = [r for r in near["files"] if r.get("track")]
    if isinstance(near, dict) and "track" in near:
        fixes = [p for p in near["track"] if p.get("lat") is not None]
        return (fixes[len(fixes) // 2]["lat"], fixes[len(fixes) // 2]["lon"]) if fixes else None
    if isinstance(near, dict):
        return (near["lat"], near["lon"]) if near.get("lat") is not None else None
    found = [reference_position(item) for item in near]
    found = [f for f in found if f]
    if not found:
        return None
    return statistics.median(f[0] for f in found), statistics.median(f[1] for f in found)


# ----------------------------------------------------------------------------- one track

def empty_info():
    return {"status": "test", "used": 0, "dropped": Counter(), "zones": [], "notes": [], "takeoff": None, "landing": None,
            "alt_source": "baro", "alt_gaps": 0, "alt_gps": 0, "alt_gps_ranges": [], "gps_minus_baro_m": None}


def _without_dropouts(values, other=None):
    """A run of exact zeros is a dropout when the readings bordering it are clearly not at
    zero, or when `other` (the second altitude source) moves by more than CLEARANCE meanwhile."""
    out, n, i = list(values), len(values), 0
    while i < n:
        if values[i] != 0:
            i += 1
            continue
        j = i
        while j < n and values[j] == 0:
            j += 1
        before = next((values[k] for k in range(i - 1, -1, -1) if values[k]), None)
        after = next((values[k] for k in range(j, n) if values[k]), None)
        moving = [v for v in (other or [])[i:j] if v is not None]
        if any(v is not None and abs(v) > 15 for v in (before, after)) or (moving and max(moving) - min(moving) > CLEARANCE):
            out[i:j] = [None] * (j - i)
        i = j
    return out


def _clean_positions(fixes, dropped):
    """Valid fixes with UTM, in time order, without position spikes."""
    pts = []
    for f in fixes:
        if not f["valid"]:
            dropped["no fix"] += 1
            continue
        p = with_utm(dict(f, t=epoch(f["time_utc"])))
        if pts and p["t"] <= pts[-1]["t"]:
            dropped["duplicate time"] += 1
            continue
        pts.append(p)

    def speed(a, b):
        return point_distance(a, b) / (b["t"] - a["t"])

    for _ in range(2):  # a spike disagrees with both neighbours; a real jump disagrees with one
        keep = []
        for i, p in enumerate(pts):
            before = speed(pts[i - 1], p) if i > 0 else None
            after = speed(p, pts[i + 1]) if i < len(pts) - 1 else None
            if len(pts) > 2:  # at either end, judge by whether the next pair is sane
                if before is None:
                    before = after if speed(pts[1], pts[2]) <= MAX_H_SPEED else 0
                if after is None:
                    after = before if speed(pts[-3], pts[-2]) <= MAX_H_SPEED else 0
            if before is not None and after is not None and before > MAX_H_SPEED and after > MAX_H_SPEED:
                dropped["position spike"] += 1
            else:
                keep.append(p)
        pts = keep
    return pts


def _altitudes(pts, qnh, info):
    """One altitude and its source per fix: 'baro' (QNH-corrected), 'gps' (as measured) or None."""
    n = len(pts)
    gps = _without_dropouts([p["gps_alt"] for p in pts])
    palt = _without_dropouts([p["pressure_alt_std"] for p in pts], gps)
    alt = [None if v is None else qnh_altitude(v, qnh) for v in palt]
    if n and all(v is None for v in palt):
        info["alt_source"] = "gps"
    both = [g - a for a, g in zip(alt, gps) if a is not None and g is not None]
    if len(both) >= 30:
        info["gps_minus_baro_m"] = statistics.median(both)
    for i in range(1, n - 1):  # altitude spikes, same two-sided rule
        a, b, c = alt[i - 1], alt[i], alt[i + 1]
        if None not in (a, b, c) and abs(b - a) / (pts[i]["t"] - pts[i - 1]["t"]) > MAX_V_SPEED \
                and abs(c - b) / (pts[i + 1]["t"] - pts[i]["t"]) > MAX_V_SPEED:
            alt[i] = None
    sources = ["baro" if a is not None else None for a in alt]
    start = None
    for k in range(n + 1):
        if k < n and alt[k] is None and gps[k] is not None:
            alt[k], sources[k] = float(gps[k]), "gps"
            info["alt_gps"] += 1
            start = k if start is None else start
            continue
        if start is not None:
            info["alt_gps_ranges"].append((pts[start]["time_utc"], pts[k - 1]["time_utc"]))
            start = None
        if k < n and alt[k] is None:
            info["alt_gaps"] += 1
    return alt, sources


def _around(pts, i, seconds):
    """Indices of the first and last fix within `seconds` of fix i."""
    j0 = j1 = i
    while j0 > 0 and pts[i]["t"] - pts[j0 - 1]["t"] <= seconds:
        j0 -= 1
    while j1 < len(pts) - 1 and pts[j1 + 1]["t"] - pts[i]["t"] <= seconds:
        j1 += 1
    return j0, j1


def build_track(flight, qnh):
    """Clean the fixes and add UTM, altitude, speed, vertical speed and phase. Returns (track, info)."""
    info = empty_info()
    pts = _clean_positions(flight["fixes"], info["dropped"])
    n = len(pts)
    alt, sources = _altitudes(pts, qnh, info)
    interval = statistics.median(b["t"] - a["t"] for a, b in zip(pts, pts[1:])) if n > 1 else 1.0
    half = max(10.0, 2 * interval) / 2
    for i, p in enumerate(pts):
        j0, j1 = _around(pts, i, half)
        j0, j1 = min(j0, max(0, i - 1)), max(j1, min(n - 1, i + 1))
        p["i"], p["interval_s"] = i, interval  # place in the cleaned track and the file's logging interval
        p["alt_m"], p["alt_source"] = alt[i], sources[i]
        p["speed_ms"] = point_distance(pts[j0], pts[j1]) / (pts[j1]["t"] - pts[j0]["t"]) if j1 > j0 else 0.0
        while j0 < i and sources[j0] != sources[i]:  # vertical speed only between fixes of the same source
            j0 += 1
        while j1 > i and sources[j1] != sources[i]:
            j1 -= 1
        same = sources[i] is not None and all(sources[k] in (sources[i], None) for k in range(j0, j1 + 1))
        p["vario_ms"] = (alt[j1] - alt[j0]) / (pts[j1]["t"] - pts[j0]["t"]) if same and j1 > j0 else None
    info.update(used=n, zones=sorted({p["zone"] for p in pts}))

    # Detection outputs times only. For it alone, GPS stretches are levelled with the file's own
    # GPS-barometric difference so a change of source on the ground is not read as a climb.
    shift = info["gps_minus_baro_m"] or 0.0
    mixed = info["alt_source"] == "baro"
    level = [a - shift if src == "gps" and mixed else a for a, src in zip(alt, sources)]
    _detect_flight(pts, level, interval, info)
    return pts, info


def _detect_flight(pts, alt, interval, info):
    """Set status (flight / partial / ground / test), take-off, landing, phases and flight statistics."""
    n = len(pts)
    known = [a for a in alt if a is not None]
    for p in pts:
        p["phase"] = "ground"
    if n < 3 or pts[-1]["t"] - pts[0]["t"] < MIN_SECONDS:
        return
    if not known or max(known) - min(known) < CLEARANCE:
        info["status"] = "ground"
        return

    first_minute = [alt[i] for i in range(max(3, _around(pts, 0, 60)[1] + 1)) if alt[i] is not None]
    ground = statistics.median(first_minute or [min(known)])
    takeoff = None
    if statistics.median(p["speed_ms"] for p in pts[:_around(pts, 0, 30)[1] + 1]) > 2.0:
        info["notes"].append("log starts airborne")
    else:
        sustain = max(20.0, 2 * interval)
        for i in range(n):
            j1 = _around(pts, i, sustain)[1]
            seg = [a for a in alt[i:j1 + 1] if a is not None]
            if seg and pts[j1]["t"] - pts[i]["t"] >= 0.75 * sustain and min(seg) > ground + CLEARANCE:
                takeoff = i
                break
        if takeoff is None:
            info["notes"].append("no take-off found")
        else:  # walk back to the last moment still on the ground
            while takeoff > 0 and not (alt[takeoff] is not None and alt[takeoff] <= ground + 3 and pts[takeoff]["speed_ms"] < 1.0):
                takeoff -= 1

    lowest_after, low = [None] * n, None
    for i in range(n - 1, -1, -1):
        if alt[i] is not None:
            low = alt[i] if low is None else min(low, alt[i])
        lowest_after[i] = low
    last_high = max((i for i in range(n) if alt[i] is not None and alt[i] > lowest_after[i] + CLEARANCE), default=None)
    landing = None
    if last_high is not None:
        window = max(30.0, 3 * interval)
        for i in range(last_high, n):
            if pts[-1]["t"] - pts[i]["t"] < 0.8 * window:
                break  # too little log left to see a standstill
            j1 = _around(pts, i, window)[1]
            if pts[j1]["t"] - pts[i]["t"] < 0.8 * window:
                continue  # gap in the log
            seg = [a for a in alt[i:j1 + 1] if a is not None]
            if seg and point_distance(pts[i], pts[j1]) < 8 and max(seg) - min(seg) < 4:
                landing = i
                break
    if landing is None:
        info["notes"].append("log ends airborne")

    start = takeoff if takeoff is not None else 0
    end = landing if landing is not None else n - 1
    for i, p in enumerate(pts):
        p["phase"] = "pre-flight" if i < start else ("airborne" if i <= end else "landed")
    air = pts[start:end + 1]
    baro = [p["alt_m"] for p in air if p["alt_source"] == "baro"]
    gps = [p["alt_m"] for p in air if p["alt_source"] == "gps"]
    varios = [p["vario_ms"] for p in air if p["vario_ms"] is not None]
    info.update(
        status="flight" if takeoff is not None and landing is not None else "partial",
        takeoff=pts[takeoff] if takeoff is not None else None, landing=pts[landing] if landing is not None else None,
        duration_s=air[-1]["t"] - air[0]["t"],
        max_alt_m=max(baro) if baro else None, max_alt_gps_m=max(gps) if gps else None,
        track_km=sum(point_distance(a, b) for a, b in zip(air, air[1:])) / 1000, straight_km=point_distance(air[0], air[-1]) / 1000,
        max_speed_kmh=max(p["speed_ms"] for p in air) * 3.6,
        max_climb_ms=max(varios) if varios else None, max_sink_ms=min(varios) if varios else None)


def tag_position_source(flight, track):
    """Label each fix 'primary' or 'fallback' from the XS events. Returns the fallback fixes."""
    primary = flight["header"]["position_source"]
    switches = [(epoch(e["time_utc"]), e["to"]) for e in flight["events"] if e["type"] == "source_change"]
    if not (primary and switches and track):
        return []
    for p in track:
        active = next((to for t, to in reversed(switches) if t <= p["t"]), primary)
        p["position_source"] = "primary" if active == primary else "fallback"
    return [p for p in track if p["position_source"] == "fallback"]


# ----------------------------------------------------------------------------- choices made once per flight

def flight_key(path, header):
    """The header has no flight number; only file names do (WatchMeFly and app exports)."""
    name = os.path.basename(path)
    m = re.search(r"E\[([^\]]*)\]F\[([^\]]*)\]", name)
    if m:
        return f"E[{m.group(1)}]F[{m.group(2)}]"
    m = re.fullmatch(r"(\d{3})-\d{12}\.igc", name, re.I)
    if m:
        return f"flight {int(m.group(1))}, date {header['date_utc']}"
    return f"date {header['date_utc']}"


def load_pilots(path, warn):
    names = {}
    if not path:
        return names
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            text = fh.read()
    except OSError as e:
        raise ValueError(f"pilot list {path}: {e.strerror}") from None
    except UnicodeDecodeError:
        raise ValueError(f"pilot list {path}: not readable as UTF-8 text") from None
    for row in csv.reader(text.splitlines(), delimiter=max(",;\t", key=text.count)):
        if row and row[0].strip().isdigit():  # a name is optional: a plain roster is enough
            number = int(row[0])
            if number in names:
                warn(f"pilot list: number {number} appears twice; last one used")
            names[number] = clean_text(row[1].strip()) if len(row) > 1 and row[1].strip() else None
    if not names:
        warn(f"pilot list {os.path.basename(path)} has no rows starting with a pilot number")
    return names


def collect_files(paths, out):
    files, seen = [], set()
    for p in paths:
        if os.path.isdir(p):
            entries = [n for n in sorted(os.listdir(p)) if not n.startswith(".") and os.path.isfile(os.path.join(p, n))]
            out["skipped"] += [n for n in entries if not n.lower().endswith(".igc")]
            candidates = [os.path.join(p, n) for n in entries if n.lower().endswith(".igc")]
        else:
            candidates = [p]
        for c in candidates:
            real = os.path.realpath(c)
            if real in seen:
                continue
            seen.add(real)
            if not os.path.isfile(c):
                out["unreadable"].append(f"{c}: not found")
            elif os.path.getsize(c) > MAX_FILE_BYTES:
                out["unreadable"].append(f"{c}: larger than {MAX_FILE_BYTES // 1_000_000} MB")
            else:
                files.append(c)
    return files


def choose_qnh(key, members, user_qnh, warn):
    """One QNH per flight: the user's, else the most common header value. Mismatches are flagged."""
    values = Counter(round(fl["header"]["qnh_hpa"], 1) for fl in members if fl["header"]["qnh_hpa"])
    ranked = values.most_common()
    info = {"files": len(members), "header_values": dict(values)}
    if user_qnh is not None:
        info.update(qnh=user_qnh, source="given by user")
        if any(v != round(user_qnh, 1) for v in values):
            warn(f"{key}: QNH {user_qnh} given by user; headers say {dict(values)}")
    elif not ranked:
        info.update(qnh=1013.25, source="DEFAULT 1013.25, no QNH in any header")
        warn(f"{key}: no QNH in any header; altitudes are uncorrected standard altitude. Pass --qnh")
    else:
        info.update(qnh=ranked[0][0], source="most common header value")
        if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
            info["source"] = "TIE between header values, first taken"
            warn(f"{key}: QNH values tie {dict(values)}; using {ranked[0][0]}. Pass --qnh to settle it")
        for fl in members:
            q, label = fl["header"]["qnh_hpa"], f"pilot {fl['header']['pilot_number']} ({os.path.basename(fl['file'])})"
            if q is None:
                warn(f"{key}: {label} has no QNH; using {info['qnh']}")
            elif round(q, 1) != info["qnh"]:
                warn(f"{key}: QNH MISMATCH {label} has {q}, flight uses {info['qnh']}; app may not have fetched flight data")
    for fl in members:
        unit = (fl["header"]["qnh_unit"] or "hPa").lower()
        if unit != "hpa":
            warn(f"{key}: pilot {fl['header']['pilot_number']} QNH unit is {unit!r}, not hPa; value not converted")
    return info


def choose_utc_shift(header, group_shifts, user_offset_h):
    if user_offset_h is not None:
        return int(user_offset_h * 3600), "given by user"
    if header["utc_shift_s"] is not None:
        return header["utc_shift_s"], "header"
    if group_shifts:
        return group_shifts.most_common(1)[0][0], "offset ASSUMED from the flight's other files"
    return 0, "UTC (no offset in file)"


def choose_counting_files(records, prefer, warn, primary="longest"):
    """Mark the one file that stands for each pilot of each flight: the longest flight or the first
    started (`primary`), unless `prefer` names one."""
    if primary not in ("longest", "first_started"):
        raise ValueError("primary must be 'longest' or 'first_started'")
    prefer = {str(k): os.path.basename(str(v)) for k, v in (prefer or {}).items()}
    flown = defaultdict(list)
    for r in records:
        r["counts"] = False
        if r["status"] in FLOWN:
            flown[(r["flight"], r["pilot"])].append(r)
    for pilot, wanted in prefer.items():
        if not any(r["file"] == wanted and str(r["pilot"]) == pilot and r["status"] in FLOWN for r in records):
            warn(f"pilot {pilot}: preferred file {wanted!r} is not one of its flight files; the {primary.replace('_', ' ')} is used instead")
    for (key, pilot), candidates in flown.items():
        chosen = next((r for r in candidates if r["file"] == prefer.get(str(pilot))), None)
        how = "as instructed"
        if chosen is None and primary == "first_started":
            chosen, how = min(candidates, key=lambda r: r["first_fix_utc"]), "the first started"
            if sum(r["first_fix_utc"] == chosen["first_fix_utc"] for r in candidates) > 1:
                how = "TIE in start time, first taken"
        elif chosen is None:
            chosen, how = max(candidates, key=lambda r: r["duration_s"]), "the longest"
            if sum(r["duration_s"] == chosen["duration_s"] for r in candidates) > 1:
                how = "TIE in length, first taken"
        chosen["counts"] = True
        if len(candidates) > 1:
            others = ", ".join(r["file"] for r in candidates if r is not chosen)
            warn(f"{key}: pilot {pilot} has {len(candidates)} files with a flight; {chosen['file']} counts ({how}), not {others}")


def _unit(value):
    v = (value or "").strip().lower()
    return {"feet": "ft", "foot": "ft", "ft": "ft", "meter": "m", "metre": "m", "meters": "m", "metres": "m", "m": "m"}.get(v)


# ----------------------------------------------------------------------------- records

def file_record(flight, key, track, info, names, shift, basis, local):
    h = flight["header"]
    rec = {
        "file": os.path.basename(flight["file"]), "flight": key, "pilot": h["pilot_number"],
        "name": h["pilot_name"] or names.get(h["pilot_number"]), "source": h["source"],
        "version": h["app_version"] or h["firmware"], "device": h["device_settings"] or h["device_description"],
        "qnh_header": h["qnh_hpa"], "bls_serial": h["bls_serial"], "position_source": h["position_source"],
        "utc_shift_s": shift, "time_basis": basis, "signature_present": flight["signature_present"],
        "fixes": len(flight["fixes"]), "used": info["used"], "dropped": dict(info["dropped"]), "zones": info["zones"],
        "alt_source": info["alt_source"], "alt_gps_fixes": info["alt_gps"], "alt_gaps": info["alt_gaps"],
        "gps_minus_baro_m": info["gps_minus_baro_m"], "status": info["status"], "notes": list(info["notes"]),
        "first_fix_utc": flight["fixes"][0]["time_utc"] if flight["fixes"] else None,
        "warnings": list(flight["warnings"]), "track": track,
    }
    add = rec["warnings"].append
    if info["status"] in FLOWN:
        for name in ("takeoff", "landing"):
            p = info[name]
            rec[name + "_local"] = local(p["time_utc"]) if p else None
            rec[name] = {k: p[k] for k in ("lat", "lon", "utm", "alt_m", "alt_source", "time_utc", "t")} if p else None
        rec.update({k: info[k] for k in ("duration_s", "max_alt_m", "max_alt_gps_m", "track_km", "straight_km",
                                         "max_speed_kmh", "max_climb_ms", "max_sink_ms")})
    if basis not in ("header", "given by user"):
        add(f"time-basis: times are {basis}")
    if info["alt_gps"]:
        ranges = info["alt_gps_ranges"]
        spans = ", ".join(local(a) if a == b else f"{local(a)}-{local(b)}" for a, b in ranges[:4]) + (" ..." if len(ranges) > 4 else "")
        diff, count = info["gps_minus_baro_m"], info["alt_gps"]
        context = (f"in this file GPS reads about {abs(diff):.0f} m {'above' if diff >= 0 else 'below'} barometric"
                   if diff is not None else "nothing in the file to compare it with")
        what = "the whole file has" if info["alt_source"] == "gps" else \
            f"{count} fix{'es' if count > 1 else ''} ({spans}) ha{'ve' if count > 1 else 's'}"
        add(f"gps-altitude: {what} no barometric altitude; GPS ALTITUDE shown there as measured, not QNH-corrected "
            f"({context}); kept out of barometric maximum and vertical speed")
    if info["alt_gaps"]:
        add(f"alt-gaps: {info['alt_gaps']} fixes have neither barometric nor GPS altitude; left out of altitude results")
    fallback = tag_position_source(flight, track)
    if fallback:
        rec["fallback_fixes"] = len(fallback)
        add(f"fallback-source: {len(fallback)} fixes ({local(fallback[0]['time_utc'])}-{local(fallback[-1]['time_utc'])}) were "
            f"recorded on the fallback source, not {h['position_source']}; position and altitude there are the phone's own")
    return rec


def marker_record(e, tag, track, qnh, local):
    """Altitude follows the track rule: barometric (record, else the sensor's response), else the marker's own GPS altitude."""
    when = epoch(e["time_utc"])
    near = min(track, key=lambda p: abs(p["t"] - when), default=None)
    dropout = e["pressure_alt_std"] == 0.0 and near is not None and abs(near["t"] - when) <= 5 and near["alt_source"] != "baro"
    std = None if dropout else e["pressure_alt_std"]
    bls_std = (e.get("bls") or {}).get("pressure_alt_std")
    if std is not None:
        alt_m, alt_source, alt_from = qnh_altitude(std, qnh), "baro", "record"
    elif bls_std:
        alt_m, alt_source, alt_from = qnh_altitude(bls_std, qnh), "baro", "XXC"
    elif e["gps_alt"]:
        alt_m, alt_source, alt_from = float(e["gps_alt"]), "gps", "GPS, as measured"
    else:
        alt_m, alt_source, alt_from = None, None, "none"
    rec = dict(tag, marker=e["marker"], time_utc=e["time_utc"], time_local=local(e["time_utc"]), t=when,
               lat=None, lon=None, utm=None, alt_m=alt_m, alt_source=alt_source, alt_from=alt_from,
               fix=e["fix_effective"], satellites=e["satellites_effective"], quality_from=e["quality_from"],
               accuracy_m=e.get("accuracy"), phone_offset_m=None, sensor_offset_m=None,
               position_source=(near or {}).get("position_source") if near is not None and abs(near["t"] - when) <= 30 else None)
    if e["lat"] or e["lon"]:  # kept even without a fix: measured, and flagged by fix=False wherever it is used
        rec.update(lat=e["lat"], lon=e["lon"])
        with_utm(rec)
        for field, other in (("phone_offset_m", e.get("app_snapshot")), ("sensor_offset_m", e.get("bls"))):
            if other and (other["lat"] or other["lon"]):
                rec[field] = grid_distance(rec["lat"], rec["lon"], other["lat"], other["lon"])
    return rec


def declaration_record(e, tag, header, track, local, competition_zone, user_digits, user_unit, qnh):
    """The declared goal as a point, plus `declared_from`: where and how high the pilot was when declaring."""
    digits, digits_from = (user_digits, "user") if user_digits else \
        (header["declaration_digits"], "header") if header["declaration_digits"] else ("4/4", "ASSUMED")
    unit, unit_from = (user_unit, "user") if user_unit else \
        (_unit(header["altitude_unit"]), "header") if _unit(header["altitude_unit"]) else ("ft", "ASSUMED")
    when = epoch(e["time_utc"])
    origin = None
    if e.get("fix_effective") and (e.get("lat") or e.get("lon")):
        std = e.get("pressure_alt_std")
        alt_m, alt_source = (qnh_altitude(std, qnh), "baro") if std else (float(e["gps_alt"]), "gps") if e.get("gps_alt") else (None, None)
        origin = with_utm({"lat": e["lat"], "lon": e["lon"], "alt_m": alt_m, "alt_source": alt_source, "time_utc": e["time_utc"], "t": when,
                           "from": "declaration position"})
    elif track:
        p = min(track, key=lambda p: abs(p["t"] - when))
        origin = with_utm({"lat": p["lat"], "lon": p["lon"], "alt_m": p["alt_m"], "alt_source": p["alt_source"], "time_utc": p["time_utc"],
                           "t": p["t"], "from": f"track fix at {local(p['time_utc'])}, {abs(p['t'] - when):.0f} s from the declaration"})
    near = (origin["lat"], origin["lon"]) if origin else None
    rec = dict(tag, number=e.get("number"), time_utc=e["time_utc"], time_local=local(e["time_utc"]), t=when, declared=True,
               text=e.get("text"), digits=digits, digits_from=digits_from, alt_unit=unit, alt_unit_from=unit_from,
               near_from=origin["from"] if origin else None, declared_at=origin["utm"] if origin else None, declared_from=origin)
    geodetic = header["geodetic_system"]
    if geodetic and "UTM" not in geodetic.upper():
        rec.update(lat=None, lon=None, utm=None, grid_zone=None, grid_from=None, grid_easting=None, grid_northing=None,
                   alt_m=None, alt_ft=None, warnings=[f"pilot used {geodetic}, not UTM; text not expanded"])
        return rec
    rec.update(expand_declaration(e.get("text") or "", *(near or (None, None)), digits, unit, competition_zone))
    rec["grid_from"] = None if rec["grid_zone"] is None else ("competition zone given" if competition_zone else "zone of " + rec["near_from"])
    if rec["lat"] is not None:
        with_utm(rec)
    return rec


# ----------------------------------------------------------------------------- access

def counting(data, flight=None):
    """{pilot: the file record that counts, or None when the pilot has no flight file}. No pilot is left out:
    pilots from the roster (`pilots_csv`) appear even without any file. When several flights were loaded
    together, say which (`flight` is a key of data["flights"])."""
    if flight is None and len(data["flights"]) > 1:
        raise ValueError(f"several flights loaded, pass flight= one of {sorted(data['flights'])}")
    out = {pilot: None for pilot in data["roster"]}
    for r in data["files"]:
        if flight is not None and r["flight"] != flight:
            continue
        out.setdefault(r["pilot"], None)
        if r["counts"]:
            out[r["pilot"]] = r
    return dict(sorted(out.items(), key=lambda kv: str(kv[0]).zfill(6)))


def markers_of(data, record):
    return [m for m in data["markers"] if m["file"] == record["file"]]


def declarations_of(data, record):
    return [d for d in data["declarations"] if d["file"] == record["file"]]


# ----------------------------------------------------------------------------- load

def is_igc(flight):
    """A known writer, or at least a dated log with some fixes."""
    return bool(flight["header"]["source"] or (flight["header"]["date_utc"] and flight["counts"].get("B", 0) >= 10))


def recording_key(flight):
    """What identifies a recording whatever the file is called: pilot, date, first and last fix, counts."""
    fixes = flight["fixes"]
    return (flight["header"]["pilot_number"], flight["header"]["date_utc"], fixes[0]["time_utc"] if fixes else None,
            fixes[-1]["time_utc"] if fixes else None, len(fixes), len(flight["events"]))


def check_zone(competition_zone, parsed, warn):
    """A competition grid more than one zone away from every fix cannot be the grid the files are on."""
    if competition_zone is None:
        return
    zones = {utm_zone_of(f) for flight in parsed for f in flight["fixes"] if f["valid"]}
    zones.discard(None)
    if zones and all(min(abs(z - competition_zone), 60 - abs(z - competition_zone)) > 1 for z in zones):
        raise ValueError(f"competition zone {competition_zone} is nowhere near the files, which lie in zone{'s' if len(zones) > 1 else ''} "
                         f"{'/'.join(map(str, sorted(zones)))}")


def utm_zone_of(fix):
    return latlon_to_utm(fix["lat"], fix["lon"])[2] if fix.get("lat") is not None else None


def load(paths, qnh=None, competition_zone=None, utc_offset_h=None, decl_digits=None, decl_alt_unit=None,
         pilots_csv=None, prefer=None, primary="longest"):
    out = {"flights": {}, "competition_zone": competition_zone, "utc_offset_h": utc_offset_h, "files": [], "markers": [],
           "declarations": [], "warnings": [], "unreadable": [], "skipped": [], "roster": []}
    warn = out["warnings"].append
    names = load_pilots(pilots_csv, warn)
    out["roster"] = sorted(names)
    parsed, seen = [], {}
    for path in collect_files(paths, out):
        try:
            flight = parse_file(path)
        except OSError as e:
            out["unreadable"].append(f"{path}: {e.strerror}")
            continue
        if not is_igc(flight):
            out["unreadable"].append(f"{path}: not an IGC file")
            continue
        key = recording_key(flight)
        if flight["fixes"] and key in seen:
            warn(f"{os.path.basename(path)} holds the same recording as {seen[key]} (same pilot, date, fixes and events); "
                 f"only {seen[key]} is used")
            continue
        seen.setdefault(key, os.path.basename(path))
        parsed.append(flight)
    check_zone(competition_zone, parsed, warn)

    groups = defaultdict(list)
    for flight in parsed:
        groups[flight_key(flight["file"], flight["header"])].append(flight)
    for key, members in groups.items():
        out["flights"][key] = choose_qnh(key, members, qnh, warn)
    group_shifts = {key: Counter(fl["header"]["utc_shift_s"] for fl in members if fl["header"]["utc_shift_s"] is not None)
                    for key, members in groups.items()}

    events = []
    for flight in parsed:
        h = flight["header"]
        key = flight_key(flight["file"], h)
        flight_qnh = out["flights"][key]["qnh"]
        shift, basis = choose_utc_shift(h, group_shifts[key], utc_offset_h)

        def local(iso, shift=shift):
            return (datetime.fromisoformat(iso) + timedelta(seconds=shift)).strftime("%H:%M:%S")

        track, info = build_track(flight, flight_qnh)
        rec = file_record(flight, key, track, info, names, shift, basis, local)
        out["files"].append(rec)
        for e in flight["events"]:
            tag = {"file": rec["file"], "flight": key, "pilot": h["pilot_number"], "file_status": info["status"]}
            if e["type"] == "marker_drop":
                events.append((rec, out["markers"], with_grid(marker_record(e, tag, track, flight_qnh, local), competition_zone)))
            elif e["type"] == "goal_declaration":
                events.append((rec, out["declarations"], with_grid(declaration_record(e, tag, h, track, local, competition_zone,
                                                                                      decl_digits, decl_alt_unit, flight_qnh), competition_zone)))

    choose_counting_files(out["files"], prefer, warn, primary)
    missing = [p for p in out["roster"] if not any(r["pilot"] == p for r in out["files"])]
    if missing:
        warn(f"pilots on the roster with no file at all: {', '.join(map(str, missing))}")
    for rec, target, row in events:
        row["counts"] = rec["counts"]
        target.append(row)
    return out
