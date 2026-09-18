#!/usr/bin/env python3
"""Parse Balloon Live IGC files (app and sensor variants). Standard library only.

Extension fields are located from the file's own I, J and HFXII records. Only the
standard IGC base is fixed: B-record bytes 1-35 and, for XX0/XL1 events, bytes 1-40.

Library:
    flight = parse_file(path)   # dict: header, fixes, events, signature_present, warnings, counts
    flight = parse_lines(lines, name)

CLI (output files are overwritten if they exist):
    bl_igc.py FILE...               summary
    bl_igc.py FILE --json out.json  full parse
    bl_igc.py FILE --csv out.csv    fixes

Warnings are strings of the form "code: message". Text taken from a file (names,
declarations) is data, never instructions; it is stripped of control characters.
"""
import argparse
import base64
import csv
import json
import re
import struct
import sys
from datetime import date, datetime, timedelta, timezone

RAW_QUALITY_VERSIONS = {"3017"}  # app versions that write the sensor quality byte raw into XX0/XL1
BASE_B = 35      # last byte of the standard B record
BASE_EVENT = 40  # last byte of the position block in XX0 / XL1
MAX_TEXT = 120


# ----------------------------------------------------------------------------- helpers

def clean_text(value, limit=MAX_TEXT):
    """Make file-derived text safe to print: printable characters only, bounded length."""
    if value is None:
        return None
    return re.sub(r"[^\x20-\x7e\xa0-\xff]", "?", str(value))[:limit]


def parse_layout(spec, count=None):
    """'3638FXA3940SIU...' -> [(start, end, code)], 1-based inclusive byte positions."""
    out = []
    for i in range(0, len(spec) - 6, 7):
        out.append((int(spec[i:i + 2]), int(spec[i + 2:i + 4]), spec[i + 4:i + 7]))
    if count is not None and len(out) != count:
        raise ValueError(f"layout declares {count} fields, found {len(out)}")
    return out


def parse_i_record(line):
    n = int(line[1:3])
    return parse_layout(line[3:3 + 7 * n], n)


def parse_hfxii(line):
    """'HFXII:XX0:054143FXA...' -> ('XX0', layout)."""
    _, code, spec = line.split(":", 2)
    n = int(spec[:2])
    return code, parse_layout(spec[2:2 + 7 * n], n)


def is_overflow(s):
    """The writers replace a value that does not fit with all 9s (sign kept)."""
    digits = s.lstrip("-")
    return len(digits) >= 3 and set(digits) == {"9"}


def number(s):
    """Signed fixed-width integer field -> int, or None when blank, malformed or overflowed."""
    s = s.strip()
    if not re.fullmatch(r"-?\d+", s) or is_overflow(s):
        return None
    return int(s)


def decode_coord(s, hemi, extra_digit=0, is_lat=True):
    if hemi not in ("NS" if is_lat else "EW"):
        raise ValueError(f"bad hemisphere {hemi!r}")
    deg_len = 2 if is_lat else 3
    deg = int(s[:deg_len])
    minutes = int(s[deg_len:deg_len + 2]) + int(s[deg_len + 2:deg_len + 5]) / 1000 + (extra_digit or 0) / 10000
    value = deg + minutes / 60
    if value == 0:
        return 0.0
    return -value if hemi in "SW" else value


def decode_pressure_alt(s, pad_digit=0):
    """Metres; the PAD digit adds tenths away from zero. None on overflow."""
    whole = number(s)
    if whole is None:
        return None
    tenths = (pad_digit or 0) / 10
    return whole - tenths if s.strip().startswith("-") else whole + tenths


def extract_extensions(line, layout):
    ext = {}
    for start, end, code in layout:
        raw = line[start - 1:end]
        if len(raw) < end - start + 1:
            continue
        value = number(raw)
        if code == "VAR":
            ext[code] = None if value is None else value / 10  # dm/s -> m/s
        else:
            ext[code] = value
    return ext


