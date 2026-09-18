#!/usr/bin/env python3
"""Zones of a competition map (prohibited zones, launch areas, contest area, any point or area
the scorer gives) in one plain format, and what a track did in them. Standard library only.

The format is the skill's own. Whatever the scorer supplies (a GPX, a map tool's export, a
table in the rule book) the agent turns into this, asks for what is missing, and names the
source per zone. Altitudes are feet MSL, radii metres, positions WGS84 latitude/longitude.

    z = zone("PZ1", "pz", lat=49.7562, lon=21.8123, radius_m=1000, ceiling_ft=3300, source="ntapz export; ceiling unit confirmed by scorer")
    z = zone("contest area", "contest", corners=[(lat, lon), ...])
    z = zone("launch point 2", "point", lat=..., lon=...)
    save_zones(zones, "zones.json")    load_zones("zones.json")
    shapes(zones) -> {name: shape or point} for measure.py; zones missing a radius or corners are listed apart
    zone_report(track, zones) -> one row per area: entered, stays, time inside, closest approach, deepest below the ceiling

A zone's floor and ceiling bound the volume that matters: a PZ "up to 3300 ft" has ceiling_ft=3300
and no floor, so a fix is inside it when it is within the outline and at or below 3300 ft.

CLI:
    zones.py ZONES.json FOLDER_OR_FILES   the report for every file's cleaned track
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import measure as m  # noqa: E402
from geo import ft_to_m, m_to_ft  # noqa: E402

FIELDS = ("name", "kind", "lat", "lon", "radius_m", "corners", "floor_ft", "ceiling_ft", "source", "note")


def zone(name, kind, lat=None, lon=None, radius_m=None, corners=None, floor_ft=None, ceiling_ft=None, source="given", note=""):
    """One zone. A circle has lat/lon and radius_m; a polygon has corners [(lat, lon), ...]; a point has lat/lon only."""
    if corners is not None and len(corners) < 3:
        raise ValueError(f"{name}: a polygon needs at least three corners")
    if corners is None and (lat is None or lon is None):
        raise ValueError(f"{name}: a zone needs a position or corners")
    return {"name": name, "kind": kind, "lat": lat, "lon": lon, "radius_m": radius_m,
            "corners": None if corners is None else [[float(a), float(b)] for a, b in corners],
            "floor_ft": floor_ft, "ceiling_ft": ceiling_ft, "source": source, "note": note}


def save_zones(zones, path):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"format": "balloon-live-igc zones", "altitudes": "ft MSL", "radii": "m", "zones": list(zones)}, fh, indent=1)


def load_zones(path):
    with open(path, encoding="utf-8") as fh:
        stored = json.load(fh)
    return [zone(**{k: z.get(k) for k in FIELDS if k in z}) for z in stored["zones"]]


def _limits(z):
    return (None if z["floor_ft"] is None else ft_to_m(z["floor_ft"]), None if z["ceiling_ft"] is None else ft_to_m(z["ceiling_ft"]))


def shapes(zones):
    """{name: shape} for the areas and {name: point} for the points, plus the names of zones that
    cannot be built yet (a circle without a radius) with what is missing."""
    out, incomplete = {}, {}
    for z in zones:
        floor_m, ceiling_m = _limits(z)
        if z["corners"]:
            out[z["name"]] = m.polygon([m.point(a, b) for a, b in z["corners"]], floor_m, ceiling_m)
        elif z["radius_m"]:
            out[z["name"]] = m.circle(m.point(z["lat"], z["lon"]), z["radius_m"], floor_m, ceiling_m)
        elif floor_m is None and ceiling_m is None:
            out[z["name"]] = m.point(z["lat"], z["lon"])  # a position without an outline or limits is a point
        else:
            incomplete[z["name"]] = "altitude limits but no radius or corners"
    return out, incomplete


def zone_report(track, zones):
    """What the track did in every area zone: entered or not, each stay (first fix in, last fix in,
    seconds), time inside, the closest horizontal approach, and how far below the ceiling (or above
    the floor) the deepest fix was. Flags carry GPS altitude, fallback source and undecidable fixes.
    Points are not reported. Nothing is interpolated: a stay is bounded by fixes."""
    built, incomplete = shapes(zones)
    rows = []
    for z in zones:
        shape = built.get(z["name"])
        if z["name"] in incomplete:
            rows.append({"zone": z["name"], "kind": z["kind"], "entered": None, "flags": [f"incomplete: {incomplete[z['name']]}"]})
            continue
        if shape is None or "kind" not in shape:
            continue
        floor_m, ceiling_m = _limits(z)
        if not track:
            rows.append({"zone": z["name"], "kind": z["kind"], "entered": None, "flags": ["no track"]})
            continue
        within = m.inside(track, shape)
        stays = [(a, b, m.time_inside(m.between(track, a, b), shape)["seconds"]) for a, b in m.runs_inside(track, shape)]
        edges = [m.edge_distance(p, shape) for p in track if p.get("lat") is not None]
        row = {"zone": z["name"], "kind": z["kind"], "entered": bool(within),
               "stays": [{"entry": m.evidence(a), "exit": m.evidence(b), "seconds": s} for a, b, s in stays],
               "time_inside_s": sum(s for _, _, s in stays), "fixes_inside": len(within),
               "closest_horizontal_m": None if not edges else 0.0 if max(edges) >= 0 else -max(edges),
               "deepest_ft": None, "flags": list(within.notes)}
        if within:
            alts = [p["alt_m"] for p in within if p.get("alt_m") is not None]
            if alts and ceiling_m is not None:
                row["deepest_ft"] = round(m_to_ft(ceiling_m - min(alts)))
                row["flags"].append(f"lowest fix inside {round(m_to_ft(min(alts)))} ft, ceiling {z['ceiling_ft']} ft")
            elif alts and floor_m is not None:
                row["deepest_ft"] = round(m_to_ft(max(alts) - floor_m))
            if any(p.get("alt_source") == "gps" for p in within):
                row["flags"].append("some fixes inside carry GPS altitude, not barometric")
            if any(p.get("position_source") == "fallback" for p in within):
                row["flags"].append("some fixes inside were recorded on the fallback source")
        rows.append(row)
    return rows


def _main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("zones", help="zones.json in the format above")
    p.add_argument("paths", nargs="+", help="folder(s) or IGC file(s)")
    a = p.parse_args(argv)
    import flight_data  # noqa: E402
    zones = load_zones(a.zones)
    data = flight_data.load(a.paths)
    for w in data["warnings"]:
        print("! " + w)
    for rec in sorted(data["files"], key=lambda r: str(r["pilot"]).zfill(6)):
        for row in zone_report(rec["track"], zones):
            if row.get("entered"):
                stays = "; ".join(f"{s['entry'].get('time_local')}-{s['exit'].get('time_local')}" for s in row["stays"])
                print(f"pilot {rec['pilot']:>3} {row['zone']:<12} ENTERED {row['time_inside_s']:.0f} s ({stays})"
                      + (f", {row['deepest_ft']} ft below the ceiling" if row["deepest_ft"] is not None else "")
                      + ("  " + "; ".join(row["flags"]) if row["flags"] else ""))
            elif row.get("entered") is None:
                print(f"pilot {rec['pilot']:>3} {row['zone']:<12} ? {'; '.join(row['flags'])}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
