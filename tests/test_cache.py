from ioncubefs.cache import MISSING, DiskCache, MemLRU


def test_lru_negative_entries_and_eviction():
    m = MemLRU(max_bytes=400)
    m.put("neg", None)
    assert m.get("neg") is None
    assert m.get("absent") is MISSING
    m.put("a", b"x" * 200)
    m.put("b", b"y" * 200)  # pushes total past budget -> oldest evicted
    assert m.get("neg") is MISSING
    assert m.get("b") == b"y" * 200


def test_disk_cache_roundtrip(tmp_path):
    d = DiskCache(str(tmp_path), "tag")
    assert d.get("ab" * 32) is None
    d.put_ok("ab" * 32, b"<?php ok")
    assert d.get("ab" * 32) == ("ok", b"<?php ok")
    d.put_fail("cd" * 32, "StreamError: bad")
    assert d.get("cd" * 32) == ("fail", "StreamError: bad")
    # a different tag (backend version) must not see old entries
    assert DiskCache(str(tmp_path), "other").get("ab" * 32) is None
