#!/usr/bin/env python3
"""Competition rules as inputs, and the checks that apply them. Standard library only.

Nothing here decides a rule. Every parameter has a default and a `source`; the agent
fills the values from the competition's rule book and task sheet, the scorer confirms,
and every result says which source each value came from.

    r = rules(separation_altitude_ft=600, primary_recording="first_started", sources={...})
    save(r, "competition-rules.json")   load_rules("competition-rules.json")   describe(r)
    override(r, sources={...}, mma_radius_m=100)              a task's own values on top of the competition's
    flight_data.load(paths, **load_options(r))                the grid, primary recording and declaration format

Checks: {"check": name, "passed": True/False/None, "evidence": text, "severity": ...}
    severity "invalidates" (default): a failed check gives No Result
             "penalty":  a failed check is an infringement; the result stands and the row names it
             "note":     information for the scorer, never fails
    check(name, passed, evidence, severity="invalidates")     make one
    check_fix(mark) check_counts(mark) check_period(mark, record, r) check_area(mark, shape, name, severity=)
    check_order(marks, severity="penalty") check_limit(name, value, min_value=, max_value=, unit=, severity=)
    nearest_note(mark, {name: goal or shape}, expected)      note: the mark is closer to another task's goal

Picking what counts (markers and declarations are the dicts flight_data gives)
    mark_for(markers, number, r, record=None)                 the mark that counts, with checks
    declaration_for(declarations, number, r, valid=None)      the declaration that counts; `valid(decl)` gives
                                                              its validity checks, so "last valid" is expressible
    declaration_origin(decl, record, r)                       where the pilot was when declaring (a point)
    marks_overview(markers, {name: goal or shape})            every mark with its nearest target, for the scorer

Measuring under the rules
    goal_distance(mark, goal, r, declared=None)               separation rule, or straight 3D for a declared goal
    floor(measurement, r)                                     MMA rules on an electronic result: the floor, and the note
                                                              that a physical mark inside the MMA takes precedence
    measurement(value, unit, points=(), flags=())             wrap any other value (an area, an angle) as a measurement
    judge(measurement, checks)                                -> result or No Result, and the infringements
    result_row(pilot, record, measurement, checks, precision=None)   one flat row per pilot
    apply_physical(rows, physical, r)                         the officials' physical measurements replace electronic results
    load_physical("task6-physical.csv")                       rows of pilot,distance (decimals) or pilot,lost
"""
import csv
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import measure as m  # noqa: E402
from flight_data import local_datetime, local_time  # noqa: E402
from geo import ft_to_m, m_to_ft  # noqa: E402

# name: (default, meaning). Find each in the rule book by its subject, wherever that book puts it.
PARAMETERS = {
    "separation_altitude_ft": (None, "2D/3D scoring of set goals: marks at or below this altitude score 2D, above it 3D "
                                     "to the point at this altitude over the goal. None: 2D, vertical difference reported apart"),
    "declared_goal_method": ("3d_to_goal", "scoring of a goal the pilot declared with an altitude: 3d_to_goal (straight-line "
                                           "distance to the declared point) or separation (the rule above, as for set goals)"),
    "primary_recording": ("longest", "which of a pilot's flight files counts: longest or first_started; a scorer's "
                                     "instruction goes to load(prefer=)"),
    "mark_reuse": ("first", "when a mark number was dropped more than once: first or last"),
    "declaration_reuse": ("last", "when a goal was declared more than once: first or last (of the valid ones)"),
    "declaration_digits": ("4/4", "digits of a declared or given goal, e.g. 4/4, 5/4"),
    "declaration_alt_unit": ("ft", "unit of declared altitudes: ft or m"),
    "declaration_before_takeoff": ("takeoff", "a declaration made before take-off is taken to be made from: "
                                              "takeoff (the take-off point) or as_declared (where the phone was)"),
    "grid_zone": (None, "UTM zone digit references are on; None: the zone where each was declared"),
    "scoring_period_end": (None, "local time HH:MM after which a mark does not count; None: no limit"),
    "sunset": (None, "local time HH:MM; scoring after it is prohibited when the rule book says so"),
    "marker_mode": ("both", "what the task is scored by: both (a physical marker inside the MMA takes precedence, "
                             "electronic mark otherwise), electronic (no physical marker, no MMA floor) or physical"),
    "mma_radius_m": (None, "marker measuring area radius around the goal or target; physical marks inside it take precedence"),
    "mma_worst_m": (None, "the worst possible result inside the MMA, the floor for electronic results; None: the MMA radius"),
    "electronic_floor": (False, "an electronic result cannot be better than the worst result possible inside the MMA "
                                "(off when no target was displayed at the coordinates)"),
}
SEVERITIES = ("invalidates", "penalty", "note")


