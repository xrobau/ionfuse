# ioncubefs

A read-only FUSE filesystem. It mirrors a directory and shows every ionCube-encoded file in it as decompiled PHP source.
Decoding is done by `ioncube_re`, from [ardzz/ioncube-decompiler](https://github.com/ardzz/ioncube-decompiler), which is checked out by hand into `third_party/` (it is not a git submodule).
The bindings are [python-fuse](https://github.com/libfuse/python-fuse) (libfuse 2).

- Files are detected by **content sniffing**, not extension, so encoded CLI scripts with no extension (`bin/`, `hooks/`) are decoded too.
  A `#!` shebang line is kept.
- Every other file passes through byte for byte.
- If an encoded file cannot be decoded, the error is logged and reads return **EIO** by default; `on_decode_error=original` serves the original bytes instead.
- All writes return `EROFS`.
- Decoded output is cached in memory (an LRU) and on disk.
  - The disk cache is keyed by sha256 of the file plus a fingerprint of the backend source, so a backend upgrade invalidates it.
  - Failures are cached too.

## Install

All install and mount-management commands below must run as root. If you
are not root, run `sudo -i` or log in as root first.

```sh
make install
```

On Debian/Ubuntu this installs the build tools, `libfuse-dev`, `fuse`,
the Python headers, `git`, `curl`, and [uv](https://docs.astral.sh/uv/) if
needed. It then clones the decoder backend if it is missing and creates
`.venv` with `uv sync`.
If you manage packages yourself, run `make install NO_APT=1` instead.

The decoder is the manually checked-out fork `third_party/ioncube-decompiler` (branch `missing-opcodes`), with the patches this project needs.
It is ignored by this repo and packaged into ioncubefs, so the install also gives you `ioncube_re` and the `ioncube-re` CLI.
`make update` pulls the backend and re-syncs the environment. Run `make help` to list the targets.

Download the ionCube loaders and the evaluation encoder with:

```sh
make ioncube
```

The archives are kept in `ioncube/downloads/`, the loaders under
`ioncube/loaders/`, and the eval encoder under `ioncube/encoder/`. The whole
`ioncube/` directory is gitignored. `make ioncube` is idempotent; remove
`ioncube/` if you want a fresh download.

## Mounting and unmounting

```sh
./mount.sh /srv/encoded /mnt/encoded
./mount.sh /srv/encoded /mnt/encoded prewarm=4 ext=.php:.inc

./umount.sh /mnt/encoded   # unmount one
./umount.sh                # unmount every ioncubefs mount
./umount.sh --list         # list active mounts
```

`MOUNTPOINT` defaults to `/mnt/ioncubefs/<source-name>`.
Extra arguments are `ioncubefs -o` options without the `-o` prefix, and can
also be supplied in `IONCUBEFS_OPTS`.

With the default `on_decode_error=error`, `stat` still reports the original
file size but `read()` returns EIO, so directory listings remain usable.

The same actions are available through `make`:

```sh
make mount SRC=/srv/encoded OPTS="prewarm=4"
make status
make umount
```

Direct invocation is still available for foreground/debug use:

```sh
ioncubefs /mnt/decoded -o root=/path/to/encoded/files
ioncubefs /mnt/decoded -o root=SRC,prewarm=4,log=/var/log/ioncubefs.log
fusermount -u /mnt/decoded
```

| option | meaning |
|---|---|
| `root=SRC` | directory to mirror (required) |
| `cache_dir=DIR` | persistent cache (default `~/.cache/ioncubefs`) |
| `no_disk_cache` | memory cache only |
| `mem_cache_mb=N` | memory cache budget (default 256) |
| `prewarm[=N]` | decode the whole tree in the background at mount, using N worker processes |
| `ext=.php:.inc` | only consider these extensions (default: sniff every file under 16 MiB) |
| `chunk=N` | which version chunk to decode in ICB0 production files (default 1) |
| `keymap=FILE` | JSON map of path (relative to root) → `ierg` override, for license-bound/dynamic-key files |
| `on_decode_error=error\|original` | encoded file that fails to decode: return EIO on read (default) or serve the original bytes |
| `log=FILE`, `loglevel=debug\|info\|warning` | logging |

Standard FUSE flags work as usual: `-f` runs in the foreground, `-d` turns on debug output, `-s` runs single-threaded.

### License-bound (dynamic-key) files

Some files are encoded with ionCube dynamic keys: the keytable mask stored in the file is a decoy and the real one is derived from the license at runtime. Their structure and operands still lift, but affected opcodes are shown as `/* nN: opcode masked ... */`. If you know the mask from a known-good decode, map the file to it:

```json
{
  "functions.inc/IoncubeLicenseLoader.php": "0x363de0"
}
```

```sh
ioncubefs /mnt/decoded -o root=SRC,keymap=/path/keys.json
```

The same override is available in the backend CLI: `ioncube-re lift FILE --ierg 0x363de0`.

The mask can also be extracted from the license through the installed loader.
The license carries a hidden `__mykey` property inside ionCube's encrypted
payload; the loader decodes it to a decimal value `K` and derives the mask as
`K // 7 - 13` (for the reference license here: `24883579` → `0x00363DE0`).
The canonical extractor lives in `third_party/ioncube-decompiler`
(`ioncube-re license-key FILE`); the wrapper here calls it:

```sh
.venv/bin/python tools/extract_license_key.py SRC/functions.inc/IoncubeLicenseLoader.php
# 0x00363de0
# or, canonical: third_party/ioncube-decompiler's `ioncube-re license-key FILE`
```

This requires the ionCube loader and a valid license on the machine, and it
executes the encoded file's top-level through the loader (its classes are
declared, nothing is called). It is loader-build specific (ionCube 13.0.2 /
PHP 8.2 here). For non-license-bound files it returns the file's stored mask.

### Performance

The decompiler is pure Python and CPU-bound. A file is decoded the first time it is `stat`ed, because the decoded size has to be reported.
On a cold cache, `ls -l` waits for every encoded file in the directory to decode, and a single large file can take about 30s.
Use `prewarm` once, or just let the disk cache fill. After that, a remount serves everything from cache:
on a 255-file sample tree, `ls -lR` takes 0.15s.

## Tests

```sh
make test
```

The tests use the backend's bundled `demo/demo-encoded.php`. `test_fs_mount.py` does a real mount and needs `/dev/fuse`.
`make clean` removes build/test caches; `make distclean` also removes `.venv`.
