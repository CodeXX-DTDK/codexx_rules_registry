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

# GitHub's author_association values that mean "inside the org". COLLABORATOR is
# deliberately absent: a repo collaborator is not necessarily an org member, and the
# reservation is on the org's identity.
ORG_ASSOCIATIONS = ("OWNER", "MEMBER")

COMPONENTS_ROOT = REPO / "components"
COMPONENT_SCHEMA_PATH = REPO / "schema" / "component.schema.json"
COMPONENT_KIND = "codegen-component"

REQUIRED_FILES = ("rule.json", "README.md", "LICENSE", "config.yaml",
                  "transform.luau", "preamble.luau")
OPTIONAL_FILES = ("grouping.luau", "input.hpp")

# A rule may ship private components in its own subdirectory. They install under the rule's
# own vendor namespace and are not separately versioned, so they are for helpers nobody else
# should depend on -- anything shared is published as its own unit (ADR-083 section 4).
EMBEDDED_COMPONENTS_DIR = "components"
MAX_EMBEDDED_COMPONENTS = 16

COMPONENT_REQUIRED_FILES = ("component.json", "README.md", "LICENSE", "component.luau")

# require("acme/typemap") -- literal argument only. A computed one cannot be validated, and
# a rule whose dependencies cannot be enumerated cannot be published.
REQUIRE_LITERAL_RE = re.compile(r"require\s*\(\s*['\"]([^'\"]+)['\"]\s*\)")
REQUIRE_ANY_RE = re.compile(r"require\s*\(")

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


def validate_embedded_components(comp_dir: Path, where: str, f: Findings):
    """A rule's private components/ directory: .luau files only, one level, lowercase stems."""
    entries = sorted(comp_dir.iterdir())

    if len(entries) > MAX_EMBEDDED_COMPONENTS:
        f.error(where, f"{EMBEDDED_COMPONENTS_DIR}/ holds more than "
                       f"{MAX_EMBEDDED_COMPONENTS} files")

    for entry in entries:
        rel = f"{EMBEDDED_COMPONENTS_DIR}/{entry.name}"

        if entry.is_dir():
            f.error(where, f"{rel} is a directory — embedded components are flat .luau files")
        elif entry.suffix != ".luau":
            f.error(where, f"{rel} is not a .luau module")
        elif not re.fullmatch(r"[a-z][a-z0-9_-]*", entry.stem):
            # Lowercase only: two stems differing in case are one file on Windows and macOS
            # and two on Linux, so a mixed-case name resolves differently per consumer.
            f.error(where, f"{rel}: component names must match [a-z][a-z0-9_-]*")


def discover_components() -> list[tuple[str, str, Path]]:
    """Yield (kind, vendor, component_dir) for every published component."""
    found = []
    if not COMPONENTS_ROOT.is_dir():
        return found
    for kind_dir in sorted(p for p in COMPONENTS_ROOT.iterdir() if p.is_dir()):
        for vendor_dir in sorted(p for p in kind_dir.iterdir() if p.is_dir()):
            for comp_dir in sorted(p for p in vendor_dir.iterdir() if p.is_dir()):
                found.append((kind_dir.name, vendor_dir.name, comp_dir))
    return found