def rules(sources=None, **values):
    """A rule set: every parameter with its value and where it came from ('default' unless told)."""
    unknown = sorted(set(values) - set(PARAMETERS))
    if unknown:
        raise ValueError(f"unknown parameters {unknown}; known: {sorted(PARAMETERS)}")
    out = {}
    for name, (default, meaning) in PARAMETERS.items():
        given = name in values
        out[name] = {"value": values[name] if given else default, "meaning": meaning,
                     "source": (sources or {}).get(name, "given" if given else "default")}
    return out


def value(r, name):
    return r[name]["value"]


def save(r, path):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({k: {"value": v["value"], "source": v["source"]} for k, v in r.items()}, fh, indent=1)


def load_rules(path):
    with open(path, encoding="utf-8") as fh:
        stored = json.load(fh)
    return rules(sources={k: v.get("source", "file") for k, v in stored.items()}, **{k: v["value"] for k, v in stored.items()})


def override(r, sources=None, **values):
    """A new rule set: `r` with these values replaced. Their source is what `sources` names, else 'task sheet'."""
    unknown = sorted(set(values) - set(PARAMETERS))
    if unknown:
        raise ValueError(f"unknown parameters {unknown}; known: {sorted(PARAMETERS)}")
    out = {k: dict(v) for k, v in r.items()}
    for name, given in values.items():
        out[name] = dict(out[name], value=given, source=(sources or {}).get(name, "task sheet"))
    return out


def load_options(r):
    """Keyword arguments for flight_data.load() from the rules: grid zone, primary recording, and the declaration
    format and unit only when the rules gave them (a default never overrides what a file's header says)."""
    out = {"competition_zone": value(r, "grid_zone"), "primary": value(r, "primary_recording")}
    for name, key in (("declaration_digits", "decl_digits"), ("declaration_alt_unit", "decl_alt_unit")):
        if r[name]["source"] != "default":
            out[key] = value(r, name)
    return out


def describe(r):
    """One line per parameter for the report, defaults last."""
    lines = [f"{k}: {v['value']!r} ({v['source']})" for k, v in r.items() if v["source"] != "default"]
    lines += [f"{k}: {v['value']!r} (default)" for k, v in r.items() if v["source"] == "default"]
    return lines


# ----------------------------------------------------------------------------- checks

def check(name, passed, evidence, severity="invalidates"):
    if severity not in SEVERITIES:
        raise ValueError(f"severity must be one of {SEVERITIES}")
    return {"check": name, "passed": passed, "evidence": evidence, "severity": severity}


def _severity(c):
    return c.get("severity", "invalidates")


def _local_cutoff(record, hhmm, when_utc_epoch):
    """The given local HH:MM on the same local day as the event, as an epoch time."""
    h, mi = (int(x) for x in hhmm.split(":"))
    return local_datetime(when_utc_epoch, record).replace(hour=h, minute=mi, second=0, microsecond=0).timestamp()


def check_period(mark, record, r):
    """Within the scoring period and before sunset, as far as the rules give them."""
    out = []
    for name, key in (("scoring period", "scoring_period_end"), ("sunset", "sunset")):
        hhmm = value(r, key)
        if hhmm is None:
            continue
        end = _local_cutoff(record, hhmm, mark["t"])
        out.append(check(name, mark["t"] <= end, f"mark at {mark['time_local']} local, {name} {hhmm} ({r[key]['source']})"))
    return out


