# Futu OpenD setup (local)

This module expects a running Futu OpenD instance:

- Host/port: `127.0.0.1:11111` (quote)
- Logged-in US market entitlement as needed for option unusual-activity / chains

Do **not** commit OpenD binaries (AppImage / vendor trees) into git.

## Safety

Scripts under this module should prefer `OpenQuoteContext` only.
Trading / `UnlockTrade` / `place_order` helpers belong outside production cron
unless explicitly reviewed.
