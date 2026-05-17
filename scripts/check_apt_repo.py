#!/usr/bin/env python3
"""Validate generated static APT repository metadata."""

from __future__ import annotations

import argparse
import bz2
import gzip
import hashlib
import sys
from pathlib import Path


REQUIRED_PACKAGE_FIELDS = {
    "Package",
    "Version",
    "Architecture",
    "Filename",
    "Size",
    "MD5sum",
    "SHA1",
    "SHA256",
}


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def parse_paragraphs(path: Path) -> list[dict[str, str]]:
    paragraphs: list[dict[str, str]] = []
    current: dict[str, str] = {}
    current_key: str | None = None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line:
            if current:
                paragraphs.append(current)
                current = {}
                current_key = None
            continue
        if raw_line[0] in " \t":
            if current_key is None:
                fail(f"{path} has a continuation line before any field")
            current[current_key] += "\n" + raw_line
            continue
        if ":" not in raw_line:
            fail(f"{path} has an invalid field line: {raw_line}")
        key, value = raw_line.split(":", 1)
        current[key] = value.lstrip()
        current_key = key

    if current:
        paragraphs.append(current)
    return paragraphs


def digest(path: Path, name: str) -> str:
    hash_obj = getattr(hashlib, name)()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            hash_obj.update(chunk)
    return hash_obj.hexdigest()


def verify_packages(root: Path) -> int:
    packages_path = root / "Packages"
    if not packages_path.is_file():
        fail("Packages is missing")

    paragraphs = parse_paragraphs(packages_path)
    if not paragraphs:
        fail("Packages has no package entries")

    for index, fields in enumerate(paragraphs, 1):
        missing = REQUIRED_PACKAGE_FIELDS - set(fields)
        if missing:
            fail(f"Packages entry {index} is missing: {', '.join(sorted(missing))}")

        deb_path = root / fields["Filename"]
        if not deb_path.is_file():
            fail(f"{fields['Package']} points to missing file: {fields['Filename']}")
        if str(deb_path.stat().st_size) != fields["Size"]:
            fail(f"{fields['Package']} has stale Size metadata")
        if digest(deb_path, "md5") != fields["MD5sum"]:
            fail(f"{fields['Package']} has stale MD5sum metadata")
        if digest(deb_path, "sha1") != fields["SHA1"]:
            fail(f"{fields['Package']} has stale SHA1 metadata")
        if digest(deb_path, "sha256") != fields["SHA256"]:
            fail(f"{fields['Package']} has stale SHA256 metadata")

    plain = packages_path.read_bytes()
    if (root / "Packages.gz").is_file():
        if gzip.decompress((root / "Packages.gz").read_bytes()) != plain:
            fail("Packages.gz does not match Packages")
    else:
        fail("Packages.gz is missing")

    if (root / "Packages.bz2").is_file():
        if bz2.decompress((root / "Packages.bz2").read_bytes()) != plain:
            fail("Packages.bz2 does not match Packages")
    else:
        fail("Packages.bz2 is missing")

    return len(paragraphs)


def verify_release(root: Path) -> None:
    release = root / "Release"
    if not release.is_file():
        fail("Release is missing")
    release_text = release.read_text(encoding="utf-8")
    for name in ("Packages", "Packages.gz", "Packages.bz2"):
        if name not in release_text:
            fail(f"Release does not reference {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Check generated APT repo metadata")
    parser.add_argument("--root", default=".", help="repository root")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    count = verify_packages(root)
    verify_release(root)
    print(f"Validated {count} package entries")


if __name__ == "__main__":
    main()
