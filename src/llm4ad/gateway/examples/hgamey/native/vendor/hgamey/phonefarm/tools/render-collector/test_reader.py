#!/usr/bin/env python3
"""Actual fixed C reader host countercases; no Android/GPU qualification."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

class Reader(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='pf-native-reader-');cls.root=Path(cls.tmp.name).resolve();cls.bin=cls.root/'reader'
        source=Path(__file__).parent/'reader.c'
        subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',str(source),'-o',str(cls.bin)],check=True,timeout=10,capture_output=True)
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def triple(self,op,name,arg):return b'\0'.join(x.encode() if isinstance(x,str) else x for x in [op,name,arg])+b'\0'
    def request(self,rows):return b''.join(self.triple(*r) for r in rows)+self.triple('end','','')
    def run_reader(self,raw,**kw):return subprocess.run([str(self.bin)],input=raw,capture_output=not kw,timeout=5,**kw)
    def put(self,b):
        p=self.root/'record';p.write_bytes(b);return str(p)
    def blocked(self,raw):
        r=self.run_reader(raw);self.assertNotEqual(r.returncode,0);self.assertNotIn(b'#end\n',r.stdout);return r
    def test_bulk_570_fields_byte_exact(self):
        raw=b'Name:\tgame\\literal\nThreads:\t190\nUTF8:\t\xe4\xb8\xad\n';p=self.put(raw)
        r=self.run_reader(self.request([('block','record',p)]*570));self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(r.stdout,b'#phonefarm_render_witness v2\n'+(b'BEGIN\trecord\n'+raw+b'\nEND\trecord\n')*570+b'#end\n')
    def test_record_empty_partial_nul_and_exact_size(self):
        for raw in [b'',b'no LF',b'a\0b\n',b'x'*65536+b'\n']:
            self.blocked(self.request([('block','record',self.put(raw))]))
        self.assertEqual(self.run_reader(self.request([('block','record',self.put(b'x'*65535+b'\n'))])).returncode,0)
    def test_missing_symlink_fifo_directory_and_relative(self):
        for path in [self.root/'absent',self.root]:self.blocked(self.request([('block','record',str(path))]))
        target=Path(self.put(b'ok\n'));link=self.root/'link';link.symlink_to(target);self.blocked(self.request([('block','record',str(link))]))
        fifo=self.root/'fifo';os.mkfifo(fifo);self.blocked(self.request([('block','record',str(fifo))]))
        self.blocked(self.request([('block','record','relative')]))
    def test_framing_missing_end_partial_trailing_and_unknown(self):
        for raw in [b'',b'value\0name\0missingnul',self.triple('value','name','ok'),self.request([])+b'extra',self.request([('unknown','name','x')]),self.request([('value','name with space','x')])]:self.blocked(raw)
    def test_request_count_input_and_output_caps(self):
        self.blocked(self.request([('value','name','x')]*2048)) # plus mandatory end exceeds2048
        self.blocked(b'x'*(2*1024*1024+1))
        p=self.put(b'x'*65535+b'\n');self.blocked(self.request([('block','record',p)]*33))
    def test_pid_filter_and_cmdline_conversion(self):
        for raw,expected in [(b'',b'EMPTY'),(b'\n\n',b'EMPTY'),(b'123\n456\n\n',b'EMPTY123\n456')]:
            r=self.run_reader(self.request([('filter','filter',self.put(raw))]));self.assertEqual(r.returncode,0,r.stderr);self.assertIn(expected+b'\nEND\tfilter\n',r.stdout)
        self.blocked(self.request([('filter','filter',self.put(b'123'))]))
        r=self.run_reader(self.request([('cmdline','cmdline',self.put(b'com.example.game\0arg\0'))]));self.assertEqual(r.returncode,0,r.stderr);self.assertIn(b'com.example.game\narg\nEND',r.stdout)
    def test_output_flush_failure(self):
        p=self.root/'readonly-output';p.write_bytes(b'untouched');fd=os.open(p,os.O_RDONLY)
        try:r=subprocess.run([str(self.bin)],input=self.request([('value','name','ok')]),stdout=fd,stderr=subprocess.PIPE,timeout=5)
        finally:os.close(fd)
        self.assertNotEqual(r.returncode,0);self.assertEqual(p.read_bytes(),b'untouched');self.assertIn(b'flush failed',r.stderr)
if __name__=='__main__':unittest.main()
