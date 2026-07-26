"""Drift guard: offline-install.sh must embed the CURRENT repo sources.

The installers carry the whole application inline as `sudo tee
"$INSTALL_DIR/<name>" << 'DELIM'` heredocs. Those copies drifted badly and
silently: the embedded hamclock_pygame.py called `data.image_fetched_at`
against an embedded hamclock_data.py that predated the attribute, so on every
installer-built Pi the SDO panel and the whole PROPAGATION panel raised
AttributeError inside `except Exception: pass` and stayed permanently blank.

Nothing caught it, because the existing mirror tests compare the two
*installers* to each other and never to the repo. These tests close that hole
three ways:

  * every heredoc body is byte-for-byte the repo file it claims to be;
  * an unlisted sixth `$INSTALL_DIR` heredoc fails the build (so hand-adding a
    file without teaching scripts/sync_installers.py about it cannot ship);
  * an AST check that every `data.<attr>` the embedded clients touch actually
    exists on the embedded HamClockData -- a direct pin for the original bug
    that survives future hand-edits of the installer.
"""
import ast
import importlib.util
import os
import py_compile
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
OFFLINE = REPO / "offline-install.sh"
MIRROR = Path("/home/kasm-user/hamclock-reborn/public/downloads/pi1-install.sh")
SYNC = REPO / "scripts" / "sync_installers.py"


