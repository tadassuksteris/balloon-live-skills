"""Measurement primitives against geometry that can be worked out by hand."""
import csv
import math
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "skills", "balloon-live-igc", "scripts"))
FIX = os.path.join(HERE, "fixtures")

import flight_data  # noqa: E402
import geo  # noqa: E402
import measure as m  # noqa: E402

T0 = 1782986400.0  # 2026-07-02 10:00:00 UTC


def utm(e, n, alt=None, zone=35):
    return m.point_utm(zone, e, n, alt)


def line(count=201, step=10.0, climb=1.0, source="baro"):
    """Due east along northing 6,050,000 from easting 500,000: 10 m and 1 m up per second."""
    pts = []
    for k in range(count):
        p = utm(500000 + step * k, 6050000, 100 + climb * k)
        p.update(i=k, t=T0 + k, time_utc=f"2026-07-02T10:{k // 60:02d}:{k % 60:02d}+00:00", alt_source=source,
                 position_source="primary", phase="airborne")
        pts.append(p)
    return pts


class Shapes(unittest.TestCase):
    def test_circle_and_cylinder(self):
        track = line()
        ring = m.circle(utm(501000, 6050000), 205)
        self.assertEqual(len(m.inside(track, ring)), 41)
        self.assertEqual(len(m.outside(track, ring)), 160)
        tube = m.circle(utm(501000, 6050000), 205, floor_m=194.5, ceiling_m=205.5)  # altitude is 100 + k, k = 95..105
        self.assertEqual([p["i"] for p in m.inside(track, tube)], list(range(95, 106)))

    def test_polygon_and_layer(self):
        track = line()
        box = m.polygon([utm(500495, 6049950), utm(500705, 6049950), utm(500705, 6050050), utm(500495, 6050050)])
        self.assertEqual([p["i"] for p in m.inside(track, box)], list(range(50, 71)))
        self.assertEqual(len(m.inside(track, m.layer(149.5, 160.5))), 11)
        self.assertEqual(len(m.altitude_between(track, 149.5, 160.5)), 11)
        self.assertEqual(len(m.altitude_between(track, None, 109.5)), 10)

    def test_fix_without_altitude_is_neither_in_nor_out_of_a_volume(self):
        track = line()
        track[100]["alt_m"] = None
        tube = m.circle(utm(501000, 6050000), 205, floor_m=0, ceiling_m=1000)
        got_in, got_out = m.inside(track, tube), m.outside(track, tube)
        self.assertEqual((len(got_in), len(got_out)), (40, 160))
        self.assertIn("1 fixes without altitude", got_in.notes[0])
        r = m.time_inside(track, tube)
        self.assertEqual((r["seconds"], r["stays"]), (38, 1))  # 40 s true; the unplaceable fix costs its two segments, not the stay
        self.assertIn("minimum", " ".join(r["flags"]))
        self.assertIn("minimum", " ".join(m.length_inside(track, tube)["flags"]))
        self.assertIn("minimum", " ".join(m.first_entry(track, tube)["flags"]))
        self.assertEqual(len(m.inside(track, m.circle(utm(501000, 6050000), 205))), 41)  # no limits: altitude not needed

    def test_shape_across_a_zone_boundary(self):
        centre = m.point(54.61, 24.0)  # on the 34/35 line
        west, east = m.point(54.61, 23.99), m.point(54.61, 24.01)
        self.assertEqual((west["zone"], east["zone"]), (34, 35))
        ring = m.circle(centre, 700)
        self.assertTrue(m.contains(ring, west) and m.contains(ring, east))
        box = m.polygon([m.point(54.60, 23.98), m.point(54.60, 24.02), m.point(54.62, 24.02), m.point(54.62, 23.98)])
        self.assertTrue(m.contains(box, west) and m.contains(box, east))
        self.assertFalse(m.contains(box, m.point(54.61, 24.03)))