def decode_bls_response(b64, kind):
    """Decode the documented part of a sensor action response.

    kind is 'marker' or 'declaration' (from the record the XXC follows). Layout as in
    the app's lib/bls/decoders/actionResponse.ts. Byte 0 echoes the command (6 marker,
    7 declaration) and byte 1 the marker/goal number; both are returned for checking.
    """
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception:
        return None
    need = 28 if kind == "declaration" else 24
    if len(raw) < need:
        return None
    quality = raw[2]
    out = {"kind": kind, "command": raw[0], "number": raw[1], "bytes": len(raw),
           "fix": bool(quality & 1), "satellites": quality >> 1,
           "unix_time": struct.unpack_from("<I", raw, 4)[0]}
    out["time_utc"] = datetime.fromtimestamp(out["unix_time"], timezone.utc).isoformat()
    pos, alt = (16, 24) if kind == "declaration" else (8, 16)
    out["lat"] = struct.unpack_from("<i", raw, pos)[0] / 1e7
    out["lon"] = struct.unpack_from("<i", raw, pos + 4)[0] / 1e7
    out["gps_alt"] = struct.unpack_from("<h", raw, alt)[0]
    out["pressure_alt_std"] = struct.unpack_from("<h", raw, alt + 2)[0] / 10
    if kind == "marker":
        out["accuracy"] = struct.unpack_from("<h", raw, 20)[0]
    return out


# ----------------------------------------------------------------------------- header

HEADER_FIELDS = (
    "source", "device_code", "device_description", "fr_id", "date_utc", "pilot_number", "pilot_name",
    "app_version", "firmware", "qnh_hpa", "qnh_unit", "pressure_offset", "logging_interval_s", "utc_shift_s",
    "position_source", "bls_serial", "bls_cert_serial", "bls_firmware", "bls_hardware", "altitude_unit",
    "declaration_digits", "multiple_marker_drop", "geodetic_system", "device_settings",
)

_L_KEYS = {
    "utcshift": ("utc_shift_s", lambda v: number(re.sub(r"[^-\d]", "", v))),
    "loggerinterval": ("logging_interval_s", lambda v: number(re.sub(r"\D", "", v))),
    "alt unit": ("altitude_unit", clean_text),
    "qnh unit": ("qnh_unit", clean_text),
    "multiple marker drop": ("multiple_marker_drop", lambda v: v.lower() == "yes"),
    "declaration digits": ("declaration_digits", clean_text),
    "BLGPS": ("position_source", clean_text),
    "BLSSN": ("bls_serial", number),
    "BLSHWV": ("bls_hardware", clean_text),
    "BLSFWV": ("bls_firmware", clean_text),
    "settings": ("device_settings", clean_text),
    "Geodetic System": ("geodetic_system", clean_text),
}


def _apply_header_line(h, line):
    if line.startswith("A"):
        if "," in line:  # sensor: AX<fr id>,BLS<serial>
            h["source"] = "sensor"
            ident, _, rest = line[1:].partition(",")
            h["fr_id"] = clean_text(ident[1:] if ident.startswith("X") else ident)
            h["device_description"] = clean_text(rest) or None
            m = re.search(r"(\d+)", rest)
            if m:
                h["bls_serial"] = int(m.group(1))
        else:            # app: AXBL<install code>BalloonLive
            h["source"] = "app"
            body = line[4:] if line.startswith("AXBL") else line[1:]
            idx = body.rfind("BalloonLive")
            h["device_code"] = clean_text(body[:idx] if idx > 0 else body)
            h["device_description"] = clean_text(body[idx:]) if idx >= 0 else None
    elif line.startswith("HFDTE"):
        s = re.search(r"(\d{6})", line[5:]).group(1)
        h["date_utc"] = date(2000 + int(s[4:6]), int(s[2:4]), int(s[0:2])).isoformat()
    elif line.startswith("HFPID"):
        m = re.search(r"(\d+)\s*$", line[5:])
        h["pilot_number"] = int(m.group(1)) if m else None
    elif line.startswith("HFPLT"):
        h["pilot_name"] = clean_text(line.split(":", 1)[-1].strip()) or None
    elif line.startswith("HFRFW"):
        v = line[5:]
        m = re.match(r"Version\s*(\S+)", v)
        if m:
            h["app_version"] = clean_text(m.group(1))
        else:
            h["firmware"] = clean_text(v.split(":", 1)[-1].strip())
    elif line.startswith("HFATS"):
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*$", line[5:])
        h["qnh_hpa"] = float(m.group(1).replace(",", ".")) if m else None
    elif line.startswith("HFXPO"):
        h["pressure_offset"] = number(line[5:])
    elif line.startswith("HFINT"):
        h["logging_interval_s"] = number(line[5:]) or h["logging_interval_s"]
    elif line.startswith("LXXX") and "=" in line:
        key, _, value = line[4:].strip().partition("=")
        if key.strip() in _L_KEYS:
            field, conv = _L_KEYS[key.strip()]
            h[field] = conv(value.strip()) if value.strip() else None


