# TapSense

Control your Windows laptop by tapping the desk. Two taps to play/pause, three
taps to skip a track. It listens through the laptop microphone and matches the
**rhythm** of the taps, not where they landed. Everything runs locally.

Originally inspired by Holo, a macOS desk-tap project. The first plan here was
four-zone desk localization; that was tested on this hardware and failed
(30.6% cross-session accuracy against a 25% chance baseline — details in
`spike/FINDINGS.md`), so TapSense was redesigned around double/triple taps.

## Status

**Done**
- Phase 0 feasibility spike: proved four-zone localization is unusable on this
  laptop — structure-borne taps give no measurable delay between microphones.
  Recordings kept in `spike/data/`.
- Phase 1 detection core (`src/tapsense.py`):
  - shared-mode microphone capture (Zoom/Discord keep working)
  - high-pass onset detection with a Schmitt trigger (one ringing tap = one event)
  - typing guard: impulses within 250ms of keyboard/mouse input are dropped
  - double/triple-tap counting; single taps ignored on purpose
  - threshold calibration from 5 taps
  - basic action engine: media keys / open URL / open file; config is validated
    before use, actions run only with `--act`
  - `selftest` covering gestures, trigger behavior and config validation — passing

**Not done / not measured**
- Phase 1 exit gates are unmeasured: detection rate, false triggers per hour,
  latency. "It works when I tap" is a manual observation, not a number yet.
- Phase 2 false-positive quality — not started
- Music through laptop speakers confuses detection (measured: drum hits overlap
  with real taps, which can turn a double-tap into a triple). Headphones fix it.
  The Phase 6 tooling to attack this (`collect` / `analyze`) is built but unused.
- Tray app, persistence, pytest suite, backend layer — not started

## Requirements

- Windows (uses WASAPI and Windows APIs; no macOS/Linux support)
- Python 3.13, `numpy`, `sounddevice` (installed manually — no requirements file)

## Usage

```bash
python src/tapsense.py calibrate        # tap 5 times to set the threshold
python src/tapsense.py listen           # print DOUBLE TAP / TRIPLE TAP
python src/tapsense.py listen --act     # actually run the configured actions
python src/tapsense.py selftest         # verify gesture/trigger/action logic, no mic
python src/tapsense.py debug            # live view of every sound the mic hears
```

Default actions: double-tap = play/pause, triple-tap = next track (Windows media
keys — works with Spotify, VLC, browser players alike).

## Layout

- `src/tapsense.py` — the application
- `spike/` — Phase 0 evidence and measurement tools (frozen, not product code)
