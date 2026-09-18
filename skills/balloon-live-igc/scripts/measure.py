#!/usr/bin/env python3
"""Measurement primitives for balloon competition tasks. Standard library only.

A task is: select the points that count, measure, choose. Compose these; do not re-derive
them. Everything works on the dicts flight_data.load() returns (track fixes, markers,
declarations) and on points made here. Distances are metres, altitudes metres, times UTC.

Points and shapes
    point(lat, lon, alt_m=None)                      point_utm(zone, easting, northing, alt_m=None, northern=True)
    point_from_digits("1234/4856,500", near, zone=None, digits="4/4", alt_unit="ft")
        `near` completes the digits: a point, a file record, or the loaded data (its flying area)
    circle(center, radius_m, floor_m=None, ceiling_m=None)       a cylinder when it has floor/ceiling
    polygon([points], floor_m=None, ceiling_m=None)              layer(floor_m, ceiling_m)
    contains(shape, point) -> True / False / None                edge_distance(point, shape) -> metres, negative outside

Select (list of points in, list of points out; chain by nesting. Slicing or adding the
results gives a plain list and drops the notes, so select with these functions.)
    between(points, start, end)   after(points, when)   before(points, when)   airborne(points)
    inside(points, shape)         outside(points, shape)    altitude_between(points, lo_m, hi_m, baro_only=False)
    `when` is an ISO time (no offset means UTC), a datetime, an epoch number, or any point/marker/declaration.

Measure (each returns a dict with the value, the points it rests on, and flags)
    distance(a, b, three_d=False)         closest(points, target, three_d=False)     farthest(...)
    length(points, three_d=False, max_gap_s=None)       length_inside(points, shape, three_d=False, max_gap_s=None)
    time_inside(points, shape, max_gap_s=None)          runs_inside(points, shape) -> [(entry, exit)]
    first_entry(points, shape)            last_exit(points, shape)            evidence(point)  point_flags(point, label)
    nearest_targets(point, {name: point or shape})  every target by distance, nearest first
    bearing(a, b, zone=None)              angle(a, b, c, zone=None)  at b     area(a, b, c, zone=None)  m2
    Bearings are GRID bearings. The grid is the zone of the first point (of b for angle)
    unless `zone` is given. Grid north differs between zones by several degrees near a
    boundary, so for direction tasks pass the competition's zone.

Rules, fixed here
- No interpolation. Entry is the first fix inside, exit the last fix inside; a segment is
  inside only when both its ends are.
- length() and time_inside() never bridge a gap: a removed fix, a pause in the log (longer
  than 3 logging intervals, at least 5 s), or in 3D a change of altitude source or a missing
  altitude. What was skipped is counted in the result; pauses and altitude gaps also raise
  a flag, because then the value is a minimum.
- 3D needs altitude at both ends; without it the value is None and the flag says why.
- A fix with no altitude is never inside or outside a shape that has a floor or ceiling;
  it is left out, counted in the notes, and flagged on anything measured inside that shape.
- A point exactly on the edge of a shape may fall either way.
- A point without a position gives a flagged result with no value, never an error.
- Flags travel: GPS altitude, fallback position source, a marker without a fix.
"""
import csv
import math
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flight_data import epoch, point_distance, reference_position, to_grid, with_utm  # noqa: E402
from geo import expand_declaration, mean_lon, utm_to_latlon, utm_zone  # noqa: E402


class Points(list):
    """A list of points that remembers what selections left out (`notes`)."""
    notes = ()


def _points(items, notes):
    out = Points(items)
    out.notes = tuple(notes)
    return out


def _notes(points):
    return list(points.notes) if isinstance(points, Points) else []


# ----------------------------------------------------------------------------- points and shapes

def point(lat, lon, alt_m=None):
    return with_utm({"lat": lat, "lon": lon, "alt_m": alt_m, "alt_source": "given" if alt_m is not None else None})


def point_utm(zone, easting, northing, alt_m=None, northern=True):
    return point(*utm_to_latlon(easting, northing, zone, northern), alt_m)


