#!/usr/bin/env python3
"""Lint every skill under skills/. Exit 1 on any failure.

This is lint, not a sandbox: it catches mistakes and obvious abuse and keeps diffs
reviewable. It cannot prove what a script does with the files it is allowed to open.

Checks
  layout      only skill folders under skills/; allowed file types; no symlinks, hidden
              files, executables other than .py, tests or sample data inside a skill;
              size limits; every skill has tests/<name>/test_*.py
  SKILL.md    strict front matter subset; name equals folder; description 1-1024 chars
  python      imports from an allow-list only; os and sys restricted to safe members;
              no eval/exec/getattr-style indirection; no dunder attribute access
  text        valid UTF-8; no invisible or bidirectional characters, HTML comments,
              very long lines or long base64 runs; no instructions that tell an agent
              to fetch, install, escalate or skip confirmation
  repo        no dependency manifests (skills must need nothing installed)
"""
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ALLOWED_IMPORTS = {"argparse", "base64", "bisect", "collections", "csv", "dataclasses", "datetime", "decimal", "enum",
                   "fractions", "functools", "itertools", "json", "math", "os", "re", "statistics", "string", "struct",
                   "sys", "textwrap", "typing", "__future__"}
OS_MEMBERS = {"path", "listdir", "makedirs", "walk", "scandir", "sep", "linesep", "fspath"}
SYS_MEMBERS = {"argv", "exit", "path", "stdout", "stderr", "stdin", "version_info", "maxsize"}
DENIED_NAMES = {"eval", "exec", "compile", "__import__", "getattr", "setattr", "delattr", "globals", "locals", "vars",
                "breakpoint", "__builtins__", "memoryview", "input"}
