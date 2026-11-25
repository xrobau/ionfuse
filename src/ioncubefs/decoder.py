"""Decode ionCube-encoded files with ioncube_re, with memory + disk caching."""
import errno
import hashlib
import logging
import os
import threading
import time

from . import detect
from .cache import MISSING, DiskCache, MemLRU

log = logging.getLogger("ioncubefs")


class DecodeError(OSError):
    """An encoded file was detected but could not be decoded."""


class _DecodeFailedSentinel:
    """Memory-cache marker for a failed decode (not a decoded value)."""

    def __len__(self) -> int:
        return 0

    def __repr__(self) -> str:
        return "DECODE_FAILED"


DECODE_FAILED = _DecodeFailedSentinel()
DECODE_ERROR_MODES = ("error", "original")


def backend_fingerprint() -> str:
    """Short hash of the installed ioncube_re sources.

    The package version never changes upstream, so hash the code itself; any
    backend update invalidates the on-disk cache automatically.
    """
    import ioncube_re

    root = os.path.dirname(ioncube_re.__file__)
    h = hashlib.sha256()
    for dp, dns, fns in os.walk(root):
        dns.sort()
        for fn in sorted(fns):
            if fn.endswith(".py"):
                p = os.path.join(dp, fn)
                h.update(os.path.relpath(p, root).encode())
                with open(p, "rb") as f:
                    h.update(f.read())
    return h.hexdigest()[:12]


def _default_lift(path: str, chunk: int, ierg: int | None = None) -> str:
    from ioncube_re.lift.pipeline import lift_file

    # m5dir=None: never use the CLI's _default_m5_dir() (hardcoded author path).
    return lift_file(path, chunk=chunk, m5dir=None, ierg=ierg)["text"]


class Decoder:
    """Turns a real path into decoded bytes, or None to mean "serve original".

    ``on_decode_error`` controls a file that sniffs as encoded but fails to
    decode: ``"error"`` raises DecodeError (the FUSE driver maps it to EIO);
    ``"original"`` returns None so the caller serves the original bytes.

    Cache layers:
      * sniff cache: (path, size, mtime_ns) -> bool       (cheap header check)
      * MemLRU:      (path, size, mtime_ns) -> bytes|None|failure sentinel
      * DiskCache:   sha256(content)+backend+chunk+keymap -> bytes | failure marker

    ``keymap`` maps real file paths to a keytable mask override (``ierg``).
    License-bound/dynamic-key files store a decoy mask in the file, so their
    opcodes only lift with the real (license-derived) value.
    """

    def __init__(self, cache_dir: str | None, mem_bytes: int = 256 << 20,
                 chunk: int = 1, lift=None, max_size: int = 16 << 20,
                 keymap: dict[str, int] | None = None,
                 on_decode_error: str = "error"):
        if on_decode_error not in DECODE_ERROR_MODES:
            raise ValueError(
                f"on_decode_error must be one of {DECODE_ERROR_MODES}, "
                f"not {on_decode_error!r}")
        self.on_decode_error = on_decode_error
        self.chunk = chunk
        self.max_size = max_size
        self._lift = lift or _default_lift
        self.keymap = dict(keymap or {})
        self.mem = MemLRU(mem_bytes)
        self.disk = None
        if cache_dir:
            tag = f"{backend_fingerprint() if lift is None else 'custom'}-c{chunk}"
            if self.keymap:
                km = repr(sorted(self.keymap.items())).encode()
                tag += "-k" + hashlib.sha256(km).hexdigest()[:12]
            self.disk = DiskCache(cache_dir, tag)
        self._sniff: dict = {}
        self._sniff_lock = threading.Lock()
        self._key_locks: dict = {}
        self._key_locks_lock = threading.Lock()
        self.stats = {"decoded": 0, "disk_hits": 0, "failed": 0}

    @staticmethod
    def _key(path: str, st: os.stat_result):
        return (path, st.st_size, st.st_mtime_ns, st.st_ino)

    def is_candidate(self, path: str, st: os.stat_result) -> bool:
        """Header sniff, cached per file version."""
        if st.st_size > self.max_size or st.st_size < 16:
            return False
        key = self._key(path, st)
        with self._sniff_lock:
            hit = self._sniff.get(key)
        if hit is not None:
            return hit
        try:
            res = detect.sniff_file(path)
        except OSError:
            return False
        with self._sniff_lock:
            if len(self._sniff) > 200_000:
                self._sniff.clear()
            self._sniff[key] = res
        return res

    def _lock_for(self, key) -> threading.Lock:
        with self._key_locks_lock:
            lk = self._key_locks.get(key)
            if lk is None:
                lk = self._key_locks[key] = threading.Lock()
            return lk

    def decode(self, path: str, st: os.stat_result | None = None) -> bytes | None:
        if st is None:
            st = os.stat(path)
        if not self.is_candidate(path, st):
            return None
        key = self._key(path, st)
        hit = self.mem.get(key)
        if hit is not MISSING:
            return self._cached_result(path, hit)
        lk = self._lock_for(key)
        with lk:
            hit = self.mem.get(key)  # decoded while we waited?
            if hit is not MISSING:
                return self._cached_result(path, hit)
            try:
                result = self._decode_uncached(path)
            except DecodeError:
                self.mem.put(key, DECODE_FAILED)
                if self.on_decode_error == "error":
                    raise
                return None
            finally:
                with self._key_locks_lock:
                    self._key_locks.pop(key, None)
            self.mem.put(key, result)
            return result

    def _cached_result(self, path: str, hit) -> bytes | None:
        if hit is DECODE_FAILED:
            if self.on_decode_error == "error":
                raise DecodeError(errno.EIO, f"cached decode failure: {path}")
            return None
        return hit

    def _decode_uncached(self, path: str) -> bytes:
        with open(path, "rb") as f:
            raw = f.read()
        sha = hashlib.sha256(raw).hexdigest()
        if self.disk is not None:
            cached = self.disk.get(sha)
            if cached is not None:
                self.stats["disk_hits"] += 1
                kind, val = cached
                if kind == "ok":
                    log.debug("disk cache hit: %s", path)
                    return val
                log.debug("disk cache (known failure): %s: %s", path, val)
                raise DecodeError(
                    errno.EIO, f"cannot decode {path}: {val}")

        t0 = time.monotonic()
        try:
            ierg = self.keymap.get(path)
            if ierg is None and self.keymap:
                ierg = self.keymap.get(os.path.realpath(path))
            text = self._lift(path, self.chunk, ierg)
            if not text:
                raise ValueError("decoder returned empty output")
        except Exception as e:  # backend raises many types; never crash the FS
            self.stats["failed"] += 1
            msg = f"{type(e).__name__}: {e}"
            if self.on_decode_error == "error":
                log.warning("decode failed, returning EIO: %s: %s", path, msg)
            else:
                log.warning("decode failed, serving original bytes: %s: %s",
                            path, msg)
            if self.disk is not None:
                try:
                    self.disk.put_fail(sha, msg)
                except OSError as we:
                    log.error("disk cache write failed: %s", we)
            raise DecodeError(errno.EIO, f"cannot decode {path}: {msg}") from e

        shebang, _ = detect.split_shebang(raw)
        out = shebang + text.encode("utf-8", "surrogateescape")
        self.stats["decoded"] += 1
        log.info("decoded %s (%d -> %d bytes) in %.2fs", path, len(raw), len(out),
                 time.monotonic() - t0)
        if self.disk is not None:
            try:
                self.disk.put_ok(sha, out)
            except OSError as we:
                log.error("disk cache write failed: %s", we)
        return out
