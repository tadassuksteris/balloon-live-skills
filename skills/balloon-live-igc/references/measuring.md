# Measuring a task

`scripts/measure.py` holds the primitives; its docstring lists them all. A task is always
select, measure, choose. Write a short script per task from these pieces; do not re-derive
distances or containment.

```python
import sys; sys.path.insert(0, "<skill-dir>/scripts")
import flight_data, measure as m
from geo import ft_to_m

data = flight_data.load(["<folder>"])              # qnh=, competition_zone=, prefer= when the scorer says so
for pilot, record in flight_data.counting(data).items():   # every pilot, record is None without a flight file
    ...                                                     # several flights loaded: counting(data, flight_key)
```

`record["track"]` is the cleaned track; `flight_data.markers_of(data, record)` and
`declarations_of(data, record)` are that file's events. All are points with `lat`, `lon`,
`utm`, `alt_m`, `alt_source`, `time_utc`. A marker without a GPS fix, or a declaration that
could not be read, has `lat` None: measurements then return no value and a flag.

## Patterns

Marker to a target, 2D:

```python
target = m.point_utm(35, 311700, 6053780)                 # or m.point_from_digits("1170/5378", data, zone=35)
drop = next((d for d in flight_data.markers_of(data, record) if d["marker"] == 4), None)
result = m.distance(drop, target) if drop else {"flags": ["no marker 4"]}
```

Closest track point to the pilot's declared goal, after the declaration, below 3000 ft:

```python
declared = flight_data.declarations_of(data, record)      # which declaration counts is the scorer's call
if declared:
    goal = declared[-1]
    fixes = m.altitude_between(m.after(m.airborne(record["track"]), goal), None, ft_to_m(3000))
    result = m.closest(fixes, goal)
else:
    result = {"flags": ["no declaration"]}
```

Distance flown inside a volume, and when it was entered:

```python
volume = m.circle(m.point_utm(35, 309000, 6054800), 1000, floor_m=300, ceiling_m=900)
flown = m.length_inside(m.airborne(record["track"]), volume, three_d=True)
entry = m.first_entry(record["track"], volume)
```

Goals from task-sheet digits: `m.point_from_digits(text, near, zone=)` completes them to the
candidate nearest `near`, which can be a point, a file record, or the loaded data (its flying
area). The point keeps its warnings: near a zone boundary, or on the other side of one.
Local times: `flight_data.local_time(point_or_time, record)`.

Results: collect one dict per pilot and `m.write_results("results.csv", rows)`.

## Applying the competition's rules

Find each of these in the rule book by its subject, wherever that book puts it, and give
`rules()` the value with its source (rule book section, task sheet, scorer): 2D/3D method
and any separation altitude; how a goal declared with an altitude is scored; which
recording counts when a pilot has several; repeated marks and declarations, and what makes
a declaration invalid (boundaries, altitude limits, declared after a set point); digit
format and altitude unit of declarations; a declaration made before take-off; scoring
period and any absolute cut-off such as sunset; per task, whether it is scored by physical
markers, electronic marks or both, the marker measuring area and any floor on electronic
results, whether a target was displayed at the coordinates, and any marker lost or dropped
in excess; contest area and map grid. For every limit, whether breaking it invalidates the
result or is a penalty matter: the checks carry that as `severity`.
`R.PARAMETERS` names the parameters with their meaning. Anything not found stays at its
default and `R.describe(r)` says so.

```python
import rules as R
r = R.rules(separation_altitude_ft=600, primary_recording="first_started", grid_zone=35, mma_radius_m=50,
            scoring_period_end="22:00", electronic_floor=True,          # marker_mode="electronic" for a task without markers
            sources={"separation_altitude_ft": "rule book, 2D/3D scoring section", "grid_zone": "task sheet"})
R.save(r, "competition-rules.json")                      # next to the results; R.describe(r) for the report
# a task sheet that states its own value: r = R.override(r, mma_radius_m=100)   (source "task sheet")
data = flight_data.load([folder], pilots_csv="pilots.csv", **R.load_options(r))   # roster: pilots with no file get a row
goal = m.point_from_digits("0725/5532,167", data, zone=35)
others = {"task 1 goal": goal, "task 3 goal": m.point_from_digits("1173/5376", data, zone=35)}
rows = []
for pilot, record in flight_data.counting(data).items():
    if record is None:
        rows.append(R.result_row(pilot, None, None, [{"check": "flight file", "passed": False, "evidence": "none"}]))
        continue
    mark, checks = R.mark_for(flight_data.markers_of(data, record), 1, r, record)   # reuse rule, fix, counts, period
    measurement = R.floor(R.goal_distance(mark, goal, r), r) if mark else None       # 2D/3D rule, MMA floor
    if mark:
        checks.append(R.nearest_note(mark, others, "task 1 goal"))   # says when the mark is closer to another goal
    rows.append(R.result_row(pilot, record, measurement, checks, precision=0))   # whole metres, exact value kept
rows = R.apply_physical(rows, R.load_physical("task1-physical.csv"), r)   # the officials' measurements, when they come
m.write_results("task1.csv", rows)
```

