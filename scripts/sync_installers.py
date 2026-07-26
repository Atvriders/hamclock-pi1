#!/usr/bin/env python3
"""Regenerate the embedded-source heredocs in offline-install.sh.

offline-install.sh is a single curl-pipeable script that carries the entire
application inline, as five `sudo tee "$INSTALL_DIR/<name>" << 'DELIM'`
heredocs.  Those copies drift silently: the shipped client called
`data.image_fetched_at` against an embedded `hamclock_data.py` that predated
the attribute, so the SDO and PROPAGATION panels were permanently blank on
every installer-built Pi while every test stayed green (the mirror tests
compared the two *installers* to each other, never to the repo).

This script is the fix for that class of bug.  It rewrites each heredoc body
from the corresponding repo file, then mirrors the result to the public
download path.  `--check` does the same computation without writing and exits
non-zero on drift, so CI/pytest can fail the build instead of shipping stale
bytes.

Usage::

    python3 scripts/sync_installers.py           # rewrite in place + mirror
    python3 scripts/sync_installers.py --check    # exit 1 if out of date
    python3 scripts/sync_installers.py --no-mirror

Design notes (deliberate, do not "simplify" away):

* Blocks are located by an **opener regex**, never by line number -- line
  numbers move every time any embedded file changes length.
* Two families of block are managed: the files written into ``$INSTALL_DIR``
  (``BLOCKS``) and the systemd units written into ``/etc/systemd/system``
  (``UNIT_BLOCKS``).  The unit opener requires a **quoted destination path**,
  which is what distinguishes a unit synced from ``systemd/`` from the two
  mode-templated units the installer composes itself (``hamclock-lite.service``
  and ``hamclock-kiosk.service`` interpolate ``$SERVICE_USER`` and are written
  with an unquoted path and an unquoted ``EOF`` on purpose).
* The delimiter must be quoted in the installer (``<< 'DELIM'``).  An unquoted
  heredoc would expand ``$`` and backticks inside Python source.  Note that
  ``hamclock-update.sh`` contains its own ``<<EOF`` and
  ``hamclock-apply-settings.sh`` contains a ``<<'PY'``, so their delimiters here
  must be neither -- ``read_source`` refuses the collision rather than emitting
  an installer that terminates a heredoc early and pipes source into bash.
* The version is stamped by treating repo ``VERSION`` as just another embedded
  source.  The single-file installer runs where there is no git, so the version
  has to live in the installer text; making it a block means ``--check`` fails
  the build the moment the two disagree.
* After the mirror is written, the sidecar update manifest is regenerated **from
  the published bytes**.  Order matters: the digest has to be of exactly what is
  being served, or the Pi refuses the update (safe, but indistinguishable from a
  broken updater).
* We refuse to run -- rather than emit a broken installer -- when a source file
  contains a line equal to its delimiter, contains CRLF, lacks a trailing
  newline, or is not valid UTF-8.  Any of those silently truncates or corrupts
  the heredoc.
* Every ``$INSTALL_DIR`` heredoc found in the installer must be listed in
  ``BLOCKS``.  A sixth one added by hand fails the build until it is declared,
  which is what stops this drifting again.
* Writes are atomic (temp file in the same directory, ``fsync``, ``os.replace``,
  directory ``fsync``) and preserve the original file mode -- the installer is
  chmod +x and is served straight off a web root.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INSTALLER = REPO / "offline-install.sh"
MIRROR = Path("/home/kasm-user/hamclock-reborn/public/downloads/pi1-install.sh")
MANIFEST_SCRIPT = Path(__file__).resolve().parent / "make_version_manifest.py"

#: Every file the installer writes into $INSTALL_DIR, mapped to the repo file
#: that is the single source of truth for it.  Keys are the basenames used in
#: the `sudo tee "$INSTALL_DIR/<key>"` line; values are repo-relative paths.
#:
#: The two shell scripts are executed BY ROOT on the target (systemd starts
#: them), so a stale embedded copy is not a cosmetic bug the way a stale panel
#: would be -- it is the wrong privileged code running.  VERSION is here so the
#: stamp the update check reads cannot drift from the repo.
BLOCKS = {
    "server.py": "server.py",
    "index.html": "index.html",
    "hamclock_data.py": "hamclock_data.py",
    "hamclock_pygame.py": "hamclock_pygame.py",
    "hamclock_tkinter.py": "hamclock_tkinter.py",
    "hamclock-update.sh": "scripts/hamclock-update.sh",
    "hamclock-apply-settings.sh": "scripts/hamclock-apply-settings.sh",
    "VERSION": "VERSION",
}

#: Units the installer drops verbatim into /etc/systemd/system, mapped to the
#: repo file that owns them.  hamclock-lite.service and hamclock-kiosk.service
#: are deliberately NOT here: the installer composes them per kiosk mode with
#: $SERVICE_USER interpolated, so there is no static file to sync them from.
UNIT_BLOCKS = {
    "hamclock-update.path": "systemd/hamclock-update.path",
    "hamclock-update.service": "systemd/hamclock-update.service",
    "hamclock-update-check.timer": "systemd/hamclock-update-check.timer",
    "hamclock-update-check.service": "systemd/hamclock-update-check.service",
    "hamclock-apply-settings.path": "systemd/hamclock-apply-settings.path",
    "hamclock-apply-settings.service": "systemd/hamclock-apply-settings.service",
    "hamclock-apply-settings-boot.service":
        "systemd/hamclock-apply-settings-boot.service",
}

#: `sudo tee "$INSTALL_DIR/server.py" > /dev/null << 'SERVEREOF'`
#: Tolerant about whitespace and the redirect spelling, strict about the
#: quoting of the delimiter (checked separately so we can explain the failure).
OPENER_RE = re.compile(
    r"^sudo tee \"\$INSTALL_DIR/(?P<dest>[^\"]+)\"\s*>\s*/dev/null\s*<<\s*"
    r"(?P<q1>['\"]?)(?P<delim>[A-Za-z_][A-Za-z0-9_]*)(?P<q2>['\"]?)\s*$"
)

#: `sudo tee "/etc/systemd/system/hamclock-update.path" > /dev/null << 'DELIM'`
#: The QUOTED destination path is load-bearing: it is what separates a unit
#: copied verbatim from systemd/ from the two units the installer templates
#: itself, which are written as `sudo tee /etc/systemd/system/... <<EOF`.
UNIT_OPENER_RE = re.compile(
    r"^sudo tee \"/etc/systemd/system/(?P<dest>[^\"]+)\"\s*>\s*/dev/null\s*<<\s*"
    r"(?P<q1>['\"]?)(?P<delim>[A-Za-z_][A-Za-z0-9_]*)(?P<q2>['\"]?)\s*$"
)


class SyncError(RuntimeError):
    """Refusal: regenerating would produce a broken installer."""


class Block:
    """One located heredoc: opener line, body slice, closer line."""

    __slots__ = ("dest", "delim", "open_idx", "close_idx")

    def __init__(self, dest, delim, open_idx, close_idx):
        self.dest = dest
        self.delim = delim
        self.open_idx = open_idx      # index of the `sudo tee ...` line
        self.close_idx = close_idx    # index of the lone delimiter line

    def __repr__(self):  # pragma: no cover - debugging aid
        return "Block(%r, %r, %d, %d)" % (
            self.dest, self.delim, self.open_idx, self.close_idx)


def find_blocks(text, opener=OPENER_RE):
    """Locate every heredoc matching *opener* in *text*, in file order.

    Defaults to the `$INSTALL_DIR` family.  Raises SyncError if a heredoc is
    never closed or uses an unquoted delimiter.
    """
    lines = text.split("\n")
    blocks = []
    i = 0
    n = len(lines)
    while i < n:
        m = opener.match(lines[i])
        if not m:
            i += 1
            continue
        dest = m.group("dest")
        delim = m.group("delim")
        if not m.group("q1") or m.group("q1") != m.group("q2"):
            raise SyncError(
                "line %d: heredoc for %s uses an unquoted delimiter %s; the "
                "delimiter must be quoted or the shell expands $ and ` inside "
                "the embedded source" % (i + 1, dest, delim)
            )
        close_idx = None
        for j in range(i + 1, n):
            if lines[j] == delim:
                close_idx = j
                break
        if close_idx is None:
            raise SyncError(
                "line %d: heredoc %s for %s is never closed" % (i + 1, delim, dest)
            )
        blocks.append(Block(dest, delim, i, close_idx))
        i = close_idx + 1
    return blocks


def check_coverage(blocks, table=None, where="$INSTALL_DIR", name="BLOCKS"):
    """Every located heredoc must be declared in *table*, exactly once."""
    if table is None:
        table = BLOCKS
    seen = {}
    for b in blocks:
        seen.setdefault(b.dest, []).append(b)
    unknown = sorted(d for d in seen if d not in table)
    if unknown:
        raise SyncError(
            "offline-install.sh writes %s into %s but scripts/"
            "sync_installers.py does not know how to regenerate %s. Add it to "
            "%s (or the installer will ship a stale hand-edited copy)."
            % (", ".join(unknown), where,
               "them" if len(unknown) > 1 else "it", name)
        )
    missing = sorted(d for d in table if d not in seen)
    if missing:
        raise SyncError(
            "offline-install.sh no longer contains a %s heredoc for "
            "%s" % (where, ", ".join(missing))
        )
    dupes = sorted(d for d, v in seen.items() if len(v) > 1)
    if dupes:
        raise SyncError(
            "offline-install.sh contains more than one %s heredoc "
            "for %s; refusing to guess which one to update" % (where, ", ".join(dupes))
        )


def read_source(path, delim):
    """Read a repo source file, refusing anything that would break a heredoc.

    Returns the decoded text (always ending in a newline).
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SyncError("cannot read %s: %s" % (path, exc))
    if not raw:
        raise SyncError("%s is empty" % path)
    if b"\r" in raw:
        raise SyncError(
            "%s contains CR bytes (CRLF line endings). The heredoc would carry "
            "them to the Pi and `python3 server.py` would fail on the shebang."
            % path
        )
    if not raw.endswith(b"\n"):
        raise SyncError(
            "%s has no trailing newline; the heredoc delimiter would be "
            "appended to its last line and the block would never terminate."
            % path
        )
    if b"\x00" in raw:
        raise SyncError("%s contains NUL bytes" % path)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SyncError("%s is not valid UTF-8: %s" % (path, exc))
    for lineno, line in enumerate(text.split("\n"), 1):
        if line == delim:
            raise SyncError(
                "%s:%d is exactly %r, which is the heredoc delimiter; bash "
                "would end the block there and pipe the rest of the file into "
                "the shell. Rename the delimiter in offline-install.sh."
                % (path, lineno, delim)
            )
    return text


