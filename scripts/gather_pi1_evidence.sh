#!/bin/bash
# gather_pi1_evidence.sh
#
# Produce the two deferred hardware deliverables that gate Phase 5:
#   - docs/sdl-backend.md (Phase 0): which SDL driver actually paints on
#     a real Pi 1B HDMI.
#   - docs/muf-source.md (Phase 2): median cairosvg render time for the
#     KC2G MUF SVG, and the chosen muf-subprocess-timeout-s.
#
# MUST be run on real Pi 1B hardware with HDMI attached -- the whole point
# is that pygame.display.init() can succeed on a driver that
# pygame.display.set_mode() then fails on, and that cairosvg perf on a Pi 1
# armv6 cannot be simulated on a workstation.
#
# Modes:
#   - Run inside a hamclock-pi1 clone -> writes docs/{sdl-backend,muf-source}.md.
#   - Run from anywhere else (e.g. curl-pipe'd into /tmp) -> prints the two
#     docs to stdout with clear dividers so the operator can copy-paste
#     them back into a clone.
#
# Final step: runs the gating tests so the operator sees pass/fail.

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Detect "running inside a hamclock-pi1 clone": both the pygame client and
# the docs/superpowers tree must exist next to us.
IN_REPO=0
if [ -f "$REPO_DIR/hamclock_pygame.py" ] && [ -d "$REPO_DIR/docs/superpowers" ]; then
    IN_REPO=1
fi

DATE_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ---------------------------------------------------------------------------
# Phase 0: SDL backend probe
# ---------------------------------------------------------------------------
echo "=== gather_pi1_evidence: running SDL backend probe ==="
PROBE_SH="$SCRIPT_DIR/probe_sdl_backends.sh"
if [ ! -x "$PROBE_SH" ]; then
    echo "ERROR: $PROBE_SH not found or not executable" >&2
    exit 1
fi

PROBE_STDOUT="$(bash "$PROBE_SH" 2>&1)"

# Parse: first driver that printed "<drv> -> OK" wins.
# probe_sdl_backends.sh tries fbcon, kmsdrm, x11, dummy in order, but only
# fbcon and kmsdrm count as "ship this driver" -- if neither pass, we fall
# back to xinit (X11 + matchbox), which the Phase 5 carry test accepts as
# a valid sdl-backend value.
CHOSEN=""
while IFS= read -r line; do
    case "$line" in
        "fbcon -> OK"*)  CHOSEN="fbcon";  break ;;
        "kmsdrm -> OK"*) CHOSEN="kmsdrm"; break ;;
    esac
done <<< "$PROBE_STDOUT"
if [ -z "$CHOSEN" ]; then
    CHOSEN="xinit"
fi
echo "SDL probe chose: $CHOSEN"

KEYBOARD_CONF=""
if [ -r /etc/default/keyboard ]; then
    KEYBOARD_CONF="$(cat /etc/default/keyboard)"
fi

SDL_DOC="$(cat <<SDLEOF
# Pi 1B SDL backend probe — gather_pi1_evidence.sh output

date: $DATE_UTC

chosen backend: $CHOSEN
sdl-backend: $CHOSEN

This document is the Phase 0 hardware deliverable. The "chosen backend"
line is read by tests/test_phase0_deliverables.py and the "sdl-backend:"
line by tests/test_phase5_phase0_phase2_carry.py. Both must match one of
fbcon | kmsdrm | xinit.

