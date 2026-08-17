#pragma once

// Example input for the ToString rule.
//
// Demonstrates inline code generation (ADR-017): the ToString rule writes
// toString() implementations to input.g.cpp AND injects the forward
// declarations back into this header at each anchor site.
//
// Run with:
//   codegen -i codegen/examples/ToString/input.hpp \
//           -a ToString                              \
//           -r codegen/examples
//
// Expected output after first run:
//   input.g.cpp  - three toString() implementations
//   This file    - each anchor expanded to a guarded forward declaration

#include <string_view>

namespace app
{

enum class Color
{
    Red,
    Green,
    Blue
};

// [[codegen::generated::ToString::app::Color:begin]]
std::string_view toString(app::Color e);
// [[codegen::generated::ToString::app::Color:end]]

// [[codegen::generated::ToString::app::Direction:begin]]
std::string_view toString(app::Direction e);
// [[codegen::generated::ToString::app::Direction:end]]

// [[codegen::generated::ToString::app::Status:begin]]
std::string_view toString(app::Status e);
// [[codegen::generated::ToString::app::Status:end]]

enum Status
{
    Active,
    Inactive,
    Pending
}; // unscoped - no Color:: prefix

// [[codegen::generated::ToString::app::Status]]

} // namespace app
