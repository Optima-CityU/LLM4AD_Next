import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('profile', Path(__file__).parents[1] / 'export_ndk_profile.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Profile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='ndk-profile-unit-')
        self.root = Path(self.tmp.name).resolve() / 'original'
        self.root.mkdir()
        for rel in m.FILES:
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b'NOT REAL ANDROID TOOL OR LIBRARY\n')
        (self.root / 'source.properties').write_text('Pkg.Desc = Android NDK\nPkg.Revision = 28.2.13676358\nPkg.ReleaseName = r28c\n')
        for rel, links in m.ALIASES.items():
            for name, target in reversed(links):
                p = self.root / Path(rel).parent / name
                p.parent.mkdir(parents=True, exist_ok=True)
                if not p.exists() and not p.is_symlink():
                    p.symlink_to(target)
            target = self.root / Path(rel).parent / links[-1][1]
            target.write_bytes(b'NOT REAL CODEGEN\n')
        for tree in m.TREES:
            p = self.root / tree
            p.mkdir(parents=True)
            (p / 'fixture.h').write_bytes(b'synthetic data\n')
        self.out = Path(self.tmp.name).resolve() / 'export'

    def tearDown(self):
        self.tmp.cleanup()

    def rehash_receipt(self):
        p = self.out / 'receipt.json'
        v = json.loads(p.read_bytes())
        v['manifest_sha256'] = hashlib.sha256((self.out / 'manifest.json').read_bytes()).hexdigest()
        p.write_text(json.dumps(v))

    def run_export(self):
        return m.export(self.root, self.out, 30, True)

    def test_synthetic_exact_profile_and_unmodified_source(self):
        r = self.run_export()
        self.assertEqual(m.verify(self.out), r)
        self.assertEqual(r['source_origin'], 'synthetic_fixture')
        self.assertFalse(any(r['proof_flags'].values()))
        self.assertTrue((self.root / m.TC / 'bin/clang++').is_symlink())
        self.assertFalse((self.out / 'sdk' / m.TC / 'bin/clang++').is_symlink())

    def test_known_alias_escape_or_unexpected_text(self):
        p = self.root / m.TC / 'bin/clang++'
        p.unlink()
        p.symlink_to('/bin/sh')
        with self.assertRaises(ValueError):
            self.run_export()
        self.assertTrue((self.out / 'failure.json').exists())
        self.assertFalse((self.out / 'receipt.json').exists())

    def test_header_symlink_and_special_file_refused(self):
        for special in ('link', 'fifo'):
            with self.subTest(special=special):
                p = self.root / m.TREES[0] / 'bad'
                if special == 'link':
                    p.symlink_to('/etc/passwd')
                else:
                    os.mkfifo(p)
                with self.assertRaises(ValueError):
                    m.export(self.root, Path(self.tmp.name).resolve() / ('export-' + special), 30, True)
                p.unlink()

    def test_existing_destination_never_covered(self):
        self.out.mkdir()
        marker = self.out / 'marker'
        marker.write_bytes(b'keep')
        with self.assertRaises(FileExistsError):
            self.run_export()
        self.assertEqual(marker.read_bytes(), b'keep')

    def test_source_revision_refused(self):
        (self.root / 'source.properties').write_text('Pkg.Revision = unknown\n')
        with self.assertRaises(ValueError):
            self.run_export()

    def test_byte_and_entry_budgets_persist_negative(self):
        for name, cap in [('LIMIT_BYTES', 32), ('LIMIT_ENTRIES', 4)]:
            out = Path(self.tmp.name).resolve() / name
            with patch.object(m, name, cap), self.assertRaises(ValueError):
                m.export(self.root, out, 30, True)
            self.assertFalse((out / 'receipt.json').exists())
            self.assertTrue((out / 'failure.json').exists())

    def test_unpublished_manifest_is_not_accepted(self):
        self.run_export()
        (self.out / 'receipt.json').unlink()
        with self.assertRaises(ValueError):
            m.verify(self.out)

    def test_current_tool_mutation_refused(self):
        self.run_export()
        p = self.out / 'sdk' / m.TC / 'bin/clang++'
        p.chmod(0o600)
        p.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            m.verify(self.out)

    def test_manifest_source_alias_claim_contradiction(self):
        self.run_export()
        p = self.out / 'manifest.json'
        data = json.loads(p.read_bytes())
        data['files'][0]['source_sha256'] = '0' * 64
        p.write_text(json.dumps(data))
        self.rehash_receipt()
        with self.assertRaises(ValueError):
            m.verify(self.out)

    def test_sdk_root_link_refused(self):
        self.run_export()
        sdk = self.out / 'sdk'
        sdk.rename(self.out / 'old-sdk')
        sdk.symlink_to(self.out / 'old-sdk')
        with self.assertRaises(ValueError):
            m.verify(self.out)

    def test_mutation_after_snapshot_persists_incomplete(self):
        original = m.Source.unchanged
        def mutate(source):
            (self.root / 'NOTICE').write_bytes(b'changed after read')
            original(source)
        with patch.object(m.Source, 'unchanged', mutate), self.assertRaises(ValueError):
            self.run_export()
        self.assertFalse((self.out / 'receipt.json').exists())
        self.assertFalse((self.out / 'manifest.json').exists())

    def test_numeric_false_and_extra_output_not_accepted(self):
        self.run_export()
        p = self.out / 'manifest.json'
        data = json.loads(p.read_bytes())
        data['proof_flags']['gpu'] = 0
        p.write_text(json.dumps(data))
        self.rehash_receipt()
        with self.assertRaises(ValueError):
            m.verify(self.out)


    def test_rehashed_unknown_manifest_field_refused(self):
        self.run_export()
        p = self.out / 'manifest.json'
        v = json.loads(p.read_bytes())
        v['toolchain_authenticated'] = True
        p.write_text(json.dumps(v))
        self.rehash_receipt()
        with self.assertRaises(ValueError):
            m.verify(self.out)

    def test_actual_extra_output_refused(self):
        self.run_export()
        (self.out / 'sdk/unknown-tool').write_bytes(b'not in closure')
        with self.assertRaises(ValueError):
            m.verify(self.out)

    def test_whole_deadline_blocks_publication(self):
        with patch('time.monotonic', side_effect=[0, 31]), self.assertRaises(ValueError):
            self.run_export()
        self.assertFalse((self.out / 'receipt.json').exists())
        self.assertTrue((self.out / 'failure.json').exists())


    def test_oversized_receipt_is_bounded_before_read(self):
        self.run_export()
        p = self.out / 'receipt.json'
        with p.open('wb') as f:
            f.truncate(m.LIMIT_METADATA + 1)
        with self.assertRaises(ValueError):
            m.verify(self.out)


    def test_negative_marker_contradiction_refused(self):
        self.run_export()
        (self.out / 'failure.json').write_text('{"complete":false}')
        with self.assertRaises(ValueError):
            m.verify(self.out)


    def test_inventory_stream_refuses_4097_entries(self):
        root = Path(self.tmp.name).resolve() / 'many'
        root.mkdir()
        for n in range(m.LIMIT_ENTRIES + 1):
            (root / str(n)).touch(mode=0o400)
        with self.assertRaisesRegex(ValueError, 'entry limit during enumeration'):
            m.inventory(root)

    def test_inventory_block_budget_checked(self):
        self.run_export()
        def stop():
            raise ValueError('deadline test')
        with self.assertRaisesRegex(ValueError, 'deadline test'):
            m.inventory(self.out / 'sdk', stop)

    def test_source_ancestor_symlink_refused(self):
        link = Path(self.tmp.name).resolve() / 'alias-parent'
        link.symlink_to(self.root.parent, target_is_directory=True)
        with self.assertRaises(OSError):
            m.export(link / 'original', self.out, 30, True)

    def test_current_permissions_binding(self):
        self.run_export()
        (self.out / 'sdk/NOTICE').chmod(0o600)
        with self.assertRaisesRegex(ValueError, 'read-only'):
            m.verify(self.out)


if __name__ == '__main__':
    unittest.main()
