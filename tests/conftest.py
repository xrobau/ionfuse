import os
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
DEMO = os.path.join(HERE, "..", "third_party", "ioncube-decompiler", "demo", "demo-encoded.php")


@pytest.fixture
def demo_encoded():
    if not os.path.exists(DEMO):
        pytest.skip(
            "backend demo file not present (check out the backend: git clone -b "
            "missing-opcodes https://github.com/xrobau/ioncube-decompiler.git "
            "third_party/ioncube-decompiler)"
        )
    return os.path.abspath(DEMO)
