"""Cheap header sniffing for ionCube-encoded PHP files.

This is only a pre-filter: the decoder is the real arbiter, and a file that
sniffs positive but fails to decode is served as its original bytes.
"""
import re

SNIFF_BYTES = 4096

# Production containers: "<?php //ICB0 ...".
_ICB0 = b"<?php //ICB0"
# Eval / classic loader stubs: "<?php //00447" followed by the loader check.
_CLASSIC = re.compile(rb"<\?php //\d+")
_LOADER_MARK = b"ionCube Loader"


def split_shebang(data: bytes) -> tuple[bytes, bytes]:
    """Return (shebang_line_including_newline, rest). shebang is b'' if absent."""
    if data.startswith(b"#!"):
        nl = data.find(b"\n")
        if nl != -1:
            return data[: nl + 1], data[nl + 1 :]
    return b"", data


def is_ioncube(header: bytes) -> bool:
    """True if the leading bytes of a file look like an ionCube-encoded PHP file."""
    _, body = split_shebang(header)
    if body.startswith(_ICB0):
        return True
    return _CLASSIC.match(body) is not None and _LOADER_MARK in body


def sniff_file(path: str) -> bool:
    with open(path, "rb") as f:
        return is_ioncube(f.read(SNIFF_BYTES))
