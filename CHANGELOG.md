# Changelog

## 0.1.0 (2026-09-19)

First release, one skill.

**balloon-live-igc**

- Parser for Balloon Live app and sensor IGC files: header, fixes, marker drops, goal
  declarations, sensor responses, phone snapshots, position-source switches; extension
  layouts read from each file; writer quirks handled (raw quality byte, overflow values,
  missing serial, day change).
- Flight check (`check_flight.py`): one report per competition flight with QNH agreement,
  file status, take-off and landing, markers and declarations in UTM WGS84, aggregated
  warnings; JSON and cleaned track CSV outputs.
- Altitude QNH-corrected exactly as the Balloon Live app computes it; GPS altitude only
  where barometric is missing, shown as measured.
- Geodesy (`geo.py`): UTM in each point's own zone, distances on a shared grid across zone
  boundaries, shortened declaration digits expanded on the competition grid.
- Measurement primitives (`measure.py`): points, circles, polygons and layers; selection by
  time, altitude and area; distances, closest and farthest points, lengths and times
  inside volumes, bearings, angles, areas; no interpolation, every result with evidence
  and flags.
- Competition rules as inputs (`rules.py`): parameters with sources, per-task overrides,
  checks with severity (invalidates, penalty, note), the mark or declaration that counts,
  declaration validity and origin, separation-altitude and declared-goal methods, the MMA
  floor and precedence of physical marks, merge of the officials' measurements; measure,
  then judge, one row per pilot.
- Zones (`zones.py`): one format for prohibited zones, areas and points, and what a track
  did in them.
- References: measuring patterns and the rule-book checklist, record layouts, WatchMeFly
  exports.

**Repository**

- Lint (`tools/validate_skills.py`): import allow-list, front-matter and text checks, file
  types, tests required; CI on Python 3.9 and current, actions pinned by commit.