class Selecting(unittest.TestCase):
    def test_times(self):
        track = line()
        self.assertEqual(len(m.between(track, "2026-07-02T10:00:10+00:00", "2026-07-02T10:00:20+00:00")), 11)
        self.assertEqual(len(m.between(track, T0 + 10, T0 + 20)), 11)
        marker = {"t": T0 + 150, "time_utc": "2026-07-02T10:02:30+00:00"}
        self.assertEqual((len(m.after(track, marker)), len(m.before(track, marker))), (51, 151))
        self.assertEqual(len(m.after(track, "2026-07-02T10:02:30")), 51)  # no offset given means UTC

    def test_notes_survive_chaining(self):
        track = line()
        track[5]["alt_m"] = None
        chained = m.after(m.altitude_between(track, 0, 1000), T0 + 3)
        self.assertEqual(chained.notes, ("1 fixes without altitude left out",))
        self.assertEqual(m.closest(chained, utm(500000, 6050000))["notes"], ["1 fixes without altitude left out"])

    def test_baro_only(self):
        track = line()
        for p in track[10:20]:
            p["alt_source"] = "gps"
        kept = m.altitude_between(track, None, None, baro_only=True)
        self.assertEqual((len(kept), kept.notes), (191, ("10 fixes with GPS altitude left out",)))