def _load_sync():
    spec = importlib.util.spec_from_file_location("hamclock_sync_installers", SYNC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


si = _load_sync()


def _blocks(text=None):
    """{dest: body_text} for every $INSTALL_DIR heredoc in the installer."""
    if text is None:
        text = OFFLINE.read_text()
    lines = text.split("\n")
    out = {}
    for b in si.find_blocks(text):
        out[b.dest] = "\n".join(lines[b.open_idx + 1:b.close_idx]) + "\n"
    return out


# --------------------------------------------------------------------------
# 1. The heredocs are the repo files.
# --------------------------------------------------------------------------

def test_sync_script_exists_and_is_executable():
    assert SYNC.exists(), f"missing: {SYNC}"
    assert SYNC.stat().st_mode & 0o111, "scripts/sync_installers.py must be chmod +x"


@pytest.mark.parametrize("dest", sorted(si.BLOCKS))
def test_installer_embeds_current_sources(dest):
    body = _blocks().get(dest)
    assert body is not None, f"offline-install.sh has no heredoc for {dest}"
    src = (REPO / si.BLOCKS[dest]).read_text()
    assert body == src, (
        f"offline-install.sh's embedded {dest} has drifted from the repo copy "
        f"(embedded {len(body.splitlines())} lines vs repo "
        f"{len(src.splitlines())} lines). Run: python3 scripts/sync_installers.py"
    )


def test_sync_installers_check_mode_is_clean():
    """The CI gate itself: --check must exit 0 on a synced tree."""
    r = subprocess.run(
        [sys.executable, str(SYNC), "--check"],
        capture_output=True, text=True, cwd=str(REPO),
    )
    assert r.returncode == 0, (
        "scripts/sync_installers.py --check reports drift:\n"
        + r.stdout + r.stderr
    )


def test_mirror_embeds_current_sources():
    """The public download copy carries the same fresh sources."""
    assert MIRROR.exists(), f"mirror missing: {MIRROR}"
    mirror_blocks = _blocks(MIRROR.read_text())
    for dest, rel in si.BLOCKS.items():
        assert mirror_blocks.get(dest) == (REPO / rel).read_text(), (
            f"mirror's embedded {dest} has drifted"
        )


def test_installer_stays_executable():
    assert OFFLINE.stat().st_mode & 0o111, (
        "offline-install.sh lost its +x bit (atomic rewrite must preserve mode)"
    )


# --------------------------------------------------------------------------
# 2. Coverage: an unlisted heredoc must fail the build.
# --------------------------------------------------------------------------

def test_installer_heredoc_set_matches_block_table():
    found = sorted(b.dest for b in si.find_blocks(OFFLINE.read_text()))
    assert found == sorted(si.BLOCKS), (
        "offline-install.sh's $INSTALL_DIR heredocs %r do not match "
        "sync_installers.BLOCKS %r" % (found, sorted(si.BLOCKS))
    )


def test_every_heredoc_written_to_install_dir_is_covered():
    """A sixth heredoc fails the build until it is listed in BLOCKS."""
    text = OFFLINE.read_text()
    injected = text.replace(
        'sudo tee "$INSTALL_DIR/server.py" > /dev/null << \'SERVEREOF\'',
        'sudo tee "$INSTALL_DIR/hamclock_extra.py" > /dev/null << \'EXTRAEOF\'\n'
        'print("hi")\n'
        'EXTRAEOF\n'
        'sudo tee "$INSTALL_DIR/server.py" > /dev/null << \'SERVEREOF\'',
        1,
    )
    assert injected != text, "injection point not found — update this test"
    blocks = si.find_blocks(injected)
    assert any(b.dest == "hamclock_extra.py" for b in blocks)
    with pytest.raises(si.SyncError) as exc:
        si.check_coverage(blocks)
    assert "hamclock_extra.py" in str(exc.value)


def test_missing_heredoc_is_reported():
    """Deleting a heredoc must also fail, not silently regenerate four files."""
    text = OFFLINE.read_text()
    lines = text.split("\n")
    blocks = si.find_blocks(text)
    tk = [b for b in blocks if b.dest == "hamclock_tkinter.py"][0]
    stripped = "\n".join(lines[:tk.open_idx] + lines[tk.close_idx + 1:])
    with pytest.raises(si.SyncError) as exc:
        si.check_coverage(si.find_blocks(stripped))
    assert "hamclock_tkinter.py" in str(exc.value)


def test_unquoted_delimiter_is_refused():
    """`<< DELIM` (unquoted) would let the shell expand $ inside Python."""
    bad = ('sudo tee "$INSTALL_DIR/server.py" > /dev/null << SERVEREOF\n'
           "x = 1\n"
           "SERVEREOF\n")
    with pytest.raises(si.SyncError) as exc:
        si.find_blocks(bad)
    assert "unquoted" in str(exc.value)


def test_unterminated_heredoc_is_refused():
    bad = ('sudo tee "$INSTALL_DIR/server.py" > /dev/null << \'SERVEREOF\'\n'
           "x = 1\n")
    with pytest.raises(si.SyncError):
        si.find_blocks(bad)


# --------------------------------------------------------------------------
# 3. Source-validation refusals (a broken heredoc is worse than a stale one).
# --------------------------------------------------------------------------

def _tmp_source(tmp_path, text, newline="\n"):
    p = tmp_path / "src.py"
    p.write_bytes(text.encode("utf-8") if newline == "\n"
                  else text.replace("\n", newline).encode("utf-8"))
    return p


def test_read_source_refuses_delimiter_collision(tmp_path):
    p = _tmp_source(tmp_path, "a = 1\nSERVEREOF\nb = 2\n")
    with pytest.raises(si.SyncError) as exc:
        si.read_source(p, "SERVEREOF")
    assert "delimiter" in str(exc.value)


def test_read_source_refuses_crlf(tmp_path):
    p = tmp_path / "crlf.py"
    p.write_bytes(b"a = 1\r\nb = 2\r\n")
    with pytest.raises(si.SyncError) as exc:
        si.read_source(p, "SERVEREOF")
    assert "CR" in str(exc.value)


def test_read_source_refuses_missing_trailing_newline(tmp_path):
    p = tmp_path / "nonl.py"
    p.write_bytes(b"a = 1\nb = 2")
    with pytest.raises(si.SyncError) as exc:
        si.read_source(p, "SERVEREOF")
    assert "trailing newline" in str(exc.value)


def test_read_source_accepts_a_clean_file(tmp_path):
    p = _tmp_source(tmp_path, "a = 1\n    SERVEREOF_NOT\n")
    assert si.read_source(p, "SERVEREOF") == "a = 1\n    SERVEREOF_NOT\n"


def test_sync_is_idempotent(tmp_path):
    """render() of an already-synced installer is a no-op (no blank-line creep)."""
    text = OFFLINE.read_text()
    once = si.render(text)
    assert once == text
    assert si.render(once) == once


# --------------------------------------------------------------------------
# 4. The embedded Python actually compiles, and the client/data contract holds.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("dest", sorted(d for d in si.BLOCKS if d.endswith(".py")))
def test_embedded_python_compiles(dest):
    body = _blocks()[dest]
    fd, path = tempfile.mkstemp(suffix=".py")
    os.close(fd)
    try:
        Path(path).write_text(body)
        py_compile.compile(path, cfile=path + "c", doraise=True)
    finally:
        for p in (path, path + "c"):
            if os.path.exists(p):
                os.unlink(p)


def _hamclockdata_attributes(src):
    """Names reachable as `<HamClockData instance>.<name>`."""
    tree = ast.parse(src)
    cls = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "HamClockData":
            cls = node
            break
    assert cls is not None, "embedded hamclock_data.py has no HamClockData class"

    attrs = set()
    # class-level constants + methods (direct children only, so method locals
    # never leak in and give a false pass)
    for st in cls.body:
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
            attrs.add(st.name)
        elif isinstance(st, ast.Assign):
            for t in st.targets:
                if isinstance(t, ast.Name):
                    attrs.add(t.id)
        elif isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
            attrs.add(st.target.id)
    # instance attributes: any `self.<x>` in a binding position
    for sub in ast.walk(cls):
        targets = []
        if isinstance(sub, ast.Assign):
            targets = list(sub.targets)
        elif isinstance(sub, (ast.AugAssign, ast.AnnAssign)):
            targets = [sub.target]
        elif isinstance(sub, ast.For):
            targets = [sub.target]
        elif isinstance(sub, ast.withitem) and sub.optional_vars is not None:
            targets = [sub.optional_vars]
        for t in targets:
            for nn in ast.walk(t):
                if (isinstance(nn, ast.Attribute)
                        and isinstance(nn.value, ast.Name)
                        and nn.value.id == "self"):
                    attrs.add(nn.attr)
    return attrs


def _data_attributes_used(src):
    """{attr: lineno} for `data.<attr>`, `self.data.<attr>` and
    `getattr(data, '<attr>', ...)` in a client module."""
    tree = ast.parse(src)
    used = {}

    def is_data(v):
        if isinstance(v, ast.Name) and v.id == "data":
            return True
        return (isinstance(v, ast.Attribute) and v.attr == "data"
                and isinstance(v.value, ast.Name) and v.value.id == "self")

    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and is_data(n.value):
            used.setdefault(n.attr, n.lineno)
        elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "getattr" and len(n.args) >= 2
                and is_data(n.args[0])
                and isinstance(n.args[1], ast.Constant)
                and isinstance(n.args[1].value, str)):
            # A guarded getattr does not crash, but a missing attribute still
            # means the feature it drives silently never runs -- which is
            # exactly how the SDO/PROPAGATION panels went dark.
            used.setdefault(n.args[1].value, n.lineno)
    return used


