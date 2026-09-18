#!/usr/bin/env python3
"""Geodesy and altitude helpers for balloon competition tracks. Standard library only.

Latitude/longitude is the truth for every point. UTM is derived: shown in each point's own
zone, and measured on a grid both points share, so zone boundaries need no special care.

Library:
    latlon_to_utm(lat, lon, zone=None, northern=None) -> (easting, northing, zone, band)
    utm_to_latlon(easting, northing, zone, northern=True) -> (lat, lon)
    format_utm(lat, lon) -> "35U 306267 6055530"
    grid_distance(lat1, lon1, lat2, lon2) -> metres
    qnh_altitude(h_std_m, qnh_hpa)   as the Balloon Live app computes it
    isa_height(p_hpa) / m_to_ft(m) / ft_to_m(ft)
    expand_declaration(text, near_lat, near_lon, digits="4/4", alt_unit="ft", zone=None)

CLI:
    geo.py utm 54.61 24.00 [--zone 35]
    geo.py dist 54.61 23.95 54.60 24.05
    geo.py qnh 1012 458.3            # std altitude (m) -> QNH altitude (m and ft)
    geo.py decl "1234/4856,168" --near 54.605 24.023 [--zone 35] [--digits 4/4] [--alt-unit ft]
"""
import argparse
import math
import re
import sys

# WGS84
_A = 6378137.0
_F = 1 / 298.257223563
_E2 = _F * (2 - _F)
_EP2 = _E2 / (1 - _E2)
_K0 = 0.9996
_BANDS = "CDEFGHJKLMNPQRSTUVWX"

FT_PER_M = 1 / 0.3048


def m_to_ft(m):
    return m * FT_PER_M


def ft_to_m(ft):
    return ft * 0.3048


_T0_LAMBDA = 44330.7692   # standard atmosphere constants, as in the Balloon Live app
_ALPHA = 0.190295


def isa_height(p_hpa):
    """Height in metres of pressure level p in the standard atmosphere (0 at 1013.25 hPa)."""
    return _T0_LAMBDA * (1 - (p_hpa / 1013.25) ** _ALPHA)


def qnh_altitude(h_std_m, qnh_hpa):
    """QNH-corrected altitude from a QNE (standard) pressure altitude, computed exactly as the Balloon
    Live app does (lib/altitude.ts, calculateQnhCorrectedPressureAltitude), so that altitudes here are
    the ones pilots see in the app and the official results use."""
    inner = (1 - h_std_m / _T0_LAMBDA) ** (1 / _ALPHA) + 1 - qnh_hpa / 1013.25
    if inner <= 0:
        raise ValueError(f"no altitude for std {h_std_m} m at QNH {qnh_hpa} hPa")
    return _T0_LAMBDA * (1 - inner ** _ALPHA)


