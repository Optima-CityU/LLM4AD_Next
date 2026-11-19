#!/usr/bin/env python3
"""Export one bounded r28c/Darwin/aarch64/API30 compiler profile; no device calls."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time

PROFILE = 'ndk-r28c-darwin-arm64-api30-v1'
TC = 'toolchains/llvm/prebuilt/darwin-x86_64/'
LIMIT_BYTES = 512 * 1024 * 1024
LIMIT_ENTRIES = 4096
LIMIT_METADATA = 2 * 1024 * 1024
FILES = ['source.properties', 'NOTICE', 'NOTICE.toolchain',
         TC + 'bin/aarch64-linux-android30-clang++', TC + 'bin/llvm-nm',
         TC + 'lib/libc++.dylib', TC + 'lib/libc++abi.dylib', TC + 'lib/libxml2.2.dylib',
         TC + 'lib/clang/19/lib/linux/libclang_rt.builtins-aarch64-android.a',
         'shader-tools/darwin-x86_64/glslc']
TREES = [TC + 'sysroot/usr/include', TC + 'sysroot/usr/lib/aarch64-linux-android',
         TC + 'lib/clang/19/include', TC + 'lib/clang/19/lib/linux/aarch64']
ALIASES = {TC + 'bin/clang++': [('clang++', 'clang'), ('clang', 'clang-19')],
           TC + 'bin/ld.lld': [('ld.lld', 'lld')],
           TC + 'bin/llvm-readelf': [('llvm-readelf', 'llvm-readobj')]}


def signature(s):
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_mode)


def encode(v):
    return (json.dumps(v, sort_keys=True, separators=(',', ':')) + '\n').encode()


def atomic(path, value):
    data = encode(value)
    if len(data) > LIMIT_METADATA:
        raise ValueError('metadata exceeds limit')
    tmp = path.with_suffix('.pending')
    with tmp.open('xb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.rename(tmp, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def directory_fd(path):
    absolute = Path(path).absolute()
    if '..' in absolute.parts:
        raise ValueError('parent traversal refused')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in absolute.parts[1:]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


class Source:
    def __init__(self, root, seconds):
        if root.is_symlink():
            raise ValueError('source root symlink refused')
        self.root = root.absolute()
        self.fd = directory_fd(self.root)
        self.deadline = time.monotonic() + seconds
        self.observed = {}
        self.directories = {}
        self.entries_seen = set()

    def close(self):
        os.close(self.fd)

    def budget(self):
        if time.monotonic() > self.deadline:
            raise ValueError('whole export deadline exceeded')

    def parent(self, rel):
        parts = Path(rel).parts
        if not parts or len(parts) > 16 or any(x in ('..', '.') for x in parts) or Path(rel).is_absolute():
            raise ValueError('unsafe source path/depth')
        fd = os.dup(self.fd)
        try:
            for part in parts[:-1]:
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = nxt
            return fd, parts[-1]
        except BaseException:
            os.close(fd)
            raise

    def open_file(self, rel):
        parent, name = self.parent(rel)
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        finally:
            os.close(parent)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise ValueError('source file must be regular')
        return fd

    def alias(self, rel):
        chain = []
        parent, unused = self.parent(rel)
        try:
            for name, expected in ALIASES[rel]:
                s = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if not stat.S_ISLNK(s.st_mode) or os.readlink(name, dir_fd=parent) != expected:
                    raise ValueError('unknown alias chain: ' + rel)
                path = str(Path(rel).parent / name)
                self.observed[path] = signature(s)
                chain.append({'path': path, 'link_text': expected})
            target = str(Path(rel).parent / chain[-1]['link_text'])
        finally:
            os.close(parent)
        return target, chain

    def walk(self, rel):
        self.budget()
        parent, name = self.parent(rel)
        try:
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        finally:
            os.close(parent)
        try:
            names = []
            with os.scandir(fd) as entries:
                for entry in entries:
                    self.budget()
                    child = str(Path(rel) / entry.name)
                    self.entries_seen.add(child)
                    if len(self.entries_seen) > LIMIT_ENTRIES:
                        raise ValueError('selected entry count exceeds limit during enumeration')
                    names.append(entry.name)
            names.sort()
            self.directories[rel] = names
            self.observed[rel] = signature(os.fstat(fd))
            files = []
            for name in names:
                child = str(Path(rel) / name)
                s = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISDIR(s.st_mode):
                    files += self.walk(child)
                elif stat.S_ISREG(s.st_mode):
                    files.append(child)
                else:
                    raise ValueError('symlink/special entry in selected tree: ' + child)
                if len(files) + len(self.directories) > LIMIT_ENTRIES:
                    raise ValueError('selected entry count exceeds limit')
            return files
        finally:
            os.close(fd)

    def read(self, rel, dest=None, total=0):
        self.budget()
        fd = self.open_file(rel)
        try:
            before = os.fstat(fd)
            if before.st_size > LIMIT_BYTES or total + before.st_size > LIMIT_BYTES:
                raise ValueError('profile bytes exceed limit')
            h = hashlib.sha256()
            count = 0
            f = dest.open('xb') if dest else None
            try:
                while True:
                    self.budget()
                    b = os.read(fd, 1024 * 1024)
                    if not b:
                        break
                    count += len(b)
                    if count > before.st_size or total + count > LIMIT_BYTES:
                        raise ValueError('source grew or profile bytes exceeded')
                    h.update(b)
                    if f:
                        f.write(b)
                if f:
                    f.flush()
                    os.fsync(f.fileno())
            finally:
                if f:
                    f.close()
            if count != before.st_size or signature(before) != signature(os.fstat(fd)):
                raise ValueError('source mutated during read: ' + rel)
            self.observed[rel] = signature(before)
            return {'bytes': count, 'sha256': h.hexdigest(),
                    'executable': bool(before.st_mode & 0o111)}
        finally:
            os.close(fd)

    def unchanged(self):
        for rel, expected in self.observed.items():
            self.budget()
            parent, name = self.parent(rel)
            try:
                if signature(os.stat(name, dir_fd=parent, follow_symlinks=False)) != expected:
                    raise ValueError('source changed: ' + rel)
                if rel in self.directories:
                    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                    try:
                        names = []
                        with os.scandir(fd) as entries:
                            for entry in entries:
                                self.budget()
                                if len(names) >= LIMIT_ENTRIES:
                                    raise ValueError('source directory entry limit')
                                names.append(entry.name)
                        if sorted(names) != self.directories[rel]:
                            raise ValueError('source directory changed: ' + rel)
                    finally:
                        os.close(fd)
            finally:
                os.close(parent)


def inventory(root, budget=None, include_count=False):
    rows = []
    total = 0
    entries_seen = 0
    root_fd = directory_fd(root)
    def tick():
        if budget:
            budget()
    def visit(fd, rel=''):
        nonlocal total, entries_seen
        names = []
        with os.scandir(fd) as entries:
            for entry in entries:
                tick()
                entries_seen += 1
                if entries_seen > LIMIT_ENTRIES:
                    raise ValueError('exported entry limit during enumeration')
                names.append(entry.name)
        for name in sorted(names):
            tick()
            path = str(Path(rel) / name)
            if len(Path(path).parts) > 16:
                raise ValueError('exported path depth')
            s = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(s.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    visit(child, path)
                finally:
                    os.close(child)
                continue
            if not stat.S_ISREG(s.st_mode):
                raise ValueError('nonregular exported file')
            child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            try:
                before = os.fstat(child)
                if not stat.S_ISREG(before.st_mode) or signature(before) != signature(s) or total + before.st_size > LIMIT_BYTES:
                    raise ValueError('exported file type/size changed or exceeds budget')
                h = hashlib.sha256()
                count = 0
                while True:
                    tick()
                    b = os.read(child, 1024 * 1024)
                    if not b:
                        break
                    count += len(b)
                    if count > before.st_size:
                        raise ValueError('exported file grew')
                    h.update(b)
                if count != before.st_size or signature(before) != signature(os.fstat(child)):
                    raise ValueError('exported file changed during read')
            finally:
                os.close(child)
            executable = bool(s.st_mode & 0o111)
            if stat.S_IMODE(s.st_mode) != (0o500 if executable else 0o400):
                raise ValueError('exported file is not frozen read-only')
            total += s.st_size
            rows.append({'path': path, 'bytes': s.st_size, 'sha256': h.hexdigest(), 'executable': executable})
    try:
        visit(root_fd)
    finally:
        os.close(root_fd)
    rows.sort(key=lambda x: x['path'])
    return (rows, entries_seen) if include_count else rows


def bounded_metadata(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > LIMIT_METADATA:
            raise ValueError('metadata type/size invalid')
        parts = []
        count = 0
        while True:
            b = os.read(fd, min(65536, LIMIT_METADATA + 1 - count))
            if not b:
                break
            count += len(b)
            if count > LIMIT_METADATA:
                raise ValueError('metadata grew beyond bound')
            parts.append(b)
        if count != before.st_size or signature(before) != signature(os.fstat(fd)):
            raise ValueError('metadata changed during bounded read')
        return b''.join(parts)
    finally:
        os.close(fd)


def verify(out, require_receipt=True, budget=None):
    if out.is_symlink() or (out / 'manifest.json').is_symlink():
        raise ValueError('profile/manifest symlink')
    expected_outer = {'sdk', 'manifest.json', 'receipt.json'} if require_receipt else {'sdk', 'manifest.json'}
    if set(os.listdir(out)) != expected_outer:
        raise ValueError('unexpected/partial/negative profile publication files')
    data = bounded_metadata(out / 'manifest.json')
    if len(data) > LIMIT_METADATA:
        raise ValueError('manifest too large')
    m = json.loads(data)
    if m.get('schema') != 'phonefarm/ndk_profile@1' or m.get('profile') != PROFILE or m.get('complete') is not True:
        raise ValueError('unknown/incomplete profile')
    if any(type(v) is not bool for v in m.get('proof_flags', {}).values()):
        raise ValueError('proof flag type')
    if m.get('source_origin') not in ('caller_supplied_unverified', 'synthetic_fixture') or m.get('proof_flags') != proof_flags():
        raise ValueError('unsupported provenance/proof')
    if require_receipt:
        receipt_path = out / 'receipt.json'
        if receipt_path.is_symlink():
            raise ValueError('receipt symlink')
        receipt = json.loads(bounded_metadata(receipt_path))
        if receipt != {'complete': True, 'profile': PROFILE, 'sdk_path': str((out / 'sdk').resolve()),
                       'manifest_sha256': hashlib.sha256(data).hexdigest(), 'proof_flags': proof_flags()}:
            raise ValueError('publication receipt mismatch')
    if set(m) != {'schema','profile','complete','source_root','package_revision','source_properties_sha256','source_origin','files','entries','regular_bytes','inventory_sha256','proof_flags','host_dependencies'}:
        raise ValueError('unknown manifest fields')
    expected = m['files']
    if not isinstance(expected, list) or len(expected) > LIMIT_ENTRIES:
        raise ValueError('bad file ledger')
    actual, entries = inventory(out / 'sdk', budget, include_count=True)
    reduced = [{k: r[k] for k in ('path', 'bytes', 'sha256', 'executable')} for r in expected]
    if actual != reduced:
        raise ValueError('profile current bytes/file set mismatch')
    if entries > LIMIT_ENTRIES or m.get('entries') != entries or m.get('regular_bytes') != sum(r['bytes'] for r in actual):
        raise ValueError('entry/byte ledger mismatch')
    required = sorted(FILES + list(ALIASES))
    known = set(required)
    for row in expected:
        if set(row) != {'path','bytes','sha256','executable','source_path','source_bytes','source_sha256','alias_chain'} or type(row['executable']) is not bool:
            raise ValueError('unknown file ledger fields/types')
        rel = row['path']
        if rel not in known and not any(rel.startswith(tree + '/') for tree in TREES):
            raise ValueError('unexpected profile input')
        if row.get('source_path') != (str(Path(rel).parent / ALIASES[rel][-1][1]) if rel in ALIASES else rel):
            raise ValueError('source path binding mismatch')
        if row.get('alias_chain') != ([{'path': str(Path(rel).parent / n), 'link_text': t} for n, t in ALIASES[rel]] if rel in ALIASES else []):
            raise ValueError('alias provenance contradiction')
        if row.get('source_sha256') != row['sha256'] or row.get('source_bytes') != row['bytes']:
            raise ValueError('source/output bytes contradict')
    if not set(required).issubset(r['path'] for r in expected):
        raise ValueError('missing required input')
    if m.get('inventory_sha256') != hashlib.sha256(encode(actual)).hexdigest():
        raise ValueError('inventory hash mismatch')
    props = next(row for row in expected if row['path'] == 'source.properties')
    if m.get('source_properties_sha256') != props['sha256']:
        raise ValueError('source.properties identity contradiction')
    properties = out / 'sdk/source.properties'
    if properties.stat().st_size > 65536:
        raise ValueError('source.properties exceeds limit')
    parsed = dict(line.split(' = ', 1) for line in bounded_metadata(properties).decode().splitlines() if ' = ' in line)
    if parsed.get('Pkg.Revision') != '28.2.13676358' or parsed.get('Pkg.ReleaseName') != 'r28c' or parsed.get('Pkg.Desc') != 'Android NDK':
        raise ValueError('current source.properties contradicts profile revision')
    if m.get('package_revision') != '28.2.13676358':
        raise ValueError('wrong package revision')
    return m


def proof_flags():
    return dict.fromkeys(('gpu', 'quality', 'semantic', 'device', 'real_game', 'deployable', 'promotion'), False)


def export(root, out, seconds, synthetic=False):
    out.mkdir(mode=0o700, parents=False, exist_ok=False)
    source = None
    try:
        source = Source(root, seconds)
        fd = source.open_file('source.properties')
        try:
            if os.fstat(fd).st_size > 65536:
                raise ValueError('source.properties exceeds limit')
        finally:
            os.close(fd)
        props = source.read('source.properties')
        fd = source.open_file('source.properties')
        try:
            text = os.read(fd, 65536).decode('utf-8')
        finally:
            os.close(fd)
        if hashlib.sha256(text.encode()).hexdigest() != props['sha256']:
            raise ValueError('source.properties changed between bounded reads')
        parsed = dict(line.split(' = ', 1) for line in text.splitlines() if ' = ' in line)
        if parsed.get('Pkg.Revision') != '28.2.13676358' or parsed.get('Pkg.ReleaseName') != 'r28c' or parsed.get('Pkg.Desc') != 'Android NDK':
            raise ValueError('unsupported NDK package identity')
        paths = list(FILES) + list(ALIASES)
        for tree in TREES:
            paths += source.walk(tree)
        paths = sorted(set(paths))
        sdk = out / 'sdk'
        sdk.mkdir(mode=0o700)
        rows = []
        total = 0
        for rel in paths:
            origin, chain = source.alias(rel) if rel in ALIASES else (rel, [])
            dest = sdk / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            r = source.read(origin, dest, total)
            total += r['bytes']
            dest.chmod(0o500 if r['executable'] else 0o400)
            rows.append(dict(r, path=rel, source_path=origin, source_bytes=r['bytes'],
                             source_sha256=r['sha256'], alias_chain=chain))
        source.unchanged()
        actual, entries = inventory(sdk, source.budget, include_count=True)
        source.budget()
        m = {'schema': 'phonefarm/ndk_profile@1', 'profile': PROFILE, 'complete': True,
             'source_root': str(source.root), 'package_revision': parsed['Pkg.Revision'],
             'source_properties_sha256': props['sha256'], 'source_origin': 'synthetic_fixture' if synthetic else 'caller_supplied_unverified',
             'files': rows, 'entries': entries, 'regular_bytes': total,
             'inventory_sha256': hashlib.sha256(encode(actual)).hexdigest(),
             'proof_flags': proof_flags(), 'host_dependencies': 'system_libSystem_libz_shared_cache_shell_OS_unfrozen'}
        atomic(out / 'manifest.json', m)
        verify(out, require_receipt=False, budget=source.budget)
        source.budget()
        atomic(out / 'receipt.json', {'complete': True, 'profile': PROFILE, 'sdk_path': str(sdk.resolve()),
                                    'manifest_sha256': hashlib.sha256((out / 'manifest.json').read_bytes()).hexdigest(),
                                    'proof_flags': proof_flags()})
        source.budget()
        return m
    except BaseException as e:
        # The valid publication marker is the receipt; an interrupted export is never retried implicitly.
        for name in ('manifest.json', 'receipt.json'):
            (out / name).unlink(missing_ok=True)
        atomic(out / 'failure.json', {'complete': False, 'error': str(e), 'profile': PROFILE, 'proof_flags': proof_flags()})
        raise
    finally:
        if source:
            source.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    run = sub.add_parser('export')
    run.add_argument('--ndk', type=Path, required=True)
    run.add_argument('--out', type=Path, required=True)
    run.add_argument('--wall-seconds', type=int, default=120)
    run.add_argument('--synthetic', action='store_true')
    check = sub.add_parser('verify')
    check.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    try:
        if a.command == 'export':
            if not 1 <= a.wall_seconds <= 600:
                raise ValueError('wall seconds must be1..600')
            m = export(a.ndk, a.out, a.wall_seconds, a.synthetic)
        else:
            m = verify(a.out)
        print(json.dumps({'complete': True, 'profile': m['profile'], 'source_origin': m['source_origin'],
                          'entries': m['entries'], 'bytes': m['regular_bytes'], 'proof_flags': m['proof_flags']}))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
        print(json.dumps({'complete': False, 'error': str(e), 'proof_flags': proof_flags()}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