def find_unit_blocks(text):
    """Locate every `/etc/systemd/system` heredoc synced from systemd/."""
    return find_blocks(text, UNIT_OPENER_RE)


def managed_blocks(installer_text):
    """[(block, repo-relative source)] for every heredoc this script owns.

    Both families are located and coverage-checked, then merged into one
    file-ordered list so a single splice can rewrite them all.
    """
    app = find_blocks(installer_text)
    check_coverage(app, BLOCKS, "$INSTALL_DIR", "BLOCKS")
    units = find_unit_blocks(installer_text)
    check_coverage(units, UNIT_BLOCKS, "/etc/systemd/system", "UNIT_BLOCKS")

    pairs = ([(b, BLOCKS[b.dest]) for b in app]
             + [(b, UNIT_BLOCKS[b.dest]) for b in units])
    pairs.sort(key=lambda p: p[0].open_idx)

    # The two families are located by two independent scans over the same text,
    # so in principle a line inside one block could look like the other's
    # opener.  Splicing overlapping ranges would silently corrupt the installer,
    # so refuse rather than guess.
    last = -1
    for b, _ in pairs:
        if b.open_idx <= last:
            raise SyncError(
                "heredoc for %s at line %d overlaps the previous block; the "
                "installer contains a line that looks like a heredoc opener "
                "inside an embedded source" % (b.dest, b.open_idx + 1)
            )
        last = b.close_idx
    return pairs


