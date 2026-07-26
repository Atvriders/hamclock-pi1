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
* The delimiter must be quoted in the installer (``<< 'DELIM'``).  An unquoted
  heredoc would expand ``$`` and backticks inside Python source.
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
import os
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INSTALLER = REPO / "offline-install.sh"
MIRROR = Path("/home/kasm-user/hamclock-reborn/public/downloads/pi1-install.sh")

#: Every file the installer writes into $INSTALL_DIR, mapped to the repo file
#: that is the single source of truth for it.  Keys are the basenames used in
#: the `sudo tee "$INSTALL_DIR/<key>"` line; values are repo-relative paths.
BLOCKS = {
    "server.py": "server.py",
    "index.html": "index.html",
    "hamclock_data.py": "hamclock_data.py",
    "hamclock_pygame.py": "hamclock_pygame.py",
    "hamclock_tkinter.py": "hamclock_tkinter.py",
}

#: `sudo tee "$INSTALL_DIR/server.py" > /dev/null << 'SERVEREOF'`
#: Tolerant about whitespace and the redirect spelling, strict about the
#: quoting of the delimiter (checked separately so we can explain the failure).
OPENER_RE = re.compile(
    r"^sudo tee \"\$INSTALL_DIR/(?P<dest>[^\"]+)\"\s*>\s*/dev/null\s*<<\s*"
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


def find_blocks(text):
    """Locate every `$INSTALL_DIR` heredoc in *text*, in file order.

    Raises SyncError if a heredoc is never closed or uses an unquoted
    delimiter.
    """
    lines = text.split("\n")
    blocks = []
    i = 0
    n = len(lines)
    while i < n:
        m = OPENER_RE.match(lines[i])
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


def check_coverage(blocks):
    """Every located heredoc must be declared in BLOCKS, exactly once."""
    seen = {}
    for b in blocks:
        seen.setdefault(b.dest, []).append(b)
    unknown = sorted(d for d in seen if d not in BLOCKS)
    if unknown:
        raise SyncError(
            "offline-install.sh writes %s into $INSTALL_DIR but scripts/"
            "sync_installers.py does not know how to regenerate %s. Add it to "
            "BLOCKS (or the installer will ship a stale hand-edited copy)."
            % (", ".join(unknown), "them" if len(unknown) > 1 else "it")
        )
    missing = sorted(d for d in BLOCKS if d not in seen)
    if missing:
        raise SyncError(
            "offline-install.sh no longer contains a $INSTALL_DIR heredoc for "
            "%s" % ", ".join(missing)
        )
    dupes = sorted(d for d, v in seen.items() if len(v) > 1)
    if dupes:
        raise SyncError(
            "offline-install.sh contains more than one $INSTALL_DIR heredoc "
            "for %s; refusing to guess which one to update" % ", ".join(dupes)
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


def render(installer_text, repo=REPO):
    """Return *installer_text* with every declared heredoc body replaced."""
    blocks = find_blocks(installer_text)
    check_coverage(blocks)
    lines = installer_text.split("\n")
    out = []
    prev = 0
    for b in blocks:
        src = read_source(repo / BLOCKS[b.dest], b.delim)
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
