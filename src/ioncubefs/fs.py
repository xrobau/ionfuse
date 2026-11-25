"""Read-only FUSE passthrough that shows ionCube-encoded files as decompiled PHP.

Usage:
    ioncubefs MOUNTPOINT -o root=/path/to/encoded [-o cache_dir=DIR] [-f]

Everything under ``root`` is mirrored read-only. Files whose header looks like
ionCube (any name or extension: encoded CLI scripts often have none) are
decoded with ioncube_re on first access.  If decoding fails, reads return EIO
by default; set -o on_decode_error=original to serve the original bytes
instead.
"""
import errno
import logging
import os
import stat
import sys
import threading

import fuse

from .decoder import DECODE_ERROR_MODES, DecodeError, Decoder

fuse.fuse_python_api = (0, 2)

log = logging.getLogger("ioncubefs")

WRITE_BITS = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
DEFAULT_CACHE = os.path.join(
    os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "ioncubefs")


def _to_stat(st: os.stat_result, size: int | None = None) -> fuse.Stat:
    return fuse.Stat(
        st_mode=st.st_mode & ~WRITE_BITS,
        st_ino=st.st_ino,
        st_dev=st.st_dev,
        st_nlink=st.st_nlink,
        st_uid=st.st_uid,
        st_gid=st.st_gid,
        st_size=st.st_size if size is None else size,
        st_atime=int(st.st_atime),
        st_mtime=int(st.st_mtime),
        st_ctime=int(st.st_ctime),
    )


def _guard(fn):
    """Map exceptions to negative errno so a backend bug can't kill the mount."""
    def wrapper(self, *a, **kw):
        try:
            return fn(self, *a, **kw)
        except OSError as e:
            return -(e.errno or errno.EIO)
        except Exception:
            log.exception("%s%r failed", fn.__name__, a)
            return -errno.EIO
    wrapper.__name__ = fn.__name__
    return wrapper


def _erofs(self, *a, **kw):
    return -errno.EROFS


def _prewarm_one(cache_dir, chunk, keymap, on_decode_error, path):
    """Worker-process entry: decode one file into the shared disk cache."""
    logging.getLogger("ioncubefs").setLevel(logging.WARNING)
    d = Decoder(cache_dir, mem_bytes=0, chunk=chunk, keymap=keymap,
                on_decode_error=on_decode_error)
    d.decode(path)
    return path


