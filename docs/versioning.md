# Versioning and the update check

Until now this project had no version identity at all: no `VERSION` file, no
`__version__`, no git tags, and no build banner in either installer. That is
fine while the only way to update is `git pull`, but it makes an update check
impossible — there is nothing to compare.

## The single source of truth

`VERSION` at the repo root holds a plain semver string and nothing else:

```
1.0.0
```

Humans edit this file, and only this file. Everything else is derived at build
time so it cannot drift.

Bump it when you ship something operators would want to pull:

- **patch** (1.0.x) — fixes that change no behaviour they configured
- **minor** (1.x.0) — new panels, new endpoints, new install options
- **major** (x.0.0) — anything that requires them to redo setup or that breaks
  an existing install path

Starting at `1.0.0` rather than `0.x` because the thing is already deployed on
real hardware and served from a public installer; pretending it is pre-release
would be inaccurate.

## What gets derived from it

`scripts/sync_installers.py` stamps the version into the generated installer and
writes a sidecar manifest next to it. The manifest carries what a version string
alone cannot: when it was built and which commit it came from.

```json
{
  "version": "1.0.0",
  "built": "2026-07-26T22:00:00Z",
  "commit": "e0be598",
  "installer": "https://hamclock-reborn.org/downloads/pi1-install.sh",
  "sha256": "<of the installer that was published>"
}
```

The installer records the version it installed into `$INSTALL_DIR`, so a running
Pi can report what it is actually on regardless of which path installed it —
`git clone` + `kiosk-install.sh`, or the single-file installer from the site.

## Two install paths, one version

This matters because the two paths have different notions of "current":

| Path | Updates by | Knows its version from |
|---|---|---|
| `git clone` + `kiosk-install.sh` | `git pull` | `VERSION` in the checkout |
| `pi1-install.sh` from the site | re-running the one-liner | the stamped value in `$INSTALL_DIR` |

The single-file path has no git, which is the whole reason it exists (it works
where GitHub is blocked). So the stamped value is what the update check reads —
never `git describe`, which is unavailable on exactly the installs that most
need the check.

## Caveat worth knowing

The update check compares against whatever the **site is currently serving**, not
against the repo. If `public/downloads/pi1-install.sh` has not been redeployed,
the check will honestly report "up to date" against a stale build. That is a
deploy question, not a bug in the checker — but it means the site must be
redeployed for the check to mean anything.
