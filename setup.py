"""Check for the manually checked-out ioncube-decompiler backend.

All metadata is in pyproject.toml.  This file only verifies that
``third_party/ioncube-decompiler`` has been checked out before setuptools
reads it, and tells you how to get it if not.  It is a plain git checkout,
not a submodule, so this repository ignores it.
"""

import os

from setuptools import setup

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(HERE, "third_party", "ioncube-decompiler", "src", "ioncube_re")
CLONE = (
    "git clone -b missing-opcodes "
    "https://github.com/xrobau/ioncube-decompiler.git "
    "third_party/ioncube-decompiler"
)

if not os.path.isdir(BACKEND):
    raise SystemExit(
        "third_party/ioncube-decompiler is missing; check out the backend first:\n\n"
        f"    {CLONE}\n"
    )

setup()
