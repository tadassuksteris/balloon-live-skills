#!/usr/bin/env python3
"""Report on the Balloon Live IGC files of a competition flight.

    check_flight.py FOLDER_OR_FILES... [options]

Prints the QNH used, one row per file, every marker drop and goal declaration with UTM
coordinates and altitude, and aggregated warnings. Every assumption is printed; nothing
falls back silently. Output files are overwritten.

The data and the rules behind it come from flight_data.load(); this script only shows it.
Coordinates are UTM WGS84 in each point's own zone, always with the zone; with --zone they
are shown on that grid and the own zone is noted where it differs.
"""
import argparse
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flight_data import FLOWN, load  # noqa: E402
from geo import m_to_ft  # noqa: E402

NOTE_TAGS = {"quality-byte": "XX0 quality byte", "quality-differs": "XX0≠XXC", "no-bls-serial": "no BLS serial",
             "no-gps": "no GPS", "midnight": "midnight", "overflow": "overflow values", "geodetic": "not UTM",
             "pressure-offset": "pressure offset", "unreadable-line": "bad lines", "time-backwards": "time backwards",
             "gps-altitude": "GPS ALTITUDE in part", "fallback-source": "on fallback source", "alt-gaps": "altitude gaps"}


def write_track_csv(path, record):
    shift = record["utc_shift_s"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["time_utc", "time_local", "lat", "lon", "utm_zone", "easting", "northing", "alt_ft", "alt_m", "alt_source",
                    "alt_std_m", "gps_alt_m", "speed_kmh", "vario_ms", "satellites", "accuracy_m", "phase", "position_source"])
        for p in record["track"]:
            a = p["alt_m"]
            local = (datetime.fromisoformat(p["time_utc"]) + timedelta(seconds=shift)).strftime("%H:%M:%S")
            std = p["pressure_alt_std"]
            if std == 0.0 and p["alt_source"] != "baro":
                std = None  # a dropout marker, not a reading
            w.writerow([p["time_utc"], local, p["lat"], p["lon"], p["utm_zone"], round(p["easting"], 1), round(p["northing"], 1),
                        None if a is None else round(m_to_ft(a)), None if a is None else round(a, 1), p["alt_source"],
                        std, p["gps_alt"], round(p["speed_ms"] * 3.6, 1),
                        None if p["vario_ms"] is None else round(p["vario_ms"], 2), p["satellites"], p["accuracy"], p["phase"],
                        p.get("position_source", "")])


def run(paths, qnh=None, zone=None, utc_offset_h=None, alt_unit="ft", decl_digits=None, decl_alt_unit=None,
        pilots_csv=None, csv_dir=None, prefer=None, primary="longest"):
    """load() plus the optional track CSVs. `zone` is the grid digit references are read on, if one is fixed."""
    out = load(paths, qnh, zone, utc_offset_h, decl_digits, decl_alt_unit, pilots_csv, prefer, primary)
    out["alt_unit"] = alt_unit
    names = Counter()
    if csv_dir and any(rec["track"] for rec in out["files"]):
        try:
            os.makedirs(csv_dir, exist_ok=True)
        except OSError as e:
            raise ValueError(f"cannot create {csv_dir}: {e.strerror}") from None
    for rec in out["files"]:
        if csv_dir and rec["track"]:
            stem = os.path.splitext(rec["file"])[0]
            names[stem] += 1
            rec["csv"] = stem + (f"-{names[stem]}" if names[stem] > 1 else "") + ".csv"
            write_track_csv(os.path.join(csv_dir, rec["csv"]), rec)
    return out