class IoncubeFS(fuse.Fuse):

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.root = None
        self.cache_dir = DEFAULT_CACHE
        self.no_disk_cache = False
        self.mem_cache_mb = 256
        self.chunk = 1
        self.ext = None
        self.prewarm = None
        self.keymap = None
        self.on_decode_error = "error"
        self.log = None
        self.loglevel = "info"
        self.decoder: Decoder | None = None
        self._exts: tuple | None = None
        self._keymap: dict[str, int] = {}

    # ---- setup -----------------------------------------------------------

    def setup(self):
        if not self.root:
            raise SystemExit("ioncubefs: -o root=PATH is required")
        self.root = os.path.realpath(self.root)
        if not os.path.isdir(self.root):
            raise SystemExit(f"ioncubefs: root {self.root!r} is not a directory")
        if self.ext:
            self._exts = tuple(
                e if e.startswith(".") else "." + e
                for e in str(self.ext).replace(":", ",").split(",") if e)
        if self.keymap:
            import json

            with open(os.path.expanduser(self.keymap)) as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                raise SystemExit("ioncubefs: keymap must be a JSON object")
            for k, v in raw.items():
                try:
                    iv = int(v, 0) if isinstance(v, str) else int(v)
                except (TypeError, ValueError):
                    raise SystemExit(f"ioncubefs: bad ierg for {k!r}: {v!r}")
                if not 0 <= iv <= 0xFFFFFFFF:
                    raise SystemExit(f"ioncubefs: ierg out of range for {k!r}")
                ks = str(k)
                p = ks if os.path.isabs(ks) else os.path.join(self.root, ks)
                p = os.path.realpath(p)
                if p != self.root and not p.startswith(self.root + os.sep):
                    raise SystemExit(f"ioncubefs: keymap path escapes root: {k!r}")
                self._keymap[p] = iv
        self.on_decode_error = str(self.on_decode_error or "error").lower()
        if self.on_decode_error not in DECODE_ERROR_MODES:
            raise SystemExit(
                "ioncubefs: on_decode_error must be error or original")
        cache_dir = None if self.no_disk_cache else os.path.expanduser(self.cache_dir)
        self.decoder = Decoder(cache_dir, mem_bytes=int(self.mem_cache_mb) << 20,
                               chunk=int(self.chunk), keymap=self._keymap,
                               on_decode_error=self.on_decode_error)
        self._cache_dir = cache_dir
        log.info("root=%s cache_dir=%s keymap=%d on_decode_error=%s", self.root,
                 cache_dir or "(disabled)", len(self._keymap),
                 self.on_decode_error)

    def fsinit(self):
        # Runs after libfuse daemonizes, so background work started here survives.
        if self.prewarm is not None:
            threading.Thread(target=self._prewarm, name="prewarm", daemon=True).start()

    def _prewarm(self):
        try:
            workers = int(self.prewarm) if str(self.prewarm) not in ("", "True") else 0
        except ValueError:
            workers = 0
        workers = workers or max(1, (os.cpu_count() or 2) - 1)
        paths = []
        for dp, dns, fns in os.walk(self.root):
            for fn in fns:
                p = os.path.join(dp, fn)
                try:
                    st = os.lstat(p)
                except OSError:
                    continue
                if stat.S_ISREG(st.st_mode) and self._wants(p) \
                        and self.decoder.is_candidate(p, st):
                    paths.append(p)
        log.info("prewarm: %d encoded files, %d workers", len(paths), workers)
        if self._cache_dir and workers > 1:
            # Separate processes sidestep the GIL; results land in the disk cache.
            import concurrent.futures as cf
            import multiprocessing as mp
            with cf.ProcessPoolExecutor(workers, mp_context=mp.get_context("spawn")) as ex:
                futs = [ex.submit(_prewarm_one, self._cache_dir,
                                  self.decoder.chunk, self._keymap,
                                  self.on_decode_error, p) for p in paths]
                for f in cf.as_completed(futs):
                    if f.exception():
                        log.warning("prewarm worker error: %s", f.exception())
        for p in paths:  # pull into memory (cheap when disk-cached)
            try:
                self.decoder.decode(p)
            except DecodeError:
                log.debug("prewarm: decode failed: %s", p)
            except Exception:
                log.exception("prewarm %s", p)
        log.info("prewarm done: %s", self.decoder.stats)

    # ---- helpers ---------------------------------------------------------

    def _real(self, path: str) -> str:
        rel = os.path.normpath(path.lstrip("/"))
        if rel == ".":
            return self.root
        if rel.startswith("..") or os.path.isabs(rel):
            raise OSError(errno.EACCES, "path escapes root")
        return os.path.join(self.root, rel)

    def _wants(self, real: str) -> bool:
        return self._exts is None or real.endswith(self._exts)

    def _decoded(self, real: str, st: os.stat_result) -> bytes | None:
        if not stat.S_ISREG(st.st_mode) or not self._wants(real):
            return None
        return self.decoder.decode(real, st)

    # ---- read-only operations --------------------------------------------

    @_guard
    def getattr(self, path):
        real = self._real(path)
        st = os.lstat(real)
        # A failed decode still gets its original stat data; the read below
        # is what returns EIO.  This keeps directory listings usable.
        try:
            data = self._decoded(real, st)
        except DecodeError:
            data = None
        return _to_stat(st, None if data is None else len(data))

    @_guard
    def readlink(self, path):
        return os.readlink(self._real(path))

    @_guard
    def readdir(self, path, offset):
        real = self._real(path)
        yield fuse.Direntry(".")
        yield fuse.Direntry("..")
        with os.scandir(real) as it:
            for e in it:
                yield fuse.Direntry(e.name)

    @_guard
    def access(self, path, mode):
        if mode & os.W_OK:
            return -errno.EROFS
        if not os.access(self._real(path), mode):
            return -errno.EACCES
        return 0

    @_guard
    def open(self, path, flags):
        if flags & os.O_ACCMODE != os.O_RDONLY or flags & (os.O_TRUNC | os.O_APPEND):
            return -errno.EROFS
        real = self._real(path)
        if not os.access(real, os.R_OK):
            return -errno.EACCES
        return 0

    @_guard
    def read(self, path, size, offset):
        real = self._real(path)
        st = os.stat(real)
        data = self._decoded(real, st)
        if data is not None:
            return data[offset:offset + size]
        with open(real, "rb") as f:
            return os.pread(f.fileno(), size, offset)

    @_guard
    def statfs(self):
        s = os.statvfs(self.root)
        return fuse.StatVfs(
            f_bsize=s.f_bsize, f_frsize=s.f_frsize, f_blocks=s.f_blocks,
            f_bfree=0, f_bavail=0, f_files=s.f_files, f_ffree=0, f_favail=0,
            f_flag=s.f_flag | os.ST_RDONLY, f_namemax=s.f_namemax)

    @_guard
    def getxattr(self, path, name, size):
        return -errno.ENODATA

    @_guard
    def listxattr(self, path, size):
        return [] if size else 0

    # ---- everything that writes ------------------------------------------

    mknod = mkdir = unlink = rmdir = symlink = rename = link = chmod = chown = \
        truncate = utime = utimens = write = create = setxattr = removexattr = \
        ftruncate = _erofs