def validate_component(kind, vendor, comp_dir, schema, root_license_hash, f: Findings):
    """Component equivalent of validate_rule. Returns its manifest, or None."""
    where = str(comp_dir.relative_to(REPO))

    if kind != COMPONENT_KIND:
        f.error(where, f"unknown component kind '{kind}' — expected '{COMPONENT_KIND}'")
        return None

    for name in COMPONENT_REQUIRED_FILES:
        if not (comp_dir / name).is_file():
            f.error(where, f"missing required file '{name}'")

    for extra in sorted(p.name for p in comp_dir.iterdir()):
        if extra == ".env":
            f.error(where, "'.env' is not permitted in a published component")
        elif extra not in COMPONENT_REQUIRED_FILES:
            f.warn(where, f"unrecognized file '{extra}' — it will not be installed")

    if not (comp_dir / "component.json").is_file():
        return None

    try:
        manifest = json.loads((comp_dir / "component.json").read_text())
    except json.JSONDecodeError as e:
        f.error(where, f"component.json is not valid JSON: {e}")
        return None

    try:
        jsonschema.validate(manifest, schema)
    except jsonschema.ValidationError as e:
        f.error(where, f"component.json: {e.message} (at {'/'.join(str(x) for x in e.path)})")
        return None

    if manifest["vendor"] != vendor:
        f.error(where, f"vendor '{manifest['vendor']}' does not match the directory '{vendor}'")
    if manifest["name"] != comp_dir.name:
        f.error(where, f"name '{manifest['name']}' does not match the directory "
                       f"'{comp_dir.name}'")

    expected_id = f"{kebab(manifest['vendor'])}.{manifest['name']}"
    if manifest["id"] != expected_id:
        f.error(where, f"id '{manifest['id']}' should be '{expected_id}'")

    if (comp_dir / "LICENSE").is_file() and sha256(comp_dir / "LICENSE") != root_license_hash:
        f.error(where, "LICENSE is not byte-identical to the repository root LICENSE")

    if (comp_dir / "README.md").is_file() and \
            len((comp_dir / "README.md").read_bytes()) < MIN_README_BYTES:
        f.error(where, f"README.md is shorter than {MIN_README_BYTES} bytes — say what the "
                       f"component provides and how to use it")

    module = comp_dir / "component.luau"
    if module.is_file():
        src = module.read_text()

        # A module that returns nothing is an E028 at run time for every rule that requires
        # it. Cheap to catch here, and impossible to diagnose from the consumer's side.
        if not re.search(r"^\s*return\b", src, re.MULTILINE):
            f.error(where, "component.luau has no top-level `return` — a component must "
                           "return its module value")

        declared = {u["component"].replace(".", "/", 1) for u in manifest.get("uses", [])}

        literal_count = len(REQUIRE_LITERAL_RE.findall(src))
        if len(REQUIRE_ANY_RE.findall(src)) != literal_count:
            f.error(where, "component.luau calls require() with a non-literal argument")

        for key in REQUIRE_LITERAL_RE.findall(src):
            if key not in declared:
                f.error(where, f"component.luau requires '{key}', which is not declared in "
                               f"component.json 'uses'")

    manifest["_kind"] = kind
    manifest["_path"] = str(comp_dir.relative_to(REPO)).replace("\\", "/")
    return manifest


def check_uses_closure(rule_manifests, component_manifests, f: Findings):
    """A rule must declare every component it will transitively need.

    This is what lets the installer stay a flat loop with no version solver: the rule names
    the whole set, so nothing has to be discovered at install time. It also means a component
    adding a dependency is a breaking change for its consumers, which is deliberate -- it
    surfaces at publish time here rather than as an E027 on a user's machine.
    """
    by_id = {m["id"]: m for m in component_manifests}

    for m in component_manifests:
        for dep in (u["component"] for u in m.get("uses", [])):
            if dep not in by_id:
                f.error(m["_path"], f"uses '{dep}', which is not published in this registry")

    # Cycle check over the component graph.
    state: dict[str, int] = {}

    def visit(cid: str, chain: list[str]) -> None:
        if state.get(cid) == 2:
            return
        if state.get(cid) == 1:
            f.error(by_id[cid]["_path"], "component cycle: " + " -> ".join(chain + [cid]))
            return
        state[cid] = 1
        for dep in (u["component"] for u in by_id.get(cid, {}).get("uses", [])):
            if dep in by_id:
                visit(dep, chain + [cid])
        state[cid] = 2

    for cid in by_id:
        visit(cid, [])

    def closure(cid: str, seen: set[str]) -> set[str]:
        for dep in (u["component"] for u in by_id.get(cid, {}).get("uses", [])):
            if dep not in seen:
                seen.add(dep)
                closure(dep, seen)
        return seen

    for m in rule_manifests:
        declared = {u["component"] for u in m.get("uses", [])}
        needed: set[str] = set()
        for cid in declared:
            needed |= closure(cid, set())

        missing = needed - declared
        if missing:
            f.error(m["_path"], "uses[] is not closed: also needs " + ", ".join(sorted(missing)) +
                                " (a component it uses depends on them)")


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
        elif extra == EMBEDDED_COMPONENTS_DIR and (rule_dir / extra).is_dir():
            validate_embedded_components(rule_dir / extra, where, f)
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

    # --- gate 6: everything the rule uses must be declared -------------------
    #
    # "Published rules must be self-contained" (ADR-083 section 4). This used to be a grep for
    # a handful of known helper names, which was a proxy: it caught the only undeclared
    # dependency that existed at the time and nothing else. The property is the same -- a
    # published rule works for whoever installs it -- but it is now checked structurally.
    declared = {u["component"].replace(".", "/", 1)
                for u in manifest.get("uses", [])}

    embedded = {f"{vendor}/{p.stem}"
                for p in (rule_dir / EMBEDDED_COMPONENTS_DIR).glob("*.luau")} \
        if (rule_dir / EMBEDDED_COMPONENTS_DIR).is_dir() else set()

    for script in sorted(rule_dir.rglob("*.luau")):
        src = script.read_text()
        rel = script.relative_to(rule_dir).as_posix()

        literal_count = len(REQUIRE_LITERAL_RE.findall(src))
        if len(REQUIRE_ANY_RE.findall(src)) != literal_count:
            f.error(where, f"{rel} calls require() with a non-literal argument — a rule whose "
                           f"dependencies cannot be enumerated cannot be validated")

        for key in REQUIRE_LITERAL_RE.findall(src):
            if key in declared or key in embedded:
                continue
            f.error(where, f"{rel} requires '{key}', which is neither declared in "
                           f"rule.json 'uses' nor shipped in {EMBEDDED_COMPONENTS_DIR}/")

    manifest["_kind"] = kind
    manifest["_path"] = str(rule_dir.relative_to(REPO)).replace("\\", "/")
    return manifest