def check_fix(mark):
    return check("GPS fix", bool(mark.get("fix")) and mark.get("lat") is not None,
                 f"fix {mark.get('fix')}, satellites {mark.get('satellites')}, quality from {mark.get('quality_from')}")


def check_counts(mark):
    return check("counting file", bool(mark.get("counts")),
                 f"file {mark.get('file')} ({mark.get('file_status')}){'' if mark.get('counts') else ' does not count for this pilot'}")


def check_area(mark, shape, name="scoring area", severity="invalidates"):
    """Inside the shape, with the margin to its edge as evidence (None when it cannot be decided)."""
    verdict = m.contains(shape, mark)
    edge = m.edge_distance(mark, shape) if mark.get("lat") is not None else None
    where = "inside" if verdict else "outside" if verdict is False else "undecidable"
    margin = "" if edge is None else f", {abs(edge):.1f} m from the edge"
    near = edge is not None and abs(edge) < 3.0
    return check(name, verdict, f"{where}{margin}" + (" (within GPS accuracy of the edge)" if near else ""), severity)


def check_order(marks, severity="penalty"):
    """Marks numbered as on the task sheet must have been dropped in that order."""
    used = sorted((k for k in marks if k.get("t") is not None), key=lambda k: k["t"])
    numbers = [k["marker"] for k in used]
    return check("in order", numbers == sorted(numbers),
                 "drops in time order: " + ", ".join(f"{k['marker']} at {k['time_local']}" for k in used), severity)


def check_limit(name, measured, min_value=None, max_value=None, unit="m", severity="penalty"):
    """A minimum and/or maximum on a measured value (a distance between points, an altitude difference)."""
    if measured is None:
        return check(name, None, "could not be measured", severity)
    limits = " ".join(s for s in (f"min {min_value:g} {unit}" if min_value is not None else "",
                                  f"max {max_value:g} {unit}" if max_value is not None else "") if s)
    ok = (min_value is None or measured >= min_value) and (max_value is None or measured <= max_value)
    return check(name, ok, f"{measured:.1f} {unit} ({limits})" + ("" if ok else " OUTSIDE THE LIMIT"), severity)


def nearest_note(mark, targets, expected):
    """A note that tells the scorer when the mark lies closer to another target (another task's
    goal, a scoring area) than to the one it is measured against."""
    ranked = m.nearest_targets(mark, targets)
    if not ranked["ranked"] or ranked["nearest"] is None:
        return check("nearest target", True, "; ".join(ranked["flags"]) or "no targets", "note")
    listed = ", ".join(f"{t['name']} {t['distance_m']:.0f} m" for t in ranked["ranked"] if t["distance_m"] is not None)
    if ranked["nearest"] == expected:
        return check("nearest target", True, f"{expected} is the nearest ({listed})", "note")
    return check("nearest target", True,
                 f"CLOSER TO {ranked['nearest']} than to {expected} ({listed}); check which task this mark was for", "note")


# ----------------------------------------------------------------------------- picking what counts

def mark_for(markers, number, r, record=None):
    """The pilot's mark `number` that counts under the rules, and the checks made on the way.

    Returns (mark or None, checks). Among several drops of the same number the first or the
    last is taken (mark_reuse); the others are named in the evidence.
    """
    drops = sorted((k for k in markers if k.get("marker") == number), key=lambda k: k["t"])
    if not drops:
        return None, [check(f"mark {number} exists", False, "no drop with this number")]
    reuse = value(r, "mark_reuse")
    chosen = drops[0] if reuse == "first" else drops[-1]
    checks = []
    if len(drops) > 1:
        checks.append(check(f"mark {number} dropped once", True,
                            f"dropped {len(drops)} times ({', '.join(k['time_local'] for k in drops)}); "
                            f"the {reuse} one counts ({r['mark_reuse']['source']})", "note"))
    checks.append(check_fix(chosen))
    checks.append(check_counts(chosen))
    if record is not None:
        checks += check_period(chosen, record, r)
    return chosen, checks


