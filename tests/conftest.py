"""Shared pytest fixtures and environment setup for the hamclock-pi1 suite.

Sets SDL_VIDEODRIVER=dummy and HAMCLOCK_DEBUG=1 BEFORE pygame is imported
anywhere in the test session. Every later phase (perf harness, theme
pixel-sample, wizard --inject-events bench) assumes these are in place.
"""
import os
import tempfile

# Must run before any `import pygame` in any test module.
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("HAMCLOCK_DEBUG", "1")

# Tier 2.1: server.CACHE_DIR is read at import time and defaults to
# /var/cache/hamclock-lite. tests/test_muf_rasterize.py calls server.fetch_muf()
# directly, which now write-throughs the rendered PNG — point that at a temp
# dir so the suite never touches (or needs root for) the real cache.
os.environ.setdefault("HAMCLOCK_CACHE_DIR",
                      tempfile.mkdtemp(prefix="hamclock-cache-"))