def point_from_digits(text, near, zone=None, digits="4/4", alt_unit="ft"):
    """A target given as shortened grid digits, completed to the candidate nearest `near`:
    a point, a file record (its track) or the loaded data (the flying area). `zone` is the
    grid the digits are on when the task or competition fixes one; otherwise the zone of
    `near`. Warnings are kept on the point (a zone boundary nearby, an ambiguous digit)."""
    ref = reference_position(near)
    if ref is None:
        raise ValueError("the digits need a position to be completed against: a point, a file record or the loaded data")
    r = expand_declaration(text, ref[0], ref[1], digits, alt_unit, zone)
    if r["lat"] is None:
        raise ValueError("; ".join(r["warnings"]) or "cannot read the digits")
    p = point(r["lat"], r["lon"], r["alt_m"])
    p.update(grid_zone=r["grid_zone"], grid_easting=r["grid_easting"], grid_northing=r["grid_northing"], warnings=r["warnings"])
    return p


def circle(center, radius_m, floor_m=None, ceiling_m=None):
    return {"kind": "circle", "center": center, "radius_m": radius_m, "floor_m": floor_m, "ceiling_m": ceiling_m}


def polygon(points, floor_m=None, ceiling_m=None):
    if len(points) < 3:
        raise ValueError("a polygon needs at least three points")
    zone = utm_zone(statistics.mean(p["lat"] for p in points), mean_lon(p["lon"] for p in points))
    northern = points[0]["lat"] >= 0
    return {"kind": "polygon", "zone": zone, "northern": northern, "floor_m": floor_m, "ceiling_m": ceiling_m,
            "xy": [to_grid(p, zone, northern) for p in points]}


def layer(floor_m=None, ceiling_m=None):
    return {"kind": "layer", "floor_m": floor_m, "ceiling_m": ceiling_m}


def _limited(shape):
    return shape.get("floor_m") is not None or shape.get("ceiling_m") is not None


def contains(shape, p):
    """True or False; None when it cannot be decided: the point has no position, or the
    shape has altitude limits and the point has no altitude."""
    if p.get("lat") is None:
        return None
    if shape["kind"] == "circle" and point_distance(shape["center"], p) > shape["radius_m"]:
        return False
    if shape["kind"] == "polygon":
        x, y = to_grid(p, shape["zone"], shape["northern"])
        hit, xy = False, shape["xy"]
        for (x1, y1), (x2, y2) in zip(xy, xy[1:] + xy[:1]):
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
                hit = not hit
        if not hit:
            return False
    if _limited(shape):
        if p.get("alt_m") is None:
            return None
        lo, hi = shape.get("floor_m"), shape.get("ceiling_m")
        return (lo is None or p["alt_m"] >= lo) and (hi is None or p["alt_m"] <= hi)
    return True


def edge_distance(p, shape):
    """Horizontal distance from the point to the shape's edge: positive inside, negative outside,
    None for a layer or a point without position. Altitude limits are not part of it."""
    if p.get("lat") is None or shape["kind"] == "layer":
        return None
    if shape["kind"] == "circle":
        return shape["radius_m"] - point_distance(shape["center"], p)
    x, y = to_grid(p, shape["zone"], shape["northern"])
    best = None
    xy = shape["xy"]
    for (x1, y1), (x2, y2) in zip(xy, xy[1:] + xy[:1]):
        dx, dy = x2 - x1, y2 - y1
        t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
        d = math.hypot(x - (x1 + t * dx), y - (y1 + t * dy))
        best = d if best is None else min(best, d)
    inside = contains(dict(shape, floor_m=None, ceiling_m=None), p)
    return best if inside else -best


# ----------------------------------------------------------------------------- select

def _when(value):
    if isinstance(value, dict):
        return value["t"] if "t" in value else epoch(value["time_utc"])
    return epoch(value)


def _time(p):
    return p["t"] if "t" in p else epoch(p["time_utc"])


def between(points, start, end):
    t0, t1 = _when(start), _when(end)
    return _points([p for p in points if t0 <= _time(p) <= t1], _notes(points))


def after(points, when):
    t0 = _when(when)
    return _points([p for p in points if _time(p) >= t0], _notes(points))


def before(points, when):
    t1 = _when(when)
    return _points([p for p in points if _time(p) <= t1], _notes(points))


