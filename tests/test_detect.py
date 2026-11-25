from ioncubefs.detect import is_ioncube, split_shebang

CLASSIC = (b"<?php //00447\n// 0 @Ioncube;\n//\n"
           b"if(extension_loaded('ionCube Loader')){die('x');}\n?>\nHR+c...")


def test_classic_header():
    assert is_ioncube(CLASSIC)


def test_shebang_classic_header():
    assert is_ioncube(b"#!/usr/bin/php -q\n" + CLASSIC)


def test_icb0_header():
    assert is_ioncube(b"<?php //ICB0 81:0 82:1a2b ?>\n....")


def test_plain_php_is_not_encoded():
    assert not is_ioncube(b"<?php\necho 'hello';\n")
    assert not is_ioncube(b"<?php // 2024 comment mentioning ionCube Loader\n")
    assert not is_ioncube(b"")


def test_split_shebang():
    assert split_shebang(b"#!/x\nrest") == (b"#!/x\n", b"rest")
    assert split_shebang(b"<?php") == (b"", b"<?php")


def test_demo_file(demo_encoded):
    with open(demo_encoded, "rb") as f:
        assert is_ioncube(f.read(4096))
