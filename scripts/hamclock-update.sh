#!/bin/bash
# HamClock Lite updater — runs as root, triggered by an unprivileged request.
#
# SECURITY MODEL
# --------------
# The dashboard server runs as SERVICE_USER (non-root). Granting it sudo to
# install software would hand a network-facing process a route to root, so it
# gets none. Instead it writes an empty flag file, and a root-owned systemd
# path unit starts this script.
#
# The critical property: the unprivileged side can only say "please update".
# It cannot say WHAT to install. This script does its own fetch, from a URL
# compiled in below, and refuses to execute anything whose SHA-256 does not
# match the signed-in-place manifest. A compromised or merely buggy dashboard
# process therefore cannot cause arbitrary code to run as root.
#
# This file must be root:root and NOT writable by SERVICE_USER — if the service
# user can edit the script that root executes, the whole model collapses.
# install_updater() in the installer enforces 0755 root:root.
#
# Exit codes: 0 ok / 1 usage / 2 network / 3 integrity / 4 apply failed
#             (rolled back) / 5 apply failed AND rollback failed
set -o pipefail

BASE_URL="https://hamclock-reborn.org"
MANIFEST_URL="$BASE_URL/downloads/pi1-install.version.json"
INSTALLER_URL="$BASE_URL/downloads/pi1-install.sh"

INSTALL_DIR="/opt/hamclock-lite"
RUN_DIR="/run/hamclock-lite"
STATE_DIR="/var/lib/hamclock-lite"
STATUS_FILE="$RUN_DIR/update-status.json"
REQUEST_FILE="$RUN_DIR/update.request"
BACKUP_DIR="$STATE_DIR/backup"
LOG_TAG="hamclock-update"

log() { echo "[$(date -u +%H:%M:%S)] $*"; logger -t "$LOG_TAG" -- "$*" 2>/dev/null || true; }

# Status is written to tmpfs so the dashboard can read it without any shared
# privilege. Never put anything secret in here.
write_status() {
    mkdir -p "$RUN_DIR" 2>/dev/null || true
    cat > "$STATUS_FILE.tmp" <<EOF
{"state":"$1","installed":"$2","available":"$3","detail":"$4","checked":"$(date -u +%Y-%m-%dT%H:%M:%SZ)"}
EOF
    mv -f "$STATUS_FILE.tmp" "$STATUS_FILE" 2>/dev/null || true
    chmod 0644 "$STATUS_FILE" 2>/dev/null || true
}

installed_version() {
    # The single-file installer has no git, which is the entire reason it
    # exists, so never ask git. Read what the install stamped.
    if [ -r "$INSTALL_DIR/VERSION" ]; then
        sanitize_version "$(tr -d ' \t\r\n' < "$INSTALL_DIR/VERSION")"
    else
        echo "0.0.0"
    fi
}

