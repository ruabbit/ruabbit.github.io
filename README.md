# ruabbit.github.io

Static Cydia/APT repository hosted by GitHub Pages.

## Source URL

Add this repo in Cydia/Sileo/Zebra:

```text
https://ruabbit.github.io
```

## Updating package indexes on macOS

The original upstream scripts depended on Linux `dpkg-scanpackages`. This fork
uses a pure Python 3 generator that reads `.deb` control metadata directly and
generates:

- `Packages`
- `Packages.gz`
- `Packages.bz2`
- `Release`

Run:

```sh
./update.sh
```

`update.sh` regenerates metadata and then runs `scripts/check_apt_repo.py` to
verify required package fields, `.deb` sizes, checksums, and compressed index
files.

Useful options:

```sh
./update.sh --depiction-base https://ruabbit.github.io
./update.sh --origin Ruabbit --label Ruabbit
./update.sh --depiction-base ""
```

To regenerate without validation, run `./push.sh`. To validate an existing
checkout without rewriting metadata, run:

```sh
python3 scripts/check_apt_repo.py
```

The default rewrites legacy `mdallak.github.io` `Depiction:` URLs in the
generated `Packages` file to `https://ruabbit.github.io` without modifying the
original `.deb` files.

## Imported upstream

Content was imported from:

```text
https://github.com/ALYASI-2020/mdallak.github.io.git
```