def declaration_for(declarations, number, r, valid=None):
    """The declaration that counts under the rules (declaration_reuse), and the checks made on the way.

    `number` None takes every declaration in the file as declarations of the same goal. `valid(decl)`
    returns that declaration's validity checks; a declaration is valid when none of its invalidating
    checks failed, and only valid ones are chosen from while any exists. The chosen declaration's
    validity checks are returned with the others, so the row shows why it counts.
    """
    made = sorted((d for d in declarations if number is None or d.get("number") == number), key=lambda d: d["t"])
    if not made:
        return None, [check("declaration exists", False, "no declaration" + ("" if number is None else f" number {number}") + " in the file")]
    judged = [(d, list(valid(d)) if valid else []) for d in made]
    usable = [(d, cs) for d, cs in judged if not any(c["passed"] is False and _severity(c) == "invalidates" for c in cs)]
    pool = usable or judged
    reuse = value(r, "declaration_reuse")
    chosen, own = pool[0] if reuse == "first" else pool[-1]
    checks = []
    if len(made) > 1:
        names = ", ".join(f"{d['time_local']} {d.get('text')!r}" + ("" if (d, cs) in usable else " INVALID") for d, cs in judged)
        checks.append(check("declared once", True, f"declared {len(made)} times ({names}); the {reuse}"
                            f"{' valid' if valid else ''} one counts ({r['declaration_reuse']['source']})"
                            + ("" if usable else "; NONE VALID, shown for the measurement"), "note"))
    readable = chosen.get("lat") is not None
    where = f"read as {chosen.get('utm')} ({chosen.get('digits')} digits on zone {chosen.get('grid_zone')})" if readable else "not resolved"
    checks.append(check("declaration read", readable,
                        f"{chosen.get('text')!r} {where}" + "".join("; " + w for w in chosen.get("warnings", []))))
    checks.append(check_counts(chosen))
    return chosen, checks + own


def declaration_origin(decl, record, r):
    """Where the pilot was when declaring: the declaration's own position, or the take-off point for a
    declaration made before take-off when the rules say so. Returns (point, how)."""
    takeoff = (record or {}).get("takeoff")
    if takeoff and takeoff.get("t") is not None and decl["t"] < takeoff["t"] and value(r, "declaration_before_takeoff") == "takeoff":
        return takeoff, f"declared {decl['time_local']}, before take-off {local_time(takeoff['t'], record)}: taken from the take-off point ({r['declaration_before_takeoff']['source']})"
    origin = decl.get("declared_from")
    if not origin or origin.get("lat") is None:
        return None, "no position at the time of the declaration"
    return origin, f"declared at {origin['utm']} ({origin['from']})"


def marks_overview(markers, targets):
    """One line per mark with its nearest target, so a mark dropped for another task stands out."""
    out = []
    for k in sorted(markers, key=lambda k: k["t"]):
        ranked = m.nearest_targets(k, targets)
        out.append({"marker": k["marker"], "time_local": k["time_local"], "utm": k.get("utm"),
                    "alt_ft": None if k.get("alt_m") is None else round(m_to_ft(k["alt_m"])), "nearest": ranked["nearest"],
                    "distances": "; ".join(f"{t['name']} {t['distance_m']:.0f} m" for t in ranked["ranked"] if t["distance_m"] is not None)
                    or "; ".join(ranked["flags"])})
    return out


# ----------------------------------------------------------------------------- measuring under the rules