def changed_paths(base: str) -> set[str] | None:
    """Repo-relative paths touched relative to `base`. None if git cannot answer."""
    try:
        out = subprocess.run(["git", "diff", "--name-only", base],
                             cwd=REPO, capture_output=True, text=True)
    except FileNotFoundError:
        return None
    if out.returncode != 0:
        return None
    return {ln.strip() for ln in out.stdout.splitlines() if ln.strip()}


def check_reserved_vendors(manifests, actor: str | None, association: str | None,
                           touched: set[str] | None, f: Findings):
    """Gate 2 — the ADR-079 OQ6 trust boundary.

    The reservation is only meaningful where vendor and provenance are both
    known. That is here, at publication, and nowhere else.

    Two things this must get right, and originally did not:

    Membership comes from the PR's `author_association`, NOT from the public
    org-members list. A member whose org membership is private does not appear
    in /public_members, so resolving it that way made the reserved tag
    unclaimable by anyone — including the people it is reserved FOR. GitHub
    computes author_association itself and reports MEMBER regardless of
    visibility, it costs no API call, and a fork cannot forge it.

    The gate applies only to units this change actually TOUCHES. Checking every
    manifest in the tree meant an outside contributor adding their own rule
    failed on a pre-existing codexx rule they had never opened — which would
    have rejected every third-party contribution the registry exists to accept.
    """
    if actor is None:
        return  # not running in a PR context; nothing to attribute a claim to

    is_org_member = (association or "").upper() in ORG_ASSOCIATIONS

    if touched is None:
        f.warn("scripts/validate_registry.py",
               "could not determine which units this change touches — applying the "
               "reserved-vendor gate to the whole tree")

    for m in manifests:
        if m["vendor"] not in RESERVED_VENDORS:
            continue
        # Untouched by this change: not this author's claim to make, either way.
        if touched is not None and not any(p == m["_path"] or p.startswith(m["_path"] + "/")
                                           for p in touched):
            continue
        if not is_org_member:
            f.error(m["_path"],
                    f"vendor '{m['vendor']}' is reserved to CodeXX-DTDK org members; "
                    f"'{actor}' is {association or 'unaffiliated'}")


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


