# balloon-live-skills

Agent skills for hot-air balloon competition data, in the open
[Agent Skills](https://agentskills.io) format: a `SKILL.md` plus scripts and references.
They work with any agent that reads that format.

| Skill | What it does |
|---|---|
| [balloon-live-igc](skills/balloon-live-igc/SKILL.md) | Parse and check IGC files from the Balloon Live app and Balloon Live Sensor, and measure competition tasks from them under the event's rules: fixes, marker drops, goal declarations, QNH-corrected altitude in feet, UTM WGS84, distances, areas and zones, one result row per pilot with checks and evidence. |

## Install

Pick one.

**Copy it.** Put `skills/<name>` into your agent's skills directory (see your agent's
documentation for where that is). Nothing else is needed.

**`skills` CLI** (third-party tool from Vercel Labs; reports anonymous install telemetry):

```bash
npx skills add tadassuksteris/balloon-live-skills --list
```

```bash
npx skills add tadassuksteris/balloon-live-skills --skill balloon-live-igc
```

**Claude Code plugin:**

```
/plugin marketplace add tadassuksteris/balloon-live-skills
/plugin install balloon-live@balloon-live-skills
```

Pin to a release tag or commit if you want to review before you update.

## What you are installing

Standard-library Python 3.9+, no network, nothing to install. Scripts read the files you
give them and write only to the output paths you give them. CI lints every change against
an import allow-list and a set of text checks; that is lint, not a sandbox.
[SECURITY.md](SECURITY.md) says exactly what is checked and what is only policy.

## Layout

```
skills/<name>/    SKILL.md, scripts/, references/   (what gets installed)
tests/<name>/     unit tests and sample files
tests/validator/  bad samples the lint must keep rejecting
tools/validate_skills.py
```

Sample IGC files are derived from public competition tracks. Pilot numbers, names, device
names, install codes, serials and signatures are replaced; positions and times are real.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Before a pull request:

```bash
python3 tools/validate_skills.py && for t in tests/*/; do python3 -m unittest discover -s "$t" || break; done
```

MIT licensed.
