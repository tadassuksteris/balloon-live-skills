"""Zones: one format, shapes for the primitives, and what a track did in them."""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "skills", "balloon-live-igc", "scripts"))
FIX = os.path.join(HERE, "fixtures")

import flight_data  # noqa: E402
import measure as m  # noqa: E402
import zones as Z  # noqa: E402
from geo import ft_to_m, utm_to_latlon  # noqa: E402

T0 = 1782986400.0


def line(count=201, step=10.0, climb=1.0):
    """Due east along northing 6,050,000 from easting 500,000 in zone 35: 10 m and 1 m up per second, from 100 m."""
    pts = []
    for k in range(count):
        p = m.point_utm(35, 500000 + step * k, 6050000, 100 + climb * k)
        p.update(i=k, t=T0 + k, time_utc=f"2026-07-02T10:{k // 60:02d}:{k % 60:02d}+00:00", time_local=f"13:{k // 60:02d}:{k % 60:02d}",
                 alt_source="baro", position_source="primary", phase="airborne", interval_s=1)
        pts.append(p)
    return pts


class Format(unittest.TestCase):
    def test_zone_kinds_and_roundtrip(self):
        lat, lon = utm_to_latlon(501000, 6050000, 35)
        zs = [Z.zone("PZ1", "pz", lat=lat, lon=lon, radius_m=200, ceiling_ft=820, source="scorer"),
              Z.zone("contest", "contest", corners=[(54.5, 23.9), (54.5, 24.3), (54.8, 24.3), (54.8, 23.9)]),
              Z.zone("launch 2", "launch", lat=54.6, lon=24.0, note="common launch point"),
              Z.zone("PZ9", "pz", lat=54.7, lon=24.1, ceiling_ft=1000)]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "zones.json")
            Z.save_zones(zs, path)
            back = Z.load_zones(path)
        self.assertEqual(back, zs)
        built, incomplete = Z.shapes(back)
        self.assertEqual((built["PZ1"]["kind"], round(built["PZ1"]["ceiling_m"], 2), built["contest"]["kind"]), ("circle", round(ft_to_m(820), 2), "polygon"))
        self.assertNotIn("kind", built["launch 2"])  # a point
        self.assertEqual(incomplete, {"PZ9": "altitude limits but no radius or corners"})
        with self.assertRaises(ValueError):
            Z.zone("bad", "pz")
        with self.assertRaises(ValueError):
            Z.zone("bad", "pz", corners=[(1, 2), (3, 4)])


class Report(unittest.TestCase):
    def test_track_through_a_cylinder(self):
        track = line()
        lat, lon = utm_to_latlon(501000, 6050000, 35)
        pz = Z.zone("PZ1", "pz", lat=lat, lon=lon, radius_m=200, ceiling_ft=round(250 / 0.3048))   # ceiling 250 m
        low = Z.zone("PZ2", "pz", lat=lat, lon=lon, radius_m=200, ceiling_ft=657)  # 200.25 m: the fix at 200 m is still inside
        far = Z.zone("PZ3", "pz", lat=utm_to_latlon(505000, 6050000, 35)[0], lon=utm_to_latlon(505000, 6050000, 35)[1], radius_m=100, ceiling_ft=5000)
        point = Z.zone("launch", "launch", lat=lat, lon=lon)
        rows = {r["zone"]: r for r in Z.zone_report(track, [pz, low, far, point])}
        self.assertEqual(set(rows), {"PZ1", "PZ2", "PZ3"})  # points are not reported
        r = rows["PZ1"]
        self.assertEqual((r["entered"], len(r["stays"]), r["fixes_inside"], r["closest_horizontal_m"]), (True, 1, 41, 0.0))
        self.assertEqual((r["stays"][0]["entry"]["time_local"], r["stays"][0]["exit"]["time_local"], r["time_inside_s"]), ("13:01:20", "13:02:00", 40.0))
        self.assertAlmostEqual(r["deepest_ft"], 820 - 180 / 0.3048, delta=1)  # lowest fix inside is at 180 m
        r = rows["PZ2"]
        self.assertEqual((r["entered"], r["fixes_inside"], r["stays"][0]["exit"]["time_local"]), (True, 21, "13:01:40"))  # above 200 m after x=1000
        r = rows["PZ3"]
        self.assertEqual((r["entered"], r["stays"], r["deepest_ft"]), (False, [], None))
        self.assertAlmostEqual(r["closest_horizontal_m"], 2900.0, delta=0.5)

    def test_flags_and_incomplete(self):
        track = line()
        for p in track[85:95]:
            p["alt_source"] = "gps"
        for p in track[95:100]:
            p["alt_m"] = None
        lat, lon = utm_to_latlon(501000, 6050000, 35)
        rows = Z.zone_report(track, [Z.zone("PZ1", "pz", lat=lat, lon=lon, radius_m=200, ceiling_ft=5000),
                                     Z.zone("PZ9", "pz", lat=lat, lon=lon, ceiling_ft=1000)])
        self.assertIn("some fixes inside carry GPS altitude, not barometric", rows[0]["flags"])
        self.assertTrue(any("could not be placed" in f for f in rows[0]["flags"]))
        self.assertEqual((rows[1]["entered"], rows[1]["flags"]), (None, ["incomplete: altitude limits but no radius or corners"]))
        empty = Z.zone_report([], [Z.zone("PZ1", "pz", lat=lat, lon=lon, radius_m=200)])[0]
        self.assertEqual((empty["entered"], empty["flags"]), (None, ["no track"]))

    def test_on_a_real_track(self):
        data = flight_data.load([FIX])
        rec = flight_data.counting(data, "date 2026-07-02")[901]
        mark = flight_data.markers_of(data, rec)[0]
        around = Z.zone("around mark 1", "test", lat=mark["lat"], lon=mark["lon"], radius_m=300, ceiling_ft=5000)
        row = Z.zone_report(rec["track"], [around])[0]
        self.assertTrue(row["entered"] and row["time_inside_s"] > 0 and row["deepest_ft"] > 0)


if __name__ == "__main__":
    unittest.main()
