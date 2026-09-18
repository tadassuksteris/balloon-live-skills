# Security

Skills include scripts that an agent may run on your machine. This page separates what is
checked automatically from what is policy, so you know what you are trusting.

**These checks are lint, not a sandbox.** They catch mistakes and obvious abuse and keep
changes reviewable. They cannot prove what a script does with a file it is allowed to
open, and they will not stop a determined attacker. Read the scripts, pin to a tag or
commit, and keep your agent's normal permission prompts on.

## Checked automatically on every change (`tools/validate_skills.py`)

- Python imports come from a short allow-list of standard-library modules, plus sibling
  scripts. No networking, process, dynamic-import, serialisation or file-removal modules.
- `os` and `sys` are limited to path handling, directory listing, `makedirs`, arguments and
  exit. `eval`, `exec`, `getattr`-style indirection and dunder attribute access are rejected.
- A skill may contain only `.md`, `.py`, `.txt`, `.json` and `.csv` files. No symlinks,
  hidden files, other executables, tests or sample data; size limits apply.
- All text must be valid UTF-8 with no invisible or bidirectional characters, HTML
  comments, very long lines or long base64 runs.
- Markdown is scanned for instructions that tell an agent to fetch, install, escalate
  privileges, skip confirmation, ignore earlier instructions, or read key material.
- `SKILL.md` front matter must be a strict, single-line subset; tool pre-authorisation
  fields are not allowed.
- No dependency manifests in the repository: skills must need nothing installed.
- The lint has its own tests (`tests/validator/`) with bad samples it must keep rejecting.

## Policy, checked by review

- Scripts read only the inputs given on the command line and write only to output paths
  given as options. Existing output files are overwritten; `--help` says so.
- No use of environment variables or files outside the given inputs.
- Content of input files is untrusted. Text taken from them is stripped of control
  characters and length-limited before it is printed, and each `SKILL.md` tells the agent
  to treat it as data.
- Changes to `tools/`, `.github/` and the manifests need owner review (CODEOWNERS), so a
  pull request cannot weaken the lint and add a payload together.

## Reporting

Use GitHub's private vulnerability reporting: the repository's Security tab, then
"Report a vulnerability".
