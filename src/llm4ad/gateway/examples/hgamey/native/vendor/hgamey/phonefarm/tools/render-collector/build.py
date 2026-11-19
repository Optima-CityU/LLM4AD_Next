#!/usr/bin/env python3
"""Build the fixed readonly reader with explicit already-installed Android NDK."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time

FLAGS = ['-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-fPIE', '-pie', '-Wl,-z,relro,-z,now']
def sha(b): return hashlib.sha256(b).hexdigest()
def regular(path, cap):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        import stat
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > cap: raise ValueError('not bounded regular file')
        b = bytearray()
        while len(b) <= cap:
            part = os.read(fd, min(65536, cap+1-len(b)))
            if not part: break
            b.extend(part)
        if len(b) > cap: raise ValueError('file cap exceeded')
        return bytes(b)
    finally: os.close(fd)
def save(path, b):
    with open(path, 'xb') as f: f.write(b); f.flush(); os.fsync(f.fileno())
def run(argv, out):
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    chunks = [bytearray(), bytearray()]; overflow = threading.Event()
    def drain(stream, buf):
        while True:
            b = stream.read(4096)
            if not b: break
            if len(buf)+len(b)>131072: overflow.set(); break
            buf.extend(b)
    workers = [threading.Thread(target=drain, args=(p.stdout,chunks[0]),daemon=True), threading.Thread(target=drain,args=(p.stderr,chunks[1]),daemon=True)]
    for w in workers:w.start()
    deadline = time.monotonic()+30
    while p.poll() is None and not overflow.is_set() and time.monotonic()<deadline: time.sleep(.01)
    if p.poll() is None or overflow.is_set():
        try: os.killpg(p.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        p.wait(timeout=5)
        for w in workers:w.join(timeout=1)
        raise ValueError('compiler timeout/output bound')
    for w in workers:w.join(timeout=1)
    try: os.killpg(p.pid,0)
    except ProcessLookupError:pass
    else:
        os.killpg(p.pid,signal.SIGKILL)
        raise ValueError('compiler left descendants')
    if any(w.is_alive() for w in workers):raise ValueError('compiler pipe incomplete')
    save(out/'compiler.stdout',chunks[0]);save(out/'compiler.stderr',chunks[1])
    if p.returncode: raise ValueError('compiler nonzero exit')
    return {'exit':p.returncode,'stdout_sha256':sha(chunks[0]),'stderr_sha256':sha(chunks[1]),'process_group_absent':True}
def main():
    a=argparse.ArgumentParser();a.add_argument('--ndk',type=Path,required=True);a.add_argument('--out',type=Path,required=True);args=a.parse_args()
    source=Path(__file__).parent/'reader.c'; raw=regular(source,65536)
    cc=args.ndk/'toolchains/llvm/prebuilt/darwin-x86_64/bin/aarch64-linux-android30-clang'
    actual_clang=(cc.parent/'clang').resolve(strict=True)
    compiler=regular(cc,65536); clang=regular(actual_clang,256*1024*1024)
    package=regular(args.ndk/'source.properties',65536)
    if b'Pkg.Revision = 28.2.13676358' not in package:raise ValueError('requires existing r28c NDK')
    args.out.mkdir(mode=0o700)
    try:
        save(args.out/'source.c',raw)
        argv=[str(cc),*FLAGS,str(args.out/'source.c'),'-o',str(args.out/'reader.elf')]
        result=run(argv,args.out)
        elf=regular(args.out/'reader.elf',1024*1024)
        if not (elf[:4]==b'\x7fELF' and elf[4:6]==b'\x02\x01' and int.from_bytes(elf[16:18],'little')==3 and int.from_bytes(elf[18:20],'little')==183):raise ValueError('not ELF64 little-endian AArch64 PIE')
        if regular(source,65536)!=raw or regular(cc,65536)!=compiler or regular(actual_clang,256*1024*1024)!=clang or regular(args.ndk/'source.properties',65536)!=package:raise ValueError('source/tool changed during build')
        r={'schema':'phonefarm/render_reader_build@1','status':'BUILT','target':'aarch64-linux-android30','ndk_revision':'28.2.13676358','flags':FLAGS,'source_sha256':sha(raw),'elf_sha256':sha(elf),'compiler':{'path':str(cc),'sha256':sha(compiler),'clang_path':str(actual_clang),'clang_sha256':sha(clang),'package_sha256':sha(package)},'execution':result,'source_authentication':'local_build_receipt_not_independent_authentication','device_run':False,'installed_kernel_source_verified':False,'game_proof':False}
        save(args.out/'build.json',(json.dumps(r,indent=2)+'\n').encode())
        fd=os.open(args.out,os.O_RDONLY);os.fsync(fd);os.close(fd)
        print(json.dumps({'artifact':str(args.out),'elf_sha256':r['elf_sha256'],'source_sha256':r['source_sha256']}))
    except Exception as e:
        save(args.out/'failure.json',(json.dumps({'status':'FAILED','error':str(e)})+'\n').encode())
        raise
if __name__=='__main__':main()
