#!/usr/bin/env python3
"""Validate published rules and (re)generate index.json.

The registry's single validation entry point. CI and contributors run the same
script, so a green local run means a green PR.

    python3 scripts/validate_registry.py              # validate everything
    python3 scripts/validate_registry.py --write-index # validate, then rewrite index.json
    python3 scripts/validate_registry.py --base main   # also enforce version bumps vs a ref

Every check maps to a numbered gate in ADR-081 section 5.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("error: PyYAML is required — pip install pyyaml")

try:
    import jsonschema
except ImportError:
    sys.exit("error: jsonschema is required — pip install jsonschema")

REPO = Path(__file__).resolve().parent.parent
RULES_ROOT = REPO / "rules"
SCHEMA_PATH = REPO / "schema" / "rule.schema.json"
ROOT_LICENSE = REPO / "LICENSE"
INDEX_PATH = REPO / "index.json"

KINDS = ("codegen",)
RESERVED_VENDORS = ("codexx",)

REQUIRED_FILES = ("rule.json", "README.md", "LICENSE", "config.yaml",
                  "transform.luau", "preamble.luau")
OPTIONAL_FILES = ("grouping.luau", "input.hpp")

# Globals exported by <rulesDir>/../shared/*.luau. That directory is Team-tier
# gated and first-party — a rule that needs it hard-fails E102 for most of its
# audience, so published rules must be self-contained (ADR-081 section 5.6).
SHARED_GLOBALS = ("makeTypesystem", "makeJSONCodegen", "makeBytePackCodegen")

MIN_README_BYTES = 200


class Findings:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, where: str, msg: str) -> None:
        self.errors.append(f"{where}: {msg}")

    def warn(self, where: str, msg: str) -> None:
        self.warnings.append(f"{where}: {msg}")


def kebab(name: str) -> str:
    """PascalCase -> kebab-case, per ADR-079 section 1.1.

    Splits on case boundaries so acronym runs survive readably:
    JSONSerializable -> json-serializable, not jsonserializable.
    """
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", name)
    s = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "-", s)
    return s.replace("_", "-").lower()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_semver(v: str) -> tuple[int, int, int] | None:
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", v)
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def discover_rules() -> list[tuple[str, str, Path]]:
    """Yield (kind, vendor, rule_dir) for every directory in the tree."""
    found = []
    if not RULES_ROOT.is_dir():
        return found
    for kind_dir in sorted(p for p in RULES_ROOT.iterdir() if p.is_dir()):
        for vendor_dir in sorted(p for p in kind_dir.iterdir() if p.is_dir()):
            for rule_dir in sorted(p for p in vendor_dir.iterdir() if p.is_dir()):
                found.append((kind_dir.name, vendor_dir.name, rule_dir))
    return found


def validate_rule(kind, vendor, rule_dir, schema, root_license_hash, f: Findings):
    """Run gates 1 and 3-7, 9 for one rule. Returns its manifest, or None."""
    where = str(rule_dir.relative_to(REPO))

    if kind not in KINDS:
        f.error(where, f"unknown rule kind '{kind}' — expected one of {', '.join(KINDS)}")
        return None

    # --- files present (gate 3, partly) --------------------------------------
    for name in REQUIRED_FILES:
        if not (rule_dir / name).is_file():
            f.error(where, f"missing required file '{name}'")

    known = set(REQUIRED_FILES) | set(OPTIONAL_FILES)
    for extra in sorted(p.name for p in rule_dir.iterdir()):
        # gate 5: a published rule must never carry secrets. The loader reads
        # <ruleDir>/.env, so shipping one is a live exfiltration surface.
        if extra == ".env":
            f.error(where, "'.env' is not permitted in a published rule — "
                           "the loader reads it, and secrets must not be published")
        elif extra not in known:
            f.warn(where, f"unrecognized file '{extra}' — it will be ignored by codegen")

    if not (rule_dir / "rule.json").is_file():
        return None

    # --- gate 1: manifest ----------------------------------------------------
    try:
        manifest = json.loads((rule_dir / "rule.json").read_text())
    except json.JSONDecodeError as e:
        f.error(where, f"rule.json is not valid JSON: {e}")
        return None

    try:
        jsonschema.validate(manifest, schema)
    except jsonschema.ValidationError as e:
        loc = "/".join(str(p) for p in e.absolute_path) or "(root)"
        f.error(where, f"rule.json fails schema at '{loc}': {e.message}")
        return None

    if manifest["vendor"] != vendor:
        f.error(where, f"rule.json vendor '{manifest['vendor']}' "
                       f"does not match directory '{vendor}'")
    if manifest["rule"] != rule_dir.name:
        f.error(where, f"rule.json rule '{manifest['rule']}' "
                       f"does not match directory '{rule_dir.name}'")

    expected_id = f"{kebab(vendor)}.{kebab(rule_dir.name)}"
    if manifest["id"] != expected_id:
        f.error(where, f"rule.json id '{manifest['id']}' should be '{expected_id}' "
                       f"(ADR-079 section 1.1 kebab-ization of vendor + rule)")

    # --- gate 3: README + LICENSE -------------------------------------------
    readme = rule_dir / "README.md"
    if readme.is_file() and len(readme.read_bytes()) < MIN_README_BYTES:
        f.error(where, f"README.md is under {MIN_README_BYTES} bytes — describe what "
                       f"the rule generates, how to trigger it, and what it emits")

    lic = rule_dir / "LICENSE"
    if lic.is_file() and sha256(lic) != root_license_hash:
        f.error(where, "LICENSE is not byte-identical to the repository root LICENSE — "
                       "copy it verbatim (`cp LICENSE <rule dir>/`)")

    # --- gate 4: config.yaml -------------------------------------------------
    cfg_path = rule_dir / "config.yaml"
    cfg = None
    if cfg_path.is_file():
        try:
            cfg = yaml.safe_load(cfg_path.read_text()) or {}
        except yaml.YAMLError as e:
            f.error(where, f"config.yaml does not parse: {e}")
        if isinstance(cfg, dict):
            if cfg.get("version") != 1:
                f.error(where, f"config.yaml declares version {cfg.get('version')!r}; "
                               f"the engine accepts only 1 (E002)")

            ext = cfg.get("extends")
            if ext is not None:
                target = (cfg_path.parent / str(ext)).resolve()
                if not target.is_relative_to(rule_dir.resolve()):
                    f.error(where, f"config.yaml 'extends: {ext}' escapes the rule "
                                   f"directory — published rules are self-contained")

            lang = (cfg.get("output") or {}).get("language", "cpp")
            declared = manifest.get("outputLanguage")
            if declared is not None and declared != lang:
                f.error(where, f"rule.json outputLanguage '{declared}' disagrees with "
                               f"config.yaml output.language '{lang}'")

            # gate 7: the ADR-008 capability escapes. Permitted, never automatic.
            perms = cfg.get("permissions") or {}
            if (perms.get("http") or {}).get("allowlist"):
                f.warn(where, "declares permissions.http.allowlist — needs security review")
            if (perms.get("env") or {}).get("os_allowlist"):
                f.warn(where, "declares permissions.env.os_allowlist — needs security review")
            if perms.get("registry"):
                f.warn(where, "declares permissions.registry — parsed but ignored by the "
                              "engine (W006); ADR-081 section 7 keeps it reserved")

    # --- gate 6: no dependence on shared/ ------------------------------------
    for script in sorted(rule_dir.glob("*.luau")):
        src = script.read_text()
        for g in SHARED_GLOBALS:
            if re.search(rf"\b{re.escape(g)}\b", src):
                f.error(where, f"{script.name} calls '{g}', provided by the Team-tier "
                               f"shared/ libraries — published rules must be self-contained")

    manifest["_kind"] = kind
    manifest["_path"] = str(rule_dir.relative_to(REPO)).replace("\\", "/")
    return manifest


def check_reserved_vendors(manifests, actor: str | None, members: set[str], f: Findings):
    """Gate 2 — the ADR-079 OQ6 trust boundary.

    The reservation is only meaningful where vendor and provenance are both
    known. That is here, at publication, and nowhere else.
    """
    if actor is None:
        return  # not running in a PR context; nothing to check against
    for m in manifests:
        if m["vendor"] in RESERVED_VENDORS and actor.lower() not in members:
            f.error(m["_path"], f"vendor '{m['vendor']}' is reserved to CodeXX-DTDK "
                                f"org members; '{actor}' is not one")


def check_version_bumps(manifests, base: str, f: Findings):
    """Gate 8 — any content change under a rule directory must bump version."""
    for m in manifests:
        path = m["_path"]
        try:
            changed = subprocess.run(
                ["git", "diff", "--quiet", base, "--", path],
                cwd=REPO, capture_output=True).returncode
        except FileNotFoundError:
            f.warn(path, "git unavailable — skipping version-bump check")
            return
        if changed == 0:
            continue  # untouched

        old = subprocess.run(["git", "show", f"{base}:{path}/rule.json"],
                             cwd=REPO, capture_output=True, text=True)
        if old.returncode != 0:
            continue  # new rule — nothing to compare against

        try:
            old_version = json.loads(old.stdout)["version"]
        except (json.JSONDecodeError, KeyError):
            f.warn(path, f"could not read previous version from {base}")
            continue

        new_v, old_v = parse_semver(m["version"]), parse_semver(old_version)
        if new_v is None or old_v is None:
            continue
        if new_v <= old_v:
            f.error(path, f"content changed but version did not increase "
                          f"({old_version} -> {m['version']})")


def build_index(manifests) -> dict:
    return {
        "schemaVersion": 1,
        "rules": [
            {
                "id": m["id"],
                "kind": m["_kind"],
                "vendor": m["vendor"],
                "rule": m["rule"],
                "version": m["version"],
                "path": m["_path"],
                "tag": f"{m['_kind']}/{m['vendor']}.{m['rule']}@{m['version']}",
                "description": m["description"],
                "outputLanguage": m.get("outputLanguage", "cpp"),
                "yanked": m.get("yanked", False),
            }
            for m in sorted(manifests, key=lambda m: m["id"])
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write-index", action="store_true",
                    help="rewrite index.json instead of only checking it")
    ap.add_argument("--base", metavar="REF",
                    help="git ref to compare against for the version-bump gate")
    ap.add_argument("--actor", metavar="LOGIN",
                    help="PR author's GitHub login, for the reserved-vendor gate")
    ap.add_argument("--org-members", metavar="FILE",
                    help="file of CodeXX-DTDK member logins, one per line")
    args = ap.parse_args()

    f = Findings()
    schema = json.loads(SCHEMA_PATH.read_text())
    root_license_hash = sha256(ROOT_LICENSE)

    rules = discover_rules()
    if not rules:
        print("no rules found under rules/ — nothing to validate")

    manifests = []
    for kind, vendor, rule_dir in rules:
        m = validate_rule(kind, vendor, rule_dir, schema, root_license_hash, f)
        if m:
            manifests.append(m)

    seen: dict[str, str] = {}
    for m in manifests:
        if m["id"] in seen:
            f.error(m["_path"], f"identity '{m['id']}' already used by {seen[m['id']]}")
        seen[m["id"]] = m["_path"]

    members = set()
    if args.org_members and Path(args.org_members).is_file():
        members = {ln.strip().lower() for ln in
                   Path(args.org_members).read_text().splitlines() if ln.strip()}
    check_reserved_vendors(manifests, args.actor, members, f)

    if args.base:
        check_version_bumps(manifests, args.base, f)

    # --- index.json ----------------------------------------------------------
    index = build_index(manifests)
    rendered = json.dumps(index, indent=2) + "\n"
    if args.write_index:
        INDEX_PATH.write_text(rendered)
        print(f"wrote {INDEX_PATH.relative_to(REPO)} ({len(manifests)} rules)")
    elif not f.errors:
        current = INDEX_PATH.read_text() if INDEX_PATH.is_file() else ""
        if current != rendered:
            f.error("index.json", "out of date — regenerate with "
                                  "`python3 scripts/validate_registry.py --write-index`")

    for w in f.warnings:
        print(f"warning: {w}")
    for e in f.errors:
        print(f"error: {e}", file=sys.stderr)

    if f.errors:
        print(f"\n{len(f.errors)} error(s) across {len(rules)} rule(s)", file=sys.stderr)
        return 1
    print(f"ok — {len(rules)} rule(s) validated, {len(f.warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
