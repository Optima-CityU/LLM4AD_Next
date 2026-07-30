# ABC binary

The evaluator needs a Berkeley ABC binary at `abc/abc` (alongside the `abc.rc`
in this directory). The binary is ~166MB and is **not** checked into git
(it exceeds GitHub's 100MB single-file limit; see the repo `.gitignore`).

Provide it in one of two ways:

## Option A — copy an existing build

```bash
cp /path/to/your/abc examples/applications/abc_pareto_evolve/abc/abc
chmod +x examples/applications/abc_pareto_evolve/abc/abc
```

## Option B — build from source

```bash
git clone https://github.com/berkeley-abc/abc.git /tmp/abc-src
cd /tmp/abc-src && make -j$(nproc)
cp abc /path/to/abc_pareto_evolve/abc/abc
```

`abc.rc` (kept in this directory) defines the script aliases the portfolios use
(`resyn2`, `resyn3`, `compress2`, ...). The evaluator `source`s it before every
run, so it must sit next to the binary. Do not delete it.

To verify:

```bash
./abc/abc -c "source abc/abc.rc; version"
```