def render(installer_text, repo=REPO):
    """Return *installer_text* with every declared heredoc body replaced."""
    lines = installer_text.split("\n")
    out = []
    prev = 0
    for b, rel in managed_blocks(installer_text):
        src = read_source(repo / rel, b.delim)
        # `src` always ends with "\n", so split("\n") gives a trailing "" that
        # would become a spurious blank line before the delimiter.
        body = src.split("\n")[:-1]
        out.extend(lines[prev:b.open_idx + 1])
        out.extend(body)
        prev = b.close_idx
    out.extend(lines[prev:])
    return "\n".join(out)


def atomic_write(path, data, mode=None):
    """Write *data* (bytes) to *path* atomically, preserving/settings mode."""
    path = Path(path)
    if mode is None:
        try:
            mode = os.stat(path).st_mode & 0o7777
        except OSError:
            mode = 0o644
    directory = path.parent
    fd, tmp = tempfile.mkstemp(dir=str(directory), prefix="." + path.name + ".")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    # The rootfs on the Pi is mounted commit=60; fsync the directory so the
    # rename is durable. Harmless (and cheap) on the dev box.
    try:
        dfd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dfd)
    except OSError:
        pass
    finally:
        os.close(dfd)


def manifest_module(path=MANIFEST_SCRIPT):
    """Load scripts/make_version_manifest.py by path.

    Imported rather than shelled out to so the manifest and this script cannot
    disagree about what "the published bytes" means, and by path because
    scripts/ is not importable when this module is itself loaded by path (which
    is how the tests load it).
    """
    spec = importlib.util.spec_from_file_location(
        "hamclock_make_version_manifest", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def manifest_path_for(installer_path):
    """pi1-install.sh -> pi1-install.version.json, beside it."""
    installer_path = Path(installer_path)
    return installer_path.with_name(installer_path.stem + ".version.json")


def sync_manifest(published, published_bytes, check=False, log=print,
                  repo=REPO):
    """Regenerate the sidecar manifest for *published*. Returns drift notes.

    Called only AFTER the published file holds *published_bytes*: the whole
    point of the manifest is that its digest is of the exact bytes being
    served.  A manifest that disagrees makes every Pi refuse the update — the
    safe failure, but one that reads as a broken updater.
    """
    published = Path(published)
    manifest = manifest_path_for(published)
    mv = manifest_module()

    want_sha = hashlib.sha256(published_bytes).hexdigest()
    want_version = mv.read_version(str(repo / "VERSION"))

    have = None
    if manifest.exists():
        try:
            have = json.loads(manifest.read_text())
        except Exception as exc:
            log("%s: unreadable (%s); regenerating" % (manifest, exc))

    if (isinstance(have, dict)
            and have.get("sha256") == want_sha
            and have.get("version") == want_version):
        return []

    drift = ["%s: does not describe the published installer" % manifest]
    if not check:
        if not published.exists():
            raise SyncError("cannot write %s: %s does not exist"
                            % (manifest, published))
        if published.read_bytes() != published_bytes:
            raise SyncError(
                "refusing to write %s: %s does not hold the bytes being "
                "hashed" % (manifest, published))
        rc = mv.main([str(published)])
        if rc != 0:
            raise SyncError("make_version_manifest failed for %s" % published)
        log("wrote %s" % manifest)
    return drift


def sync(installer=INSTALLER, mirror=MIRROR, repo=REPO, check=False,
         do_mirror=True, log=print):
    """Regenerate *installer* (and the mirror). Returns a list of drift notes.

    In ``check`` mode nothing is written and the returned list is non-empty
    when the on-disk files do not match what would be generated.
    """
    installer = Path(installer)
    current = installer.read_bytes()
    new_text = render(current.decode("utf-8"), repo=repo)
    new = new_text.encode("utf-8")

    drift = []
    if new != current:
        drift.append("%s: embedded sources are stale" % installer)
        if not check:
            atomic_write(installer, new)
            log("updated %s" % installer)
    if do_mirror and mirror is not None:
        mirror = Path(mirror)
        if not mirror.exists():
            drift.append("%s: missing" % mirror)
            if not check:
                atomic_write(mirror, new, mode=os.stat(installer).st_mode & 0o7777)
                log("created %s" % mirror)
        elif mirror.read_bytes() != new:
            drift.append("%s: has drifted from offline-install.sh" % mirror)
            if not check:
                atomic_write(mirror, new)
                log("mirrored to %s" % mirror)
        # Strictly after the mirror is on disk: the manifest is the integrity
        # anchor for a script the Pi runs as root, and it must describe the
        # bytes actually being served, not the ones we intended to serve.
        drift.extend(sync_manifest(mirror, new, check=check, log=log, repo=repo))
    if not drift:
        log("installers already up to date")
    return drift


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="report drift and exit non-zero; write nothing")
    ap.add_argument("--no-mirror", action="store_true",
                    help="skip the hamclock-reborn public download copy")
    args = ap.parse_args(argv)
    try:
        drift = sync(check=args.check, do_mirror=not args.no_mirror)
    except SyncError as exc:
        sys.stderr.write("sync_installers: REFUSING: %s\n" % exc)
        return 2
    if args.check and drift:
        sys.stderr.write(
            "sync_installers: out of date:\n  " + "\n  ".join(drift) +
            "\nRun: python3 scripts/sync_installers.py\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
