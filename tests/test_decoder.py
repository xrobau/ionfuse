import os

import pytest

from ioncubefs.decoder import DecodeError, Decoder

ENC = (b"#!/usr/bin/php -q\n<?php //00447\n"
       b"if(extension_loaded('ionCube Loader')){die('x');}\n?>\nPAYLOAD")


def _write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def test_plain_file_passes_through(tmp_path):
    calls = []
    d = Decoder(None, lift=lambda p, c, ierg=None: calls.append(p) or "x")
    p = _write(tmp_path, "plain.php", b"<?php echo 1; // long enough")
    assert d.decode(p) is None
    assert calls == []


def test_decode_prepends_shebang_and_caches(tmp_path):
    calls = []
    lift = lambda p, c, ierg=None: calls.append(p) or "<?php\necho 1;\n"
    cache = str(tmp_path / "cache")
    p = _write(tmp_path, "script", ENC)
    d = Decoder(cache, lift=lift)
    assert d.decode(p) == b"#!/usr/bin/php -q\n<?php\necho 1;\n"
    assert d.decode(p) == b"#!/usr/bin/php -q\n<?php\necho 1;\n"
    assert len(calls) == 1  # memory hit
    d2 = Decoder(cache, lift=lift)  # fresh process: disk hit
    assert d2.decode(p).endswith(b"echo 1;\n")
    assert len(calls) == 1 and d2.stats["disk_hits"] == 1


def test_failure_defaults_to_error_and_is_remembered(tmp_path, caplog):
    calls = []

    def lift(p, c, ierg=None):
        calls.append(p)
        raise ValueError("boom")

    cache = str(tmp_path / "cache")
    p = _write(tmp_path, "bad.php", ENC)
    d = Decoder(cache, lift=lift)
    with pytest.raises(DecodeError):
        d.decode(p)
    assert "boom" in caplog.text
    with pytest.raises(DecodeError):
        Decoder(cache, lift=lift).decode(p)
    assert len(calls) == 1  # failure marker on disk prevents retry


def test_failure_can_serve_original_bytes(tmp_path):
    calls = []

    def lift(p, c, ierg=None):
        calls.append(p)
        raise ValueError("boom")

    cache = str(tmp_path / "cache")
    p = _write(tmp_path, "bad.php", ENC)
    d = Decoder(cache, lift=lift, on_decode_error="original")
    assert d.decode(p) is None
    assert d.decode(p) is None  # memory-cache failure marker
    assert len(calls) == 1


def test_failure_error_is_memory_cached_without_disk(tmp_path):
    calls = []

    def lift(p, c, ierg=None):
        calls.append(p)
        raise ValueError("boom")

    p = _write(tmp_path, "bad.php", ENC)
    d = Decoder(None, lift=lift)
    with pytest.raises(DecodeError):
        d.decode(p)
    with pytest.raises(DecodeError):
        d.decode(p)
    assert len(calls) == 1  # in-memory failure sentinel


def test_invalid_decode_error_mode(tmp_path):
    with pytest.raises(ValueError):
        Decoder(None, on_decode_error="nonsense")


def test_modified_file_is_redecoded(tmp_path):
    n = []
    d = Decoder(None, lift=lambda p, c, ierg=None: n.append(1) or f"<?php // v{len(n)}")
    p = _write(tmp_path, "f.php", ENC)
    assert d.decode(p).endswith(b"v1")
    with open(p, "ab") as f:
        f.write(b"MORE")
    assert d.decode(p).endswith(b"v2")


def test_keymap_ierg_is_passed_to_the_backend(tmp_path):
    seen = []

    def lift(p, c, ierg=None):
        seen.append(ierg)
        return "<?php echo 1;\n"

    p = _write(tmp_path, "dyn.php", ENC)
    d = Decoder(None, lift=lift, keymap={p: 0x00363DE0})
    assert d.decode(p) is not None
    assert seen == [0x00363DE0]


def test_keymap_changes_the_cache_tag(tmp_path):
    lift = lambda p, c, ierg=None: "<?php echo 1;\n"
    a = Decoder(str(tmp_path / "a"), lift=lift)
    b = Decoder(str(tmp_path / "b"), lift=lift, keymap={"/x": 1})
    assert a.disk.tag != b.disk.tag


def test_real_backend_on_demo(demo_encoded, tmp_path):
    d = Decoder(str(tmp_path))
    out = d.decode(demo_encoded)
    assert out is not None and out.startswith(b"<?php")
    assert b"LicenseIssuer" in out
