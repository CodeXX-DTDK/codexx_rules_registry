# ToString

Generates a `toString(E) -> std::string_view` function for each C++ enum you anchor.

Both scoped (`enum class`) and unscoped enums are handled — scoped enumerators get the
qualified `Namespace::Enum::` prefix, unscoped ones do not.

- **Vendor:** `codexx` · **Identity:** `codexx.to-string` · **Version:** 1.0.0
- **Output language:** C++
- **Triggered by:** anchor comment (an `enum` carries no attribute, so the rule cannot be
  selected by `[[codegen::ToString]]`)

## Use it

Copy the rule directory into your project's rules root:

```sh
cp -r rules/codegen/codexx/ToString /path/to/your/project/.codegen/rules/
```

Anchor each enum you want covered, in the header that declares it:

```cpp
namespace app
{

enum class Color { Red, Green, Blue };

// [[codegen::generated::ToString::app::Color]]

} // namespace app
```

Then run:

```sh
codegen -i include/app/color.hpp -a ToString
```

## What you get

The implementation is written to a sibling `.g.cpp`, and the forward declaration is
injected back into your header at the anchor site, wrapped in `:begin` / `:end` markers
that make the region safe to regenerate.

```cpp
// color.g.cpp
std::string_view toString(app::Color e)
{
    switch (e)
    {
        case app::Color::Red: return "Red";
        case app::Color::Green: return "Green";
        case app::Color::Blue: return "Blue";
        default: return "<unknown>";
    }
}
```

An enum with no enumerators produces no output rather than an empty function.

## Try it here

`input.hpp` in this directory is a runnable sample covering a scoped enum, an unscoped
enum, and an enum declared after its anchor. From the **registry root** (whose
`codexx.workspace.yaml` gives the daemon a workspace to serve):

```sh
codegen -r rules/codegen/codexx -a ToString \
        -i rules/codegen/codexx/ToString/input.hpp --dry-run
```

The sample deliberately anchors one enum (`app::Direction`) that is never declared, so
you should see a `W003 anchor references unknown entity (skipped)` — that is the rule
declining to invent code for something it cannot see, not a failure.

## Files

| File | Purpose |
|---|---|
| `rule.json` | registry manifest — identity, version, authors |
| `config.yaml` | output language; `autoDeduceIncludes: false` (the preamble supplies the one include) |
| `transform.luau` | builds the switch and the declaration |
| `preamble.luau` | emits `#include <string_view>` |
| `input.hpp` | sample input |

## License

MIT — see [`LICENSE`](./LICENSE). You may edit this rule; see the registry
[README](../../../../README.md#editing-a-rule) for what that means for licensing.
