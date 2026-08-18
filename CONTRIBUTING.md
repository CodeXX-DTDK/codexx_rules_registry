# Contributing a rule

Publishing happens through pull requests only. There is no publish command — review is
the point, not an obstacle to route around.

## Licensing — read this first

By submitting a rule you agree to license it under this repository's
[MIT license](./LICENSE) (inbound = outbound). You keep your copyright; you are granting
everyone the MIT permissions over your contribution.

You are credited in your rule's `rule.json` (`authors`) and its `README.md` — **not** in
the `LICENSE` file, which is a verbatim copy of the repo-wide one so that validation is a
simple hash check.

Only submit rules you wrote, or that you have the right to relicense under MIT.

## Pick a vendor tag

Your rules live under a vendor tag you choose — lowercase, `[a-z0-9-]+`. It namespaces
your work, so your `ToString` never collides with anyone else's.

`codexx` is reserved for first-party rules and CI rejects it from anyone outside the
CodeXX-DTDK organisation.

## Add your rule

One directory, at `rules/codegen/<your-vendor>/<RuleName>/`:

```
rules/codegen/acme/ToString/
    rule.json        required
    README.md        required
    LICENSE          required — cp ../../../../LICENSE .
    config.yaml      required
    transform.luau   required
    preamble.luau    required
    grouping.luau    optional
    input.hpp        optional — a sample header your rule runs against
    components/      optional — private Luau modules, flat .luau, lowercase stems
```

`<RuleName>` is also the C++ attribute token your rule matches — `[[codegen::ToString]]`, or
`[[acme::ToString]]` if you set `"namespaced": true` (see below) — so it is `PascalCase`, not
kebab.

A rule may also ship private helpers in a `components/` subdirectory; see *Publishing a
component*.

### `rule.json`

```json
{
  "schemaVersion": 1,
  "id": "acme.to-string",
  "vendor": "acme",
  "rule": "ToString",
  "version": "1.0.0",
  "description": "Generates std::string_view toString(Enum) switches.",
  "authors": [{ "name": "Jane Doe", "github": "jdoe" }],
  "license": "MIT",
  "homepage": "https://github.com/jdoe/to-string-rule",
  "keywords": ["enum", "reflection"],
  "outputLanguage": "cpp",
  "requires": [{ "component": "codegen", "range": ">=0.1.0 <1.0.0" }]
}
```

- **`id`** is your vendor and rule name kebab-ized and joined with one `.`. Case
  boundaries become dashes, so `JSONSerializable` becomes `json-serializable` — the
  validator tells you the expected value if you get it wrong.
- **`version`** is plain semver. No channels, no `-rc.1`.
- **`requires[].range`** is the codegen version your rule needs: `>=X.Y.Z` or
  `>=X.Y.Z <A.B.C`. No caret, no tilde. codegen now **enforces** this — a user whose build is
  outside the range is told to update rather than installing a rule that cannot run. Set the
  floor to the oldest codegen you have actually tested against; if your rule uses components,
  that floor is the first release that understands them.
- **`uses`** lists the components your rule requires — see *Publishing a component* below.
  Declaring anything here also opts your rule out of the consumer's own flat `shared/`
  scripts, which is the isolation a published rule wants: your rule should not absorb whatever
  else happens to be sitting in someone's tree.

The full schema is [`schema/rule.schema.json`](./schema/rule.schema.json).

### `README.md`

Say what the rule generates, how to trigger it, and what the output looks like. Show the
generated code. If your rule requests any permission, explain why.

## Rules must declare everything they use

Your rule must work for whoever installs it. That used to mean "ship everything in one
directory", because there was no way to publish a library. There is now, so the rule is
stated as what it always meant:

- **Every `require()` must be declared.** A `require("acme/typesystem")` in any of your
  `.luau` files must name either a component listed in your `rule.json` `uses[]`, or one you
  ship yourself in `components/`. The validator reads your scripts and checks this.