LEGEND = {
    "altitudes": "every altitude in this file is metres, whatever --alt-unit was",
    "status": "flight; partial (starts or ends airborne); ground; test (under 5 minutes)",
    "counts": "the one file that stands for its pilot in this flight (--primary, --prefer)",
    "alt_source": "baro: QNH-corrected barometric; gps: GPS altitude as measured, only where barometric is missing",
    "alt_std_m, pressure_alt_std": "pressure altitude in the standard atmosphere as logged, before the QNH correction",
    "position_source": "primary or fallback position source of the phone, from the XS records",
    "grid_zone, grid_easting, grid_northing, grid_utm": "the point on the competition grid given with --zone",
    "declared_from": "where and how high the pilot was when declaring (declaration position, else the track fix at that time)",
    "phone_offset_m, sensor_offset_m": "metres from the marker position to the phone's snapshot and to the sensor's response",
    "warnings": "code: message; words in capitals need a person's attention",
}


def without_tracks(out):
    """The data for JSON: no tracks (they go to CSV), no internal fields, and a legend."""
    def public(rows, drop=()):
        return [{k: v for k, v in r.items() if k not in drop and not k.startswith("_")} for r in rows]
    return dict(out, alt_unit="m", legend=LEGEND,
                files=public(out["files"], ("track",)), markers=public(out["markers"]), declarations=public(out["declarations"]))


def aggregated_warnings(out):
    """Same message for many files becomes one line naming the pilots."""
    by_message = defaultdict(list)
    several = Counter(r["pilot"] for r in out["files"])
    for r in out["files"]:
        who = str(r["pilot"]) if several[r["pilot"]] == 1 else f"{r['pilot']} ({r['file']})"
        for w in r["warnings"]:
            by_message[w.split(": ", 1)[-1]].append(who)
        for note in r["notes"]:
            by_message[note].append(who)
        drops = {k: v for k, v in r["dropped"].items() if k != "no fix" and v}
        if drops:
            by_message[f"fixes dropped in cleaning {drops}"].append(who)
    return list(out["warnings"]) + [f"pilot{'s' if len(who) > 1 else ''} {', '.join(who)}: {msg}" for msg, who in by_message.items()]