def goal_distance(mark, goal, r, declared=None):
    """Distance from an electronic mark to a goal.

    A set goal follows the separation-altitude rule: 2D when the mark is at or below it, else 3D to
    the point at that altitude above the goal; without a separation altitude 2D, with the vertical
    difference reported apart. A goal the pilot declared with an altitude (`declared`, taken from the
    goal when not given) follows declared_goal_method: straight 3D distance to the declared point, or
    the same separation rule. The other method's value is kept in `alternative`.
    """
    if declared is None:
        declared = bool(goal.get("declared"))
    sep_ft = value(r, "separation_altitude_ft")
    flat = m.distance(mark, goal)
    res = {"distance_m": flat["distance_m"], "unit": "m", "method": "2D", "horizontal_m": flat["horizontal_m"],
           "vertical_m": None, "mark_alt_m": mark.get("alt_m"), "mark_alt_source": mark.get("alt_source"),
           "goal_alt_m": goal.get("alt_m"), "reference_alt_m": None, "mark": flat["a"], "goal": flat["b"],
           "flags": list(flat["flags"]), "alternative": None,
           "rule": f"separation altitude {sep_ft} ft ({r['separation_altitude_ft']['source']})" if sep_ft is not None
                   else "2D, vertical reported apart (default)"}
    if flat["distance_m"] is None:
        return res
    alt = mark.get("alt_m")
    if mark.get("alt_source") == "gps":
        res["flags"].append("mark altitude is GPS, not barometric")
    straight = None
    if goal.get("alt_m") is not None and alt is not None:
        straight = {"method": "3D to the declared point", "distance_m": math.hypot(flat["distance_m"], alt - goal["alt_m"]),
                    "vertical_m": alt - goal["alt_m"]}
    if sep_ft is None:
        if alt is not None and goal.get("alt_m") is not None:
            res["vertical_m"] = alt - goal["alt_m"]
    else:
        res["reference_alt_m"] = sep = ft_to_m(sep_ft)
        if alt is None:
            res["flags"].append("mark has no altitude; 2D/3D rule could not be applied, 2D shown")
        elif alt > sep:
            res.update(method="3D", vertical_m=alt - sep, distance_m=math.hypot(flat["distance_m"], alt - sep))
        else:
            res["vertical_m"] = alt - sep
    if declared and value(r, "declared_goal_method") == "3d_to_goal":
        res["alternative"] = {"method": res["method"] + " (separation rule)", "distance_m": res["distance_m"]}
        res["rule"] = f"declared goal, 3D to the declared point ({r['declared_goal_method']['source']})"
        if straight is None:
            res["flags"].append("declared goal has no altitude, or the mark none; separation rule shown instead")
        else:
            res.update(method=straight["method"], distance_m=straight["distance_m"], vertical_m=straight["vertical_m"])
    elif declared and straight is not None:
        res["alternative"] = straight
    return res


def floor(measurement, r):
    """The MMA rules on an electronic result. In a task scored by both kinds of mark the result is
    provisional, since a valid physical mark inside the MMA takes precedence, and with the floor rule
    it cannot be better than the worst result possible inside the MMA. Electronic-only tasks are
    left alone."""
    if value(r, "marker_mode") != "both" or measurement.get("distance_m") is None:
        return measurement
    worst = value(r, "mma_worst_m") if value(r, "mma_worst_m") is not None else value(r, "mma_radius_m")
    measurement = dict(measurement, flags=measurement["flags"] + ["PROVISIONAL: a valid physical mark inside the MMA takes precedence"])
    if value(r, "electronic_floor") and worst is not None and measurement["distance_m"] < worst:
        measurement.update(distance_m=float(worst), measured_m=measurement["distance_m"])
        measurement["flags"].append(f"raised to the worst result possible in the MMA, {worst:g} m ({r['electronic_floor']['source']})")
    return measurement


def measurement(value_, unit, points=(), flags=(), **extra):
    """Any measured value as a measurement dict: an area in m2, an angle in deg, a time in s."""
    out = {"value": value_, "unit": unit, "points": [m.evidence(p) for p in points], "flags": list(flags)}
    for p, label in zip(points, "abcdefgh"):
        out["flags"] += m.point_flags(p, label)
    out.update(extra)
    return out


def _value(measurement):
    if measurement is None:
        return None, None
    if "value" in measurement:
        return measurement["value"], measurement.get("unit")
    return measurement.get("distance_m"), measurement.get("unit", "m")