def airborne(points):
    return _points([p for p in points if p.get("phase") == "airborne"], _notes(points))


def _split(points, shape):
    verdicts = [contains(shape, p) for p in points]
    unknown = sum(v is None for v in verdicts)
    notes = _notes(points) + ([f"{unknown} fixes without altitude or position could not be placed against the shape"] if unknown else [])
    return verdicts, notes


def _undecided(points, shape):
    n = sum(contains(shape, p) is None for p in points)
    return [f"{n} fixes could not be placed against the shape; the value is a minimum"] if n else []


def inside(points, shape):
    verdicts, notes = _split(points, shape)
    return _points([p for p, v in zip(points, verdicts) if v], notes)


def outside(points, shape):
    verdicts, notes = _split(points, shape)
    return _points([p for p, v in zip(points, verdicts) if v is False], notes)


def altitude_between(points, lo_m=None, hi_m=None, baro_only=False):
    kept, missing, gps = [], 0, 0
    for p in points:
        if p.get("alt_m") is None:
            missing += 1
        elif baro_only and p.get("alt_source") == "gps":
            gps += 1
        elif (lo_m is None or p["alt_m"] >= lo_m) and (hi_m is None or p["alt_m"] <= hi_m):
            kept.append(p)
    notes = _notes(points)
    notes += [f"{missing} fixes without altitude left out"] if missing else []
    notes += [f"{gps} fixes with GPS altitude left out"] if gps else []
    return _points(kept, notes)


# ----------------------------------------------------------------------------- measure

def evidence(p):
    """What a scorer needs to check a point."""
    keys = ("time_utc", "time_local", "utm", "lat", "lon", "alt_m", "alt_source", "position_source", "marker", "number", "pilot", "file")
    return {k: p[k] for k in keys if p.get(k) is not None}


def point_flags(p, label, three_d=False):
    """What weakens a point: no position, no fix, fallback source, GPS altitude (in 3D)."""
    out = []
    if p.get("lat") is None:
        out.append(f"{label}: no position")
    if p.get("fix") is False:
        out.append(f"{label}: recorded without a GPS fix")
    if p.get("position_source") == "fallback":
        out.append(f"{label}: recorded on the fallback position source")
    if three_d and p.get("alt_source") == "gps":
        out.append(f"{label}: GPS altitude, not barometric")
    return out


def distance(a, b, three_d=False):
    res = {"distance_m": None, "horizontal_m": None, "vertical_m": None, "three_d": three_d,
           "a": evidence(a), "b": evidence(b), "flags": point_flags(a, "a", three_d) + point_flags(b, "b", three_d)}
    if a.get("lat") is None or b.get("lat") is None:
        return res
    res["horizontal_m"] = res["distance_m"] = point_distance(a, b)
    if a.get("alt_m") is not None and b.get("alt_m") is not None:
        res["vertical_m"] = b["alt_m"] - a["alt_m"]
    if three_d:
        if res["vertical_m"] is None:
            res["distance_m"] = None
            res["flags"].append("3D not possible: a point has no altitude")
        else:
            res["distance_m"] = math.hypot(res["horizontal_m"], res["vertical_m"])
    return res


def _extreme(points, target, three_d, pick):
    notes = _notes(points)
    if target.get("lat") is None or (three_d and target.get("alt_m") is None):
        return {"distance_m": None, "three_d": three_d, "point": None, "target": evidence(target),
                "flags": ["target has no " + ("position" if target.get("lat") is None else "altitude; 3D not possible")], "notes": notes}
    usable = [p for p in points if p.get("lat") is not None and (not three_d or p.get("alt_m") is not None)]
    if len(usable) < len(points):
        notes.append(f"{len(points) - len(usable)} points without {'altitude' if three_d else 'position'} left out")
    if not usable:
        return {"distance_m": None, "three_d": three_d, "point": None, "target": evidence(target),
                "flags": ["no usable points"], "notes": notes}
    best = pick(usable, key=lambda p: distance(p, target, three_d)["distance_m"])
    res = distance(best, target, three_d)
    return {"distance_m": res["distance_m"], "horizontal_m": res["horizontal_m"], "vertical_m": res["vertical_m"],
            "three_d": three_d, "point": res["a"], "target": res["b"],
            "flags": [f.replace("a:", "point:").replace("b:", "target:") for f in res["flags"]], "notes": notes}