In a task scored by both kinds of mark every electronic result is PROVISIONAL: a valid
physical mark inside the MMA takes precedence, and the logger cannot know whether one exists.
`apply_physical` merges the officials' list (pilot, distance or `lost`) and names the source
of each result.

`goal_distance` applies the separation-altitude rule to a set goal (2D at or below, 3D
above, to the point at that altitude over the goal; with no separation altitude 2D and the
vertical difference apart) and `declared_goal_method` to a goal the pilot declared with an
altitude; the other method's value is kept in `alternative`.

A pilot's declared goal, with the validity rules of the task:

```python
def valid(d):                                       # the task sheet's conditions on the declaration
    at, how = R.declaration_origin(d, record, r)    # where the pilot was when declaring
    away = m.distance(at, d)["distance_m"] if at else None
    return [R.check_area(d, contest_area, "goal inside the contest area"),
            R.check_limit("declaration point to goal", away, min_value=2000, severity="penalty")]
goal, checks = R.declaration_for(flight_data.declarations_of(data, record), 2, r, valid)   # last valid one
```

Checks have a `severity`: `invalidates` (No Result when failed), `penalty` (the result
stands, the row lists the infringement) or `note`. `check_order` is a penalty by default,
`check_area` invalidates, `check_limit` takes either. `R.nearest_note` tells the scorer, in
capitals, when a mark lies closer to another task's goal; `R.marks_overview(markers, goals)`
gives that for every mark of a pilot, which is how marks dropped for the wrong task show up.
A value that is not a distance (an area, an angle) becomes a measurement with
`R.measurement(value, "m2", points=(a, b, c))` and goes through `judge` and `result_row` like
any other; `result_row(..., precision=0)` rounds to the rule book's reporting precision and
keeps the exact value. In every row, `evidence` is the points a value rests on, `flags` what
weakens it, `checks` the rules applied, each `ok`, `FAIL`, `INFRINGED`, `note` or `?` (could
not be decided) with why, and `infringements` the failed penalty checks.

## Zones

Prohibited zones, launch areas, the contest area: whatever the scorer supplies (a GPX with
centres only, a map tool's export, a table) becomes the skill's own zone file, with what
is missing asked for and the source named per zone.

```python
import zones as Z
zs = [Z.zone("PZ1", "pz", lat=49.7562, lon=21.8123, radius_m=1000, ceiling_ft=3300, source="map export; unit confirmed by scorer"),
      Z.zone("contest area", "contest", corners=[(lat, lon), ...], source="rule book")]
Z.save_zones(zs, "zones.json")                       # next to the rules file
areas, incomplete = Z.shapes(zs)                     # shapes for check_area, inside, length_inside ...
for row in Z.zone_report(record["track"], zs):       # per zone: entered, stays, seconds, deepest below the ceiling
    ...
```

A zone's floor and ceiling bound the volume that matters: a PZ "up to 3300 ft" has only a
ceiling, so a fix is inside when it is within the outline and at or below it. Points
(a launch point) are zones with a position only. Whether entering a zone invalidates a
result or is a penalty matter is the rule book's, expressed as a check's severity.

Comparing with official results: `references/watchmefly.md`.

## Rules the primitives enforce

- No interpolation: entry is the first fix inside, exit the last; a segment counts as inside
  only when both ends are.
- `length` and `time_inside` never bridge a removed fix, a pause in the log (over 3 logging
  intervals, at least 5 s), or in 3D a change of altitude source. What was skipped is in
  `not_bridged`; pauses and altitude gaps also raise a flag, since the value is then a minimum.
- 3D needs altitude at both ends, otherwise the value is `None` with a flag.
- A fix without altitude is neither inside nor outside a shape with a floor or ceiling.
- Every result names the points it rests on and carries their flags: GPS altitude,
  fallback position source, no GPS fix.
- `bearing`, `angle` and `area` work on one grid: the first point's zone, or `zone=`. Grid
  north differs by several degrees between neighbouring zones, so for a direction task
  near a zone boundary ask which grid the competition uses and pass it.
- Select with the functions above. Slicing or joining their results gives a plain list
  and drops the notes about what was left out.