def render(out):
    unit = out.get("alt_unit", "ft")

    def alt(m):
        return "-" if m is None else (f"{m_to_ft(m):.0f} ft" if unit == "ft" else f"{m:.1f} m")

    def cell(v, width, right=True):
        text = "-" if v is None or v == "" else str(v)
        return text[:width].rjust(width) if right else text[:width].ljust(width)

    def rounded(v, suffix=""):
        return None if v is None else f"{round(v)}{suffix}"

    def standing(r):  # why a marker or declaration may not be the one to use
        if r["file_status"] not in FLOWN:
            return f"{r['file_status']} file {r['file']}"
        return "" if r["counts"] else f"file {r['file']} does not count for this pilot"

    lines = []
    if not out["files"]:
        lines.append("no readable IGC files")
    for key, f in out["flights"].items():
        lines.append(f"{key}: {f['files']} files, QNH {f['qnh']} hPa ({f['source']})")
    zone = out["competition_zone"]
    if out["files"]:
        grid = f"zone {zone} (given)" if zone else "the zone where each was declared"
        where = f"on grid zone {zone} (given; own zone noted where it differs)" if zone else "in each point's own zone"
        offset = out.get("utc_offset_h")
        times = f"times at UTC{offset:+g} (given)" if offset is not None else "times local per file's UTC offset unless noted"
        lines.append(f"coordinates UTM WGS84 {where}; digit references read on {grid}; "
                     f"altitude QNH-corrected barometric in {unit}; {times}")

    def shown_utm(p):  # on the competition grid when one is given, else the point's own zone
        return p.get("grid_utm") if zone and p.get("grid_utm") else p.get("utm")

    def own_zone(p):
        return f"own zone {p['utm']}" if zone and p.get("utm") and p.get("zone") != zone else ""

    head = (f"{'pilot':>5} {'name':<14} {'src':<6} {'ver':<8} {'QNH':>6} {'BLS':>5} {'fixes':>6} {'status':<8} "
            f"{'takeoff':>8} {'landing':>8} {'dur':>5} {'max alt':>9} {'track':>8} note")
    lines += ["", head, "-" * len(head)]
    for r in sorted(out["files"], key=lambda r: (str(r["pilot"]).zfill(6), r["file"])):
        tags = [NOTE_TAGS.get(w.split(":", 1)[0]) for w in r["warnings"]] + r["notes"]
        if r["alt_source"] == "gps":
            tags = ["GPS ALTITUDE only" if t == "GPS ALTITUDE in part" else t for t in tags]
        if r["time_basis"] not in ("header", "given by user"):
            tags.append("times " + r["time_basis"].split(" (")[0].replace(" from the flight's other files", ""))
        if len(r["zones"]) > 1:
            tags.append("zones " + "/".join(map(str, r["zones"])))
        if r["status"] in FLOWN and not r["counts"]:
            tags.append("DOES NOT COUNT, another file stands for this pilot")
        results = [""] * 5
        if r["status"] in FLOWN:
            top = alt(r["max_alt_m"]) if r["max_alt_m"] is not None else alt(r["max_alt_gps_m"]) + " GPS"
            results = [(r["takeoff_local"] or "-")[:5], (r["landing_local"] or "-")[:5], rounded(r["duration_s"] / 60, "m"),
                       top, f"{r['track_km']:.1f}km"]
        lines.append(f"{cell(r['pilot'], 5)} {cell(r['name'], 14, False)} {cell(r['source'], 6, False)} {cell(r['version'], 8, False)} "
                     f"{cell(r['qnh_header'], 6)} {cell(r['bls_serial'], 5)} {cell(r['fixes'], 6)} {cell(r['status'], 8, False)} "
                     f"{cell(results[0], 8)} {cell(results[1], 8)} {cell(results[2], 5)} {cell(results[3], 9)} {cell(results[4], 8)} "
                     f"{', '.join(dict.fromkeys(t for t in tags if t))}")

    if out["markers"]:
        lines += ["", "markers: fix = GPS fix at the drop (a position without one is shown but does not stand on its own); "
                      "phone Δ / sensor Δ = distance from the recorded position to the phone's snapshot and to the sensor's own response",
                  f"{'pilot':>5} {'mk':>3} {'time':>8} {'UTM':<19} {'alt':>9} {'fix':<5} {'sats':>4} {'phone Δ':>8} {'sensor Δ':>8} note"]
        for m in sorted(out["markers"], key=lambda m: (str(m["marker"]).zfill(3), str(m["pilot"]).zfill(6))):
            notes = [standing(m), own_zone(m), f"altitude from {m['alt_from']}" if m["alt_from"] != "record" else "",
                     "fix/sats from XXC" if m["quality_from"] == "xxc" else
                     "fix/sats decoded from the raw quality byte" if m["quality_from"].startswith("raw") else "",
                     "NO FIX, position as recorded" if m["fix"] is False and m["lat"] is not None else "",
                     "ON FALLBACK SOURCE" if m.get("position_source") == "fallback" else ""]
            lines.append(f"{cell(m['pilot'], 5)} {cell(m['marker'], 3)} {m['time_local']:>8} {cell(shown_utm(m), 19, False)} "
                         f"{alt(m['alt_m']):>9} {cell(m['fix'], 5, False)} {cell(m['satellites'], 4)} "
                         f"{cell(rounded(m['phone_offset_m'], 'm'), 8)} {cell(rounded(m['sensor_offset_m'], 'm'), 8)} "
                         f"{', '.join(n for n in notes if n)}")

    quiet = sorted({r["pilot"] for r in out["files"] if r["counts"]} - {m["pilot"] for m in out["markers"]}, key=lambda p: str(p).zfill(6))
    if quiet:
        lines.append(f"no marker drops in the counting file: pilot{'s' if len(quiet) > 1 else ''} {', '.join(map(str, quiet))}")

    if out["declarations"]:
        lines += ["", f"{'pilot':>5} {'no':>3} {'time':>8} {'declared':<18} {'goal UTM':<19} {'alt':>9} read as"]
        for d in out["declarations"]:
            shown = "-" if d.get("alt_m") is None else (f"{d['alt_ft']:.0f} ft" if unit == "ft" else f"{d['alt_m']:.1f} m")
            grid = f"on zone {d['grid_zone']} ({d['grid_from']})" if d.get("grid_zone") else "not resolved"
            notes = [f"{d['digits']} ({d['digits_from']}), altitude in {d['alt_unit']} ({d['alt_unit_from']}), {grid}",
                     standing(d), own_zone(d)] + d.get("warnings", [])
            lines.append(f"{cell(d['pilot'], 5)} {cell(d['number'], 3)} {d['time_local']:>8} {cell(d['text'], 18, False)} "
                         f"{cell(shown_utm(d), 19, False)} {shown:>9} {'; '.join(n for n in notes if n)}")

    lines.append("")
    lines += [f"! {w}" for w in aggregated_warnings(out)]
    lines += [f"! could not read {u}" for u in out["unreadable"]]
    if out["skipped"]:
        lines.append(f"! ignored {len(out['skipped'])} non-IGC files")
    return "\n".join(lines)


