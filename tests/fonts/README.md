# Test fonts

`FreeMonoBold.ttf` is the face the Pi actually renders: `SysFont('monospace',
bold=True)` resolves through pygame's alias table to GNU FreeFont, which
Debian's `python3-pygame` pulls in as `fonts-freefont-ttf`. A desktop without
that package resolves the same call to DejaVu Sans Mono instead, whose faces
are 14-19% taller — so tests measured there describe a display nobody owns.
`tests/conftest.py` points `HAMCLOCK_FONT` here so every rendering test uses
the Pi's face.

Copied unmodified from Debian's `fonts-freefont-ttf` 20211204+svn4273-2.
GNU FreeFont is GPL-3+ with the Special Font Exception; see
`COPYING.FreeFont`.