def closest(points, target, three_d=False):
    return _extreme(points, target, three_d, min)


def farthest(points, target, three_d=False):
    return _extreme(points, target, three_d, max)


def _segments(points, three_d=False, max_gap_s=None):
    """Pairs of truly consecutive fixes, and why the other neighbours were not used."""
    pairs = list(zip(points, points[1:]))
    known = [p["interval_s"] for p in points if p.get("interval_s")]  # the file's logging interval, set by flight_data
    steps = [_time(b) - _time(a) for a, b in pairs if a.get("i") is None or b.get("i") == a["i"] + 1]
    interval = statistics.median(known) if known else min(statistics.median(steps), 20.0) if steps else 1.0
    limit = max_gap_s if max_gap_s is not None else max(5.0, 3 * interval)
    good, skipped = [], {"removed fixes": 0, "pause in the log": 0, "altitude source changes or is missing": 0}
    for a, b in pairs:
        if a.get("i") is not None and b.get("i") != a["i"] + 1:
            skipped["removed fixes"] += 1
        elif _time(b) - _time(a) > limit:
            skipped["pause in the log"] += 1
        elif three_d and (a.get("alt_m") is None or b.get("alt_m") is None or a.get("alt_source") != b.get("alt_source")):
            skipped["altitude source changes or is missing"] += 1
        else:
            good.append((a, b))
    return good, {k: v for k, v in skipped.items() if v}


def length(points, three_d=False, max_gap_s=None):
    good, skipped = _segments(points, three_d, max_gap_s)
    total = sum(math.hypot(point_distance(a, b), b["alt_m"] - a["alt_m"]) if three_d else point_distance(a, b) for a, b in good)
    data_gaps = {k: v for k, v in skipped.items() if k != "removed fixes"}  # removed fixes are the caller's own selection
    flags = [f"not bridged: {data_gaps}; the length is a minimum"] if data_gaps else []
    if len(points) > 1 and not good:
        flags.append("no two neighbouring fixes among these points; nothing could be measured")
    on_gps = sum(1 for a, b in good if a.get("alt_source") == "gps") if three_d else 0
    if on_gps:
        flags.append(f"{on_gps} segments use GPS altitude, not barometric")
    return {"length_m": total, "three_d": three_d, "segments": len(good), "not_bridged": skipped,
            "from": evidence(points[0]) if points else None, "to": evidence(points[-1]) if points else None,
            "flags": flags, "notes": _notes(points)}


def length_inside(points, shape, three_d=False, max_gap_s=None):
    """Distance flown inside: the sum of the segments whose two ends are both inside."""
    res = length(inside(points, shape), three_d, max_gap_s)
    res["flags"] += _undecided(points, shape)
    return res


def runs_inside(points, shape):
    """[(entry, exit)]: the first and last fix of every unbroken stay inside the shape."""
    runs, start, last, seen = [], None, None, None
    for p in points:
        verdict = contains(shape, p)
        jumped = seen is not None and p.get("i") is not None and p["i"] != seen + 1  # fixes were removed in between
        if start is not None and (verdict is False or jumped):
            runs.append((start, last))
            start = None
        if verdict:
            start, last = (p if start is None else start), p
        seen = p.get("i")  # a fix that cannot be placed neither ends a stay nor starts one
    if start is not None:
        runs.append((start, last))
    return runs


def first_entry(points, shape):
    runs = runs_inside(points, shape)
    return {"point": evidence(runs[0][0]) if runs else None, "stays": len(runs),
            "flags": ([] if runs else ["never inside"]) + _undecided(points, shape), "notes": _split(points, shape)[1]}


def last_exit(points, shape):
    runs = runs_inside(points, shape)
    flags = [] if runs else ["never inside"]
    if runs and points and runs[-1][1] is points[-1]:
        flags.append("still inside at the last point")
    return {"point": evidence(runs[-1][1]) if runs else None, "stays": len(runs), "flags": flags + _undecided(points, shape),
            "notes": _split(points, shape)[1]}


