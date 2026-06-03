"""Phase 2 tests for the server-side MUF SVG -> PNG rasterizer.

These tests stub `subprocess.run` so the test suite never invokes cairosvg or
cpulimit. The contract under test is the wrapper around the subprocess —
argument list, timeout, error handling, and the CACHE wiring done by
fetch_muf() + the /api/muf-map handler.
"""
import subprocess
import sys
from unittest import mock

import pytest

import server


def test_rasterize_muf_symbol_exists():
    assert hasattr(server, "_rasterize_muf"), (
        "Phase 2 contract: server must export `_rasterize_muf(svg_bytes)`."
    )


def test_phase2_timeout_constant_exists():
    assert hasattr(server, "PHASE2_TIMEOUT_S"), (
        "Phase 2 contract: server must export PHASE2_TIMEOUT_S."
    )
    assert isinstance(server.PHASE2_TIMEOUT_S, int)
    assert server.PHASE2_TIMEOUT_S >= 45
