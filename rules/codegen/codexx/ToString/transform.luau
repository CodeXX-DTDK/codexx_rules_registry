-- ToString.luau
-- Generates a toString(E) -> std::string_view function for each enum targeted by an anchor.
-- Triggered via anchor comment only (EnumNode has no attribute field):
--   // [[codegen::generated::ToString::qualified::EnumName]]
--
-- source  -> toString implementation, written to input.g.cpp
-- inline  -> toString declaration, injected at the anchor site in the source header

return function(input)
    local node = json.decode(input)
    if not node then error("failed to decode input") end

    local enumName = node.identifier and node.identifier.name
    if not enumName or enumName == "" then error("enum has no name") end

    local namespaces = node._namespaces or {}
    local nsPrefix   = table.concat(namespaces, "::")
    local qualName   = nsPrefix ~= "" and (nsPrefix .. "::" .. enumName) or enumName
    local isScoped   = node.isScoped or false

    -- Collect enumerator names from the enumerators array
    local enumerators = {}
    for _, e in ipairs(node.enumerators or {}) do
        if e.name and e.name ~= "" then
            table.insert(enumerators, e.name)
        end
    end

    if #enumerators == 0 then
        return json.encode({ source = "", inline = {} })
    end

    -- Scoped enum values need the qualified prefix (Color::Red), unscoped do not
    local valuePrefix = isScoped and (qualName .. "::") or ""

    -- Build switch cases
    local cases = {}
    for _, name in ipairs(enumerators) do
        table.insert(cases,
            '        case ' .. valuePrefix .. name .. ': return "' .. name .. '";')
    end

    -- Implementation — goes to .g.cpp
    local impl = table.concat({
        "std::string_view toString(" .. qualName .. " e)",
        "{",
        "    switch (e)",
        "    {",
        table.concat(cases, "\n"),
        '        default: return "<unknown>";',
        "    }",
        "}"
    }, "\n")

    -- Declaration — injected inline at the anchor site
    local decl = "std::string_view toString(" .. qualName .. " e);"

    return json.encode({
        source = impl,
        inline = { { source = decl } }
    })
end
