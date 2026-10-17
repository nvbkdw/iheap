"""Derive the pure-Python ``py3-none-any`` wheel from a maturin-built wheel.

The pure wheel serves interpreters that no native wheel fits (e.g. CPython
3.13t, PyPy); pip prefers any matching native wheel over it. It is the
platform wheel minus the compiled extension, retagged, with a fresh RECORD.

Usage: python scripts/make_pure_wheel.py <native.whl> <out-dir>
"""

import base64
import hashlib
import re
import sys
import zipfile
from pathlib import Path

EXTENSION = re.compile(r"^iheap/_iheap\..*(so|pyd)$")


def digest(data: bytes) -> str:
    raw = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def main(source: Path, out_dir: Path) -> Path:
    name, version = source.name.split("-")[:2]
    target = out_dir / f"{name}-{version}-py3-none-any.whl"
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as src, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as dst:
        files = {}
        for info in src.infolist():
            # Drop the extension, its Rust-dependency SBOM, and the old RECORD.
            if (
                EXTENSION.match(info.filename)
                or ".dist-info/sboms/" in info.filename
                or info.filename.endswith("/RECORD")
            ):
                continue
            data = src.read(info)
            if info.filename.endswith(".dist-info/WHEEL"):
                lines = [
                    line
                    for line in data.decode().splitlines()
                    if not line.startswith(("Tag:", "Root-Is-Purelib:"))
                ]
                lines += ["Root-Is-Purelib: true", "Tag: py3-none-any"]
                data = ("\n".join(lines) + "\n").encode()
            files[info.filename] = data
        dist_info = next(n.split("/")[0] for n in files if n.endswith(".dist-info/WHEEL"))
        record = f"{dist_info}/RECORD"
        rows = [f"{n},{digest(d)},{len(d)}" for n, d in files.items()] + [f"{record},,"]
        files[record] = ("\n".join(rows) + "\n").encode()
        for n, d in files.items():
            dst.writestr(n, d)
    return target


if __name__ == "__main__":
    print(main(Path(sys.argv[1]), Path(sys.argv[2])))