DUNDER_OK = {"__name__", "__doc__", "__file__"}
ALLOWED_EXT = {".md", ".py", ".txt", ".json", ".csv"}
FRONT_KEYS = {"name", "description", "license", "compatibility", "metadata"}
DEPENDENCY_FILES = {"requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "package.json", ".gitmodules"}
MAX_FILE = 200_000
MAX_SKILL = 400_000
MAX_LINE = 1000
INVISIBLE = re.compile("[­​-‏‪-‮⁠-⁩﻿]|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
BASE64_RUN = re.compile(r"[A-Za-z0-9+/=]{200,}")
RISKY = re.compile(
    r"\b(curl|wget|sudo|chmod|chown|pip3?\s+install|npm\s+(install|i)\b|npx|brew\s+install|rm\s+-rf|ssh|scp|nc\s+-)"
    r"|--dangerously|skip\w*[ -]permission|without\s+(asking|confirm\w*)|do\s+not\s+ask|"
    r"ignore\s+(all\s+|any\s+)?(previous|prior|above)\s+instructions|disregard\s+.{0,20}instructions|"
    r"~/\.|\.env\b|credential|api[_ -]?key|password|secret|token", re.I)
ALLOW_MARK = "lint: allow"  # put on a line that legitimately mentions a risky word


def front_matter(text):
    """Strict subset of YAML: `key: value` lines, plus indented lines under `metadata:`."""
    text = text.replace("\r\n", "\n")
    m = re.match(r"---\n(.*?)\n---[ \t]*\n", text, re.S)
    if not m:
        return None, "front matter must start the file and end with a line of ---"
    fields, parent = {}, None
    for line in m.group(1).split("\n"):
        if not line.strip():
            continue
        if "\t" in line or line.lstrip().startswith("#"):
            return None, f"tabs and comments are not allowed in front matter: {line!r}"
        key, sep, value = line.partition(":")
        if not sep or not re.fullmatch(r" {0,2}[A-Za-z][\w-]*", key):
            return None, f"not a simple 'key: value' line: {line!r}"
        value = value.strip()
        if line.startswith(" "):
            if parent != "metadata":
                return None, f"nested values are only allowed under metadata: {line!r}"
            continue
        parent = key
        if key in fields:
            return None, f"duplicate key {key!r}"
        if value[:1] in (">", "|", "[", "{", "&", "*", "!"):
            return None, f"{key}: block scalars, flow collections and anchors are not allowed; use one plain line"
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        fields[key] = value
    return fields, None


def check_python(path, rel, problems):
    try:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
    except (SyntaxError, UnicodeDecodeError) as e:
        problems.append(f"{rel}: cannot parse: {e}")
        return
    siblings = {f[:-3] for f in os.listdir(os.path.dirname(path)) if f.endswith(".py")}
    alias = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split(".")[0]
                if top not in ALLOWED_IMPORTS | siblings:
                    problems.append(f"{rel}:{node.lineno}: import {a.name} is not on the allow-list")
                alias[(a.asname or a.name).split(".")[0]] = top
        elif isinstance(node, ast.ImportFrom):
            top = (node.module or "").split(".")[0]
            if node.level or top not in ALLOWED_IMPORTS | siblings:
                problems.append(f"{rel}:{node.lineno}: from {'.' * node.level}{node.module or ''} is not on the allow-list")
            if top in ("os", "sys"):
                problems.append(f"{rel}:{node.lineno}: write 'import {top}', not 'from {top} import ...'")
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in DENIED_NAMES:
            problems.append(f"{rel}:{node.lineno}: use of {node.id}")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") and node.attr not in DUNDER_OK:
                problems.append(f"{rel}:{node.lineno}: dunder attribute .{node.attr}")
            if isinstance(node.value, ast.Name):
                module = alias.get(node.value.id)
                if module == "os" and node.attr not in OS_MEMBERS:
                    problems.append(f"{rel}:{node.lineno}: os.{node.attr} is not allowed")
                if module == "sys" and node.attr not in SYS_MEMBERS:
                    problems.append(f"{rel}:{node.lineno}: sys.{node.attr} is not allowed")


def check_text(path, rel, problems):
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except UnicodeDecodeError:
        problems.append(f"{rel}: not valid UTF-8")
        return
    if rel.endswith(".md") and "<!--" in text:
        problems.append(f"{rel}: HTML comments are not allowed (hidden text)")
    for number, line in enumerate(text.split("\n"), 1):
        if INVISIBLE.search(line):
            problems.append(f"{rel}:{number}: invisible, control or bidirectional character")
        if len(line) > MAX_LINE and not line.startswith("description:"):
            problems.append(f"{rel}:{number}: line longer than {MAX_LINE} characters")
        if BASE64_RUN.search(line):
            problems.append(f"{rel}:{number}: long base64-like run")
        hit = RISKY.search(line)
        if hit and ALLOW_MARK not in line and not rel.endswith(".py"):
            problems.append(f"{rel}:{number}: risky instruction or term {hit.group(0)!r} "
                            f"(add '{ALLOW_MARK}' to the line if it is legitimate)")


def check_skill(root, name, problems):
    path = os.path.join(root, "skills", name)
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name) or len(name) > 64:
        problems.append(f"{name}: folder name must be lowercase kebab-case, at most 64 characters")
    skill_md = os.path.join(path, "SKILL.md")
    if not os.path.isfile(skill_md):
        problems.append(f"{name}: no SKILL.md")
    else:
        with open(skill_md, encoding="utf-8", errors="replace") as fh:
            fields, err = front_matter(fh.read())
        if err:
            problems.append(f"{name}/SKILL.md: {err}")
        else:
            if fields.get("name") != name:
                problems.append(f"{name}/SKILL.md: name {fields.get('name')!r} must equal the folder name")
            if not 1 <= len(fields.get("description", "")) <= 1024:
                problems.append(f"{name}/SKILL.md: description must be 1-1024 characters on one line")
            for key in sorted(set(fields) - FRONT_KEYS):
                problems.append(f"{name}/SKILL.md: front matter key {key!r} is not allowed here")
    total = 0
    for dirpath, dirnames, filenames in os.walk(path):
        for d in list(dirnames):
            full = os.path.join(dirpath, d)
            if d == "__pycache__":  # bytecode written by any local run; ignored by git and by this walk
                dirnames.remove(d)
                continue
            if os.path.islink(full) or d.startswith(".") or d in ("tests", "fixtures"):
                problems.append(f"{os.path.relpath(full, root)}: not allowed inside a skill (symlink, hidden or tests)")
                dirnames.remove(d)
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            ext = os.path.splitext(fn)[1].lower()
            if os.path.islink(full) or fn.startswith("."):
                problems.append(f"{rel}: symlinks and hidden files are not allowed")
                continue
            if ext not in ALLOWED_EXT:
                problems.append(f"{rel}: file type {ext or '(none)'} is not allowed in a skill")
                continue
            size = os.path.getsize(full)
            total += size
            if size > MAX_FILE:
                problems.append(f"{rel}: larger than {MAX_FILE // 1000} KB")
            if ext != ".py" and os.access(full, os.X_OK):
                problems.append(f"{rel}: only .py files may be executable")
            check_text(full, rel, problems)
            if ext == ".py":
                check_python(full, rel, problems)
    if total > MAX_SKILL:
        problems.append(f"{name}: skill is {total // 1000} KB, limit {MAX_SKILL // 1000} KB")
    tests = os.path.join(root, "tests", name)
    if not (os.path.isdir(tests) and any(f.startswith("test_") and f.endswith(".py") for f in os.listdir(tests))):
        problems.append(f"{name}: no tests/{name}/test_*.py")


def validate(root=ROOT):
    problems = []
    skills_dir = os.path.join(root, "skills")
    if not os.path.isdir(skills_dir):
        return ["no skills/ directory"], 0
    names = []
    for entry in sorted(os.listdir(skills_dir)):
        full = os.path.join(skills_dir, entry)
        if os.path.isdir(full) and not os.path.islink(full):
            names.append(entry)
            check_skill(root, entry, problems)
        elif entry != ".gitkeep":
            problems.append(f"skills/{entry}: only skill folders belong here")
    for dep in sorted(DEPENDENCY_FILES):
        if os.path.exists(os.path.join(root, dep)):
            problems.append(f"{dep}: skills must not need anything installed")
    return problems, len(names)


def main():
    problems, count = validate()
    for p in problems:
        print(f"FAIL {p}")
    print(f"{count} skill(s) checked, {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
