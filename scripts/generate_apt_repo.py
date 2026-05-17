#!/usr/bin/env python3
"""Generate a static Cydia/APT repository index on macOS.

This intentionally avoids dpkg-scanpackages so the repo can be maintained on a
stock macOS machine with only Python 3 plus the system gzip/bzip2 libraries.
"""

from __future__ import annotations

import argparse
import bz2
import email.utils
import gzip
import hashlib
import io
import lzma
import os
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path


LEGACY_BASES = (
    "http://mdallak.github.io",
    "https://mdallak.github.io",
)

PACKAGE_DEPICTION_OVERRIDES = {
    "com.repo.xarold.com.cydown": "/depic/index.html?p=CyDown606",
}

GENERATED_FIELDS = {
    "filename",
    "size",
    "md5sum",
    "sha1",
    "sha256",
    "sha512",
}

INSERT_AFTER = (
    "depends",
    "pre-depends",
    "conflicts",
    "replaces",
    "provides",
    "architecture",
)


@dataclass
class Field:
    name: str
    value: str


@dataclass
class PackageEntry:
    fields: list[Field]
    deb_path: Path
    filename: str
    size: int
    md5: str
    sha1: str
    sha256: str


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def read_ar_member(path: Path, names: tuple[str, ...]) -> bytes:
    """Return the first matching member from a Debian ar archive."""
    data = path.read_bytes()
    if not data.startswith(b"!<arch>\n"):
        fail(f"{path} is not an ar-format .deb")

    offset = 8
    while offset + 60 <= len(data):
        header = data[offset : offset + 60]
        raw_name = header[:16].decode("utf-8", "replace").strip()
        name = raw_name.rstrip("/")
        try:
            size = int(header[48:58].decode("ascii").strip())
        except ValueError as exc:
            raise ValueError(f"invalid ar member size in {path}") from exc

        body_start = offset + 60
        body_end = body_start + size
        body = data[body_start:body_end]
        if name in names or raw_name in names:
            return body

        offset = body_end + (size % 2)

    fail(f"{path} does not contain any of: {', '.join(names)}")


def control_tar_bytes(path: Path) -> bytes:
    return read_ar_member(
        path,
        (
            "control.tar",
            "control.tar.gz",
            "control.tar.xz",
            "control.tar.bz2",
            "control.tar.zst",
        ),
    )


def open_control_tar(path: Path) -> tarfile.TarFile:
    payload = control_tar_bytes(path)
    if payload.startswith(b"\x1f\x8b"):
        payload = gzip.decompress(payload)
    elif payload.startswith(b"BZh"):
        payload = bz2.decompress(payload)
    elif payload.startswith(b"\xfd7zXZ\x00"):
        payload = lzma.decompress(payload)
    elif payload.startswith(b"(\xb5/\xfd"):
        fail(f"{path} uses zstd-compressed control.tar; install dpkg or repack it first")

    return tarfile.open(fileobj=io.BytesIO(payload), mode="r:")


def read_control(path: Path) -> str:
    with open_control_tar(path) as tar:
        for member in tar.getmembers():
            if member.name in {"control", "./control"}:
                extracted = tar.extractfile(member)
                if extracted is None:
                    break
                return extracted.read().decode("utf-8", "replace").strip() + "\n"

    fail(f"{path} does not contain a DEBIAN/control file")


def parse_fields(control: str) -> list[Field]:
    fields: list[Field] = []
    current: Field | None = None

    for raw_line in control.splitlines():
        if not raw_line:
            continue
        if raw_line[0] in " \t":
            if current is None:
                fail("control file continuation line appears before any field")
            current.value += "\n" + raw_line
            continue
        if ":" not in raw_line:
            fail(f"invalid control line: {raw_line}")
        name, value = raw_line.split(":", 1)
        current = Field(name=name, value=value.lstrip())
        fields.append(current)

    return fields


def rewrite_legacy_urls(fields: list[Field], depiction_base: str | None) -> None:
    if not depiction_base:
        return
    clean_base = depiction_base.rstrip("/")
    for field in fields:
        if field.name.lower() != "depiction":
            continue
        for legacy_base in LEGACY_BASES:
            field.value = field.value.replace(legacy_base, clean_base)


def apply_depiction_overrides(fields: list[Field], depiction_base: str | None) -> None:
    if not depiction_base:
        return

    package = next((field.value for field in fields if field.name.lower() == "package"), None)
    if package not in PACKAGE_DEPICTION_OVERRIDES:
        return

    depiction_url = depiction_base.rstrip("/") + PACKAGE_DEPICTION_OVERRIDES[package]
    for field in fields:
        if field.name.lower() == "depiction":
            field.value = depiction_url
            return
    fields.append(Field("Depiction", depiction_url))


def checksums(path: Path) -> tuple[str, str, str]:
    md5 = hashlib.md5()
    sha1 = hashlib.sha1()
    sha256 = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            md5.update(chunk)
            sha1.update(chunk)
            sha256.update(chunk)
    return md5.hexdigest(), sha1.hexdigest(), sha256.hexdigest()