class Measuring(unittest.TestCase):
    def test_distance_2d_and_3d(self):
        a, b = utm(500000, 6050000, 100), utm(500300, 6050400, 1300)
        r = m.distance(a, b)
        self.assertAlmostEqual(r["distance_m"], 500, delta=0.01)
        self.assertAlmostEqual(r["vertical_m"], 1200, delta=1e-9)
        self.assertAlmostEqual(m.distance(a, b, three_d=True)["distance_m"], 1300, delta=0.01)
        flat = m.distance(a, utm(500300, 6050400), three_d=True)
        self.assertIsNone(flat["distance_m"])
        self.assertIn("3D not possible", " ".join(flat["flags"]))

    def test_distance_across_zones_matches_the_geodesy(self):
        a, b = m.point(54.61, 23.95, 100), m.point(54.60, 24.05, 100)
        self.assertEqual(m.distance(a, b)["distance_m"], geo.grid_distance(54.61, 23.95, 54.60, 24.05))
        self.assertEqual((m.distance(a, b)["a"]["utm"][:3], m.distance(a, b)["b"]["utm"][:3]), ("34U", "35U"))

    def test_closest_and_farthest_with_evidence(self):
        track = line()
        target = utm(501003, 6050300, 500)
        near = m.closest(track, target)
        self.assertAlmostEqual(near["distance_m"], math.hypot(3, 300), delta=0.01)
        self.assertEqual((near["point"]["utm"], near["point"]["time_utc"]), ("35U 501000 6050000", "2026-07-02T10:01:40+00:00"))
        self.assertEqual(near["flags"], [])
        deep = m.closest(track, target, three_d=True)  # altitude on the line is 100 + k; best trade-off is past k = 100
        brute = min(math.sqrt((500000 + 10 * k - 501003) ** 2 + 300 ** 2 + (100 + k - 500) ** 2) for k in range(201))
        self.assertAlmostEqual(deep["distance_m"], brute, delta=0.01)
        self.assertEqual(m.farthest(track, target)["point"]["utm"], "35U 500000 6050000")
        self.assertEqual(m.closest([], target)["flags"], ["no usable points"])

    def test_flags_travel_with_the_deciding_point(self):
        track = line()
        track[100].update(alt_source="gps", position_source="fallback")
        r = m.closest(track, utm(501000, 6050050, 200), three_d=True)
        self.assertEqual(r["flags"], ["point: recorded on the fallback position source", "point: GPS altitude, not barometric"])
        self.assertEqual(m.closest(track, utm(501000, 6050050))["flags"], ["point: recorded on the fallback position source"])
        nofix = dict(utm(501000, 6050000, 200), fix=False, marker=1)
        self.assertIn("a: recorded without a GPS fix", m.distance(nofix, utm(501000, 6050050))["flags"])
        lost = {"lat": None, "lon": None, "marker": 2}
        self.assertEqual((m.distance(lost, track[0])["distance_m"], m.distance(lost, track[0])["flags"]), (None, ["a: no position"]))

    def test_length(self):
        track = line()
        self.assertAlmostEqual(m.length(track)["length_m"], 2000, delta=0.05)
        self.assertAlmostEqual(m.length(track, three_d=True)["length_m"], 200 * math.hypot(10, 1), delta=0.05)
        self.assertEqual((m.length(track)["segments"], m.length(track)["not_bridged"], m.length(track)["flags"]), (200, {}, []))

    def test_length_never_bridges(self):
        track = line()
        holed = track[:80] + track[121:]
        r = m.length(holed)
        self.assertAlmostEqual(r["length_m"], 790 + 790, delta=0.05)
        self.assertEqual((r["not_bridged"], r["flags"]), ({"removed fixes": 1}, []))  # the caller's own selection: no alarm
        paused = line()
        for p in paused[100:]:
            p["t"] += 120
        r = m.length(paused)
        self.assertAlmostEqual(r["length_m"], 1990, delta=0.05)
        self.assertEqual(r["not_bridged"], {"pause in the log": 1})
        self.assertIn("minimum", r["flags"][0])
        mixed = line()
        for p in mixed[50:60]:
            p["alt_source"] = "gps"
        self.assertAlmostEqual(m.length(mixed)["length_m"], 2000, delta=0.05)  # 2D does not care
        r = m.length(mixed, three_d=True)
        self.assertEqual((r["segments"], r["not_bridged"]), (198, {"altitude source changes or is missing": 2}))

    def test_pause_is_seen_even_in_a_tiny_selection(self):
        paused = line()
        for p in paused:
            p["interval_s"] = 1.0  # what flight_data records for a 1 s log
        for p in paused[100:]:
            p["t"] += 600
        r = m.length(paused[99:101])  # only the two fixes either side of the pause
        self.assertEqual((r["segments"], r["length_m"], r["not_bridged"]), (0, 0, {"pause in the log": 1}))
        ring = m.circle(paused[100], 1e6)
        t = m.time_inside(paused, ring)
        self.assertEqual((t["seconds"], t["not_bridged"]), (199, {"pause in the log": 1}))
        self.assertIn("minimum", " ".join(t["flags"]))

    def test_length_says_when_nothing_could_be_measured_and_when_it_rests_on_gps(self):
        track = line()
        r = m.length(track[::7])
        self.assertEqual((r["length_m"], r["segments"]), (0, 0))
        self.assertIn("nothing could be measured", " ".join(r["flags"]))
        self.assertIn("200 segments use GPS altitude", " ".join(m.length(line(source="gps"), three_d=True)["flags"]))
        self.assertEqual(m.length(line(source="gps"))["flags"], [])

    def test_points_without_a_position_never_crash(self):
        track = line()
        lost = {"lat": None, "lon": None, "marker": 3, "time_utc": "2026-07-02T10:00:00+00:00"}
        ring = m.circle(track[100], 205)
        self.assertIsNone(m.contains(ring, lost))
        self.assertEqual(len(m.inside([lost] + track, ring)), 41)
        self.assertEqual(m.closest(track, lost)["flags"], ["target has no position"])
        self.assertEqual(m.closest(track, utm(501000, 6050300), three_d=True)["flags"], ["target has no altitude; 3D not possible"])
        self.assertEqual((m.bearing(lost, track[0]), m.angle(track[0], lost, track[1]), m.area(lost, track[0], track[1])), (None, None, None))
        self.assertEqual((m.bearing(track[0], track[0]), m.angle(track[0], track[0], track[1])), (None, None))

    def test_stays_are_not_merged_across_removed_fixes(self):
        track = line()
        for k, p in enumerate(track):
            p["alt_m"] = 200.0 if k % 2 == 0 else 50.0  # above and below the floor on alternate fixes
        ring = m.circle(track[100], 1e6, floor_m=100)
        self.assertEqual(m.time_inside(track, ring)["stays"], 101)
        self.assertEqual(m.time_inside(m.altitude_between(track, 100, None), ring)["stays"], 101)

    def test_inside_a_volume_by_fixes_only(self):
        track = line()
        ring = m.circle(utm(501000, 6050000), 205)
        self.assertAlmostEqual(m.length_inside(track, ring)["length_m"], 400, delta=0.05)
        self.assertEqual(m.length_inside(track, ring)["flags"], [])
        self.assertEqual(m.time_inside(track, ring)["seconds"], 40)
        self.assertEqual(m.first_entry(track, ring)["point"]["utm"], "35U 500800 6050000")
        self.assertEqual(m.last_exit(track, ring)["point"]["utm"], "35U 501200 6050000")
        odd = m.circle(utm(501005, 6050000), 200)  # edges fall between fixes: 500805..501205, fixes inside 500810..501200
        self.assertAlmostEqual(m.length_inside(track, odd)["length_m"], 390, delta=0.05)
        self.assertEqual(m.first_entry(track, odd)["point"]["utm"], "35U 500810 6050000")

    def test_several_stays(self):
        out_and_back = line(101) + [dict(p, i=101 + k, t=T0 + 101 + k) for k, p in enumerate(reversed(line(101)))]
        ring = m.circle(utm(500500, 6050000), 105)
        self.assertEqual(len(m.runs_inside(out_and_back, ring)), 2)
        self.assertEqual((m.time_inside(out_and_back, ring)["seconds"], m.time_inside(out_and_back, ring)["stays"]), (40, 2))
        self.assertAlmostEqual(m.length_inside(out_and_back, ring)["length_m"], 400, delta=0.05)
        self.assertEqual(m.first_entry(out_and_back, ring)["stays"], 2)
        end = m.circle(utm(500000, 6050000), 50)
        self.assertIn("still inside at the last point", m.last_exit(out_and_back, end)["flags"])
        self.assertEqual(m.first_entry(line(), m.circle(utm(510000, 6050000), 50))["flags"], ["never inside"])

    def test_bearing_angle_area(self):
        a, b, c = utm(500000, 6050000), utm(500100, 6050000), utm(500100, 6050100)
        def off(found, expected):  # difference on the circle, so 359.999 equals 0
            return abs((found - expected + 180) % 360 - 180)

        self.assertLess(off(m.bearing(a, b), 90), 1e-6)
        self.assertLess(off(m.bearing(b, a), 270), 1e-6)
        self.assertLess(off(m.bearing(b, c), 0), 1e-6)
        self.assertAlmostEqual(m.angle(a, b, c), 90, delta=1e-6)
        self.assertAlmostEqual(m.angle(a, b, utm(500200, 6050000)), 180, delta=1e-6)
        self.assertAlmostEqual(m.angle(c, b, utm(500200, 6050100)), 45, delta=1e-6)
        self.assertAlmostEqual(m.area(a, b, c), 5000, delta=0.05)

    def test_bearing_does_not_jump_when_the_far_point_crosses_a_zone(self):
        origin = m.point(54.6, 23.98)  # zone 34
        just_west, just_east = m.point(54.62, 24.0199), m.point(54.62, 24.0201)  # 13 m apart, either side of... the mean
        self.assertLess(abs(m.bearing(origin, just_west) - m.bearing(origin, just_east)), 0.5)
        on_35 = m.bearing(origin, just_east, zone=35)
        self.assertGreater(abs(on_35 - m.bearing(origin, just_east)), 4)  # grid north differs by about 5 degrees between 34 and 35 here
        self.assertAlmostEqual(m.bearing(origin, just_west, zone=35), on_35, delta=0.5)

    def test_target_from_digits(self):
        here = m.point(54.605, 24.023)
        goal = m.point_from_digits("1234/4856,500", here)
        self.assertEqual((goal["utm"], goal["grid_zone"]), ("35U 312340 6048560", 35))
        self.assertAlmostEqual(goal["alt_m"], 152.4, delta=0.01)
        west = m.point(54.605, 23.99)
        self.assertEqual(m.point_from_digits("1234/4856", west)["grid_zone"], 34)
        self.assertEqual(m.point_from_digits("1234/4856", west, zone=35)["utm"], "35U 312340 6048560")
        self.assertEqual((m.point_from_digits("1234/4856", west)["grid_easting"], m.point_from_digits("1234/4856", west)["utm"][:3]),
                         (712340, "35U"))
        with self.assertRaises(ValueError):
            m.point_from_digits("north field", here)

    def test_target_from_digits_against_a_record_or_the_loaded_data(self):
        data = flight_data.load([FIX], competition_zone=35)
        record = flight_data.counting(data, "date 2026-07-02")[901]
        by_record = m.point_from_digits("0725/5532,167", record, zone=35)
        by_data = m.point_from_digits("0725/5532,167", data, zone=35)
        self.assertEqual((by_record["utm"], by_data["utm"]), ("35U 307250 6055320", "35U 307250 6055320"))
        self.assertTrue(any("from a UTM zone boundary" in w for w in by_data["warnings"]))  # 989 m from 24 E
        self.assertEqual(flight_data.reference_position(record), flight_data.reference_position([record]))
        with self.assertRaises(ValueError):
            m.point_from_digits("0725/5532", None, zone=35)
        with self.assertRaises(ValueError):
            m.point_from_digits("0725/5532", {"lat": None, "lon": None})

    def test_goal_read_on_the_other_side_of_a_boundary_is_said(self):
        west = m.point(54.62, 23.99)
        goal = m.point_from_digits("0005/5532", west, zone=35)  # 300 050 E on grid 35 lies in zone 34
        self.assertEqual((goal["zone"], goal["grid_zone"], goal["grid_easting"]), (34, 35, 300050))
        self.assertTrue(any(w.startswith("point lies in zone 34; read on grid 35") for w in goal["warnings"]))

    def test_to_grid_and_grid_fields(self):
        p = utm(307305, 6055341)
        self.assertEqual(tuple(round(v) for v in flight_data.to_grid(p, 35)), (307305, 6055341))
        e, n = flight_data.to_grid(p, 34)
        self.assertEqual((round(e), round(n)), (694784, 6055430))
        self.assertEqual(flight_data.with_grid(dict(p), 34)["grid_utm"], "34U 694784 6055430")
        self.assertNotIn("grid_utm", flight_data.with_grid({"lat": None, "lon": None}, 34))
        self.assertAlmostEqual(math.hypot(*(a - b for a, b in zip(flight_data.to_grid(p, 34), flight_data.to_grid(utm(307405, 6055341), 34)))),
                               100.0, delta=0.05)

    def test_write_results_rounds_to_what_is_usable(self):
        rows = [{"pilot": 1, "distance_m": 110.123456789, "a": {"lat": 54.1234567891, "horizontal_m": 3.14159},
                 "flags": [1.23456789, "text"], "speed_kmh": 12.3456789, "count": 7}]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.csv")
            m.write_results(path, rows)
            with open(path, newline="") as fh:
                row = next(csv.DictReader(fh))
        self.assertEqual(row, {"pilot": "1", "distance_m": "110.12", "a.lat": "54.123457", "a.horizontal_m": "3.14",
                               "flags": "1.235; text", "speed_kmh": "12.346", "count": "7"})

    def test_nearest_targets(self):
        mark = utm(500045, 6050000)
        goals = {"task 3 goal": utm(500000, 6050000), "task 4 goal": utm(504000, 6050000),
                 "zone A": m.circle(utm(500000, 6050000), 100), "zone B": m.circle(utm(510000, 6050000), 100)}
        res = m.nearest_targets(mark, goals)
        self.assertEqual([r["name"] for r in res["ranked"]], ["zone A", "task 3 goal", "task 4 goal", "zone B"])
        self.assertEqual((res["nearest"], round(res["ranked"][1]["distance_m"]), round(res["ranked"][3]["distance_m"])), ("zone A", 45, 9855))
        lost = m.nearest_targets({"lat": None, "lon": None}, goals)
        self.assertEqual((lost["ranked"], lost["flags"]), ([], ["point: no position"]))
        res = m.nearest_targets(mark, {"a": utm(500000, 6050000), "b": {"lat": None, "lon": None}})
        self.assertEqual((res["nearest"], res["ranked"][-1]["distance_m"], res["flags"]), ("a", None, ["b: no position"]))


