"""Shared pytest fixtures and environment setup for the hamclock-pi1 suite.

Sets SDL_VIDEODRIVER=dummy and HAMCLOCK_DEBUG=1 BEFORE pygame is imported
anywhere in the test session. Every later phase (perf harness, theme
pixel-sample, wizard --inject-events bench) assumes these are in place.
"""
import os

# Must run before any `import pygame` in any test module.
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("HAMCLOCK_DEBUG", "1")
