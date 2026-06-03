#!/bin/bash
# SDL backend probe for Raspberry Pi 1B running Raspberry Pi OS Bookworm.
#
# MUST be run on real Pi 1B hardware with HDMI attached. The whole point of
# this probe is that pygame.display.init() can succeed on a driver that
# pygame.display.set_mode() then fails on -- a headless check gives false
# positives. We therefore set_mode + flip + sleep 1 so a human at the HDMI
# can confirm the screen actually painted purple.
#
# Tries drivers in order: fbcon, kmsdrm, x11, dummy.
# Logs the active /etc/default/keyboard for non-US install debug context.
# Checks /dev/dri/card0 presence before the kmsdrm attempt.
#
# Output of this script goes verbatim into docs/sdl-backend.md.

set -u

echo "=== hamclock-pi1 SDL backend probe ==="
echo "host:   $(uname -a)"
echo "date:   $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "python: $(python3 --version 2>&1)"
echo "pygame: $(python3 -c 'import pygame; print(pygame.version.ver)' 2>&1)"
echo "SDL:    $(python3 -c 'import pygame; print(pygame.get_sdl_version())' 2>&1)"
echo
echo "--- /etc/default/keyboard ---"
if [ -r /etc/default/keyboard ]; then
    cat /etc/default/keyboard
else
    echo "(not readable)"
fi
echo
echo "--- /dev/dri ---"
ls -la /dev/dri 2>&1 || echo "(no /dev/dri -- kmsdrm will not work)"
echo "--- /boot/config.txt vc4/gpu_mem lines ---"
grep -E '^(dtoverlay=vc4|gpu_mem)' /boot/config.txt 2>/dev/null \
    || grep -E '^(dtoverlay=vc4|gpu_mem)' /boot/firmware/config.txt 2>/dev/null \
    || echo "(no vc4/gpu_mem lines found)"
echo

python3 - <<'PY'
import os, time, pygame
for drv in ('fbcon', 'kmsdrm', 'x11', 'dummy'):
    os.environ['SDL_VIDEODRIVER'] = drv
    print('--- trying', drv, '---')
    try:
        pygame.display.init()
        scr = pygame.display.set_mode((1440, 900), pygame.FULLSCREEN)
        scr.fill((40, 20, 80))
        pygame.display.flip()
        time.sleep(1)
        print(drv, '-> OK driver=', pygame.display.get_driver())
    except Exception as e:
        print(drv, '-> FAIL', type(e).__name__, e)
    finally:
        try:
            pygame.display.quit()
        except Exception:
            pass
PY

echo
echo "=== probe complete ==="
