"""End-to-end: mount ioncubefs over a temp tree and read through it."""
import errno
import os
import shutil
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(
    not os.path.exists("/dev/fuse") or shutil.which("fusermount") is None,
    reason="FUSE not available")

BAD = (b"<?php //00447\n// 0 @Ioncube;\n//\n"
       b"if(extension_loaded('ionCube Loader')){die('x');}\n?>\n"
       b"not a real ionCube payload")


def _mount_bad(tmp_path, extra_opts=""):
    src, mnt, cache = tmp_path / "bad-src", tmp_path / "bad-mnt", tmp_path / "bad-cache"
    src.mkdir()
    mnt.mkdir()
    (src / "bad.php").write_bytes(BAD)
    proc = subprocess.Popen(
        [sys.executable, "-m", "ioncubefs.fs", str(mnt), "-f",
         "-o", f"root={src},cache_dir={cache}{extra_opts}"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for _ in range(100):
        if os.path.ismount(mnt):
            break
        time.sleep(0.05)
    else:
        proc.kill()
        pytest.fail("mount did not appear: " + proc.stdout.read().decode())
    return proc, src, mnt


def _unmount(proc, mnt):
    subprocess.run(["fusermount", "-u", str(mnt)], check=False)
    proc.wait(timeout=10)


@pytest.fixture
def mounted_bad(tmp_path):
    proc, src, mnt = _mount_bad(tmp_path)
    yield src, mnt
    _unmount(proc, mnt)


@pytest.fixture
def mounted_bad_original(tmp_path):
    proc, src, mnt = _mount_bad(tmp_path, ",on_decode_error=original")
    yield src, mnt
    _unmount(proc, mnt)


@pytest.fixture
def mounted(tmp_path, demo_encoded):
    src, mnt, cache = tmp_path / "src", tmp_path / "mnt", tmp_path / "cache"
    (src / "sub").mkdir(parents=True)
    mnt.mkdir()
    shutil.copy(demo_encoded, src / "enc.php")
    with open(demo_encoded, "rb") as f:
        (src / "sub" / "script").write_bytes(b"#!/usr/bin/php\n" + f.read())
    (src / "plain.txt").write_bytes(b"hello world\n")
    (src / "sub" / "plain.php").write_bytes(b"<?php echo 'plain';\n")
    proc = subprocess.Popen(
        [sys.executable, "-m", "ioncubefs.fs", str(mnt), "-f",
         "-o", f"root={src},cache_dir={cache}"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for _ in range(100):
        if os.path.ismount(mnt):
            break
        time.sleep(0.05)
    else:
        proc.kill()
        pytest.fail("mount did not appear: " + proc.stdout.read().decode())
    yield src, mnt
    subprocess.run(["fusermount", "-u", str(mnt)], check=False)
    proc.wait(timeout=10)


def test_mount(mounted):
    src, mnt = mounted
    dec = (mnt / "enc.php").read_bytes()
    assert dec.startswith(b"<?php") and b"LicenseIssuer" in dec
    assert b"ionCube Loader" not in dec
    assert os.stat(mnt / "enc.php").st_size == len(dec)
    script = (mnt / "sub" / "script").read_bytes()
    assert script.startswith(b"#!/usr/bin/php\n<?php")
    assert (mnt / "plain.txt").read_bytes() == b"hello world\n"
    assert (mnt / "sub" / "plain.php").read_bytes() == (src / "sub" / "plain.php").read_bytes()
    assert sorted(os.listdir(mnt)) == ["enc.php", "plain.txt", "sub"]
    with pytest.raises(OSError) as e:
        (mnt / "new").write_text("x")
    assert e.value.errno == 30  # EROFS


def test_failed_decode_returns_eio_on_read(mounted_bad):
    _, mnt = mounted_bad
    # stat still works (the metadata is readable); reading the file fails.
    assert os.stat(mnt / "bad.php").st_size == len(BAD)
    with pytest.raises(OSError) as e:
        (mnt / "bad.php").read_bytes()
    assert e.value.errno == errno.EIO


def test_on_decode_error_original_serves_original_bytes(mounted_bad_original):
    _, mnt = mounted_bad_original
    assert (mnt / "bad.php").read_bytes() == BAD
    assert os.stat(mnt / "bad.php").st_size == len(BAD)
