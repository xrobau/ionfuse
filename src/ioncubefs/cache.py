"""In-memory LRU and persistent on-disk caches for decoded output."""
import os
import tempfile
import threading
from collections import OrderedDict

MISSING = object()


class MemLRU:
    """Thread-safe LRU bounded by the total size of the cached values.

    Values are ``bytes``, ``None`` (not encoded), or the decoder's failure
    sentinel.
    """

    def __init__(self, max_bytes: int):
        self.max_bytes = max_bytes
        self._d: "OrderedDict[object, bytes | None]" = OrderedDict()
        self._size = 0
        self._lock = threading.Lock()

    @staticmethod
    def _cost(v) -> int:
        return (len(v) if v else 0) + 64

    def get(self, key, default=MISSING):
        """Return the cached value (may be None), or ``default`` if absent."""
        with self._lock:
            if key in self._d:
                self._d.move_to_end(key)
                return self._d[key]
        return default

    def __contains__(self, key) -> bool:
        with self._lock:
            return key in self._d

    def put(self, key, value) -> None:
        with self._lock:
            if key in self._d:
                self._size -= self._cost(self._d.pop(key))
            self._d[key] = value
            self._size += self._cost(value)
            while self._size > self.max_bytes and len(self._d) > 1:
                _, old = self._d.popitem(last=False)
                self._size -= self._cost(old)

    def __len__(self) -> int:
        with self._lock:
            return len(self._d)


class DiskCache:
    """Content-addressed cache: <dir>/<sha[:2]>/<sha>-<tag>.php (or .fail)."""

    def __init__(self, directory: str, tag: str):
        self.dir = directory
        self.tag = tag
        os.makedirs(self.dir, exist_ok=True)

    def _base(self, sha: str) -> str:
        return os.path.join(self.dir, sha[:2], f"{sha}-{self.tag}")

    def get(self, sha: str):
        """Return ('ok', bytes) | ('fail', str) | None if not cached."""
        base = self._base(sha)
        try:
            with open(base + ".php", "rb") as f:
                return "ok", f.read()
        except FileNotFoundError:
            pass
        try:
            with open(base + ".fail", "r", encoding="utf-8", errors="replace") as f:
                return "fail", f.read()
        except FileNotFoundError:
            return None

    def _write(self, path: str, data: bytes) -> None:
        d = os.path.dirname(path)
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def put_ok(self, sha: str, data: bytes) -> None:
        self._write(self._base(sha) + ".php", data)

    def put_fail(self, sha: str, message: str) -> None:
        self._write(self._base(sha) + ".fail", message.encode("utf-8", "replace"))