def time_inside(points, shape, max_gap_s=None):
    good, skipped = _segments(inside(points, shape), False, max_gap_s)
    pauses = {k: v for k, v in skipped.items() if k != "removed fixes"}
    flags = ([f"not bridged: {pauses}; the time is a minimum"] if pauses else []) + _undecided(points, shape)
    return {"seconds": sum(_time(b) - _time(a) for a, b in good), "stays": len(runs_inside(points, shape)),
            "not_bridged": pauses, "flags": flags, "notes": _split(points, shape)[1]}


def nearest_targets(p, targets):
    """Every named target ordered by horizontal distance from the point, nearest first. A shape's
    distance is to its edge (0 when inside). For the note "this mark is closer to another goal"."""
    if p.get("lat") is None:
        return {"ranked": [], "flags": ["point: no position"]}
    ranked = []
    for name, target in targets.items():
        if "kind" in target:
            edge = edge_distance(p, target)
            d = None if edge is None else max(0.0, -edge)
        else:
            d = point_distance(p, target) if target.get("lat") is not None else None
        ranked.append({"name": name, "distance_m": d})
    ranked.sort(key=lambda r: (r["distance_m"] is None, r["distance_m"]))
    return {"ranked": ranked, "nearest": ranked[0]["name"] if ranked and ranked[0]["distance_m"] is not None else None,
            "flags": [f"{r['name']}: no position" for r in ranked if r["distance_m"] is None]}


def _on_grid(points, zone):
    """(x, y) of every point on one grid: `zone`, else the zone of the first point. None without positions."""
    if any(p.get("lat") is None for p in points):
        return None
    zone = zone or utm_zone(points[0]["lat"], points[0]["lon"])
    northern = points[0]["lat"] >= 0
    return [to_grid(p, zone, northern) for p in points]


def bearing(a, b, zone=None):
    """Grid bearing from a to b in degrees, 0 = grid north, clockwise. The grid is a's zone unless `zone` is given."""
    xy = _on_grid([a, b], zone)
    if xy is None or xy[0] == xy[1]:
        return None
    (x1, y1), (x2, y2) = xy
    return math.degrees(math.atan2(x2 - x1, y2 - y1)) % 360


def angle(a, b, c, zone=None):
    """The angle at b between the legs b-a and b-c, 0 to 180 degrees. None when a leg has no length."""
    xy = _on_grid([b, a, c], zone)
    if xy is None or xy[0] in (xy[1], xy[2]):
        return None
    (xb, yb), (xa, ya), (xc, yc) = xy
    turn = math.degrees(math.atan2(xc - xb, yc - yb) - math.atan2(xa - xb, ya - yb)) % 360
    return min(turn, 360 - turn)


def area(a, b, c, zone=None):
    """Area of the triangle in square metres."""
    xy = _on_grid([a, b, c], zone)
    if xy is None:
        return None
    (xa, ya), (xb, yb), (xc, yc) = xy
    return abs((xb - xa) * (yc - ya) - (xc - xa) * (yb - ya)) / 2


# ----------------------------------------------------------------------------- results

def _tidy(key, value):
    """Floats at a precision that means something: lat/lon 6 decimals, metres 2, the rest 3."""
    if not isinstance(value, float) or value != value:
        return value
    if key.endswith(("lat", "lon")):
        return round(value, 6)
    if key.endswith(("_m", "_m2", "_km")):
        return round(value, 2)
    return round(value, 3)


def write_results(path, rows):
    """One CSV row per pilot. Nested dicts become dotted columns; lists are joined with '; ';
    floats are rounded to what a scorer can use (see _tidy)."""
    flat = []
    for row in rows:
        out = {}

        def walk(prefix, value):
            if isinstance(value, dict):
                for k, v in value.items():
                    walk(f"{prefix}.{k}" if prefix else str(k), v)
            elif isinstance(value, (list, tuple)):
                out[prefix] = "; ".join(str(_tidy(prefix, v)) for v in value)
            else:
                out[prefix] = _tidy(prefix, value)

        walk("", row)
        flat.append(out)
    columns = list(dict.fromkeys(k for r in flat for k in r))
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns)
        w.writeheader()
        w.writerows(flat)