def build_entry(root: Path, deb_path: Path, depiction_base: str | None) -> PackageEntry:
    fields = parse_fields(read_control(deb_path))
    rewrite_legacy_urls(fields, depiction_base)
    apply_depiction_overrides(fields, depiction_base)

    fields = [field for field in fields if field.name.lower() not in GENERATED_FIELDS]
    rel_path = deb_path.relative_to(root).as_posix()
    size = deb_path.stat().st_size
    md5, sha1, sha256 = checksums(deb_path)

    return PackageEntry(
        fields=fields,
        deb_path=deb_path,
        filename=rel_path,
        size=size,
        md5=md5,
        sha1=sha1,
        sha256=sha256,
    )


def sort_key(entry: PackageEntry) -> tuple[str, str]:
    values = {field.name.lower(): field.value for field in entry.fields}
    return (values.get("package", entry.filename).lower(), values.get("version", ""))


def render_entry(entry: PackageEntry) -> str:
    output_fields: list[Field] = []
    inserted = False

    for index, field in enumerate(entry.fields):
        output_fields.append(field)
        lower_names_so_far = [item.name.lower() for item in entry.fields[: index + 1]]
        later_names = [item.name.lower() for item in entry.fields[index + 1 :]]
        should_insert_here = any(
            anchor in lower_names_so_far and anchor not in later_names for anchor in INSERT_AFTER
        )
        if should_insert_here and not inserted:
            output_fields.extend(
                [
                    Field("Filename", entry.filename),
                    Field("Size", str(entry.size)),
                    Field("MD5sum", entry.md5),
                    Field("SHA1", entry.sha1),
                    Field("SHA256", entry.sha256),
                ]
            )
            inserted = True

    if not inserted:
        output_fields.extend(
            [
                Field("Filename", entry.filename),
                Field("Size", str(entry.size)),
                Field("MD5sum", entry.md5),
                Field("SHA1", entry.sha1),
                Field("SHA256", entry.sha256),
            ]
        )

    return "".join(f"{field.name}: {field.value}\n" for field in output_fields).rstrip() + "\n"


def write_packages(root: Path, entries: list[PackageEntry]) -> Path:
    packages_path = root / "Packages"
    content = "\n".join(render_entry(entry) for entry in entries)
    packages_path.write_text(content, encoding="utf-8", newline="\n")

    with packages_path.open("rb") as src:
        with (root / "Packages.gz").open("wb") as raw_dst:
            with gzip.GzipFile(fileobj=raw_dst, mode="wb", compresslevel=9, mtime=0) as dst:
                dst.write(src.read())

    with packages_path.open("rb") as src:
        (root / "Packages.bz2").write_bytes(bz2.compress(src.read(), compresslevel=9))

    return packages_path


def release_checksum_block(algorithm: str, paths: list[Path], root: Path) -> str:
    hash_names = {
        "MD5Sum": "md5",
        "SHA1": "sha1",
        "SHA256": "sha256",
    }
    hash_factory = getattr(hashlib, hash_names[algorithm])
    lines = [f"{algorithm}:"]
    for path in paths:
        digest = hash_factory(path.read_bytes()).hexdigest()
        lines.append(f" {digest} {path.stat().st_size:16d} {path.relative_to(root).as_posix()}")
    return "\n".join(lines)


def write_release(root: Path, args: argparse.Namespace) -> None:
    indexed_paths = [root / "Packages", root / "Packages.gz", root / "Packages.bz2"]
    header = [
        f"Origin: {args.origin}",
        f"Label: {args.label}",
        f"Suite: {args.suite}",
        f"Version: {args.version}",
        f"Codename: {args.codename}",
        f"Architectures: {args.architectures}",
        f"Components: {args.components}",
        f"Description: {args.description}",
        f"Date: {email.utils.formatdate(usegmt=True)}",
        release_checksum_block("MD5Sum", indexed_paths, root),
        release_checksum_block("SHA1", indexed_paths, root),
        release_checksum_block("SHA256", indexed_paths, root),
    ]
    (root / "Release").write_text("\n".join(header) + "\n", encoding="utf-8", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Packages and Release for a static APT repo")
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument("--deb-dir", default="debs", help="directory containing .deb files")
    parser.add_argument("--depiction-base", default="https://ruabbit.github.io", help="base URL used to rewrite legacy mdallak depiction URLs; pass an empty string to disable")
    parser.add_argument("--origin", default="Ruabbit")
    parser.add_argument("--label", default="Ruabbit")
    parser.add_argument("--suite", default="stable")
    parser.add_argument("--version", default="1.0")
    parser.add_argument("--codename", default="ios")
    parser.add_argument("--architectures", default="iphoneos-arm")
    parser.add_argument("--components", default="main")
    parser.add_argument("--description", default="Ruabbit Cydia/APT repository")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    deb_dir = root / args.deb_dir
    if not deb_dir.is_dir():
        fail(f"{deb_dir} does not exist")

    deb_paths = sorted(deb_dir.glob("*.deb"), key=lambda path: path.name.lower())
    if not deb_paths:
        fail(f"no .deb files found in {deb_dir}")

    depiction_base = args.depiction_base or None
    entries = [build_entry(root, path, depiction_base) for path in deb_paths]
    entries.sort(key=sort_key)

    write_packages(root, entries)
    write_release(root, args)
    print(f"Generated {len(entries)} package entries in {root / 'Packages'}")


if __name__ == "__main__":
    main()