# Pure-shell semver compare. Returns 0 when $1 is strictly newer than $2.
# sort -V would be shorter but is not guaranteed on a minimal Pi OS image.
version_gt() {
    local a b i
    IFS='.' read -r -a a <<< "${1%%-*}"
    IFS='.' read -r -a b <<< "${2%%-*}"
    for i in 0 1 2; do
        local x=${a[$i]:-0} y=${b[$i]:-0}
        # Strip anything non-numeric so a malformed field cannot inject.
        x=${x//[^0-9]/}; y=${y//[^0-9]/}
        x=${x:-0}; y=${y:-0}
        if [ "$x" -gt "$y" ] 2>/dev/null; then return 0; fi
        if [ "$x" -lt "$y" ] 2>/dev/null; then return 1; fi
    done
    return 1
}

json_field() {
    # Extract one string field without needing python/jq on the target.
    sed -n 's/.*"'"$2"'"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' <<< "$1" | head -1
}

# Everything below comes off the network. Even though the manifest is served
# from our own site over TLS, it is parsed by a hand-rolled sed and then
# interpolated into the JSON status file, so a stray quote would corrupt that
# file and a stray shell metacharacter has no business being here at all.
# Whitelist rather than escape.
sanitize_version() {
    local v="${1//[^0-9A-Za-z.+-]/}"
    printf '%s' "${v:0:32}"
}

# A digest that is not exactly 64 hex characters is not a digest, and treating
# it as one would let a malformed manifest slip past the integrity gate.
sanitize_sha256() {
    local s="${1,,}"
    s="${s//[^0-9a-f]/}"
    [ ${#s} -eq 64 ] || return 1
    printf '%s' "$s"
}

fetch_manifest() {
    curl -fsS --max-time 20 --proto '=https' --tlsv1.2 "$MANIFEST_URL" 2>/dev/null
}

do_check() {
    local cur mani avail
    cur=$(installed_version)
    mani=$(fetch_manifest) || {
        log "check: manifest unreachable"
        write_status "error" "$cur" "" "cannot reach $BASE_URL"
        return 2
    }
    avail=$(sanitize_version "$(json_field "$mani" version)")
    if [ -z "$avail" ]; then
        log "check: manifest has no version field"
        write_status "error" "$cur" "" "malformed manifest"
        return 3
    fi
    if version_gt "$avail" "$cur"; then
        log "check: update available $cur -> $avail"
        write_status "available" "$cur" "$avail" ""
    else
        log "check: up to date ($cur)"
        write_status "current" "$cur" "$avail" ""
    fi
    return 0
}

health_ok() {
    # An update that leaves the API dead is a failed update, no matter what
    # the installer's exit code said.
    local i
    for i in $(seq 1 30); do
        if curl -fsS --max-time 3 http://localhost:8080/api/health >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

do_apply() {
    local cur mani avail want_sha tmp got_sha
    cur=$(installed_version)

    mani=$(fetch_manifest) || { write_status "error" "$cur" "" "cannot reach $BASE_URL"; return 2; }
    avail=$(sanitize_version "$(json_field "$mani" version)")
    want_sha=$(sanitize_sha256 "$(json_field "$mani" sha256)") || want_sha=""
    [ -n "$avail" ] || { write_status "error" "$cur" "" "malformed manifest"; return 3; }

    if ! version_gt "$avail" "$cur"; then
        log "apply: already at $cur, nothing to do"
        write_status "current" "$cur" "$avail" ""
        return 0
    fi

    # An unverifiable download is not installed. Refusing here is the whole
    # point of the manifest; without the digest this is just curl | bash.
    if [ -z "$want_sha" ]; then
        log "apply: REFUSING — manifest carries no sha256"
        write_status "error" "$cur" "$avail" "manifest has no sha256; refusing to install"
        return 3
    fi

    write_status "applying" "$cur" "$avail" "downloading"
    tmp=$(mktemp /tmp/hamclock-install.XXXXXX.sh) || return 2
    trap 'rm -f "$tmp"' RETURN

    if ! curl -fsS --max-time 180 --proto '=https' --tlsv1.2 -o "$tmp" "$INSTALLER_URL"; then
        log "apply: download failed"
        write_status "error" "$cur" "$avail" "download failed"
        return 2
    fi

    got_sha=$(sha256sum "$tmp" | awk '{print $1}')
    if [ "$got_sha" != "$want_sha" ]; then
        log "apply: REFUSING — sha256 mismatch (want $want_sha got $got_sha)"
        write_status "error" "$cur" "$avail" "integrity check failed; nothing installed"
        return 3
    fi
    log "apply: integrity verified ($got_sha)"

    # Back up what we are about to overwrite. A headless kiosk with a broken
    # display and no keyboard is not somewhere you want to debug an update.
    rm -rf "$BACKUP_DIR.prev" 2>/dev/null
    [ -d "$BACKUP_DIR" ] && mv "$BACKUP_DIR" "$BACKUP_DIR.prev"
    mkdir -p "$BACKUP_DIR"
    cp -a "$INSTALL_DIR/." "$BACKUP_DIR/" 2>/dev/null || {
        log "apply: backup failed; refusing to continue without a rollback path"
        write_status "error" "$cur" "$avail" "backup failed; nothing installed"
        return 4
    }

    write_status "applying" "$cur" "$avail" "installing"
    log "apply: running installer $cur -> $avail"
    if bash "$tmp" >>/var/log/hamclock-update.log 2>&1 && health_ok; then
        log "apply: success, now $(installed_version)"
        write_status "updated" "$(installed_version)" "$avail" "restart complete"
        maybe_reboot
        return 0
    fi

    # Roll back. Restore files first, then prove the service actually recovers.
    log "apply: FAILED — rolling back to $cur"
    write_status "rollback" "$cur" "$avail" "update failed; restoring previous version"
    rm -rf "$INSTALL_DIR.failed" 2>/dev/null
    mv "$INSTALL_DIR" "$INSTALL_DIR.failed" 2>/dev/null
    mkdir -p "$INSTALL_DIR"
    if cp -a "$BACKUP_DIR/." "$INSTALL_DIR/" 2>/dev/null; then
        systemctl restart hamclock-lite 2>/dev/null
        if health_ok; then
            log "rollback: restored $cur and service is healthy"
            write_status "rolled_back" "$cur" "$avail" "update failed; previous version restored"
            return 4
        fi
    fi
    log "rollback: FAILED — manual recovery needed; failed tree kept at $INSTALL_DIR.failed"
    write_status "broken" "$cur" "$avail" "update and rollback both failed; see /var/log/hamclock-update.log"
    return 5
}

maybe_reboot() {
    # Almost nothing this project ships needs a reboot — restarting the two
    # units is enough, and the installer already does that. Rebooting a kiosk
    # is disruptive and risks not coming back, so it happens only when
    # something outside our services genuinely changed.
    local need=0
    [ -f /var/run/reboot-required ] && need=1
    if [ -f "$STATE_DIR/boot-config.sha" ]; then
        local now
        now=$(cat /boot/config.txt /boot/cmdline.txt 2>/dev/null | sha256sum | awk '{print $1}')
        [ "$now" != "$(cat "$STATE_DIR/boot-config.sha")" ] && need=1
    fi
    if [ "$need" = "1" ]; then
        log "reboot: boot configuration changed; rebooting in 10s"
        write_status "rebooting" "$(installed_version)" "" "boot config changed"
        ( sleep 10; systemctl reboot ) &
    else
        log "reboot: not required; services restarted in place"
    fi
}

record_boot_config() {
    mkdir -p "$STATE_DIR" 2>/dev/null || true
    cat /boot/config.txt /boot/cmdline.txt 2>/dev/null \
        | sha256sum | awk '{print $1}' > "$STATE_DIR/boot-config.sha" 2>/dev/null || true
}

case "${1:-}" in
    --check)
        record_boot_config
        do_check
        ;;
    --apply)
        rm -f "$REQUEST_FILE" 2>/dev/null    # consume the trigger first, so a
                                             # failure cannot spin the path unit
        record_boot_config
        do_apply
        ;;
    *)
        echo "usage: $0 --check | --apply" >&2
        exit 1
        ;;
esac
