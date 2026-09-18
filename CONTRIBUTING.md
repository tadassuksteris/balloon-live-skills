# Contributing

## Adding a skill

1. `skills/<name>/SKILL.md` with front matter `name` (lowercase, hyphens, equal to the
   folder) and `description` (what it does and when to use it; one line, under 1024
   characters). Allowed keys: `name`, `description`, `license`, `compatibility`, `metadata`.
   Plain `key: value` lines only: no block scalars, comments or tabs.
2. Aim for a `SKILL.md` under 100 lines. Detail goes in `references/`, code in `scripts/`.
3. Scripts: Python 3.9+, standard library from the allow-list in
   `tools/validate_skills.py`, importable, with `--help`. A new module is a one-line change
   to that list and is reviewed like any other change.
4. Tests and sample files go in `tests/<name>/`, never inside the skill. Replace anything
   that identifies a person or device in sample files, and say what is still real.
5. Keep skills vendor-neutral: no agent-specific fields, tool names or paths.

```bash
python3 tools/validate_skills.py && for t in tests/*/; do python3 -m unittest discover -s "$t" || break; done
```

Optional, for Claude Code users: `claude plugin validate .` checks the plugin manifests.

If the lint flags a legitimate sentence in a reference file, end that line with
`(lint: allow)`. Expect to explain it in review.

## Changing a skill

Add or update a test for any parsing or calculation change. Keep `SKILL.md` to what
changes what the agent does; anything the scripts already handle stays in the code.

## Releases

One version for the repository: bump `version` in `.claude-plugin/plugin.json`, add a line
to `CHANGELOG.md`, tag `vX.Y.Z`.

## Maintainer settings (GitHub)

Branch protection on `main` requiring CI and code-owner review; secret scanning with push
protection; private vulnerability reporting; approval required for workflows from outside
collaborators. Never change the workflow trigger to `pull_request_target`.