def judge(measurement, checks):
    """measurement + checks -> the row a scorer sees: measurement, every check, the result or why not,
    and the infringements (failed penalty checks) that leave the result standing."""
    failed = [c["check"] for c in checks if c["passed"] is False and _severity(c) == "invalidates"]
    undecided = [c["check"] for c in checks if c["passed"] is None and _severity(c) == "invalidates"]
    infringements = [c["check"] for c in checks if c["passed"] is False and _severity(c) == "penalty"]
    val, unit = _value(measurement)
    ok = not failed and not undecided and val is not None
    return {"measurement": measurement, "checks": checks, "result": val if ok else None, "unit": unit,
            "infringements": infringements,
            "no_result": None if ok else ("no measurement" if val is None else
                                          "; ".join(failed + [f"{u} (undecidable)" for u in undecided]))}


def result_row(pilot, record, measurement, checks, precision=None):
    """One row per pilot for write_results, with the evidence flattened enough to read. `precision`
    rounds the result to that many decimals (the rule book's reporting precision) and keeps the
    exact value beside it."""
    verdict = judge(measurement, checks)
    row = {"pilot": pilot, "file": record["file"] if record else None, "result": verdict["result"], "unit": verdict["unit"],
           "result_from": None if not measurement else "electronic mark" if measurement.get("mark") else "track points",
           "no_result": verdict["no_result"], "infringements": "; ".join(verdict["infringements"])}
    if precision is not None and row["result"] is not None:
        row["result_exact"] = row["result"]
        row["result"] = round(row["result"], precision)
    if measurement:
        mark = measurement.get("mark") or (measurement.get("points") or [{}])[0]
        row.update(method=measurement.get("method"), horizontal_m=measurement.get("horizontal_m"),
                   vertical_m=measurement.get("vertical_m"),
                   mark_alt_ft=None if measurement.get("mark_alt_m") is None else round(m_to_ft(measurement["mark_alt_m"])),
                   mark=mark.get("utm"), mark_time=mark.get("time_local") or mark.get("time_utc"),
                   alternative=None if not measurement.get("alternative") else
                   f"{measurement['alternative']['distance_m']:.1f} m {measurement['alternative']['method']}",
                   flags="; ".join(measurement.get("flags", [])))
    words = {"invalidates": ("ok", "FAIL"), "penalty": ("ok", "INFRINGED"), "note": ("note", "note")}
    row["checks"] = "; ".join(f"{c['check']}: {words[_severity(c)][0] if c['passed'] else '?' if c['passed'] is None else words[_severity(c)][1]} "
                              f"({c['evidence']})" for c in checks)
    return row


# ----------------------------------------------------------------------------- physical marks

def load_physical(path):
    """{pilot: distance in metres, or "lost"} from a CSV of the officials' measurements: pilot,distance
    (decimals as measured inside the MMA) or pilot,lost for a marker not found."""
    out = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.reader(fh):
            if len(row) < 2 or not row[0].strip().isdigit():
                continue
            text = row[1].strip().lower()
            out[int(row[0])] = "lost" if text == "lost" else float(text)
    return out


def apply_physical(rows, physical, r):
    """Merge the officials' physical measurements into result rows (result_row output). A physical
    distance replaces the electronic result and is named as the source; a lost marker keeps the
    electronic result and says so; everyone else keeps the electronic result. Returns new rows."""
    if value(r, "marker_mode") == "electronic":
        raise ValueError("this task is scored by electronic mark only; physical measurements do not apply")
    out = []
    for row in rows:
        row = dict(row)
        given = physical.get(row["pilot"])
        if given == "lost":
            row["result_from"] = "electronic mark; marker LOST inside the MMA, scored as if the MMA was missed"
        elif given is not None:
            row.update(electronic_result=row.get("result"), result=float(given), unit="m", no_result=None,
                       result_from="physical mark, measured by the officials inside the MMA")
        elif row.get("result") is not None:
            row["result_from"] = (row.get("result_from") or "electronic mark") + "; no physical mark inside the MMA"
        out.append(row)
    return out