# ----------------------------------------------------------------------------- parser

class _Parser:
    def __init__(self, name):
        self.header = dict.fromkeys(HEADER_FIELDS)
        self.fixes, self.events, self.warnings, self.g_lines = [], [], [], []
        self.counts = {}
        self.b_layout, self.k_layout, self.xii = [], [], {}
        self.current_date = None
        self.last_sod = None
        self.last_action = None  # most recent marker/declaration event, for XXC/XD0/XD1
        self.name = name
        self._warned = set()

    def warn(self, code, message, once=False):
        if once:
            if code in self._warned:
                return
            self._warned.add(code)
        self.warnings.append(f"{code}: {message}")

    def stamp(self, hhmmss, date_record=False):
        sod = int(hhmmss[0:2]) * 3600 + int(hhmmss[2:4]) * 60 + int(hhmmss[4:6])
        if self.current_date is None:
            if self.header["date_utc"]:
                self.current_date = date.fromisoformat(self.header["date_utc"])
            else:
                self.current_date = date(2000, 1, 1)
                self.warn("no-date", "records before any HFDTE header; dated 2000-01-01", once=True)
        if not date_record and self.last_sod is not None and sod < self.last_sod:
            if sod < self.last_sod - 6 * 3600:
                self.current_date += timedelta(days=1)
                self.warn("midnight", f"time wrapped at {hhmmss} without a K record; assumed next day")
            else:
                self.warn("time-backwards", "timestamps go backwards in places", once=True)
        self.last_sod = sod
        return datetime.combine(self.current_date, datetime.min.time(), timezone.utc) + timedelta(seconds=sod)

    # -- records

    def feed(self, line):
        t = line[0]
        self.counts[t] = self.counts.get(t, 0) + 1
        if t == "A" or t == "H" or (t == "L" and not self.fixes):
            if line.startswith("HFXII"):
                code, layout = parse_hfxii(line)
                self.xii[code] = layout
            else:
                _apply_header_line(self.header, line)
        elif t == "I":
            self.b_layout = parse_i_record(line)
        elif t == "J":
            self.k_layout = parse_i_record(line)
        elif t == "K":
            self.k_record(line)
        elif t == "B":
            self.b_record(line)
        elif t == "E":
            self.e_record(line)
        elif t == "G":
            self.g_lines.append(line)
        elif t != "L":
            self.counts["unknown_records"] = self.counts.get("unknown_records", 0) + 1

    def k_record(self, line):
        ext = {code: line[s - 1:e] for s, e, code in self.k_layout}
        d = ext.get("DTE", "")
        if re.fullmatch(r"\d{6}", d):
            self.current_date = date(2000 + int(d[4:6]), int(d[2:4]), int(d[0:2]))
        when = self.stamp(line[1:7], date_record=True)
        self.events.append({"type": "date_change", "time_utc": when.isoformat(), "date": self.current_date.isoformat()})

    def b_record(self, line):
        if len(line) < BASE_B:
            raise ValueError("short B record")
        ext = extract_extensions(line, self.b_layout)
        lat = decode_coord(line[7:14], line[14], ext.get("LAD"), True)
        lon = decode_coord(line[15:23], line[23], ext.get("LOD"), False)
        fix = line[24] == "A"
        palt = decode_pressure_alt(line[25:30], ext.get("PAD"))
        if palt is None or (line[30:35].strip() and number(line[30:35]) is None) or \
                any(is_overflow(line[s - 1:e]) for s, e, _ in self.b_layout):
            self.warn("overflow", "some fields hold the writer's overflow marker (all 9s); read as no value", once=True)
        self.fixes.append({
            "time_utc": self.stamp(line[1:7]).isoformat(),
            "lat": round(lat, 7), "lon": round(lon, 7),
            "fix": fix, "valid": fix and (lat != 0 or lon != 0),
            "pressure_alt_std": palt, "gps_alt": number(line[30:35]),
            "accuracy": ext.get("FXA"), "satellites": ext.get("SIU"), "vario": ext.get("VAR"),
        })

    def positioned(self, full, base_code, when):
        """Decode an XX0/XL1-shaped record (already re-aligned to plain positions)."""
        layout = self.xii.get(base_code)
        if layout is None:
            self.warn("no-hfxii", f"no HFXII:{base_code} header; extension digits and text offset unknown", once=True)
            layout = []
        if len(full) < BASE_EVENT:
            raise ValueError(f"short {base_code} record")
        ext = extract_extensions(full, layout)
        ev = {
            "time_utc": when.isoformat(),
            "number": number(full[10:12]),
            "lat": round(decode_coord(full[12:19], full[19], ext.get("LAD"), True), 7),
            "lon": round(decode_coord(full[20:28], full[28], ext.get("LOD"), False), 7),
            "fix": full[29] == "A",
            "pressure_alt_std": decode_pressure_alt(full[30:35], ext.get("PAD")),
            "gps_alt": number(full[35:40]),
            "accuracy": ext.get("FXA"), "satellites": ext.get("SIU"),
        }
        tail = full[max([e for _, e, _ in layout], default=BASE_EVENT):] if layout else None
        return ev, tail

    def e_record(self, line):
        if len(line) < 10:
            raise ValueError("short E record")
        code, payload = line[7:10], line[10:]
        when = self.stamp(line[1:7])
        if code in ("XX0", "XD0", "XL1", "XD1"):
            base_code = "XX0" if code in ("XX0", "XD0") else "XL1"
            if code in ("XD0", "XD1"):
                payload = base64.b64decode(payload, validate=True).decode("ascii")
            ev, tail = self.positioned(line[:10] + payload, base_code, when)
            if code == "XX0":
                ev["type"] = "marker_drop"
                ev["marker"] = ev.pop("number")
                self.events.append(ev)
                self.last_action = ev
            elif code == "XL1":
                target = self.last_action
                if not (target and target["type"] == "goal_declaration" and target.get("number") == ev["number"]
                        and target["time_utc"] == ev["time_utc"] and "lat" not in target):
                    target = {"type": "goal_declaration", "time_utc": ev["time_utc"], "number": ev["number"],
                              "text": clean_text(tail)}
                    self.events.append(target)
                    self.last_action = target
                target.update({k: ev[k] for k in ("lat", "lon", "fix", "pressure_alt_std", "gps_alt")})
            else:  # phone snapshot of the preceding action
                kind = "marker_drop" if code == "XD0" else "goal_declaration"
                target = self.last_action
                if target and target["type"] == kind and target.get("marker", target.get("number")) == ev["number"]:
                    target["app_snapshot"] = {k: ev[k] for k in ("lat", "lon", "fix", "pressure_alt_std", "gps_alt",
                                                                 "accuracy", "satellites")}
                else:
                    self.warn("orphan", f"{code} at {line[1:7]} does not follow its {kind} record")
        elif code == "XX1":
            ev = {"type": "goal_declaration", "time_utc": when.isoformat(),
                  "number": number(payload[:2]), "text": clean_text(payload[2:])}
            self.events.append(ev)
            self.last_action = ev
        elif code == "XXC":
            target = self.last_action
            if target is None:
                self.warn("orphan", f"XXC at {line[1:7]} does not follow a marker or declaration")
                return
            kind = "marker" if target["type"] == "marker_drop" else "declaration"
            bls = decode_bls_response(payload, kind)
            target["bls_raw"] = payload if re.fullmatch(r"[A-Za-z0-9+/=]*", payload) else None
            target["bls"] = bls
            if target["time_utc"] != when.isoformat():
                self.warn("xxc-time", f"XXC at {line[1:7]} is not in the same second as its action")
            if bls and (bls["command"] != (6 if kind == "marker" else 7)
                        or bls["number"] != target.get("marker", target.get("number"))):
                self.warn("xxc-mismatch", f"XXC at {line[1:7]} does not echo its action's type and number")
        elif code in ("XS0", "XS1"):  # XS1 = primary, as the app writes it (the 2021 format document has them swapped)
            parts = payload.split(",")
            self.events.append({"type": "source_change", "time_utc": when.isoformat(),
                                "to": clean_text(parts[0], 8), "is_primary": code == "XS1",
                                "bls_cert_serial": clean_text(parts[1], 20) if len(parts) > 1 and parts[1] else None,
                                "bls_serial": number(parts[2]) if len(parts) > 2 else None})
        elif code in ("XD2", "XD3"):
            self.events.append({"type": "app_state", "time_utc": when.isoformat(),
                                "state": "background" if code == "XD2" else "foreground"})
        elif re.fullmatch(r"X[DE]\d", code):
            self.counts["debug_events"] = self.counts.get("debug_events", 0) + 1
        else:
            self.counts["unknown_events"] = self.counts.get("unknown_events", 0) + 1

    # -- after the last line

    def finish(self):
        h = self.header
        for e in self.events:  # sensor identity from the first populated switch to BLS
            if e["type"] == "source_change" and e["to"] == "BLS" and (e["bls_serial"] or e["bls_cert_serial"]):
                h["bls_serial"] = h["bls_serial"] if h["bls_serial"] is not None else e["bls_serial"]
                h["bls_cert_serial"] = h["bls_cert_serial"] or e["bls_cert_serial"]
                break
        used_bls = h["source"] == "app" and (h["position_source"] == "BLS" or any(e.get("bls") for e in self.events))
        if used_bls and h["bls_serial"] is None:
            self.warn("no-bls-serial", "sensor was used but its serial is in neither the header nor the XS records")
        if h["pressure_offset"]:
            self.warn("pressure-offset", f"sensor pressure offset {h['pressure_offset']} is applied in this file")
        if h["geodetic_system"] and "UTM" not in h["geodetic_system"].upper():
            self.warn("geodetic", f"pilot's geodetic system is {h['geodetic_system']!r}; declarations are not UTM digits")

        raw_byte, differs = [], []
        for e in self.events:  # XX0/XL1 quality fields versus the sensor's own response
            if e["type"] not in ("marker_drop", "goal_declaration"):
                continue
            bls = e.get("bls")
            e["fix_effective"], e["satellites_effective"], e["quality_from"] = e.get("fix"), e.get("satellites"), "record"
            if not bls or "fix" not in e:
                sats = e.get("satellites")
                if h["app_version"] in RAW_QUALITY_VERSIONS and e.get("fix") is False and sats is not None and sats > 1:
                    # this writer stores the sensor's quality byte (2n+fix) in the satellites field and always writes fix V
                    e["fix_effective"], e["satellites_effective"] = bool(sats & 1), sats >> 1
                    e["quality_from"] = f"raw quality byte decoded (app {h['app_version']}, no XXC)"
                    raw_byte.append(e.get("marker", e.get("number")))
                continue
            sats = e.get("satellites")
            if bls["fix"] != e["fix"] or (sats is not None and bls["satellites"] != sats):
                e["fix_effective"], e["satellites_effective"], e["quality_from"] = bls["fix"], bls["satellites"], "xxc"
                is_raw = sats == 2 * bls["satellites"] + int(bls["fix"]) if sats is not None else not e["fix"]
                (raw_byte if is_raw else differs).append(e.get("marker", e.get("number")))
        if raw_byte:
            self.warn("quality-byte", f"records for actions {raw_byte} carry the raw sensor quality byte "
                                      "(fix V, satellites = 2n+fix); fix and satellites taken from XXC, or decoded from the byte")
        if differs:
            self.warn("quality-differs", f"fix/satellites of actions {differs} differ from XXC; XXC used")

        if self.fixes and not any(f["valid"] for f in self.fixes):
            self.warn("no-gps", "no valid position fixes (test recording or no GPS)")
        varios = [f["vario"] for f in self.fixes if f["vario"] is not None]
        palts = [f["pressure_alt_std"] for f in self.fixes if f["pressure_alt_std"] is not None]
        if varios and not any(varios) and palts and max(palts) - min(palts) > 50:
            self.warn("var-zero", "VAR field is always 0 although altitude changes; derive vertical speed from altitude")

        hex_part = "".join(g[1:] for g in self.g_lines)  # a sensor without a key writes a zero-filled line
        signature_present = bool(hex_part) and not set(hex_part[8:]) <= {"0"}
        return {"file": self.name, "header": h, "layouts": {"B": self.b_layout, "K": self.k_layout, "HFXII": self.xii},
                "fixes": self.fixes, "events": self.events, "signature_present": signature_present,
                "warnings": self.warnings, "counts": self.counts}


