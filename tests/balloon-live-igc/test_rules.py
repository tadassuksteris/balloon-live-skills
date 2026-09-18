"""Rules as inputs, and the checks that apply them."""
import math
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "skills", "balloon-live-igc", "scripts"))
FIX = os.path.join(HERE, "fixtures")

import flight_data  # noqa: E402
import measure as m  # noqa: E402
import rules as R  # noqa: E402
from geo import ft_to_m  # noqa: E402

T0 = 1782986400.0  # 2026-07-02 10:00:00 UTC


def mark(number, e, n, alt_m, t=T0, **extra):
    p = m.point_utm(35, e, n, alt_m)
    p.update(marker=number, t=t, time_utc="2026-07-02T10:00:00+00:00", time_local="13:00:00", fix=True, satellites=12,
             quality_from="record", counts=True, file="a.igc", file_status="flight", alt_source="baro")
    p.update(extra)
    return p


class Parameters(unittest.TestCase):
    def test_every_parameter_has_a_value_and_a_source(self):
        r = R.rules(separation_altitude_ft=600, sources={"separation_altitude_ft": "rule book, 2D/3D scoring section"})
        self.assertEqual(R.value(r, "separation_altitude_ft"), 600)
        self.assertEqual(r["separation_altitude_ft"]["source"], "rule book, 2D/3D scoring section")
        self.assertEqual((R.value(r, "primary_recording"), r["primary_recording"]["source"]), ("longest", "default"))
        self.assertTrue(R.describe(r)[0].startswith("separation_altitude_ft: 600 (rule book"))
        with self.assertRaises(ValueError):
            R.rules(separation_altitude=600)

    def test_only_applied_parameters_exist_and_load_options_respect_headers(self):
        for gone in ("altitude_reference", "boundary_inclusive"):
            with self.assertRaises(ValueError):
                R.rules(**{gone: True})
        self.assertEqual(R.load_options(R.rules(grid_zone=35, primary_recording="first_started")),
                         {"competition_zone": 35, "primary": "first_started"})  # defaults never override a header
        self.assertEqual(R.load_options(R.rules(declaration_digits="5/4", declaration_alt_unit="m")),
                         {"competition_zone": None, "primary": "longest", "decl_digits": "5/4", "decl_alt_unit": "m"})
        data = flight_data.load([FIX], **R.load_options(R.rules(grid_zone=35)))
        self.assertEqual(data["competition_zone"], 35)

    def test_override_for_one_task(self):
        base = R.rules(scoring_period_end="22:00", mma_radius_m=50, sources={"scoring_period_end": "rule book"})
        task = R.override(base, sources={"scoring_period_end": "task sheet, task 2"}, scoring_period_end="21:30", mma_radius_m=100)
        self.assertEqual((R.value(task, "scoring_period_end"), task["scoring_period_end"]["source"]), ("21:30", "task sheet, task 2"))
        self.assertEqual((R.value(task, "mma_radius_m"), task["mma_radius_m"]["source"]), (100, "task sheet"))
        self.assertEqual((R.value(base, "scoring_period_end"), R.value(base, "mma_radius_m")), ("22:00", 50))  # base untouched
        self.assertEqual(task["mark_reuse"], base["mark_reuse"])
        with self.assertRaises(ValueError):
            R.override(base, mma_radius=100)

    def test_save_and_load_keep_sources(self):
        r = R.rules(grid_zone=35, mma_radius_m=50, sources={"grid_zone": "task sheet"})
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "rules.json")
            R.save(r, path)
            back = R.load_rules(path)
        self.assertEqual((R.value(back, "grid_zone"), back["grid_zone"]["source"], back["mma_radius_m"]["source"]), (35, "task sheet", "given"))
        self.assertEqual(back["sunset"]["source"], "default")


