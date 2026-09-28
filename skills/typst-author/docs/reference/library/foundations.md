# Foundations

Foundational types and functions.

Here, you'll find documentation for basic data types like integers and
strings as well as details about core computational functions.

## Calculation

Module for calculations and processing of numeric values.

These definitions are part of the `calc` module and not imported by
default. In addition to the functions listed below, the `calc` module
also defines the constants `pi`, `tau`, `e`, and `inf`.

## Standard Library

A module that contains all globally accessible items.

### Using "shadowed" definitions

The `std` module is useful whenever you overrode a name from the global
scope (this is called *shadowing*). For instance, you might have used
the name `text` for a parameter. To still access the `text` element,
write `std.text`.

```typ
#set page(margin: (left: 3em))
#let par = [My special paragraph.]
#let special(text) = {
  set std.text(style: "italic")
  set std.par.line(numbering: "1")
  text
}

#special(par)

#lorem(10)
```

### Conditional access

You can also use this in combination with the dictionary constructor to
conditionally access global definitions. This can, for instance, be
useful to use new or experimental functionality when it is available,
while falling back to an alternative implementation if used on an older
Typst version. In particular, this allows us to create
[polyfills](https://en.wikipedia.org/wiki/Polyfill_(programming)).

This can be as simple as creating an alias to prevent warning messages,
for example, conditionally using `pattern` in Typst version 0.12, but
using `tiling` in newer versions. Since the parameters accepted by the
`tiling` function match those of the older `pattern` function, using the
`tiling` function when available and falling back to `pattern` otherwise
will unify the usage across all versions. Note that, when creating a
polyfill, `sys.version` can also be very useful.

```typ
#let tiling = if "tiling" in std { tiling } else { pattern }

...
```

## System

Module for system interactions.

This module defines the following items:

- The `sys.version` constant (of type `version`) that specifies the
  currently active Typst compiler version.

- The `sys.inputs` dictionary, which makes external inputs available to
  the project. An input specified in the command line as
  `--input key=value` becomes available under `sys.inputs.key` as
  `{"value"}`. To include spaces in the value, it may be enclosed with
  single or double quotes.

  The value is always of type string. More complex data may be parsed
  manually using functions like `json`.
