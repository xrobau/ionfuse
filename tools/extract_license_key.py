#!/usr/bin/env python3
"""Extract the dynamic-key mask for a license-bound encoded file.

Thin wrapper: the canonical implementation, loader-build check, and caveats
live in the vendored `third_party/ioncube-decompiler` package
(``ioncube_re.license_key`` / ``ioncube-re license-key``).  Requires the
ionCube Loader and a valid license; it runs the encoded file's top level
through the loader.  Prints the mask, e.g. ``0x00363de0``.
"""
import sys

from ioncube_re.license_key import LicenseKeyError, extract_license_key


def main(argv: list[str]) -> int:
    if not argv:
        print(f"usage: {sys.argv[0]} ENCODED_FILE [PHP]", file=sys.stderr)
        return 2
    try:
        mask = extract_license_key(argv[0], argv[1] if len(argv) > 1 else "php")
    except LicenseKeyError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"0x{mask:08x}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
