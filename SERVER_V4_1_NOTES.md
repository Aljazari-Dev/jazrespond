# Promobot Server V4.1

This patch fixes the connected-but-no-response Live API path without changing the Promobot C++ application.

## Changes
- Robot/application event speech now uses `clientContent` with `turnComplete=true` so a discrete event starts generation immediately.
- Gemini server-side automatic VAD is disabled by default (`GEMINI_MANUAL_VAD=true`).
- Bridge local VAD boundaries are forwarded to Gemini as `activityStart` / `activityEnd`.
- `audioStreamEnd` is no longer used as the normal turn boundary while manual VAD is enabled.

## Render environment
Set:
- `GEMINI_MANUAL_VAD=true`
- `GEMINI_MODEL=gemini-3.1-flash-live-preview`

Keep existing secrets and `/var/data` settings unchanged.