def main(argv=None):
    server = IoncubeFS(
        version="%prog " + fuse.__version__,
        usage="ioncubefs MOUNTPOINT -o root=SRC [options]\n\n" + (__doc__ or ""),
        dash_s_do="setsingle",
    )
    p = server.parser
    p.add_option(mountopt="root", metavar="SRC", help="directory to mirror (required)")
    p.add_option(mountopt="cache_dir", metavar="DIR",
                 help=f"persistent decode cache [default: {DEFAULT_CACHE}]")
    p.add_option(mountopt="no_disk_cache", action="store_true",
                 help="disable the persistent decode cache")
    p.add_option(mountopt="mem_cache_mb", metavar="N", help="in-memory cache size [256]")
    p.add_option(mountopt="chunk", metavar="N",
                 help="ICB0 production files: which version chunk to decode [1]")
    p.add_option(mountopt="ext", metavar="LIST",
                 help="only consider these extensions, e.g. .php:.inc "
                      "[default: sniff every file]")
    p.add_option(mountopt="prewarm", metavar="[N]",
                 help="decode the whole tree in the background at mount, "
                      "with N worker processes [cpus-1]")
    p.add_option(mountopt="keymap", metavar="FILE",
                 help="JSON map of path (relative to root) -> ierg override, "
                      "e.g. {\"functions.inc/IoncubeLicenseLoader.php\": \"0x363de0\"} "
                      "for license-bound/dynamic-key files")
    p.add_option(mountopt="on_decode_error", metavar="MODE",
                 help="encoded file that fails to decode: error returns EIO "
                      "on read; original serves the original bytes [error]")
    p.add_option(mountopt="log", metavar="FILE", help="log to FILE instead of stderr")
    p.add_option(mountopt="loglevel", metavar="LEVEL", help="debug|info|warning [info]")
    server.parse(values=server, errex=1)

    if server.fuse_args.mount_expected():
        handler = logging.FileHandler(server.log) if server.log \
            else logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
        log.setLevel(getattr(logging, str(server.loglevel).upper(), logging.INFO))
        server.setup()
        server.fuse_args.add("ro")
        server.fuse_args.add("fsname", "ioncubefs")
        server.fuse_args.add("subtype", "ioncubefs")
    server.main()


if __name__ == "__main__":
    main()
