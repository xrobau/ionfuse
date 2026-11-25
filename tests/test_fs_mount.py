"""End-to-end: mount ioncubefs over a temp tree and read through it."""
import os
import shutil
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(
    not os.path.exists("/dev/fuse") or shutil.which("fusermount") is None,
    reason="FUSE not available")


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
