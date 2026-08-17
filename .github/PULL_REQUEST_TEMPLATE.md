<!-- Publishing or updating a rule? Keep the checklist. Anything else (docs, CI,
     tooling), delete it and just describe the change. -->

## Summary

<!-- What does this rule generate, and what problem does it solve? -->

## Rule

- **Identity:** `<vendor>.<rule-name>`
- **Version:** `<x.y.z>`
- **New rule / update:** <!-- pick one -->

## Checklist

- [ ] Directory is `rules/codegen/<vendor>/<RuleName>/`
- [ ] `rule.json`, `README.md`, `LICENSE`, `config.yaml`, `transform.luau`, `preamble.luau` all present
- [ ] `LICENSE` is a verbatim copy of the repo root `LICENSE` (`cp LICENSE <rule dir>/`)
- [ ] `README.md` shows how to trigger the rule and what it generates
- [ ] `version` bumped (updates only — CI rejects a content change without one)
- [ ] `python3 scripts/validate_registry.py --write-index` passes, and `index.json` is committed
- [ ] Rule is self-contained — no `shared/` helpers, no `.env`
- [ ] I license this contribution under this repository's MIT license

## Generated output

<!-- Paste a short before/after: the annotated C++ in, the generated code out.
     This is the fastest way for a reviewer to see whether the rule is right. -->

```cpp

```

## Permissions

<!-- Does config.yaml declare permissions.http.allowlist or permissions.env.os_allowlist?
     If so, say which hosts / variables and why the rule cannot work without them.
     This triggers a security review before merge. If not, write "none". -->

none

## Notes

<!-- Edge cases you handled or deliberately didn't, prior art, anything a reviewer
     should know. Optional. -->
