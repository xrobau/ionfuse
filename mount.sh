#!/usr/bin/env bash
# Mount a tree through ioncubefs.
#
#   ./mount.sh SOURCE [MOUNTPOINT] [OPTION ...]
#
# OPTIONs are ioncubefs -o options without the leading "-o", for example:
#   prewarm=4   ext=.php:.inc   log=/var/log/ioncubefs.log   no_disk_cache
#   on_decode_error=original
# They can also be placed in IONCUBEFS_OPTS.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    case ${1:-} in
        -h|--help) ;;
        *) echo "mount.sh: must be run as root." >&2
           echo "Run 'sudo -i' or log in as root." >&2
           exit 1 ;;
    esac
fi

SELF=$(readlink -f -- "$0" 2>/dev/null || printf '%s' "$0")
ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$SELF")" && pwd)
BIN=${IONCUBEFS_BIN:-"$ROOT_DIR/.venv/bin/ioncubefs"}

usage() {
    cat <<EOF
Usage: $(basename "$0") SOURCE [MOUNTPOINT] [OPTION ...]

Mirror SOURCE read-only through ioncubefs, decoding ionCube files on access.

MOUNTPOINT defaults to /mnt/ioncubefs/<source-name>
(or IONCUBEFS_MOUNT_BASE/<source-name> when that variable is set).

OPTIONs are ioncubefs mount options, either bare or after "-o":
  prewarm[=N]  ext=.php:.inc  log=FILE  loglevel=debug  no_disk_cache
  on_decode_error=error|original
Use "--" before options when no MOUNTPOINT is given and an option does not
contain "=" (e.g. "-- prewarm").
Options can also be supplied in IONCUBEFS_OPTS (space separated).

Examples:
  ./mount.sh /srv/encoded /mnt/encoded prewarm=4
  ./mount.sh ./src ./mnt -o ext=.php,prewarm=4
  ./mount.sh ./src -- ext=.php
  ./umount.sh                    # unmount every ioncubefs mount
  ./umount.sh /mnt/encoded       # unmount one mount

Environment:
  IONCUBEFS_BIN         ioncubefs executable (default: .venv/bin/ioncubefs)
  IONCUBEFS_MOUNT_BASE  base directory for default mountpoints
  IONCUBEFS_OPTS        extra options, space separated
EOF
}

is_ioncubefs_mount() {
    local mp=$1 fstype
    mountpoint -q -- "$mp" || return 1
    if command -v findmnt >/dev/null 2>&1; then
        fstype=$(findmnt -n -M "$mp" -o FSTYPE 2>/dev/null || true)
        [ "$fstype" = "fuse.ioncubefs" ]
    else
        awk -v m="$mp" '
            function unescape(s) {
                gsub(/\\040/, " ", s); gsub(/\\011/, "\t", s)
                gsub(/\\012/, "\n", s); gsub(/\\134/, "\\", s)
                return s
            }
            unescape($2) == m && ($1 == "ioncubefs" || $3 == "fuse.ioncubefs") {
                found = 1
            }
            END { exit(found ? 0 : 1) }
        ' /proc/mounts
    fi
}

case ${1:-} in
    -h|--help) usage; exit 0 ;;
    '') usage >&2; exit 2 ;;
esac

SRC=$1
shift
MP=
optargs=()
while [ $# -gt 0 ]; do
    case $1 in
        -h|--help) usage; exit 0 ;;
        --) shift; optargs+=("$@"); break ;;
        -o)
            [ $# -ge 2 ] || { echo "mount.sh: -o requires an argument" >&2; exit 2; }
            optargs+=("$2")
            shift 2
            ;;
        -o*) optargs+=("${1#-o}"); shift ;;
        -*) echo "mount.sh: unknown option: $1" >&2; usage >&2; exit 2 ;;
        *)
            if [ -z "$MP" ] && [[ $1 != *=* ]] \
                    && [ "$1" != "prewarm" ] && [ "$1" != "no_disk_cache" ]; then
                MP=$1
            else
                optargs+=("$1")
            fi
            shift
            ;;
    esac
done

[ -d "$SRC" ] || { echo "mount.sh: source is not a directory: $SRC" >&2; exit 2; }
SRC=$(realpath -- "$SRC")
[ -x "$BIN" ] || { echo "mount.sh: $BIN not found; run 'make install' first" >&2; exit 2; }

if [ -z "$MP" ]; then
    base=${IONCUBEFS_MOUNT_BASE:-/mnt/ioncubefs}
    name=$(basename -- "$SRC")
    name=$(printf '%s' "$name" | tr -c 'A-Za-z0-9._-' '_')
    MP=$base/$name
fi

mkdir -p -- "$MP"
MP=$(realpath -- "$MP")
if mountpoint -q -- "$MP"; then
    if is_ioncubefs_mount "$MP"; then
        echo "Already mounted: $MP"
        exit 0
    fi
    echo "mount.sh: $MP is already a mountpoint (not ioncubefs)" >&2
    exit 1
fi

opts="root=$SRC"
for o in ${IONCUBEFS_OPTS:-}; do
    opts="$opts,$o"
done
for o in "${optargs[@]}"; do
    opts="$opts,$o"
done

if [ -n "${IONCUBEFS_LOG_DIR:-}" ] && [[ $opts != *,log=* ]]; then
    mkdir -p -- "$IONCUBEFS_LOG_DIR"
    opts="$opts,log=$IONCUBEFS_LOG_DIR/$(basename -- "$MP").log"
fi

"$BIN" "$MP" -o "$opts"

for _ in {1..100}; do
    if is_ioncubefs_mount "$MP"; then
        echo "Mounted $SRC at $MP"
        exit 0
    fi
    sleep 0.1
done

echo "mount.sh: mount did not appear at $MP" >&2
exit 1
