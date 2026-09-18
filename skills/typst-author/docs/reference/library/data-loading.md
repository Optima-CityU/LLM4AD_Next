# Data Loading

Data loading from external files.

These functions help you with loading and embedding data, for example
from the results of an experiment.

## Encoding

Some of the functions are also capable of encoding, e.g. `cbor.encode`.
They facilitate passing structured data to plugins.

However, each data format has its own native types. Therefore, for an
arbitrary Typst value, the encode-to-decode roundtrip might be lossy. In
general, numbers, strings, and arrays or dictionaries composed of them
can be reliably converted, while other types may fall back to strings
via `repr`, which is for debugging purposes only. Please refer to the
page of each data format for details.
