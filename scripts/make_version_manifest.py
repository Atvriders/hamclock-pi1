#!/usr/bin/env python3
"""Generate the update manifest that sits beside the published installer.

The updater on a Pi fetches this file, compares its `version` against what is
installed, and — critically — refuses to run the downloaded installer unless it
hashes to `sha256`. So this manifest is the integrity anchor for a script that
executes as root, and the one rule that matters is:

    the sha256 MUST be of the exact bytes being published

If the manifest and the installer ever drift apart, the updater will correctly
refuse to install anything. That is the safe failure, but it looks like a broken
updater, so this script always derives the digest from the published file rather
than from anything upstream of it.

Usage:
    make_version_manifest.py <installer.sh> [-o <manifest.json>]

Defaults to writing "<installer>.version.json" next to the installer, which is
where the updater expects it (pi1-install.sh -> pi1-install.version.json).
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION_FILE = os.path.join(REPO, "VERSION")
DEFAULT_BASE_URL = "https://hamclock-reborn.org"

# Same shape the updater's sanitize_version() will accept. Keeping the two in
# agreement matters: a version this script happily writes but the Pi silently
# strips would make an available update look like it was already installed.
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+(\.[0-9]+)?([0-9A-Za-z.+-]*)$")


def read_version(path=VERSION_FILE):
    with open(path) as f:
        v = f.read().strip()
    if not VERSION_RE.match(v):
        raise SystemExit(
            f"VERSION contains {v!r}, which is not a version the Pi updater "
            f"will accept. Use MAJOR.MINOR.PATCH.")
    return v


def git_commit():
    """Short commit of the tree this was built from, or None outside git.

    The single-file installer is used precisely where git is unavailable, so
    this is provenance for a human reading the manifest — never something the
    update check depends on.
    """
    try:
        out = subprocess.run(["git", "-C", REPO, "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except Exception:
        return None


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build(installer, base_url=DEFAULT_BASE_URL, version_file=VERSION_FILE):
    if not os.path.isfile(installer):
        raise SystemExit(f"installer not found: {installer}")
    name = os.path.basename(installer)
    return {
        "version": read_version(version_file),
        "built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit": git_commit(),
        "installer": f"{base_url}/downloads/{name}",
        "sha256": sha256_of(installer),
        "size": os.path.getsize(installer),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("installer", help="the installer being published")
    ap.add_argument("-o", "--out", default=None,
                    help="manifest path (default: <installer>.version.json)")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--check", action="store_true",
                    help="verify an existing manifest matches the installer; "
                         "exit non-zero on drift, write nothing")
    args = ap.parse_args(argv)

    out = args.out or (os.path.splitext(args.installer)[0] + ".version.json")
    m = build(args.installer, args.base_url)

    if args.check:
        if not os.path.exists(out):
            print(f"missing manifest: {out}", file=sys.stderr)
            return 1
        try:
            have = json.load(open(out))
        except Exception as e:
            print(f"unreadable manifest {out}: {e}", file=sys.stderr)
            return 1
        drift = [k for k in ("version", "sha256") if have.get(k) != m[k]]
        if drift:
            # This is exactly the state that makes the Pi refuse to update, so
            # say which field moved rather than just failing.
            for k in drift:
                print(f"{out}: {k} is {have.get(k)!r}, installer says {m[k]!r}",
                      file=sys.stderr)
            return 1
        print(f"{out}: matches {os.path.basename(args.installer)}")
        return 0

    tmp = out + ".tmp"
    with open(tmp, "w") as f:
        json.dump(m, f, indent=2, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, out)
    print(f"{out}: v{m['version']} sha256={m['sha256'][:16]}... "
          f"({m['size']} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
