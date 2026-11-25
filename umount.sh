#!/usr/bin/env bash
# Unmount ioncubefs mounts.
#
#   ./umount.sh                 unmount every ioncubefs mount
#   ./umount.sh MOUNTPOINT ...  unmount specific mounts
#   ./umount.sh --list          list active ioncubefs mounts
#   ./umount.sh --force ...     lazy unmount if a mount is busy
set -euo pipefail

usage() {
    cat <<EOF
Usage: $(basename "$0") [--list] [--force] [MOUNTPOINT ...]

With no MOUNTPOINT, every active ioncubefs mount is unmounted.

Options:
  -l, --list    list active ioncubefs mounts and exit
  -f, --force   lazy unmount (fusermount -z / umount -l)
  -h, --help    show this help
EOF
}

list_mounts() {
    # /proc/mounts (not findmnt) so spaces in mountpoints are decoded to
    # real spaces rather than findmnt's \x20 escapes.
    awk '
        function unescape(s) {
            gsub(/\\040/, " ", s); gsub(/\\011/, "\t", s)
            gsub(/\\012/, "\n", s); gsub(/\\134/, "\\", s)
            return s
        }
        $1 == "ioncubefs" || $3 == "fuse.ioncubefs" { print unescape($2) }
    ' /proc/mounts
}

is_ioncubefs_mount() {
    local mp=$1 fstype
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

unmount_one() {
    local mp=$1 force=$2
    if command -v fusermount >/dev/null 2>&1; then
        if [ "$force" = 1 ]; then
            fusermount -uz -- "$mp"
        else
            fusermount -u -- "$mp"
        fi
    elif [ "$force" = 1 ]; then
        umount -l -- "$mp"
    else
        umount -- "$mp"
    fi
}

action=unmount
force=0
mounts=()
for arg in "$@"; do
    case $arg in
        -h|--help) usage; exit 0 ;;
        -l|--list) action=list ;;
        -f|--force) force=1 ;;
        -*) echo "umount.sh: unknown option: $arg" >&2; usage >&2; exit 2 ;;
        *) mounts+=("$arg") ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "umount.sh: must be run as root." >&2
    echo "Run 'sudo -i' or log in as root." >&2
    exit 1
fi

if [ "$action" = list ]; then
    found=$(list_mounts)
    if [ -n "$found" ]; then
        printf '%s\n' "$found"
    else
        echo "No ioncubefs mounts."
    fi
    exit 0
fi

if [ ${#mounts[@]} -eq 0 ]; then
    while IFS= read -r mp; do
        [ -n "$mp" ] && mounts+=("$mp")
    done < <(list_mounts)
    if [ ${#mounts[@]} -eq 0 ]; then
        echo "No ioncubefs mounts."
        exit 0
    fi
fi

rc=0
for mp in "${mounts[@]}"; do
    if ! mountpoint -q -- "$mp"; then
        echo "umount.sh: not a mountpoint: $mp" >&2
        rc=1
        continue
    fi
    if ! is_ioncubefs_mount "$mp"; then
        echo "umount.sh: refusing to unmount non-ioncubefs mount: $mp" >&2
        rc=1
        continue
    fi
    if unmount_one "$mp" "$force"; then
        echo "Unmounted $mp"
    else
        echo "umount.sh: failed to unmount $mp" >&2
        rc=1
    fi
done
exit "$rc"