## /etc/default/keyboard
\`\`\`
${KEYBOARD_CONF:-(not readable)}
\`\`\`

## Raw probe_sdl_backends.sh stdout
\`\`\`
$PROBE_STDOUT
\`\`\`
SDLEOF
)"

# ---------------------------------------------------------------------------
# Phase 2: cairosvg perf on the real KC2G SVG
# ---------------------------------------------------------------------------
echo "=== gather_pi1_evidence: running cairosvg perf benchmark ==="

# Auto-install python3-cairosvg on Debian/Bookworm if it's missing.
if ! python3 -c 'import cairosvg' 2>/dev/null; then
    if [ -r /etc/os-release ] && grep -qi 'bookworm\|debian' /etc/os-release 2>/dev/null; then
        echo "python3-cairosvg missing; installing via apt..."
        sudo apt install -y python3-cairosvg || true
    fi
fi
if ! python3 -c 'import cairosvg' 2>/dev/null; then
    echo "ERROR: python3-cairosvg not importable; cannot benchmark" >&2
    exit 1
fi

TIMES=()
for i in 1 2 3 4 5; do
    start=$(date +%s.%N)
    python3 -c "import cairosvg; cairosvg.svg2png(url='https://prop.kc2g.com/renders/current/mufd-normal-now.svg', output_width=720, write_to='/tmp/m_$i.png')"
    end=$(date +%s.%N)
    elapsed=$(echo "$end - $start" | bc -l)
    TIMES+=("$elapsed")
    echo "  run $i: ${elapsed}s"
done

# Median of 5 = the 3rd value once sorted.
MEDIAN=$(printf '%s\n' "${TIMES[@]}" | sort -g | sed -n '3p')

# Decision rule:
#   median <= 20 s  -> ship cairosvg, PHASE2_TIMEOUT_S=45
#   20 < median <=30 -> ship cairosvg, PHASE2_TIMEOUT_S=max(60, ceil(3*median))
#   median > 30 s    -> fall back to BOM World I-Map GIF
DECISION=""
TIMEOUT_S=0
MUF_SOURCE_LINE=""
if (( $(echo "$MEDIAN <= 20" | bc -l) )); then
    DECISION="median ${MEDIAN}s <= 20s -> ship cairosvg, PHASE2_TIMEOUT_S=45"
    TIMEOUT_S=45
    MUF_SOURCE_LINE="muf-source: kc2g-svg-cairosvg"
elif (( $(echo "$MEDIAN <= 30" | bc -l) )); then
    THREEX=$(echo "$MEDIAN * 3" | bc -l)
    THREEX_CEIL=$(python3 -c "import math; print(int(math.ceil(float('$THREEX'))))")
    TIMEOUT_S=$THREEX_CEIL
    if [ "$TIMEOUT_S" -lt 60 ]; then TIMEOUT_S=60; fi
    DECISION="median ${MEDIAN}s in (20,30]s -> ship cairosvg, PHASE2_TIMEOUT_S=$TIMEOUT_S (max of 60 and 3*median ceil)"
    MUF_SOURCE_LINE="muf-source: kc2g-svg-cairosvg"
else
    DECISION="median ${MEDIAN}s > 30s -> cairosvg too slow on Pi 1B; use BOM World I-Map GIF instead"
    TIMEOUT_S=0
    MUF_SOURCE_LINE="muf-source: bom-world-imap"
fi

TIMES_LIST=""
i=1
for t in "${TIMES[@]}"; do
    TIMES_LIST="${TIMES_LIST}  run $i: ${t}s
"
    i=$((i+1))
done

MUF_DOC="$(cat <<MUFEOF
# Pi 1B MUF source decision — gather_pi1_evidence.sh output

date: $DATE_UTC

## Measurements (cairosvg rasterize of https://prop.kc2g.com/renders/current/mufd-normal-now.svg, output_width=720)
${TIMES_LIST}
median: ${MEDIAN}s

## Decision
$DECISION

$MUF_SOURCE_LINE
muf-subprocess-timeout-s: $TIMEOUT_S

The "muf-subprocess-timeout-s" line is the Phase 2 hardware deliverable
read by tests/test_phase5_phase0_phase2_carry.py::test_installer_carries_muf_timeout.
A value of 0 signals the BOM-GIF fallback path (cairosvg not used).
MUFEOF
)"

# ---------------------------------------------------------------------------
# Write or print
# ---------------------------------------------------------------------------
if [ "$IN_REPO" = "1" ]; then
    mkdir -p "$REPO_DIR/docs"
    printf '%s\n' "$SDL_DOC" > "$REPO_DIR/docs/sdl-backend.md"
    printf '%s\n' "$MUF_DOC" > "$REPO_DIR/docs/muf-source.md"
    echo ""
    echo "Wrote $REPO_DIR/docs/sdl-backend.md"
    echo "Wrote $REPO_DIR/docs/muf-source.md"
else
    echo ""
    echo "##### BEGIN docs/sdl-backend.md #####"
    printf '%s\n' "$SDL_DOC"
    echo "##### END docs/sdl-backend.md #####"
    echo ""
    echo "##### BEGIN docs/muf-source.md #####"
    printf '%s\n' "$MUF_DOC"
    echo "##### END docs/muf-source.md #####"
    echo ""
    echo "(not running inside a hamclock-pi1 clone -- copy the two blocks"
    echo " above into docs/sdl-backend.md and docs/muf-source.md in the clone)"
fi

# ---------------------------------------------------------------------------
# Run the gating tests so the operator knows immediately if Phase 5 unblocks
# ---------------------------------------------------------------------------
if [ "$IN_REPO" = "1" ]; then
    echo ""
    echo "=== running gating tests ==="
    cd "$REPO_DIR" && python3 -m pytest \
        tests/test_phase0_deliverables.py \
        tests/test_phase5_phase0_phase2_carry.py \
        -v
    TEST_RC=$?
    if [ "$TEST_RC" -eq 0 ]; then
        echo ""
        echo "OK: gating tests pass -- Phase 5 is unblocked."
    else
        echo ""
        echo "FAIL: gating tests still failing (rc=$TEST_RC). See output above."
        echo "If the failures are in the installer-carry tests, you also need"
        echo "to update kiosk-install.sh / offline-install.sh to declare:"
        echo "  SDL_VIDEODRIVER=$CHOSEN"
        echo "  PHASE2_TIMEOUT_S=$TIMEOUT_S"
    fi
fi