def _rule_row(m: dict) -> dict:
    row = {
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
    if m.get("namespaced"):
        row["namespaced"] = True
    if m.get("uses"):
        row["uses"] = [u["component"].replace(".", "/", 1) for u in m["uses"]]
    if m.get("requires"):
        row["requires"] = m["requires"]
    return row


def _component_row(m: dict) -> dict:
    row = {
        "id": m["id"],
        "kind": m["_kind"],
        "vendor": m["vendor"],
        "name": m["name"],
        "version": m["version"],
        "path": m["_path"],
        "tag": f"{m['_kind']}/{m['vendor']}.{m['name']}@{m['version']}",
        "description": m["description"],
        "yanked": m.get("yanked", False),
    }
    if m.get("uses"):
        row["uses"] = [u["component"].replace(".", "/", 1) for u in m["uses"]]
    if m.get("requires"):
        row["requires"] = m["requires"]
    return row


def build_index(manifests, component_manifests=()) -> dict:
    """Partition the catalog so an ALREADY-RELEASED codegen stays correct.

    A shipped client reads doc["rules"] and nothing else -- not schemaVersion, not any sibling
    key. So `rules` holds only rules that client can actually run: self-contained ones. A rule
    that gains components moves to `rulesWithComponents` and disappears from its view, which is
    visible ("it vanished") rather than broken ("it installed and every run fails"). That is as
    loud as a binary already in the wild can be made.
    """
    ordered = sorted(manifests, key=lambda m: m["id"])

    return {
        "schemaVersion": 2,
        "rules": [_rule_row(m) for m in ordered if not m.get("uses")],
        "rulesWithComponents": [_rule_row(m) for m in ordered if m.get("uses")],
        "components": [_component_row(m)
                       for m in sorted(component_manifests, key=lambda m: m["id"])],
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
    ap.add_argument("--actor-association", metavar="ASSOC",
                    help="PR author_association as GitHub reports it (OWNER/MEMBER/"
                         "COLLABORATOR/CONTRIBUTOR/NONE), for the reserved-vendor gate")
    args = ap.parse_args()

    f = Findings()
    schema = json.loads(SCHEMA_PATH.read_text())
    component_schema = json.loads(COMPONENT_SCHEMA_PATH.read_text())
    root_license_hash = sha256(ROOT_LICENSE)

    rules = discover_rules()
    components = discover_components()
    if not rules and not components:
        print("nothing found under rules/ or components/ — nothing to validate")

    manifests = []
    for kind, vendor, rule_dir in rules:
        m = validate_rule(kind, vendor, rule_dir, schema, root_license_hash, f)
        if m:
            manifests.append(m)

    component_manifests = []
    for kind, vendor, comp_dir in components:
        m = validate_component(kind, vendor, comp_dir, component_schema, root_license_hash, f)
        if m:
            component_manifests.append(m)

    seen: dict[str, str] = {}
    for m in manifests + component_manifests:
        if m["id"] in seen:
            f.error(m["_path"], f"identity '{m['id']}' already used by {seen[m['id']]}")
        seen[m["id"]] = m["_path"]

    # Every component a rule declares must exist and be usable.
    published = {m["id"] for m in component_manifests}
    for m in manifests:
        for dep in (u["component"] for u in m.get("uses", [])):
            if dep not in published:
                f.error(m["_path"], f"uses '{dep}', which is not published in this registry")
            elif next(c for c in component_manifests if c["id"] == dep).get("yanked"):
                f.error(m["_path"], f"uses '{dep}', which is yanked")

    check_uses_closure(manifests, component_manifests, f)

    touched = changed_paths(args.base) if args.base else None
    check_reserved_vendors(manifests + component_manifests, args.actor,
                           args.actor_association, touched, f)

    if args.base:
        check_version_bumps(manifests, args.base, f)
        check_version_bumps(component_manifests, args.base, f)

    # --- index.json ----------------------------------------------------------
    index = build_index(manifests, component_manifests)
    rendered = json.dumps(index, indent=2) + "\n"
    if args.write_index:
        INDEX_PATH.write_text(rendered)
        print(f"wrote {INDEX_PATH.relative_to(REPO)} "
              f"({len(manifests)} rules, {len(component_manifests)} components)")
    elif not f.errors:
        current = INDEX_PATH.read_text() if INDEX_PATH.is_file() else ""
        if current != rendered:
            f.error("index.json", "out of date — regenerate with "
                                  "`python3 scripts/validate_registry.py --write-index`")

    for w in f.warnings:
        print(f"warning: {w}")
    for e in f.errors:
        print(f"error: {e}", file=sys.stderr)

    units = len(rules) + len(components)
    if f.errors:
        print(f"\n{len(f.errors)} error(s) across {units} unit(s)", file=sys.stderr)
        return 1
    print(f"ok — {len(rules)} rule(s), {len(components)} component(s) validated, "
          f"{len(f.warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