@pytest.mark.parametrize("client", ["hamclock_pygame.py", "hamclock_tkinter.py"])
def test_embedded_client_and_data_agree_on_attributes(client):
    blocks = _blocks()
    available = _hamclockdata_attributes(blocks["hamclock_data.py"])
    used = _data_attributes_used(blocks[client])
    assert used, f"AST walk found no data.<attr> uses in embedded {client}"
    missing = {k: v for k, v in used.items() if k not in available}
    assert not missing, (
        f"embedded {client} uses HamClockData attributes that the embedded "
        f"hamclock_data.py does not define: {sorted(missing)} "
        f"(client lines {sorted(missing.values())}). This is the exact shape "
        f"of the bug that left the SDO and PROPAGATION panels blank on every "
        f"installer-built Pi. Run: python3 scripts/sync_installers.py"
    )


def test_attribute_agreement_check_has_teeth():
    """Guard the guard: reproduce the original P0 in miniature and confirm
    the walker reports it (an always-green check is worse than none)."""
    stale_data = (
        "class HamClockData:\n"
        "    IMAGE_TIMEOUT = 20\n"
        "    def __init__(self):\n"
        "        self.images = {}\n"
        "        self.last_image_refresh = 0.0\n"
        "    def refresh_images(self):\n"
        "        image_fetched_at = {}\n"   # a local, NOT an attribute
        "        return image_fetched_at\n"
    )
    available = _hamclockdata_attributes(stale_data)
    assert {"images", "last_image_refresh", "refresh_images",
            "IMAGE_TIMEOUT"} <= available
    assert "image_fetched_at" not in available, \
        "method locals must not count as instance attributes"

    client = (
        "def draw(data, key):\n"
        "    ts = data.image_fetched_at.get(key, data.last_image_refresh)\n"
        "    fa = getattr(data, 'image_next_due', None)\n"
        "    return data.images.get(key), ts, fa\n"
    )
    used = _data_attributes_used(client)
    assert set(used) == {"image_fetched_at", "last_image_refresh",
                         "images", "image_next_due"}
    assert sorted(k for k in used if k not in available) == \
        ["image_fetched_at", "image_next_due"]


def test_attribute_walker_sees_the_real_embedded_client():
    """The real embedded client must exercise the attribute under test, or
    test_embedded_client_and_data_agree_on_attributes proves nothing."""
    used = _data_attributes_used(_blocks()["hamclock_pygame.py"])
    assert "image_fetched_at" in used, (
        "embedded hamclock_pygame.py no longer references image_fetched_at — "
        "the P0 pin has gone slack; retarget it at whatever replaced it"
    )


# --------------------------------------------------------------------------
# 5. The Tier 1/2 work actually reached the installer.
# --------------------------------------------------------------------------

def test_embedded_client_carries_muf_propagation_tab():
    client = _blocks()["hamclock_pygame.py"]
    assert "PROP_TABS" in client, "embedded client lost PROP_TABS"
    assert "'muf': 'muf-map'" in client, \
        "embedded client is missing the muf -> muf-map propagation tab"


def test_embedded_client_carries_per_key_image_state():
    client = _blocks()["hamclock_pygame.py"]
    data = _blocks()["hamclock_data.py"]
    assert "image_fetched_at" in data, \
        "embedded hamclock_data.py predates image_fetched_at (the P0)"
    assert "image_fetched_at" in client
    assert "image_next_due" in data and "IMAGE_RETRY_BACKOFF" in data, \
        "embedded hamclock_data.py is missing the per-key retry backoff"


def test_embedded_server_carries_tier1_and_tier2_work():
    server = _blocks()["server.py"]
    for token in ("def fetch_sdo", "HAMCLOCK_CACHE_DIR", "start_new_session",
                  "PHASE2_TIMEOUT_S"):
        assert token in server, f"embedded server.py is missing {token!r}"