def utm_zone(lat, lon):
    zone = min(60, int(((lon + 180) % 360) // 6) + 1)
    if 56 <= lat < 64 and 3 <= lon < 12:
        zone = 32
    if 72 <= lat < 84:
        if 0 <= lon < 9:
            zone = 31
        elif 9 <= lon < 21:
            zone = 33
        elif 21 <= lon < 33:
            zone = 35
        elif 33 <= lon < 42:
            zone = 37
    return zone


def boundary_distance_km(lat, lon):
    """Distance to the nearest UTM zone boundary meridian, km (the regular 6-degree grid)."""
    return min((lon + 180) % 6, 6 - (lon + 180) % 6) * 111.32 * math.cos(math.radians(lat))


def utm_band(lat):
    if -80 <= lat < 84:
        return _BANDS[min(19, int((lat + 80) // 8))]  # band X is 12 degrees tall
    return "Z"


def latlon_to_utm(lat, lon, zone=None, northern=None):
    """Project to UTM WGS84.

    Pass `zone` to keep a whole flight in one grid (accurate to centimetres one zone
    away, metres beyond that). Pass `northern` to keep one false northing for a flight
    that crosses the equator; by default it follows the sign of `lat`.
    """
    if zone is None:
        zone = utm_zone(lat, lon)
    lon0 = math.radians((zone - 1) * 6 - 180 + 3)
    phi = math.radians(lat)
    lam = (math.radians(lon) - lon0 + math.pi) % (2 * math.pi) - math.pi  # wrapped, so 180 E/W is no barrier
    sin_phi, cos_phi, tan_phi = math.sin(phi), math.cos(phi), math.tan(phi)
    n = _A / math.sqrt(1 - _E2 * sin_phi ** 2)
    t = tan_phi ** 2
    c = _EP2 * cos_phi ** 2
    a = cos_phi * lam
    e4, e6 = _E2 ** 2, _E2 ** 3
    m = _A * (
        (1 - _E2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * phi
        - (3 * _E2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * math.sin(2 * phi)
        + (15 * e4 / 256 + 45 * e6 / 1024) * math.sin(4 * phi)
        - (35 * e6 / 3072) * math.sin(6 * phi)
    )
    easting = _K0 * n * (
        a + (1 - t + c) * a ** 3 / 6 + (5 - 18 * t + t ** 2 + 72 * c - 58 * _EP2) * a ** 5 / 120
    ) + 500000.0
    northing = _K0 * (
        m + n * tan_phi * (
            a ** 2 / 2
            + (5 - t + 9 * c + 4 * c ** 2) * a ** 4 / 24
            + (61 - 58 * t + t ** 2 + 600 * c - 330 * _EP2) * a ** 6 / 720
        )
    )
    if (lat < 0) if northern is None else (not northern):
        northing += 10000000.0
    return easting, northing, zone, utm_band(lat)


def format_utm(lat, lon):
    """Coordinates in the point's own zone, never without the zone: '35U 306267 6055530'."""
    e, n, zone, band = latlon_to_utm(lat, lon)
    return f"{zone}{band} {e:.0f} {n:.0f}"


def mean_lon(lons):
    """Mean longitude that is safe across 180 E/W."""
    lons = list(lons)
    return (lons[0] + sum((lon - lons[0] + 180) % 360 - 180 for lon in lons) / len(lons) + 180) % 360 - 180


def grid_distance(lat1, lon1, lat2, lon2):
    """Horizontal grid distance in metres. Points in different zones (or hemispheres) are
    both projected into the zone of their midpoint, so the result does not depend on any
    global choice of zone. Within 0.1% of the geodesic up to about 400 km; meant for
    competition distances, not for points several zones apart."""
    z1, z2 = utm_zone(lat1, lon1), utm_zone(lat2, lon2)
    zone = z1 if z1 == z2 else utm_zone((lat1 + lat2) / 2, mean_lon([lon1, lon2]))
    northern = lat1 + lat2 >= 0
    e1, n1 = latlon_to_utm(lat1, lon1, zone, northern)[:2]
    e2, n2 = latlon_to_utm(lat2, lon2, zone, northern)[:2]
    return math.hypot(e2 - e1, n2 - n1)


def utm_to_latlon(easting, northing, zone, northern=True):
    x = easting - 500000.0
    y = northing if northern else northing - 10000000.0
    lon0 = math.radians((zone - 1) * 6 - 180 + 3)
    e4, e6 = _E2 ** 2, _E2 ** 3
    mu = (y / _K0) / (_A * (1 - _E2 / 4 - 3 * e4 / 64 - 5 * e6 / 256))
    e1 = (1 - math.sqrt(1 - _E2)) / (1 + math.sqrt(1 - _E2))
    phi1 = (
        mu
        + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
        + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
        + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
        + (1097 * e1 ** 4 / 512) * math.sin(8 * mu)
    )
    sin1, cos1, tan1 = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    n1 = _A / math.sqrt(1 - _E2 * sin1 ** 2)
    t1 = tan1 ** 2
    c1 = _EP2 * cos1 ** 2
    r1 = _A * (1 - _E2) / (1 - _E2 * sin1 ** 2) ** 1.5
    d = x / (n1 * _K0)
    lat = phi1 - (n1 * tan1 / r1) * (
        d ** 2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * _EP2) * d ** 4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * _EP2 - 3 * c1 ** 2) * d ** 6 / 720
    )
    lon = lon0 + (
        d
        - (1 + 2 * t1 + c1) * d ** 3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * _EP2 + 24 * t1 ** 2) * d ** 5 / 120
    ) / cos1
    return math.degrees(lat), math.degrees(lon)


_DECL_RE = re.compile(r"^\s*(\d{3,7})\s*/\s*(\d{3,7})\s*(?:,\s*(-?\d+(?:\.\d+)?)?\s*(ft|m)?\s*)?$", re.I)
_ALT_ONLY_RE = re.compile(r"^\s*,\s*(-?\d+(?:\.\d+)?)\s*(ft|m)?\s*$", re.I)


def expand_declaration(text, near_lat, near_lon, digits="4/4", alt_unit="ft", zone=None):
    """Expand a shortened UTM goal declaration such as "1234/4856,168" to a point.

    Groups of up to 5 digits are the 10 m digits of easting and northing; a 6- or
    7-digit group is full metres. The digits are read on one grid: `zone` when the task
    or competition fixes it, else the zone of the reference position (normally where
    the declaration was made). Each group is completed with the candidate nearest the
    reference. Altitude defaults to feet; a unit in the text ("168ft", "50m") wins.

    Returns dict(lat, lon, utm, grid_zone, grid_easting, grid_northing, alt_m, alt_ft,
    warnings). grid_easting/grid_northing are the completed digits on grid_zone; `utm` is
    the same point written in its own zone, which can differ near a boundary.
    """
    out = {"lat": None, "lon": None, "utm": None, "grid_zone": None, "grid_easting": None, "grid_northing": None,
           "alt_m": None, "alt_ft": None, "warnings": []}

    def set_alt(value, unit):
        unit = (unit or alt_unit).lower()
        out["alt_ft"], out["alt_m"] = (value, ft_to_m(value)) if unit == "ft" else (m_to_ft(value), value)

    alt_only = _ALT_ONLY_RE.match(text or "")
    if alt_only:
        set_alt(float(alt_only.group(1)), alt_only.group(2))
        out["warnings"].append("altitude only, no coordinates declared")
        return out
    m = _DECL_RE.match(text or "")
    if not m:
        out["warnings"].append("declaration text is not in E/N[,alt] form")
        return out
    e_str, n_str, alt_str, unit = m.groups()
    if alt_str:
        set_alt(float(alt_str), unit)
    try:
        want_e, want_n = (int(x) for x in digits.split("/"))
    except ValueError:
        want_e, want_n = 4, 4
        out["warnings"].append(f"unknown declaration format {digits!r}, assuming 4/4")
    if (len(e_str), len(n_str)) != (want_e, want_n):
        out["warnings"].append(f"{len(e_str)}/{len(n_str)} digits given, format is {want_e}/{want_n}")
    if 5 in (len(e_str), len(n_str)):
        out["warnings"].append("5-digit group read as 10 m digits (convention not confirmed by the writer)")
    if near_lat is None or near_lon is None:
        out["warnings"].append("no position to resolve the shortened coordinates against")
        return out

    northern = near_lat >= 0
    ref_e, ref_n, grid_zone, _ = latlon_to_utm(near_lat, near_lon, zone, northern)
    edge_km = boundary_distance_km(near_lat, near_lon)
    if zone is None and edge_km < 1.0:
        out["warnings"].append(f"declared {edge_km * 1000:.0f} m from a UTM zone boundary; confirm which grid the digits are on")

    def complete(group, ref, axis):
        step = 1 if len(group) >= 6 else 10
        modulus = step * 10 ** len(group)
        base = ref - (ref % modulus)
        ranked = sorted((base + int(group) * step + k * modulus for k in (-1, 0, 1)), key=lambda v: abs(v - ref))
        if abs(abs(ranked[0] - ref) - abs(ranked[1] - ref)) < step:
            out["warnings"].append(f"{axis} is ambiguous: two candidates equally far from the reference")
        return ranked[0]

    easting, northing = complete(e_str, ref_e, "easting"), complete(n_str, ref_n, "northing")
    lat, lon = utm_to_latlon(easting, northing, grid_zone, northern)
    out.update(lat=lat, lon=lon, utm=format_utm(lat, lon), grid_zone=grid_zone, grid_easting=easting, grid_northing=northing)
    away_km = grid_distance(near_lat, near_lon, lat, lon) / 1000
    if away_km > 50:
        out["warnings"].append(f"resolved point is {away_km:.0f} km from where it was declared; check the format")
    own = utm_zone(lat, lon)
    if own != grid_zone:
        out["warnings"].append(f"point lies in zone {own}; read on grid {grid_zone}, so its own-zone UTM ({out['utm']}) differs from the digits")
    elif boundary_distance_km(lat, lon) < 1.0:
        out["warnings"].append(f"point is {boundary_distance_km(lat, lon) * 1000:.0f} m from a UTM zone boundary")
    return out


def _main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("utm", help="lat lon -> UTM")
    u.add_argument("lat", type=float)
    u.add_argument("lon", type=float)
    u.add_argument("--zone", type=int)
    q = sub.add_parser("qnh", help="QNH hPa + std altitude m -> QNH altitude")
    q.add_argument("qnh", type=float)
    q.add_argument("h_std", type=float)
    d = sub.add_parser("decl", help="expand a shortened UTM declaration")
    d.add_argument("text")
    d.add_argument("--near", nargs=2, type=float, metavar=("LAT", "LON"), required=True, help="where it was declared")
    d.add_argument("--zone", type=int, help="grid the digits are on, when the task or competition fixes one")
    d.add_argument("--digits", default="4/4")
    d.add_argument("--alt-unit", default="ft", choices=["ft", "m"])
    g = sub.add_parser("dist", help="grid distance between two lat/lon points")
    g.add_argument("coords", nargs=4, type=float, metavar=("LAT1", "LON1", "LAT2", "LON2"))
    a = p.parse_args(argv)
    if a.cmd == "dist":
        print(f"{grid_distance(*a.coords):.1f} m")
    elif a.cmd == "utm":
        e, n, z, b = latlon_to_utm(a.lat, a.lon, a.zone)
        print(f"{z}{b} {e:.1f} {n:.1f}")
    elif a.cmd == "qnh":
        h = qnh_altitude(a.h_std, a.qnh)
        print(f"{h:.1f} m = {m_to_ft(h):.0f} ft")
    else:
        r = expand_declaration(a.text, a.near[0], a.near[1], a.digits, a.alt_unit, a.zone)
        if r["lat"] is None:
            print("not resolved")
        else:
            print(f"{r['utm']}  lat {r['lat']:.6f} lon {r['lon']:.6f}  on grid {r['grid_zone']}: {r['grid_easting']:.0f} {r['grid_northing']:.0f}"
                  + (f"  alt {r['alt_ft']:.0f} ft / {r['alt_m']:.1f} m" if r["alt_m"] is not None else ""))
        for w in r["warnings"]:
            print("! " + w)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
