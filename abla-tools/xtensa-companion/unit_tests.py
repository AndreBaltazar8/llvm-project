#!/usr/bin/env python3
"""Small packaging-only tests; never compile or run LLVM locally."""
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock
import build


class Packaging(unittest.TestCase):
    def test_ldd_paths_with_spaces(self):
        path = "/tmp/relocated Xtensa companion abc/lib/libLLVM.so.23.1"
        text = "\tlibLLVM.so.23.1 => " + path + " (0x00007f0123456000)\n"
        with mock.patch.object(build, "capture", return_value=text):
            self.assertEqual(build.linux_dependencies("llc"), {"libLLVM.so.23.1": path})

    def test_revision(self):
        self.assertEqual(build.tag("1"), "abla-xtensa-llvm-23.1.3-1")
        for value in ("", "0", "01", "1/../../x", "1;true", "1\n", "1234567"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build.tag(value)

    def test_deterministic_relocation(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "companion"
            (root / "bin").mkdir(parents=True)
            (root / "bin/llc").write_bytes(b"fake tool for packaging only\n")
            (root / "bin/llc").chmod(0o755)
            first, second = base / "first.tar.gz", base / "second.tar.gz"
            build.archive(root, first)
            build.archive(root, second)
            self.assertEqual(build.digest(first), build.digest(second))
            relocated = base / "relocated with spaces"
            relocated.mkdir()
            extracted = build.extract(first, relocated)
            self.assertEqual((extracted / "bin/llc").read_bytes(), (root / "bin/llc").read_bytes())
            self.assertEqual((extracted / "bin/llc").stat().st_mode & 0o777, 0o755)

    def test_unsafe_archives(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            for index, name in enumerate(("../escape", "/escape", "root/../../escape")):
                archive = base / f"bad-{index}.tar.gz"
                with tarfile.open(archive, "w:gz") as tar:
                    entry = tarfile.TarInfo(name)
                    entry.size = 1
                    tar.addfile(entry, io.BytesIO(b"x"))
                output = base / f"out-{index}"
                output.mkdir()
                with self.assertRaises(ValueError):
                    build.extract(archive, output)
            for index, kind in enumerate((tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE)):
                archive = base / f"link-{index}.tar.gz"
                with tarfile.open(archive, "w:gz") as tar:
                    entry = tarfile.TarInfo("root/escape")
                    entry.type = kind
                    entry.linkname = "../../escape"
                    tar.addfile(entry)
                output = base / f"link-out-{index}"
                output.mkdir()
                with self.assertRaises(ValueError):
                    build.extract(archive, output)


if __name__ == "__main__":
    unittest.main()