- **`require()` takes a string literal.** `require(someVariable)` cannot be validated, so a
  rule whose dependencies cannot be enumerated cannot be published.
- **No `.env` file.** The loader reads per-rule dotenvs; a published one is a published
  secret.

`makeTypesystem` / `makeJSONCodegen` / `makeBytePackCodegen`, which you may have seen in
CodeXX's own tree, are first-party internals published nowhere. Depend on a published
component instead.

## Publishing a component

A **component** is a reusable Luau module. Publish one when two rules — yours or anyone's —
would otherwise carry the same helper twice.

```
components/codegen-component/acme/typesystem/
    component.json   required
    README.md        required
    LICENSE          required — cp ../../../../LICENSE .
    component.luau   required — the module; it must `return` its value
```

```json
{
  "schemaVersion": 1,
  "id": "acme.typesystem",
  "kind": "codegen-component",
  "vendor": "acme",
  "name": "typesystem",
  "version": "1.0.0",
  "description": "C++ type resolution helpers for codegen rules.",
  "authors": [{ "name": "Jane Doe", "github": "jdoe" }],
  "license": "MIT"
}
```

- **`name` is lowercase.** Two names differing only in case are one file on Windows and macOS
  and two on Linux, so a mixed-case name would resolve to a different module depending on who
  installed it.
- A rule reaches it as `require("acme/typesystem")` — the `.` in the identity becomes a `/`.
- Components may use other components (`uses[]` in `component.json`). The graph must be
  acyclic, and **a rule's `uses[]` must be closed**: if you use `acme.typesystem` and it uses
  `acme.base`, your rule lists both. That is what lets installation stay a flat download with
  no version solving — and it means a component adding a dependency is a breaking change for
  its consumers, surfaced here rather than on a user's machine.
- Anything a single rule needs and nobody else should depend on can go in that rule's own
  `components/` directory instead: flat `.luau` files, lowercase stems, not separately
  versioned.

Changes under any `components/` directory are labelled `Needs: Security Review`. A component
is code that runs inside every rule that requires it.

## Namespaced rules

Set `"namespaced": true` in `rule.json` and your rule installs to
`.codegen/rules/<vendor>/<Rule>/` and matches `[[<vendor>::<Rule>]]` rather than
`[[codegen::<Rule>]]`. Two vendors can then both publish a `ToString` and a consumer can use
both at once.

New rules should set it. It is opt-in because a rule published before namespacing existed
documents the flat attribute in its own README and sample header, and moving it would stop it
matching them.

Say the attribute in your README, including the compiler suppression it needs — an unknown
attribute namespace warns by default:

```cpp
// clang: -Wno-unknown-attributes
// gcc:   #pragma GCC diagnostic ignored_attributes "acme::"
struct [[acme::ToString]] Color { int r, g, b; };
```

## Validate before you open the PR

```sh
pip install pyyaml jsonschema
python3 scripts/validate_registry.py --write-index
```

That checks every gate CI checks and regenerates `index.json` — commit the result. A
green local run means a green PR.

## Updating a rule

Change the files and **bump `version` in `rule.json`**. CI rejects a content change
without a version increase, because the previous version is already tagged and immutable.

- patch — a fix that generates the same shape
- minor — new output, backward compatible for existing inputs
- major — existing inputs now generate different code

## Withdrawing a rule

Set `"yanked": true` in `rule.json` and open a PR. The rule stays in the tree and its
tags stay valid — anyone who pinned one keeps working — but it is flagged in the catalog.
Tags are never deleted.

## Permissions and review

`permissions.http.allowlist` and `permissions.env.os_allowlist` open real capability
escapes. They are allowed, never automatic: declaring one applies `Needs: Security
Review` and blocks merge until a maintainer signs off. Explain the need in your PR.

## Review

A maintainer reads the Luau. Expect questions about generated-code correctness, edge
cases (empty enums, nested namespaces, templates), and anything the sandbox lets you
reach. Rules land when they're right, not when CI is green.