def _main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("paths", nargs="+", help="folder(s) or IGC file(s)")
    p.add_argument("--qnh", type=float, help="QNH in hPa for every flight given (overrides headers)")
    p.add_argument("--zone", type=int, help="UTM zone that digit references (declarations) are on, when the competition or "
                                            "task fixes one; default is the zone where each was declared")
    p.add_argument("--utc-offset", type=float, metavar="HOURS", help="show times with this offset instead of the files' own")
    p.add_argument("--alt-unit", default="ft", choices=["ft", "m"], help="unit for altitudes shown (default ft)")
    p.add_argument("--decl-digits", metavar="E/N", help="declaration format, e.g. 4/4 or 5/4 (default: header, else 4/4)")
    p.add_argument("--decl-alt-unit", choices=["ft", "m"], help="unit of declared altitudes (default: header, else ft)")
    p.add_argument("--pilots", metavar="CSV", help="pilot list with rows number,name")
    p.add_argument("--prefer", action="append", default=[], metavar="PILOT=FILE",
                   help="the file that counts for a pilot with several flight files")
    p.add_argument("--primary", default="longest", choices=["longest", "first_started"],
                   help="which of a pilot's flight files counts when nothing is preferred (rule book; default longest)")
    p.add_argument("--json", metavar="OUT", help="write everything shown, and more, as JSON (altitudes in metres, no tracks)")
    p.add_argument("--csv-dir", metavar="DIR", help="write one cleaned track CSV per file")
    a = p.parse_args(argv)
    if a.qnh is not None and not 850 <= a.qnh <= 1100:
        p.error("--qnh must be in hPa (850 to 1100)")
    if a.zone is not None and not 1 <= a.zone <= 60:
        p.error("--zone must be 1 to 60")
    if a.decl_digits and not re.fullmatch(r"[3-6]/[3-7]", a.decl_digits):
        p.error("--decl-digits looks like 4/4 (easting up to 6 digits, northing up to 7)")
    prefer = {}
    for item in a.prefer:
        pilot, sep, name = item.partition("=")
        if not (sep and pilot.strip().isdigit() and name):
            p.error("--prefer looks like 12=file.igc")
        prefer[int(pilot)] = os.path.basename(name)
    try:
        out = run(a.paths, a.qnh, a.zone, a.utc_offset, a.alt_unit, a.decl_digits, a.decl_alt_unit, a.pilots, a.csv_dir, prefer, a.primary)
    except ValueError as e:
        p.error(str(e))
    print(render(out))
    if a.json:
        try:
            with open(a.json, "w", encoding="utf-8") as fh:
                json.dump(without_tracks(out), fh, indent=1, default=str)
        except OSError as e:
            p.error(f"cannot write {a.json}: {e.strerror}")
    return 0 if out["files"] else 2


if __name__ == "__main__":
    sys.exit(_main())
