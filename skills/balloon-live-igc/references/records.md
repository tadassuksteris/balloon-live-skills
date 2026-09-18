# Record reference

Byte positions are 1-based and inclusive. Lines end with CRLF (LF is accepted), ASCII. `bl_igc.py` reads
extension positions from each file's `I`, `J` and `HFXII` records; the ones below are
what current writers use.

## Header

| Record | Example | Meaning |
|---|---|---|
| `A` | `AXBL<8-char install code>BalloonLive` (app), `AX<FR id>,BLS<serial>` (sensor) | writer |
| `HFDTE` | `HFDTE150626` | UTC date of the first record, DDMMYY |
| `HFPID` | `HFPID001` | pilot number |
| `HFPLT` | `HFPLTPILOTINCHARGE:<name>` | pilot name (sensor only) |
| `HFRFW` | `HFRFWVersion 3017` (app), `HFRFWFIRMWAREVERSION:FS.04.17, <sha>` (sensor) | writer version |
| `HFATS` | `HFATS1012` | QNH in hPa at recording start |
| `HFXPO` | `HFXPO0` | pressure offset already applied (sensor only) |
| `HFXAS` | `HFXAS:PRESSURE` | altitude source shown to the pilot; the track is always standard altitude |
| `HFINT` | `HFINT001` | seconds between fixes |
| `I` | `I063638FXA3940SIU4141LAD4242LOD4343PAD4447VAR` | fix extensions |
| `J` | `J010813DTE` | `K` record layout |
| `HFXII` | `HFXII:XX0:054143FXA4445SIU4646LAD4747LOD4848PAD`, `HFXII:XL1:034141LAD4242LOD4343PAD` | event extensions |
| `LXXX key=value` | app only | `utcshift` (`10800s`), `alt unit`, `declaration digits` (4/4, 4/5, 5/4, 6/6), `BLGPS` (primary source `BLS` or `INT`), `BLSSN` (sensor serial), `BLSFWV`, `BLSHWV`, `settings` (device), `Geodetic System`, `qnh unit`, `multiple marker drop` |

## B record (fix)

```
B 101500 5436951N 02359282E A 00052 00062 001 16 7 8 5 0000
  2-7    8-15     16-24     25 26-30 31-35 36-38 39-40 41 42 43 44-47
  time   lat      lon       fix palt  galt  FXA   SIU  LAD LOD PAD VAR
```

- `time` HHMMSS UTC. `lat` DDMMmmm, `lon` DDDMMmmm; `LAD`/`LOD` add a fourth decimal to the minutes.
- `fix`: `A` valid, `V` none (coordinates are then `0000000S00000000W`).
- `palt`: standard pressure altitude in metres, sign included; `PAD` adds tenths.
- `galt`: GPS altitude in metres. `FXA`: accuracy in metres. `SIU`: satellites.
  `VAR`: vertical speed in dm/s, signed.
- A value that does not fit is written as all 9s and is read as no value.

`K<HHMMSS><DDMMYY>` marks a UTC date change; files do not restart at midnight.

## E records (events)

`E<HHMMSS><code><payload>`

| Code | Payload |
|---|---|
| `XX0` | marker drop: number (11-12), then position as in a fix (13-40), then the `HFXII:XX0` extensions |
| `XX1` | goal declaration: number (11-12), then text, normally `EEEE/NNNN[,alt]` |
| `XL1` | the same declaration with the position where it was made (13-40), `HFXII:XL1` extensions, then the text |
| `XXC` | base64 response of the sensor to the preceding marker or declaration |
| `XD0`, `XD1` | base64 copy of `XX0` / `XL1` with the phone's own position (app only) |
| `XS1`, `XS0` | now on the primary / fallback position source: `BLS,<cert serial>,<sensor serial>` or `INT,,` (app only) |
| `XD2`, `XD3` | app to background / foreground |
| other `XDn`, `XEn` | debug; ignored |

`XXC`, little-endian: byte 0 command (6 marker, 7 declaration), byte 1 marker or goal
number, byte 2 bit 0 fix and bits 1-7 satellites, bytes 4-7 unix time. Then:

| | marker | declaration |
|---|---|---|
| lat, lon (int32, 1e-7 degrees) | 8, 12 | 16, 20 |
| GPS height (int16, m) | 16 | 24 |
| pressure altitude (int16, dm) | 18 | 26 |
| accuracy (int16, m) | 20 | - |

`G` records hold a signature; only its presence is used. A sensor without a signing key
writes a zero-filled line, which counts as absent.

## File names

- WatchMeFly: `E[<event>]F[<flight>]P[<pilot>]-<id>-<pilot>.igc`, one per upload, in a zip
  `E[<event>]F[<flight>][<yyyymmdd-hhmmss>].zip`; `<id>` starts with `BL-<sensor serial>`
  when a sensor was used
- App export: `NNN-YYMMDDHHMMSS.igc`, `NNN` = flight number
- Sensor: `YMDCXXXF.IGC` (date, `X`, last three characters of the FR id, recording of the day)