class OnRealFiles(unittest.TestCase):
    def test_every_pilot_appears_and_a_task_can_be_measured(self):
        data = flight_data.load([FIX])
        who = flight_data.counting(data, "date 2026-07-02")
        self.assertEqual(sorted(who), [901, 902, 903])
        self.assertEqual([p for p, r in who.items() if r is None], [903])  # only test files: listed, not dropped
        target = m.point_utm(35, 311700, 6053780, 300)
        rows = []
        for pilot, record in who.items():
            row = {"pilot": pilot, "file": record and record["file"], "marker": None, "track": None}
            if record:
                drops = [d for d in flight_data.markers_of(data, record) if d["marker"] == 4]
                row["marker"] = m.distance(drops[0], target) if drops else {"flags": ["no marker 4"]}
                row["track"] = m.closest(m.airborne(record["track"]), target, three_d=True)
            rows.append(row)
        self.assertAlmostEqual(rows[0]["marker"]["distance_m"], 11.6, delta=1.0)
        self.assertLess(rows[1]["marker"]["distance_m"], 60)
        self.assertEqual(rows[0]["marker"]["a"]["marker"], 4)
        self.assertLessEqual(rows[0]["track"]["horizontal_m"], rows[0]["track"]["distance_m"])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "results.csv")
            m.write_results(path, rows)
            with open(path, newline="") as fh:
                table = list(csv.DictReader(fh))
        self.assertEqual([r["pilot"] for r in table], ["901", "902", "903"])
        self.assertIn("marker.a.utm", table[0])
        self.assertEqual(table[2]["marker.distance_m"], "")


if __name__ == "__main__":
    unittest.main()
