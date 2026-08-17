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
```

`<RuleName>` is also the C++ attribute token your rule matches (`[[codegen::ToString]]`),
so it is `PascalCase`, not kebab.

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
- **`requires[].range`** is `>=X.Y.Z` or `>=X.Y.Z <A.B.C`. No caret, no tilde.

The full schema is [`schema/rule.schema.json`](./schema/rule.schema.json).

### `README.md`

Say what the rule generates, how to trigger it, and what the output looks like. Show the
generated code. If your rule requests any permission, explain why.

## Rules must be self-contained

Everything your rule needs lives in its own directory. Two consequences worth stating,
because both are enforced:

- **No `shared/` libraries.** The `makeTypesystem` / `makeJSONCodegen` helpers you may
  have seen come from a Team-tier gated directory. A rule that calls them fails hard for
  most of its audience.
- **No `.env` file.** The loader reads per-rule dotenvs; a published one is a published
  secret.

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