def parse_lines(lines, name="<memory>"):
    p = _Parser(name)
    bad = 0
    for raw in lines:
        line = raw.rstrip("\r\n")
        if not line:
            continue
        try:
            p.feed(line)
        except (ValueError, IndexError, AttributeError, UnicodeDecodeError, struct.error):
            bad += 1
            if bad <= 3:
                p.warn("unreadable-line", f"skipped {clean_text(line, 40)!r}")
    if bad > 3:
        p.warn("unreadable-line", f"{bad} lines skipped in total")
    return p.finish()


def parse_file(path):
    with open(path, encoding="latin-1") as fh:
        return parse_lines(fh, name=str(path))


# ----------------------------------------------------------------------------- CLI

def summary(flight):
    h = flight["header"]
    valid = sum(f["valid"] for f in flight["fixes"])
    out = [
        flight["file"],
        f"  {h['source']}  pilot {h['pilot_number']}{' ' + h['pilot_name'] if h['pilot_name'] else ''}  date {h['date_utc']} UTC  "
        f"version {h['app_version'] or h['firmware']}  QNH {h['qnh_hpa']} {h['qnh_unit'] or 'hPa'}",
        f"  position source {h['position_source']}  BLS serial {h['bls_serial']}  device {h['device_settings'] or h['device_description']}",
        f"  declaration digits {h['declaration_digits']}  altitude unit {h['altitude_unit']}  utc shift {h['utc_shift_s']} s  "
        f"interval {h['logging_interval_s']} s",
        f"  fixes {len(flight['fixes'])} ({valid} valid)"
        + (f"  {flight['fixes'][0]['time_utc']} .. {flight['fixes'][-1]['time_utc']}" if flight["fixes"] else ""),
    ]
    out.append(f"  signature {'present' if flight['signature_present'] else 'absent'}")
    for e in flight["events"]:
        if e["type"] == "marker_drop":
            alt = "-" if e["pressure_alt_std"] is None else f"{e['pressure_alt_std']:.1f} m std"
            out.append(f"  marker {e['marker']} {e['time_utc'][11:19]} UTC  {e['lat']:.6f} {e['lon']:.6f}  {alt}  "
                       f"fix {e['fix_effective']} sats {e['satellites_effective']} (from {e['quality_from']})")
        elif e["type"] == "goal_declaration":
            out.append(f"  declaration {e.get('number')} {e['time_utc'][11:19]} UTC  text {e.get('text')!r}")
        elif e["type"] == "source_change":
            out.append(f"  source -> {e['to']} ({'primary' if e['is_primary'] else 'fallback'}) {e['time_utc'][11:19]} UTC")
    out += [f"  ! {w}" for w in flight["warnings"]]
    return "\n".join(out)


def write_csv(flight, path):
    fields = ["time_utc", "lat", "lon", "fix", "valid", "pressure_alt_std", "gps_alt", "accuracy", "satellites", "vario"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for f in flight["fixes"]:
            w.writerow({k: f.get(k) for k in fields})


def _main(argv=None):
    p = argparse.ArgumentParser(description="Parse Balloon Live IGC files. Output files are overwritten if they exist.")
    p.add_argument("files", nargs="+")
    p.add_argument("--json", metavar="OUT", help="write the full parse as JSON (a list when several files are given)")
    p.add_argument("--csv", metavar="OUT", help="write fixes as CSV (single input file only)")
    a = p.parse_args(argv)
    if a.csv and len(a.files) != 1:
        p.error("--csv takes exactly one input file")
    flights = []
    for path in a.files:
        try:
            flights.append(parse_file(path))
        except OSError as e:
            print(f"cannot read {path}: {e.strerror}", file=sys.stderr)
    for fl in flights:
        print(summary(fl))
    if not flights:
        return 2
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(flights[0] if len(flights) == 1 else flights, fh, indent=1)
    if a.csv:
        write_csv(flights[0], a.csv)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
