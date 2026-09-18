"""Tests for the balloon-live-igc scripts. Run: python3 -m unittest discover -s tests/balloon-live-igc"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "skills", "balloon-live-igc", "scripts"))
FIX = os.path.join(HERE, "fixtures")

import bl_igc  # noqa: E402
import check_flight  # noqa: E402
import flight_data  # noqa: E402
import geo  # noqa: E402

HEAD = ["AXBLTESTCODEBalloonLive", "HFXII:XX0:054143FXA4445SIU4646LAD4747LOD4848PAD",
        "HFXII:XL1:034141LAD4242LOD4343PAD", "I063638FXA3940SIU4141LAD4242LOD4343PAD4447VAR", "J010813DTE", "HFDTE020125"]
B = "B0304051220740N04559259WA0045600124005076280120"  # from the app's IgcBuilder.test.ts


def codes(flight):
    return [w.split(":")[0] for w in flight["warnings"]]


class Records(unittest.TestCase):
    def test_b_record_from_app_test_vector(self):
        f = bl_igc.parse_lines(HEAD + [B])["fixes"][0]
        self.assertEqual(f["time_utc"], "2025-01-02T03:04:05+00:00")
        self.assertAlmostEqual(f["lat"], 12.345678, delta=2e-5)
        self.assertAlmostEqual(f["lon"], -45.987654, delta=2e-5)
        self.assertEqual((f["fix"], f["pressure_alt_std"], f["gps_alt"]), (True, 456.8, 124))
        self.assertEqual((f["accuracy"], f["satellites"], f["vario"]), (5, 7, 12.0))

    def test_declaration_records_from_app_test_vectors(self):
        fl = bl_igc.parse_lines(HEAD + ["E030405XX1071234/5678,1500", "E030405XL1071220740N04559259WA00456001246281234/5678,1500"])
        self.assertEqual(len(fl["events"]), 1)
        d = fl["events"][0]
        self.assertEqual((d["type"], d["number"], d["text"]), ("goal_declaration", 7, "1234/5678,1500"))
        self.assertAlmostEqual(d["lat"], 12.345678, delta=2e-5)
        self.assertEqual((d["pressure_alt_std"], d["gps_alt"]), (456.8, 124))

    def test_xl1_without_layout_keeps_xx1_text(self):
        head = [l for l in HEAD if "XL1" not in l]
        fl = bl_igc.parse_lines(head + ["E030405XX1071234/5678,1500", "E030405XL1071220740N04559259WA00456001246281234/5678,1500"])
        self.assertEqual(fl["events"][0]["text"], "1234/5678,1500")
        self.assertIn("no-hfxii", codes(fl))

    def test_signs_and_overflow(self):
        fl = bl_igc.parse_lines(HEAD + ["B1912380000000S00000000WV-00710000000000004-005",
                                        "B1912390000000S00000000WV-00000000000000005-005",
                                        "B1912400000000N00000000EA9999999999999990009999"])
        a, b, c = fl["fixes"]
        self.assertEqual((a["pressure_alt_std"], a["vario"], a["valid"], a["lat"], a["lon"]), (-71.4, -0.5, False, 0.0, 0.0))
        self.assertEqual(b["pressure_alt_std"], -0.5)
        self.assertEqual((c["pressure_alt_std"], c["gps_alt"], c["accuracy"], c["vario"]), (None, None, None, None))
        self.assertIn("overflow", codes(fl))

    def test_layout_is_read_from_the_file(self):
        head = [l if not l.startswith("I") else "I023638FXA3940SIU" for l in HEAD]
        f = bl_igc.parse_lines(head + [B])["fixes"][0]
        self.assertEqual((f["pressure_alt_std"], f["vario"]), (456.0, None))

    def test_midnight_with_and_without_k_record(self):
        late, early = "B2359590000000N00000000EA0000000000000000000000", "B0000010000000N00000000EA0000000000000000000000"
        for lines, warned in (([late, "K000000030125", early], False), ([late, early, "K000001030125"], True), ([late, early], True)):
            fl = bl_igc.parse_lines(HEAD + lines)
            self.assertEqual([f["time_utc"][:10] for f in fl["fixes"]], ["2025-01-02", "2025-01-03"])
            self.assertEqual("midnight" in codes(fl), warned)

    def test_bad_lines_are_skipped_not_fatal(self):
        fl = bl_igc.parse_lines(HEAD + ["B12000054369X1N02359282EA0005200062001167850000", "Bxx0000", "E1200", "Ixx", B])
        self.assertEqual(len(fl["fixes"]), 1)
        self.assertIn("unreadable-line", codes(fl))

    def test_no_date_header(self):
        fl = bl_igc.parse_lines([l for l in HEAD if not l.startswith("HFDTE")] + [B])
        self.assertIn("no-date", codes(fl))

    def test_file_text_is_sanitised(self):
        fl = bl_igc.parse_lines(HEAD + ["HFPLTPILOTINCHARGE:Eve\x1b[2J\x07 " + "x" * 300, "E030405XX101run\x00this"])
        self.assertNotIn("\x1b", fl["header"]["pilot_name"])
        self.assertLessEqual(len(fl["header"]["pilot_name"]), bl_igc.MAX_TEXT)
        self.assertEqual(fl["events"][0]["text"], "run?this")

    def test_xxc_attaches_to_preceding_action_and_checks_echo(self):
        marker = "E174751XX0015436521N02400970EA002860029600121555"
        fl = bl_igc.parse_lines(HEAD + [marker, "E174752XXCBgEqAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"])
        self.assertIsNotNone(fl["events"][0]["bls"])
        self.assertIn("xxc-time", codes(fl))
        fl = bl_igc.parse_lines(HEAD + [marker, "E174751XXCBwIqAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"])
        self.assertIn("xxc-mismatch", codes(fl))
        self.assertIn("orphan", codes(bl_igc.parse_lines(HEAD + ["E174751XXCBgEqAAAA"])))


class Fixtures(unittest.TestCase):
    def test_app_v2035(self):
        fl = bl_igc.parse_file(os.path.join(FIX, "app-v2035-flight.igc"))
        h = fl["header"]
        self.assertEqual((h["source"], h["pilot_number"], h["app_version"], h["qnh_hpa"]), ("app", 901, "2035", 1012.0))
        self.assertEqual((h["bls_serial"], h["bls_cert_serial"], h["utc_shift_s"]), (1, "10000001", 10800))
        self.assertEqual((h["declaration_digits"], h["altitude_unit"], h["position_source"]), ("4/4", "feet", "BLS"))
        markers = [e for e in fl["events"] if e["type"] == "marker_drop"]
        self.assertEqual([m["marker"] for m in markers], [1, 2, 3, 4, 5])
        self.assertTrue(all(m["quality_from"] == "record" and m["fix_effective"] for m in markers))
        self.assertTrue(all(m["bls"]["kind"] == "marker" and m["app_snapshot"] for m in markers))
        self.assertAlmostEqual(markers[0]["bls"]["lat"], markers[0]["lat"], delta=2e-5)
        self.assertTrue(fl["signature_present"])
        self.assertEqual(fl["warnings"], [])

    def test_app_v3017_quality_byte_and_missing_serial(self):
        fl = bl_igc.parse_file(os.path.join(FIX, "app-v3017-flight.igc"))
        self.assertIsNone(fl["header"]["bls_serial"])
        m = [e for e in fl["events"] if e["type"] == "marker_drop"][0]
        self.assertEqual((m["fix"], m["satellites"]), (False, 37))
        self.assertEqual((m["fix_effective"], m["satellites_effective"], m["quality_from"]), (True, 18, "xxc"))
        self.assertEqual(sorted(codes(fl)), ["no-bls-serial", "quality-byte", "var-zero"])
        self.assertEqual(sum(e["type"] == "app_state" for e in fl["events"]), 10)

    def test_sensor_file(self):
        fl = bl_igc.parse_file(os.path.join(FIX, "sensor-nogps.igc"))
        h = fl["header"]
        self.assertEqual((h["source"], h["fr_id"], h["bls_serial"], h["pilot_number"]), ("sensor", "TST01", 1, 904))
        self.assertEqual((h["pilot_name"], h["qnh_hpa"], h["firmware"], h["utc_shift_s"]), ("Test PILOT", 1021.0, "FS.04.17, 3a1a9cd", None))
        self.assertEqual(sum(f["valid"] for f in fl["fixes"]), 0)
        markers = [e for e in fl["events"] if e["type"] == "marker_drop"]
        self.assertEqual([(m["marker"], m["fix"], m["pressure_alt_std"]) for m in markers], [(1, False, -71.5), (2, False, -71.5)])
        d = [e for e in fl["events"] if e["type"] == "goal_declaration"][0]
        self.assertEqual((d["number"], d["text"]), (2, "4334/3434,4334"))
        self.assertEqual((d["bls"]["kind"], d["bls"]["command"], d["bls"]["pressure_alt_std"]), ("declaration", 7, -71.4))
        self.assertFalse(fl["signature_present"])  # zero-filled line, no key on the sensor
        self.assertEqual(codes(fl), ["no-gps"])

    def test_declaration_and_source_switches(self):
        fl = bl_igc.parse_file(os.path.join(FIX, "app-test-declaration.igc"))
        d = [e for e in fl["events"] if e["type"] == "goal_declaration"][0]
        self.assertEqual((d["number"], d["text"]), (1, "1234/4856,168"))
        self.assertAlmostEqual(d["lat"], 54.605, delta=1e-3)
        self.assertNotIn("bls", d)  # declared on the phone's GPS, so no sensor response
        switches = [(e["to"], e["is_primary"]) for e in fl["events"] if e["type"] == "source_change"]
        self.assertEqual(switches, [("INT", False), ("BLS", True), ("INT", False)])  # XS1 = primary


class CheckFlight(unittest.TestCase):
    def test_fixture_folder(self):
        out = check_flight.run([FIX])
        self.assertEqual({k: v["qnh"] for k, v in out["flights"].items()}, {"date 2026-07-02": 1012.0, "date 2026-08-02": 1021.0})
        rows = {r["file"]: r for r in out["files"]}
        self.assertEqual(rows["app-test-nofix.igc"]["status"], "test")
        sensor = rows["sensor-nogps.igc"]
        self.assertEqual((sensor["status"], sensor["time_basis"]), ("test", "UTC (no offset in file)"))
        flight = rows["app-v3017-flight.igc"]  # one fix every 20 s
        self.assertEqual(flight["status"], "flight")
        self.assertAlmostEqual(flight["max_alt_m"], geo.qnh_altitude(953.3, 1012), delta=0.5)
        self.assertGreater(flight["max_speed_kmh"], 10)
        self.assertLess(flight["duration_s"], 45 * 60)
        self.assertTrue(all(m["file"] and m["file_status"] for m in out["markers"]))
        self.assertEqual(sum(m["file_status"] == "test" for m in out["markers"]), 4)  # never silently dropped
        decl = {d["pilot"]: d for d in out["declarations"]}
        self.assertEqual((decl[903]["alt_ft"], decl[903]["digits_from"], decl[903]["alt_unit_from"]), (168.0, "header", "header"))
        self.assertEqual((decl[903]["utm"], decl[903]["grid_zone"], decl[903]["grid_from"]),
                         ("35U 312340 6048560", 35, "zone of declaration position"))
        self.assertEqual((decl[904]["digits_from"], decl[904]["alt_unit_from"], decl[904]["utm"]), ("ASSUMED", "ASSUMED", None))
        first = [m for m in out["markers"] if m["pilot"] == 901][0]
        self.assertTrue(first["utm"].startswith("35U 3073") and first["counts"] and first["alt_source"] == "baro")
        self.assertEqual(sorted({p["utm_zone"] for p in rows["app-v2035-flight.igc"]["track"]}), ["34U", "35U"])
        text = check_flight.render(out)
        for expected in ("QNH 1012.0 hPa (most common header value)", "each point's own zone", "ASSUMED",
                         "test file sensor-nogps.igc", "zones 34/35"):
            self.assertIn(expected, text)
        self.assertNotIn("track", check_flight.without_tracks(out)["files"][0])

    def _write(self, folder, name, lines):
        with open(os.path.join(folder, name), "w", newline="") as fh:
            fh.write("\r\n".join(lines) + "\r\n")

    def _flight(self, qnh="1012", pid="001", date="310726", start=23 * 3600 + 40 * 60, step=1, ground_after=True):
        """Synthetic flight: 5 min on the ground, climb to 500 m, cruise, descend, optional 5 min standstill."""
        lines = ["AXBLTESTCODEBalloonLive", "I063638FXA3940SIU4141LAD4242LOD4343PAD4447VAR", "J010813DTE",
                 f"HFDTE{date}", f"HFPID{pid}", f"HFATS{qnh}", "LXXX utcshift=7200s"]
        profile = [60.0] * 300 + [60 + 2 * i for i in range(220)] + [500.0] * 600 + [500 - 2 * i for i in range(220)]
        profile += [60.0] * 300 if ground_after else []
        for i in range(0, len(profile), step):
            sod = (start + i) % 86400
            moving = min(max(i - 300, 0), 1040)
            lon_min = 30.0 + moving * 0.004  # about 4.3 m/s east at 54 N
            if start + i >= 86400 and start + i - step < 86400:
                lines.append(f"K{sod // 3600:02d}{sod // 60 % 60:02d}{sod % 60:02d}010826")
            lines.append(f"B{sod // 3600:02d}{sod // 60 % 60:02d}{sod % 60:02d}5430000N024{int(lon_min):02d}{int(lon_min % 1 * 1000):03d}E"
                         f"A{int(profile[i]):05d}{int(profile[i]) + 20:05d}00312000{int(profile[i] % 1 * 10)}0000")
        return lines

    def test_grid_shown_when_a_zone_is_given(self):
        own = check_flight.render(check_flight.run([FIX]))
        self.assertIn("in each point's own zone", own)
        self.assertNotIn("own zone", own.split("pilot  mk")[1])
        on34 = check_flight.run([FIX], zone=34)
        first = [k for k in on34["markers"] if k["pilot"] == 901 and k["marker"] == 1][0]
        self.assertEqual((first["utm"], first["grid_utm"], first["grid_zone"]), ("35U 307305 6055341", "34U 694784 6055430", 34))
        text = check_flight.render(on34)
        self.assertIn("on grid zone 34 (given; own zone noted where it differs)", text)
        self.assertIn("34U 694784 6055430", text)
        self.assertIn("own zone 35U 307305 6055341", text)
        self.assertNotIn("own zone", check_flight.render(check_flight.run([FIX], zone=35)).split("pilot  mk")[1])

    def test_copies_and_odd_files_are_said(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(FIX, "app-v2035-flight.igc"), "rb") as fh:
                content = fh.read()
            for name in ("app-v2035-flight.igc", "E[x]F[2]-copy.igc"):
                with open(os.path.join(tmp, name), "wb") as fh:
                    fh.write(content)
            os.makedirs(os.path.join(tmp, "sub"))
            self._write(tmp, "readme.txt", ["notes"])
            self._write(tmp, "one-line.igc", ["B1000005430000N02400000EA0010000100"])
            out = check_flight.run([tmp])
        self.assertEqual(len(out["files"]), 1)
        self.assertTrue(any("holds the same recording as" in w and "only" in w for w in out["warnings"]))
        self.assertEqual(out["skipped"], ["readme.txt"])  # the folder is not a "non-IGC file"
        self.assertTrue(any(u.endswith("one-line.igc: not an IGC file") for u in out["unreadable"]))

    def test_far_competition_zone_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            check_flight.run([FIX], zone=3)
        self.assertIn("zone 3 is nowhere near the files", str(caught.exception))
        self.assertEqual(check_flight.run([FIX], zone=34)["competition_zone"], 34)  # one zone away is fine

    def test_bad_paths_are_errors_not_tracebacks(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError) as caught:
                check_flight.run([FIX], pilots_csv=os.path.join(tmp, "none.csv"))
            self.assertIn("pilot list", str(caught.exception))
            self._write(tmp, "afile", ["x"])
            with self.assertRaises(ValueError) as caught:
                check_flight.run([FIX], csv_dir=os.path.join(tmp, "afile"))
            self.assertIn("cannot create", str(caught.exception))

    def test_raw_quality_byte_without_xxc_is_decoded(self):
        with open(os.path.join(FIX, "app-v3017-flight.igc"), encoding="ascii") as fh:
            lines = [l for l in fh.read().splitlines() if "XXC" not in l]
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "noxxc.igc", lines)
            out = check_flight.run([tmp])
            self._write(tmp, "noxxc.igc", [l.replace("HFRFWVersion 3017", "HFRFWVersion 2036") for l in lines])
            other = check_flight.run([tmp])
        first = min(out["markers"], key=lambda k: k["marker"])
        self.assertEqual((first["fix"], first["satellites"], first["lat"] is not None), (True, 18, True))
        self.assertTrue(first["quality_from"].startswith("raw quality byte decoded"))
        self.assertIn("decoded from the raw quality byte", check_flight.render(out))
        # another version writes fix V for real: the position is kept, but flagged
        first = min(other["markers"], key=lambda k: k["marker"])
        self.assertEqual((first["fix"], first["lat"] is not None, first["quality_from"]), (False, True, "record"))
        self.assertIn("NO FIX, position as recorded", check_flight.render(other))

    def test_markers_on_the_fallback_source_are_flagged(self):
        out = check_flight.run([FIX])
        on_fallback = [k for k in out["markers"] if k.get("position_source") == "fallback"]
        self.assertTrue(on_fallback)
        self.assertEqual(check_flight.render(out).count("ON FALLBACK SOURCE"), len(on_fallback))

    def test_quiet_pilot_offset_banner_and_json_units(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "a.igc", self._flight(start=36000, date="020726"))
            out = check_flight.run([tmp], utc_offset_h=3)
        text = check_flight.render(out)
        self.assertIn("no marker drops in the counting file: pilot 1", text)
        self.assertIn("times at UTC+3 (given)", text)
        self.assertNotIn("no marker drops", check_flight.render(check_flight.run([FIX])))
        public = check_flight.without_tracks(check_flight.run([FIX], alt_unit="ft"))
        self.assertEqual(public["alt_unit"], "m")
        self.assertIn("metres", public["legend"]["altitudes"])
        self.assertTrue({"status", "counts", "alt_source", "position_source"} <= set(public["legend"]))

    def test_flight_across_midnight_and_month_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "a.igc", self._flight())
            row = check_flight.run([tmp])["files"][0]
        self.assertEqual((row["status"], row["dropped"]), ("flight", {}))
        self.assertAlmostEqual(row["duration_s"], 1040, delta=60)
        self.assertAlmostEqual(row["max_alt_m"], geo.qnh_altitude(500, 1012), delta=1)

    def test_slow_logging_interval_and_log_ending_airborne(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "slow.igc", self._flight(step=20, start=10 * 3600, date="020726"))
            self._write(tmp, "cut.igc", self._flight(pid="002", ground_after=False, start=10 * 3600, date="020726"))
            rows = {r["file"]: r for r in check_flight.run([tmp])["files"]}
        self.assertEqual(rows["slow.igc"]["status"], "flight")
        self.assertGreater(rows["slow.igc"]["max_speed_kmh"], 10)
        self.assertEqual((rows["cut.igc"]["status"], rows["cut.igc"]["notes"]), ("partial", ["log ends airborne"]))

    def test_qnh_mismatch_tie_default_and_grouping(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i, q in enumerate(["1012", "1012", "1018"]):
                self._write(tmp, f"E[x]F[1]P[{i}].igc", self._flight(qnh=q, pid=f"00{i}", start=36000, date="020726"))
            self._write(tmp, "E[x]F[2]P[0].igc", self._flight(qnh="1005", start=64800, date="020726"))
            out = check_flight.run([tmp])
        self.assertEqual({k: v["qnh"] for k, v in out["flights"].items()}, {"E[x]F[1]": 1012.0, "E[x]F[2]": 1005.0})
        header = {"date_utc": "2026-07-02"}
        self.assertEqual(flight_data.flight_key("/x/003-260702172625.igc", header), "flight 3, date 2026-07-02")
        self.assertEqual(flight_data.flight_key("/x/682X0QT1.igc", header), "date 2026-07-02")
        self.assertEqual(sum("QNH MISMATCH" in w for w in out["warnings"]), 1)
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "a.igc", self._flight(qnh="1012", start=36000, date="020726"))
            self._write(tmp, "b.igc", self._flight(qnh="1018", pid="002", start=36000, date="020726"))
            self._write(tmp, "c.igc", [l for l in self._flight(pid="003", start=36000, date="030726") if not l.startswith("HFATS")])
            out = check_flight.run([tmp])
        self.assertTrue(any("tie" in w for w in out["warnings"]))
        self.assertEqual(out["flights"]["date 2026-07-03"]["qnh"], 1013.25)
        self.assertTrue(any("no QNH in any header" in w for w in out["warnings"]))

    def test_short_baro_dropout_takes_gps_not_an_invented_value(self):
        lines = self._flight(start=36000, date="020726")
        hit = [i for i, l in enumerate(lines) if l.startswith("B")][600:603]
        for i in hit:
            lines[i] = lines[i][:25] + "00000" + lines[i][30:]
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "a.igc", lines)
            out = check_flight.run([tmp], csv_dir=os.path.join(tmp, "csv"))
            with open(os.path.join(tmp, "csv", "a.csv")) as fh:
                csv_text = fh.read()
        row = out["files"][0]
        self.assertEqual((row["alt_source"], row["alt_gps_fixes"], row["alt_gaps"], row["dropped"]), ("baro", 3, 0, {}))
        self.assertEqual(csv_text.count(",gps,"), 3)
        self.assertNotIn("interpolated", csv_text)
        header = csv_text.splitlines()[0].split(",")
        for line in csv_text.splitlines():
            if ",gps," in line:
                self.assertEqual(line.split(",")[header.index("alt_std_m")], "")  # a dropout is not a reading of 0 m
        self.assertAlmostEqual(row["max_alt_m"], geo.qnh_altitude(500, 1012), delta=1)

    def test_long_baro_loss_shows_gps_as_measured_and_says_so(self):
        lines = self._flight(start=36000, date="020726")
        rows = [i for i, l in enumerate(lines) if l.startswith("B")]
        for i in rows[560:680]:  # two minutes around the top of the climb
            lines[i] = lines[i][:25] + "00000" + lines[i][30:]
        lines.insert(rows[560], "E100920XS0INT,,")
        lines.insert(rows[680] + 1, "E101120XS1BLS,1,1")
        lines.insert(7, "LXXX BLGPS=BLS")
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "a.igc", lines)
            out = check_flight.run([tmp], csv_dir=os.path.join(tmp, "csv"))
            with open(os.path.join(tmp, "csv", "a.csv")) as fh:
                csv_rows = fh.read().splitlines()
        row = out["files"][0]
        self.assertEqual((row["alt_source"], row["alt_gps_fixes"], row["alt_gaps"], row["dropped"]), ("baro", 120, 0, {}))
        self.assertAlmostEqual(row["gps_minus_baro_m"], 20 + geo.isa_height(1012), delta=1)  # synthetic GPS = std + 20 m
        self.assertAlmostEqual(row["max_alt_m"], geo.qnh_altitude(500, 1012), delta=1)  # barometric fixes only
        self.assertEqual(row["max_alt_gps_m"], 520.0)  # exactly what the file says, reported apart
        self.assertLess(row["max_climb_ms"], 2.5)  # no vertical speed across the change of source
        self.assertAlmostEqual(row["duration_s"], 1040, delta=60)
        messages = " ".join(row["warnings"])
        self.assertIn("GPS ALTITUDE shown there as measured", messages)
        self.assertRegex(messages, r"GPS reads about 3[01] m above barometric")
        self.assertTrue(any(",520.0,gps," in r for r in csv_rows))
        self.assertIn("fallback-source: 120 fixes", messages)
        self.assertEqual(sum(",gps," in r for r in csv_rows), 120)
        self.assertEqual(sum(r.endswith(",fallback") for r in csv_rows), 120)
        self.assertIn("GPS ALTITUDE in part", check_flight.render(out))

    def test_file_without_any_baro_uses_gps_uncalibrated(self):
        lines = [l[:25] + "00000" + l[30:] if l.startswith("B") else l for l in self._flight(start=36000, date="020726")]
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "a.igc", lines)
            out = check_flight.run([tmp])
        row = out["files"][0]
        self.assertEqual((row["alt_source"], row["gps_minus_baro_m"], row["status"]), ("gps", None, "flight"))
        self.assertEqual((row["max_alt_m"], row["max_alt_gps_m"]), (None, 520.0))
        self.assertIn("nothing in the file to compare it with", " ".join(row["warnings"]))
        self.assertIn("GPS", check_flight.render(out).split("a.igc")[0].splitlines()[-1] + check_flight.render(out))
        self.assertIn("GPS ALTITUDE only", check_flight.render(out))

    def test_sea_level_zero_is_not_a_dropout(self):
        lines = [l[:25] + "00000" + l[30:] if l.startswith("B") else l for l in self._flight(start=36000, date="020726")[:200]]
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "a.igc", lines)
            row = check_flight.run([tmp])["files"][0]
        self.assertEqual((row["alt_source"], row["alt_gaps"]), ("baro", 0))

    def test_odd_inputs_do_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "nopid.igc", [l for l in self._flight(start=36000, date="020726") if not l.startswith("HFPID")])
            self._write(tmp, "notes.igc", ["hello", "this is not an IGC file"])
            self._write(tmp, "readme.txt", ["x"])
            out = check_flight.run([tmp, os.path.join(tmp, "missing.igc")])
            self.assertIn("-", check_flight.render(out))
            self.assertEqual(len(out["files"]), 1)
            self.assertEqual(len(out["unreadable"]), 2)
            self.assertEqual(out["skipped"], ["readme.txt"])
            self.assertEqual(check_flight.run([os.path.join(tmp, "nothing")])["files"], [])

    def test_longest_flight_file_counts_unless_told_otherwise(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "whole.igc", self._flight(start=36000, date="020726"))
            self._write(tmp, "cut.igc", self._flight(start=50000, date="020726", ground_after=False)[:900])
            out = check_flight.run([tmp])
            again = check_flight.run([tmp], prefer={1: "cut.igc"})
        self.assertEqual({r["file"]: r["counts"] for r in out["files"]}, {"whole.igc": True, "cut.igc": False})
        self.assertTrue(any("whole.igc counts (the longest)" in w for w in out["warnings"]))
        self.assertIn("DOES NOT COUNT", check_flight.render(out))
        self.assertEqual({r["file"]: r["counts"] for r in again["files"]}, {"whole.igc": False, "cut.igc": True})
        self.assertTrue(any("as instructed" in w for w in again["warnings"]))

    def test_track_across_a_zone_boundary(self):
        lines = [l.replace("N02430", "N02359").replace("N02431", "N02400").replace("N02432", "N02401").replace("N02433", "N02402")
                 .replace("N02434", "N02403") for l in self._flight(start=36000, date="020726")]
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "a.igc", lines)
            row = check_flight.run([tmp])["files"][0]
        self.assertEqual((row["status"], row["zones"], row["dropped"]), ("flight", [34, 35], {}))
        self.assertAlmostEqual(row["track_km"], 4.5, delta=0.2)  # 1040 s at about 4.3 m/s, no jump at the boundary
        self.assertLess(row["max_speed_kmh"], 20)

    def test_preference_is_forgiving_and_ties_are_said(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "a.igc", self._flight(start=36000, date="020726"))
            self._write(tmp, "b.igc", self._flight(start=50000, date="020726"))
            tie = check_flight.run([tmp])
            told = check_flight.run([tmp], prefer={"1": os.path.join(tmp, "b.igc")})  # string key, full path
            wrong = check_flight.run([tmp], prefer={1: "nothing.igc"})
        self.assertTrue(any("TIE in length" in w for w in tie["warnings"]))
        self.assertEqual({r["file"]: r["counts"] for r in told["files"]}, {"a.igc": False, "b.igc": True})
        self.assertTrue(any("not one of its flight files" in w for w in wrong["warnings"]))

    def test_several_flights_must_be_named(self):
        data = flight_data.load([FIX])
        with self.assertRaises(ValueError):
            flight_data.counting(data)
        self.assertEqual(sorted(flight_data.counting(data, "date 2026-07-02")), [901, 902, 903])
        self.assertEqual(sorted(flight_data.counting(data, "date 2026-08-02")), [904])

    def test_times_are_utc_whatever_the_machine(self):
        self.assertEqual(flight_data.epoch("2026-07-02T10:00:00"), flight_data.epoch("2026-07-02T10:00:00+00:00"))
        self.assertEqual(flight_data.epoch("2026-07-02T10:00:00Z"), flight_data.epoch("2026-07-02T13:00:00+03:00"))
        self.assertEqual(flight_data.epoch(1782986400), 1782986400.0)

    def test_stale_coordinates_are_not_trusted(self):
        a = flight_data.with_utm({"lat": 54.6, "lon": 24.1})
        b = flight_data.with_utm({"lat": 54.6, "lon": 24.2})
        moved = dict(b, lat=54.7, lon=24.3)  # a copy with new lat/lon still carries the old UTM fields
        self.assertAlmostEqual(flight_data.point_distance(a, moved), geo.grid_distance(54.6, 24.1, 54.7, 24.3), delta=1e-6)
        self.assertGreater(flight_data.point_distance(a, moved), 15000)

    def test_pilot_list_with_bom_and_semicolons(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "p.csv")
            with open(path, "w", encoding="utf-8-sig") as fh:
                fh.write("901;Alice\n902;Bob\n")
            self.assertEqual(flight_data.load_pilots(path, print), {901: "Alice", 902: "Bob"})


class Geo(unittest.TestCase):
    def test_utm_known_points(self):
        e, n, z, b = geo.latlon_to_utm(45, 9)
        self.assertEqual((z, b), (32, "T"))
        self.assertAlmostEqual(e, 500000.0, delta=0.01)
        self.assertAlmostEqual(n, 4982950.4, delta=0.5)
        self.assertEqual(geo.latlon_to_utm(0, 3)[:2], (500000.0, 0.0))
        e, n, z, b = geo.latlon_to_utm(-33.8568, 151.2153)  # Sydney Opera House
        self.assertEqual((z, b), (56, "H"))
        self.assertAlmostEqual(e, 334900, delta=300)
        self.assertAlmostEqual(n, 6252300, delta=300)

    def test_zones_and_bands(self):
        self.assertEqual((geo.utm_zone(54.61, 23.99), geo.utm_zone(54.61, 24.00)), (34, 35))
        self.assertEqual((geo.utm_zone(60, 5), geo.utm_zone(78, 15), geo.utm_zone(10, 180.0)), (32, 33, 1))
        self.assertEqual(geo.latlon_to_utm(83.9, 40)[3], "X")

    def test_round_trip_in_forced_zone_and_south(self):
        for zone in (34, 35):
            e, n, _, _ = geo.latlon_to_utm(54.6108, 24.0034, zone)
            lat, lon = geo.utm_to_latlon(e, n, zone)
            self.assertAlmostEqual(lat, 54.6108, places=6)
            self.assertAlmostEqual(lon, 24.0034, places=6)
        e, n, z, _ = geo.latlon_to_utm(-22.9, -43.2)
        lat, lon = geo.utm_to_latlon(e, n, z, northern=False)
        self.assertAlmostEqual(lat, -22.9, places=6)
        self.assertAlmostEqual(geo.latlon_to_utm(0.001, 33, northern=False)[1] - geo.latlon_to_utm(-0.001, 33)[1], 221, delta=2)

    def test_antimeridian_and_far_digits(self):
        self.assertAlmostEqual(geo.grid_distance(10, 179.99995, 10, -179.99995), 10.96, delta=0.05)
        self.assertAlmostEqual(abs(geo.mean_lon([179.9, -179.9])), 180, delta=1e-9)  # -180 and 180 are the same meridian
        far = geo.expand_declaration("11700/53780", 54.605, 24.023)
        self.assertTrue(any("km from where it was declared" in w for w in far["warnings"]))

    def test_qnh(self):
        self.assertEqual(geo.isa_height(1013.25), 0.0)
        self.assertAlmostEqual(geo.qnh_altitude(52.5, 1012), 42.1, delta=0.1)
        # the app's own test vectors (lib/altitude.ts): the skill must give the same altitudes
        self.assertAlmostEqual(geo.qnh_altitude(1500, 1013.25), 1500, delta=0.1)
        self.assertAlmostEqual(geo.qnh_altitude(1000, 1000), 879.2, delta=0.1)
        self.assertAlmostEqual(geo.qnh_altitude(1000, 1030), 1154.8, delta=0.1)
        self.assertAlmostEqual(geo.isa_height(900), 988.7, delta=0.1)
        self.assertAlmostEqual(geo.isa_height(800), 1949.3, delta=0.1)
        with self.assertRaises(ValueError):
            geo.qnh_altitude(44330, 1030)  # no pressure left to correct
        self.assertAlmostEqual(geo.isa_height(1021), -64.3, delta=0.1)
        self.assertGreater(geo.qnh_altitude(100, 1030), 100)
        self.assertAlmostEqual(geo.m_to_ft(304.8), 1000.0, places=6)

    def test_distance_does_not_depend_on_a_zone_choice(self):
        a, b = (54.61, 23.95), (54.60, 24.05)  # either side of 24 E
        d = geo.grid_distance(*a, *b)
        self.assertEqual(d, geo.grid_distance(*b, *a))
        for zone in (34, 35):
            (e1, n1), (e2, n2) = geo.latlon_to_utm(*a, zone)[:2], geo.latlon_to_utm(*b, zone)[:2]
            self.assertAlmostEqual(d, ((e2 - e1) ** 2 + (n2 - n1) ** 2) ** 0.5, delta=0.5)
        self.assertAlmostEqual(geo.grid_distance(0.01, 33, -0.01, 33), 2211, delta=5)  # across the equator
        self.assertEqual((geo.format_utm(*a), geo.format_utm(*b)), ("34U 690505 6055394", "35U 309448 6054281"))

    def test_declaration_expansion(self):
        def at(easting, northing, zone=35):
            return geo.utm_to_latlon(easting, northing, zone)

        r = geo.expand_declaration("1234/4856,168", *at(320000, 6050000))
        self.assertEqual((r["grid_easting"], r["grid_northing"], r["grid_zone"], r["alt_ft"], r["warnings"]), (312340, 6048560, 35, 168.0, []))
        self.assertEqual(r["utm"], "35U 312340 6048560")
        self.assertAlmostEqual(r["alt_m"], 51.2, delta=0.1)
        self.assertEqual(geo.expand_declaration("0001/0000", *at(599990, 6000000))["grid_easting"], 600010)
        self.assertEqual(geo.expand_declaration("1234/4856,50", *at(320000, 6050000), alt_unit="m")["alt_m"], 50.0)
        self.assertEqual(geo.expand_declaration("1234/4856,50m", *at(320000, 6050000))["alt_m"], 50.0)
        self.assertEqual(geo.expand_declaration("512345/048567", *at(510000, 6050000))["grid_northing"], 6048567)
        self.assertEqual(geo.expand_declaration(",500", None, None)["alt_ft"], 500.0)
        self.assertTrue(any("ambiguous" in w for w in geo.expand_declaration("5000/4856", *at(500000, 6050000))["warnings"]))
        self.assertTrue(geo.expand_declaration("12345/4856", *at(500000, 6050000))["warnings"])
        for bad in ("north field", "1234/4856,1,5", ""):
            self.assertIsNone(geo.expand_declaration(bad, 54.6, 24.0)["lat"])
        self.assertIsNone(geo.expand_declaration("1234/4856", None, None)["lat"])

    def test_declaration_grid_is_a_parameter(self):
        west = (54.605, 23.99)  # 700 m west of the 24 E boundary
        own = geo.expand_declaration("1234/4856", *west)
        fixed = geo.expand_declaration("1234/4856", *west, zone=35)
        self.assertEqual((own["grid_zone"], own["grid_easting"], own["utm"][:3]), (34, 712340, "35U"))  # digits on 34, point lies in 35
        self.assertTrue(any("zone boundary" in w for w in own["warnings"]))
        self.assertEqual(fixed["warnings"], [])  # the grid was stated, nothing to confirm
        self.assertEqual((fixed["grid_zone"], fixed["utm"]), (35, "35U 312340 6048560"))
        self.assertGreater(geo.grid_distance(own["lat"], own["lon"], fixed["lat"], fixed["lon"]), 10000)


if __name__ == "__main__":
    unittest.main()
