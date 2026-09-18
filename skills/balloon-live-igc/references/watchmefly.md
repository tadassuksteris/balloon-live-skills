# WatchMeFly exports

The scoring system most competition files come from. What its files look like, and how
its official results relate to what the scripts measure.

## Files

- Flight download: a zip with one IGC per upload, named as in `records.md` (File names).
  The scripts group them into a flight by the `E[..]F[..]` part, so a folder can hold
  several flights.
- A pilot who uploaded twice has two files. Which one counts follows the rule book's
  primary-recording rule (`primary=`), and the report says which was taken. An exact copy
  of the same recording is ignored and said.
- Measurement export: `Measurements-Task[<n>]-<TYPE>-Final.csv`, rows `pilot,result`, no
  header, result in metres or `No Result`.

## Reading official results beside the scripts' numbers

- **Whole metres are electronic results**, from the logger, under the rule book's 2D/3D
  rule. With the same rules and the exact goal, they match the scripts within 1 m.
- **Decimals are physical measurements**: an official measured the marker inside the MMA
  with a tape. They cannot be reproduced from a logger and are not wrong when they differ
  from the electronic distance. With the officials' list, `rules.apply_physical` produces
  the same final column; without it, report the electronic value as provisional.
- **Goals are held to the metre** in WatchMeFly; the task sheet shows 10 m digits. Ask the
  scorer for the exact goal coordinates before comparing; with digits alone, electronic
  results differ by up to a few metres.
- A result below the MMA radius that is a whole number was still measured physically, or
  the rule book has no floor on electronic results. Ask.
- **Zone edges**: a mark a fraction of a metre outside a scoring area has been accepted as
  inside. Report the margin to the edge and let the scorer decide.
- A `No Result` next to a good measurement usually comes from a rule the scripts were not
  told about (scoring period, order of marks, a declaration rule). Say what was measured
  and which check would explain it, never just "No Result".

## Comparing a task

One row per pilot: the scripts' result, the official result, and one of `match` (within
1 m), `physical` (official has decimals), `no result` (both), or `differs` with the gap in
metres. List every `differs` row for the scorer with the checks and flags behind it.