class GoalDistance(unittest.TestCase):
    def test_two_d_by_default_with_vertical_apart(self):
        goal = m.point_utm(35, 500000, 6050000, 50)
        res = R.goal_distance(mark(1, 500300, 6050400, 350), goal, R.rules())
        self.assertEqual((res["method"], round(res["distance_m"], 3), res["vertical_m"], res["flags"]), ("2D", 500.0, 300, []))

    def test_separation_altitude(self):
        r = R.rules(separation_altitude_ft=600)
        goal = m.point_utm(35, 500000, 6050000, 50)
        sep = ft_to_m(600)
        low = R.goal_distance(mark(1, 500300, 6050400, sep - 1), goal, r)
        self.assertEqual((low["method"], round(low["distance_m"], 3)), ("2D", 500.0))
        edge = R.goal_distance(mark(1, 500300, 6050400, sep), goal, r)
        self.assertEqual(edge["method"], "2D")  # at the separation altitude scores 2D
        high = R.goal_distance(mark(1, 500300, 6050400, sep + 1200), goal, r)
        self.assertEqual((high["method"], round(high["distance_m"], 3), round(high["reference_alt_m"], 3)), ("3D", 1300.0, round(sep, 3)))
        self.assertIn("separation altitude 600 ft", high["rule"])
        gps = R.goal_distance(mark(1, 500300, 6050400, sep + 1200, alt_source="gps"), goal, r)
        self.assertIn("mark altitude is GPS", " ".join(gps["flags"]))
        blind = R.goal_distance(mark(1, 500300, 6050400, None), goal, r)
        self.assertEqual(blind["method"], "2D")
        self.assertIn("could not be applied", " ".join(blind["flags"]))

    def test_floor_and_marker_mode(self):
        r = R.rules(mma_radius_m=50, electronic_floor=True)
        near = R.goal_distance(mark(1, 500030, 6050000, 100), m.point_utm(35, 500000, 6050000), r)
        floored = R.floor(near, r)
        self.assertEqual((round(floored["distance_m"]), round(floored["measured_m"])), (50, 30))
        self.assertIn("raised to the worst result possible in the MMA, 50 m", " ".join(floored["flags"]))
        self.assertIn("PROVISIONAL: a valid physical mark inside the MMA takes precedence", floored["flags"])
        off = R.floor(near, R.rules(mma_radius_m=50))  # floor rule off: provisional, not raised
        self.assertEqual((off["distance_m"], "PROVISIONAL" in " ".join(off["flags"])), (near["distance_m"], True))
        worst = R.floor(near, R.rules(mma_radius_m=50, mma_worst_m=80, electronic_floor=True))
        self.assertEqual(round(worst["distance_m"]), 80)
        electronic = R.floor(near, R.rules(mma_radius_m=50, electronic_floor=True, marker_mode="electronic"))
        self.assertEqual((electronic["distance_m"], electronic["flags"]), (near["distance_m"], near["flags"]))
        self.assertNotIn("PROVISIONAL", " ".join(electronic["flags"]))

    def test_physical_marks_merge(self):
        r = R.rules(mma_radius_m=50, electronic_floor=True)
        goal = m.point_utm(35, 500000, 6050000)
        record = {"utc_shift_s": 10800, "file": "a.igc"}
        rows = [R.result_row(p, record, R.floor(R.goal_distance(mark(1, 500000 + e, 6050000, 100), goal, r), r), [])
                for p, e in ((1, 30), (2, 30), (3, 400), (4, 900))]
        self.assertEqual([(row["result"], row["result_from"]) for row in rows][:1], [(50.0, "electronic mark")])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "physical.csv")
            with open(path, "w") as fh:
                fh.write("\ufeffpilot,distance\n1,12.35\n2,lost\n9,5\n")
            physical = R.load_physical(path)
        self.assertEqual(physical, {1: 12.35, 2: "lost", 9: 5.0})
        merged = R.apply_physical(rows, physical, r)
        self.assertEqual((merged[0]["result"], merged[0]["electronic_result"], merged[0]["result_from"]),
                         (12.35, 50.0, "physical mark, measured by the officials inside the MMA"))
        self.assertEqual((merged[1]["result"], merged[1]["result_from"][:41]), (50.0, "electronic mark; marker LOST inside the M"))
        self.assertEqual((round(merged[2]["result"]), merged[2]["result_from"]), (400, "electronic mark; no physical mark inside the MMA"))
        self.assertEqual(rows[0]["result"], 50.0)  # the input rows are untouched
        with self.assertRaises(ValueError):
            R.apply_physical(rows, physical, R.rules(marker_mode="electronic"))


