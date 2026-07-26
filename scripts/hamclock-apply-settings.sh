#!/bin/bash
# Apply the parts of settings.json that need root. Runs as root, triggered by
# an unprivileged request, exactly like hamclock-update.sh.
#
# Right now that means the NTP server. The on-device setup wizard runs as
# SERVICE_USER and can write /etc/hamclock-lite/settings.json, but the thing
# that actually changes the clock source is
# /etc/systemd/timesyncd.conf.d/hamclock.conf, which is root-owned. Rather than
# grant the wizard sudo, it saves its choice and asks for it to be applied.
#
# SECURITY: settings.json is writable by an unprivileged user, and this script
# takes a value out of it and puts it into a systemd unit config as root. That
# is a privilege boundary, so the value is validated against a strict whitelist
# HERE, before it is trusted — never on the assumption that the wizard already
# checked. A hostile settings.json must not be able to inject directives.
#
# Exit codes: 0 applied / 1 usage / 2 no/!readable settings / 3 invalid value
set -o pipefail

SETTINGS="/etc/hamclock-lite/settings.json"
CONF="/etc/systemd/timesyncd.conf.d/hamclock.conf"
RUN_DIR="/run/hamclock-lite"
REQUEST_FILE="$RUN_DIR/settings.request"
STATUS_FILE="$RUN_DIR/settings-status.json"
LOG_TAG="hamclock-settings"

log() { echo "[$(date -u +%H:%M:%S)] $*"; logger -t "$LOG_TAG" -- "$*" 2>/dev/null || true; }

write_status() {
    mkdir -p "$RUN_DIR" 2>/dev/null || true
    printf '{"state":"%s","ntp":"%s","detail":"%s","at":"%s"}\n' \
        "$1" "$2" "$3" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$STATUS_FILE.tmp" 2>/dev/null
    mv -f "$STATUS_FILE.tmp" "$STATUS_FILE" 2>/dev/null || true
    chmod 0644 "$STATUS_FILE" 2>/dev/null || true
}

# One NTP server: a hostname or an IPv4/IPv6 literal. Deliberately strict —
# no spaces, no '#', no '=', no newlines, nothing that could start a second
# directive inside timesyncd.conf.
valid_one() {
    local s="$1"
    [ -n "$s" ] || return 1
    [ ${#s} -le 253 ] || return 1
    [[ "$s" =~ ^[A-Za-z0-9]([A-Za-z0-9.:-]*[A-Za-z0-9])?$ ]] || return 1
    # A bare run of digits and dots that is not a valid IPv4 is a typo, not a
    # hostname; catching it here beats a silently broken time source.
    if [[ "$s" =~ ^[0-9.]+$ ]]; then
        local IFS=. o n=0
        for o in $s; do
            [[ "$o" =~ ^[0-9]{1,3}$ ]] || return 1
            [ "$o" -le 255 ] || return 1
            n=$((n+1))
        done
        [ "$n" -eq 4 ] || return 1
    fi
    return 0
}

# systemd's NTP= takes a space-separated list, which is how an operator
# specifies fallbacks. Every element must pass on its own.
valid_ntp_list() {
    local v="$1" one
    [ -n "$v" ] || return 1
    [ ${#v} -le 512 ] || return 1
    for one in $v; do
        valid_one "$one" || return 1
    done
    return 0
}

read_ntp() {
    # No jq on a minimal Pi OS image, and python3 is guaranteed here because
    # the whole application is written in it.
    python3 - "$SETTINGS" <<'PY' 2>/dev/null
import json, sys
try:
    with open(sys.argv[1]) as f:
        v = json.load(f).get("ntp", "")
    print(v if isinstance(v, str) else "")
except Exception:
    print("")
PY
}

apply_ntp() {
    local ntp
    [ -r "$SETTINGS" ] || { log "no readable $SETTINGS"; return 2; }
    ntp=$(read_ntp)

    if [ -z "$ntp" ]; then
        # Empty means "use the distro default". Remove our drop-in rather than
        # writing an empty NTP= line, which systemd treats as a parse error.
        # (The old python path validated with gethostbyname alone, and
        # gethostbyname("") RESOLVES — so an empty value used to sail through
        # and produce exactly that broken config.)
        if [ -f "$CONF" ]; then
            rm -f "$CONF"
            log "ntp cleared; removed $CONF and reverted to distro defaults"
            systemctl restart systemd-timesyncd 2>/dev/null
        fi
        write_status "cleared" "" "using distro default time servers"
        return 0
    fi

    if ! valid_ntp_list "$ntp"; then
        log "REFUSING invalid ntp value from settings.json"
        write_status "invalid" "" "rejected; not a valid host or address list"
        return 3
    fi

    # Normalise whitespace before writing. Word-splitting already treats a tab
    # or a run of spaces as a separator, so 'a.com<TAB>b' validates as the
    # two-server list it is — but the config file should record it as one clean
    # space-separated line rather than echoing whatever spacing was in the JSON.
    ntp=$(echo $ntp)

    mkdir -p "$(dirname "$CONF")" 2>/dev/null
    printf '[Time]\nNTP=%s\n' "$ntp" > "$CONF.tmp" || return 2
    chmod 0644 "$CONF.tmp"
    mv -f "$CONF.tmp" "$CONF" || return 2
    log "ntp set to '$ntp'"

    if systemctl restart systemd-timesyncd 2>/dev/null; then
        # Resolution is checked AFTER saving, not before. An operator setting an
        # internal time server on a network that is not up yet must still be
        # able to configure it; a name that does not resolve is worth reporting,
        # not worth refusing.
        local first="${ntp%% *}"
        if getent hosts "$first" >/dev/null 2>&1; then
            write_status "applied" "$ntp" "timesyncd restarted"
        else
            write_status "applied" "$ntp" "saved, but '$first' does not resolve yet"
            log "warning: '$first' does not currently resolve"
        fi
    else
        write_status "applied" "$ntp" "saved; could not restart timesyncd"
    fi
    return 0
}

case "${1:-}" in
    --apply)
        rm -f "$REQUEST_FILE" 2>/dev/null   # consume the trigger first so a
                                            # failure cannot re-arm the path unit
        apply_ntp
        ;;
    --validate)
        # For tests and for the wizard to shell out to if it wants a check.
        valid_ntp_list "${2:-}" && { echo valid; exit 0; } || { echo invalid; exit 3; }
        ;;
    *)
        echo "usage: $0 --apply | --validate <value>" >&2
        exit 1
        ;;
esac