class Checks(unittest.TestCase):
    record = {"utc_shift_s": 10800, "file": "a.igc"}

    def test_period_and_sunset_in_local_time(self):
        r = R.rules(scoring_period_end="13:30", sunset="13:10")
        checks = R.check_period(mark(1, 500000, 6050000, 100), self.record, r)  # 10:00 UTC = 13:00 local
        self.assertEqual([(c["check"], c["passed"]) for c in checks], [("scoring period", True), ("sunset", True)])
        late = mark(1, 500000, 6050000, 100, t=T0 + 20 * 60)  # 13:20 local
        checks = R.check_period(late, self.record, r)
        self.assertEqual([(c["check"], c["passed"]) for c in checks], [("scoring period", True), ("sunset", False)])
        self.assertEqual(R.check_period(late, self.record, R.rules()), [])

    def test_area_with_margin(self):
        ring = m.circle(m.point_utm(35, 500000, 6050000), 100)
        inside = R.check_area(mark(1, 500050, 6050000, 100), ring)
        self.assertEqual((inside["passed"], inside["evidence"]), (True, "inside, 50.0 m from the edge"))
        close = R.check_area(mark(1, 500101, 6050000, 100), ring)
        self.assertEqual(close["passed"], False)
        self.assertIn("1.0 m from the edge (within GPS accuracy of the edge)", close["evidence"])
        box = m.polygon([m.point_utm(35, 500000, 6050000), m.point_utm(35, 500100, 6050000), m.point_utm(35, 500100, 6050100)])
        self.assertAlmostEqual(m.edge_distance(mark(1, 500080, 6050010, 100), box), 10.0, delta=0.01)
        self.assertAlmostEqual(m.edge_distance(mark(1, 500080, 6049990, 100), box), -10.0, delta=0.01)
        lost = dict(mark(1, 500000, 6050000, 100), lat=None, lon=None)
        self.assertEqual((R.check_area(lost, ring)["passed"], R.check_area(lost, ring)["evidence"]), (None, "undecidable"))

    def test_order_fix_counts(self):
        a, b = mark(2, 500000, 6050000, 100, t=T0), mark(3, 500500, 6050000, 100, t=T0 + 60)
        self.assertTrue(R.check_order([a, b])["passed"])
        self.assertFalse(R.check_order([mark(2, 500000, 6050000, 100, t=T0 + 60), mark(3, 500500, 6050000, 100, t=T0)])["passed"])
        self.assertFalse(R.check_fix(dict(a, fix=False))["passed"])
        self.assertFalse(R.check_counts(dict(a, counts=False))["passed"])

    def test_mark_for_reuse_rule(self):
        drops = [mark(4, 500000, 6050000, 100, t=T0), mark(4, 500100, 6050000, 100, t=T0 + 30, time_local="13:00:30")]
        first, checks = R.mark_for(drops, 4, R.rules(), self.record)
        self.assertEqual((first["easting"] // 1, checks[0]["check"]), (500000, "mark 4 dropped once"))
        self.assertIn("the first one counts", checks[0]["evidence"])
        last, _ = R.mark_for(drops, 4, R.rules(mark_reuse="last"), self.record)
        self.assertEqual(last["easting"] // 1, 500100)
        none, checks = R.mark_for(drops, 5, R.rules(), self.record)
        self.assertEqual((none, checks[0]["passed"]), (None, False))

    def test_declaration_for_reuse_and_validity(self):
        def decl(number, t, text, lat=54.6, **extra):
            d = {"number": number, "t": t, "time_local": f"13:{int(t - T0) // 60:02d}:00", "text": text, "lat": lat, "lon": 24.0,
                 "utm": "35U 312340 6048560", "digits": "4/4", "grid_zone": 35, "counts": True, "file": "a.igc", "file_status": "flight",
                 "declared": True, "alt_m": 100.0}
            d.update(extra)
            return d
        made = [decl(1, T0, "1234/4856"), decl(1, T0 + 120, "1240/4860", alt_m=400.0), decl(2, T0 + 60, "0000/0000", lat=None, warnings=["far"])]
        last, checks = R.declaration_for(made, 1, R.rules())
        self.assertEqual((last["text"], [c["check"] for c in checks]), ("1240/4860", ["declared once", "declaration read", "counting file"]))
        self.assertIn("the last one counts", checks[0]["evidence"])
        first, _ = R.declaration_for(made, 1, R.rules(declaration_reuse="first"))
        self.assertEqual(first["text"], "1234/4856")
        unread, checks = R.declaration_for(made, 2, R.rules())
        self.assertEqual((checks[0]["check"], checks[0]["passed"]), ("declaration read", False))
        self.assertIn("not resolved; far", checks[0]["evidence"])
        self.assertEqual(R.judge(None, checks)["no_result"], "no measurement")
        none, checks = R.declaration_for(made, 3, R.rules())
        self.assertEqual((none, checks[0]["passed"], checks[0]["evidence"]), (None, False, "no declaration number 3 in the file"))
        self.assertEqual(R.declaration_for(made, None, R.rules())[0]["number"], 1)  # the last of all three
        # last VALID: the second declaration breaks an altitude limit that invalidates
        valid = lambda d: [R.check_limit("goal altitude", d["alt_m"], max_value=300, unit="m", severity="invalidates")]
        chosen, checks = R.declaration_for(made, 1, R.rules(), valid)
        self.assertEqual(chosen["text"], "1234/4856")
        self.assertIn("'1240/4860' INVALID", checks[0]["evidence"])
        self.assertIn("the last valid one counts", checks[0]["evidence"])
        self.assertEqual([c["check"] for c in checks][-1], "goal altitude")  # the chosen one's validity checks travel
        self.assertIsNone(R.judge({"distance_m": 5.0, "flags": []}, checks)["no_result"])
        # the same limit as a penalty: the last one still counts, and the row names the infringement
        lenient = lambda d: [R.check_limit("goal altitude", d["alt_m"], max_value=300, unit="m")]
        chosen, checks = R.declaration_for(made, 1, R.rules(), lenient)
        verdict = R.judge({"distance_m": 5.0, "flags": []}, checks)
        self.assertEqual((chosen["text"], verdict["result"], verdict["infringements"]), ("1240/4860", 5.0, ["goal altitude"]))
        # none valid: shown for the measurement, said in capitals
        chosen, checks = R.declaration_for(made, 1, R.rules(), lambda d: [R.check("boundary", False, "off the map")])
        self.assertEqual(chosen["text"], "1240/4860")
        self.assertIn("NONE VALID", checks[0]["evidence"])
        self.assertEqual(R.judge({"distance_m": 5.0, "flags": []}, checks)["no_result"], "boundary")

    def test_declaration_origin_and_marks_overview(self):
        r = R.rules(sources={"declaration_before_takeoff": "rule book"})
        origin = flight_data.with_utm({"lat": 54.6, "lon": 24.0, "alt_m": 90.0, "alt_source": "baro", "t": T0, "from": "declaration position"})
        decl = {"t": T0, "time_local": "13:00:00", "declared_from": origin}
        record = dict(self.record, takeoff=flight_data.with_utm({"lat": 54.61, "lon": 24.01, "alt_m": 60.0, "alt_source": "baro", "t": T0 + 600}))
        at, how = R.declaration_origin(decl, record, r)
        self.assertEqual((at["lat"], how[:30]), (54.61, "declared 13:00:00, before take"))
        at, how = R.declaration_origin(decl, record, R.rules(declaration_before_takeoff="as_declared"))
        self.assertEqual((at["lat"], how), (54.6, f"declared at {origin['utm']} (declaration position)"))
        at, how = R.declaration_origin(dict(decl, t=T0 + 900), record, r)
        self.assertEqual(at["lat"], 54.6)
        self.assertEqual(R.declaration_origin({"t": T0, "time_local": "x", "declared_from": None}, None, r), (None, "no position at the time of the declaration"))
        goals = {"task 3 goal": m.point_utm(35, 500000, 6050000), "task 4 goal": m.point_utm(35, 504000, 6050000)}
        rows = R.marks_overview([mark(4, 500045, 6050000, 100, t=T0 + 60), mark(3, 504010, 6050000, 100, t=T0)], goals)
        self.assertEqual([(w["marker"], w["nearest"]) for w in rows], [(3, "task 4 goal"), (4, "task 3 goal")])
        self.assertEqual(rows[1]["distances"], "task 3 goal 45 m; task 4 goal 3955 m")

    def test_declared_goal_method_and_limits(self):
        r = R.rules(separation_altitude_ft=600)
        sep = ft_to_m(600)
        goal = dict(m.point_utm(35, 500000, 6050000, 300.0), declared=True)      # declared with altitude 300 m
        drop = mark(1, 500400, 6050000, sep + 100)                              # above the separation altitude
        res = R.goal_distance(drop, goal, r)
        self.assertEqual(res["method"], "3D to the declared point")
        self.assertAlmostEqual(res["distance_m"], math.hypot(400, sep + 100 - 300), places=6)
        self.assertEqual(res["alternative"]["method"], "3D (separation rule)")
        self.assertAlmostEqual(res["alternative"]["distance_m"], math.hypot(400, 100), places=6)
        by_sep = R.goal_distance(drop, goal, R.rules(separation_altitude_ft=600, declared_goal_method="separation"))
        self.assertEqual((by_sep["method"], round(by_sep["distance_m"], 3)), ("3D", round(math.hypot(400, 100), 3)))
        self.assertEqual(round(by_sep["alternative"]["distance_m"], 3), round(res["distance_m"], 3))
        self.assertEqual(R.goal_distance(drop, m.point_utm(35, 500000, 6050000, 300.0), r)["method"], "3D")  # a set goal
        self.assertEqual(R.goal_distance(drop, dict(goal, alt_m=None), r)["method"], "3D")
        self.assertIn("separation rule shown instead", " ".join(R.goal_distance(drop, dict(goal, alt_m=None), r)["flags"]))
        lim = R.check_limit("declaration point to goal", 1500.0, min_value=2000)
        self.assertEqual((lim["passed"], lim["severity"], lim["evidence"]), (False, "penalty", "1500.0 m (min 2000 m) OUTSIDE THE LIMIT"))
        self.assertTrue(R.check_limit("x", 2500.0, min_value=2000, max_value=6000)["passed"])
        self.assertIsNone(R.check_limit("x", None, min_value=1)["passed"])
        with self.assertRaises(ValueError):
            R.check("x", True, "y", severity="hard")

    def test_measurement_wrapper_and_precision(self):
        a, b, c = mark(5, 500000, 6050000, 100), mark(6, 501000, 6050000, 100, fix=False), mark(7, 500000, 6051000, 100)
        area = R.measurement(m.area(a, b, c, zone=35), "m2", points=(a, b, c))
        self.assertEqual((round(area["value"], 3), area["unit"], len(area["points"])), (500000.0, "m2", 3))
        self.assertEqual(area["flags"], ["b: recorded without a GPS fix"])
        order = R.check_order([a, b, c])
        verdict = R.judge(area, [order])
        self.assertEqual((round(verdict["result"], 3), verdict["unit"], verdict["no_result"]), (500000.0, "m2", None))
        row = R.result_row(1, self.record, area, [order], precision=None)
        self.assertEqual((round(row["result"], 3), row["unit"], row["mark"], row["mark_time"]), (500000.0, "m2", "35U 500000 6050000", "13:00:00"))
        km2 = R.result_row(1, self.record, dict(area, value=area["value"] / 1e6, unit="km2"), [order], precision=1)
        self.assertEqual((km2["result"], round(km2["result_exact"], 6)), (0.5, 0.5))
        row = R.result_row(1, self.record, {"distance_m": 110.123, "flags": []}, [], precision=0)
        self.assertEqual((row["result"], row["result_exact"], row["unit"]), (110.0, 110.123, "m"))

    def test_nearest_note_names_a_closer_goal(self):
        drop = mark(4, 500045, 6050000, 100)
        goals = {"task 3 goal": m.point_utm(35, 500000, 6050000), "task 4 goal": m.point_utm(35, 504000, 6050000)}
        fine = R.nearest_note(drop, goals, "task 3 goal")
        self.assertEqual((fine["passed"], fine["evidence"]), (True, "task 3 goal is the nearest (task 3 goal 45 m, task 4 goal 3955 m)"))
        wrong = R.nearest_note(drop, goals, "task 4 goal")
        self.assertTrue(wrong["passed"])
        self.assertTrue(wrong["evidence"].startswith("CLOSER TO task 3 goal than to task 4 goal"))
        self.assertIsNone(R.judge({"distance_m": 1.0, "flags": []}, [wrong])["no_result"])  # a note, not a rule
        self.assertEqual(R.nearest_note(dict(drop, lat=None, lon=None), goals, "task 3 goal")["evidence"], "point: no position")

    def test_local_time_helper(self):
        rec = {"utc_shift_s": 10800}
        self.assertEqual(flight_data.local_time(T0, rec), "13:00:00")
        self.assertEqual(flight_data.local_time("2026-07-02T10:00:00+00:00", rec, fmt="%Y-%m-%d %H:%M"), "2026-07-02 13:00")
        self.assertEqual(flight_data.local_time(mark(1, 500000, 6050000, 100), utc_shift_s=-3600), "09:00:00")
        self.assertEqual(flight_data.local_time(T0), "10:00:00")  # no offset known: UTC
        self.assertEqual(flight_data.local_datetime(T0, rec).utcoffset().total_seconds(), 10800)

    def test_judge_and_row(self):
        goal = m.point_utm(35, 500000, 6050000)
        good = R.judge(R.goal_distance(mark(1, 500030, 6050040, 100), goal, R.rules()), [R.check_fix(mark(1, 500030, 6050040, 100))])
        self.assertEqual((round(good["result"]), good["unit"], good["no_result"], good["infringements"]), (50, "m", None, []))
        late = R.judge(R.goal_distance(mark(1, 500030, 6050040, 100), goal, R.rules()),
                       [{"check": "scoring period", "passed": False, "evidence": "22:00:15 > 22:00"}, {"check": "zone", "passed": None, "evidence": "?"}])
        self.assertEqual((late["result"], late["no_result"]), (None, "scoring period; zone (undecidable)"))
        self.assertEqual(round(late["measurement"]["distance_m"]), 50)  # the measurement is still there
        self.assertEqual(R.judge(None, [])["no_result"], "no measurement")
        row = R.result_row(7, self.record, late["measurement"], late["checks"])
        self.assertEqual((row["pilot"], row["result"], row["no_result"][:14], row["method"]), (7, None, "scoring period", "2D"))
        self.assertIn("scoring period: FAIL (22:00:15 > 22:00)", row["checks"])
        penalised = R.judge(R.goal_distance(mark(1, 500030, 6050040, 100), goal, R.rules()),
                            [R.check_order([mark(3, 500000, 6050000, 100, t=T0), mark(2, 500500, 6050000, 100, t=T0 + 60)])])
        self.assertEqual((round(penalised["result"]), penalised["infringements"], penalised["no_result"]), (50, ["in order"], None))
        row = R.result_row(7, self.record, penalised["measurement"], penalised["checks"])
        self.assertEqual(row["infringements"], "in order")
        self.assertIn("in order: INFRINGED (", row["checks"])
        self.assertIn("nearest target: note (", R.result_row(7, self.record, None, [R.nearest_note(mark(1, 500030, 6050040, 100), {"g": goal}, "g")])["checks"])


class OnFixtures(unittest.TestCase):
    def test_first_started_and_roster(self):
        with tempfile.TemporaryDirectory() as tmp:
            roster = os.path.join(tmp, "pilots.csv")
            with open(roster, "w") as fh:
                fh.write("901\n902\n903,Named\n905\n")
            data = flight_data.load([FIX], pilots_csv=roster, primary="first_started")
        who = flight_data.counting(data, "date 2026-07-02")
        self.assertEqual(sorted(who), [901, 902, 903, 905])  # 905 from the roster only
        self.assertIsNone(who[905])
        self.assertTrue(any("roster with no file at all: 905" in w for w in data["warnings"]))
        self.assertEqual([r["name"] for r in data["files"] if r["pilot"] == 903][0], "Named")

    def test_a_task_end_to_end(self):
        data = flight_data.load([FIX], competition_zone=35)
        r = R.rules(separation_altitude_ft=600, grid_zone=35, mma_radius_m=50, scoring_period_end="22:00")
        rec = flight_data.counting(data, "date 2026-07-02")[901]
        goal = m.point_from_digits("0725/5532,167", rec["track"][0], zone=35)
        found, checks = R.mark_for(flight_data.markers_of(data, rec), 1, r, rec)
        res = R.judge(R.floor(R.goal_distance(found, goal, r), r), checks)
        self.assertEqual(res["measurement"]["method"], "3D")  # dropped at 906 ft, above 600
        self.assertAlmostEqual(res["result"], 109.9, delta=0.2)
        self.assertEqual(res["no_result"], None)
        self.assertEqual([c["check"] for c in checks], ["GPS fix", "counting file", "scoring period"])
        decl = flight_data.declarations_of(data, flight_data.counting(data, "date 2026-07-02")[903] or
                                           [f for f in data["files"] if f["pilot"] == 903][0])
        self.assertTrue(decl and decl[0]["declared"] and decl[0]["declared_from"]["lat"] is not None)
        self.assertEqual(decl[0]["declared_from"]["from"], "declaration position")
        self.assertIsNotNone(decl[0]["declared_from"]["alt_m"])


if __name__ == "__main__":
    unittest.main()
