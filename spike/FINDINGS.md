# Phase 0 findings — Lenovo 83DV (Realtek mic array)

Measured 2026-08-26 with `spike.py`. Update as sessions are recorded.

## Decision Point 1 — is there a usable signal? **YES, but only via one path**

| Path | Channels | Result |
|---|---|---|
| WASAPI **shared** (default) | 2 | **Bit-identical, corr 1.000** — beamformed to mono and duplicated. No TDOA. |
| WASAPI **exclusive** | 2 | **corr 0.43, independent** — two real mics. TDOA available. |
| MME / DirectSound | 2 | Duplicated mono (tiny resample noise only). |
| WDM-KS | 2 | Will not open (`Unanticipated host error`). |

Level difference between shared and exclusive: **rms 0.00015 vs 0.103** — roughly 4 orders of
magnitude. The Realtek/Nahimic APOs are not merely mixing the array down, they are gating the
signal almost to silence. Exclusive mode bypasses the Windows audio engine and both problems.

**This is the answer to "why was Holo Mac-only".** The signal exists on this laptop; it is just
hidden behind the driver's beamformer. It had to be measured, not assumed.

### Consequences for the PRD

1. `spike.py` defaults to WASAPI exclusive, 2ch, 48kHz, auto-detected device (`find_device()`).
2. **Exclusive mode means TapSense owns the microphone.** No other app can capture while it runs —
   no Zoom, no Discord, no voice input. This is a hard product constraint the PRD does not
   currently acknowledge, and it is the main cost of the only path that works.
3. Any per-device calibration must also record *which capture path* was used; a profile trained in
   exclusive mode is meaningless in shared mode.
4. The finding is device-specific. Another Windows laptop may duplicate mono even in exclusive
   mode. `spike.py devices` is the portable probe for that.

## Signal character

Idle spectrum in exclusive mode: **89% of energy below 200 Hz**, 0.3% above 4 kHz. Raw MEMS output
with no high-pass. Occasional samples clip (|v| > 0.99).

Consequence: an RMS onset detector measures desk and fan rumble, not taps. `detect_loop` therefore
thresholds on a **high-passed** signal (`np.diff`, +6 dB/octave) while storing the raw window for
feature extraction. Without this the detector is nearly useless.

## Detector sensitivity — a product problem, not a feasibility one

At `--k 10`, **only hard knuckle raps register. Single-finger taps do not.**

Cause: the raw exclusive-mode stream has a high noise floor (idle ratio drifts to 3.6x), so a
quiet impact never clears the threshold. Lowering `k` to catch finger taps drags in background
noise instead — measured margin between "loudest tap" (17.2x) and "idle max" (3.6x) is only ~5x.

This does **not** invalidate the zone experiment: a knuckle rap and a finger tap travel the same
physical path, so if zones are separable with one they are separable with the other. It is a
sensitivity problem, downstream of localization.

It **is** a serious product problem. "Bang your desk to open VS Code" is not an interaction anyone
wants. Untried fixes, cheapest first:

1. Replace the crude `np.diff` high-pass with a real band-pass over where tap energy actually
   lives (measure it from the recorded windows first — do not guess the band).
2. Onset detection on **spectral flux** instead of broadband level; far more robust to stationary
   rumble than a level threshold.
3. Only then lower `k`.

## Session A — zone signal confirmed

173 taps, 4 zones, 2ch, knuckle raps at `--k 10`. Peak levels healthy (median 0.47–0.63), 4 clipped.

Same-session (optimistic, train/test split within A):

| Features | Accuracy |
|---|---|
| spectral (32 log bands) | 70.9% |
| TDOA (inter-mic lag + level ratio) | 53.5% |
| **combined** | **74.4%** (chance 25%) |

Axis breakdown: left/right **87.2%**, front/rear **84.9%** (chance 50%). Both axes carry comparable
signal — neither dominates, so a 2-zone fallback could go either way.

TDOA alone beats chance by 2x, confirming the two mics are genuinely independent and useful. But
spectral coloration carries more of the signal than the time delay does.

**Cross-session is still the number that decides the project.** Session B not yet recorded.

## Detector tuning — open

`monitor` on an idle desk: ratio 0.6–3.9x, occasional blips to ~23x. Default `--k 8` sits inside
the noise and fired ~3600/hour with nobody touching the desk.

**`--k` must be set from `monitor` with real taps, not guessed.** Not yet done — needs hands on
the desk.

## Decision Point 2 — cross-session accuracy: **FAILED**

173 taps session A, 160 session B, ~1h apart, laptop nudged a few cm between. Knuckle raps both
sessions, `--k 10`.

Cross-session (train A → test B), by feature set and task:

| Features | 4-zone (chance 25%) | left/right (chance 50%) | front/rear (chance 50%) |
|---|---|---|---|
| spectral | 28.7% | 53.1% | 53.1% |
| **TDOA** | **40.6%** | **72.5%** | 58.8% |
| combined | 30.6% | 60.0% | 50.6% |
| TDOA w/ GCC-PHAT sub-sample | 36.2% | 62.5% | 59.4% |

Gate was >=85% for 4 zones. **Best result on any variant: 40.6%.** Not close.

### Why — the physics

1. **Same-session scores were position memorization.** Spectral features: 65.1% same-session,
   28.7% cross-session, a **+36.3% gap**. Move the laptop 3cm and the model is gone. This is
   exactly the trap the cross-session protocol exists to catch, and the PRD's plain "held-out
   evaluation" would have shipped 65% as a success.

2. **Spectral features are actively harmful.** `combined` (30.6%) scores *worse* than TDOA alone
   (40.6%) — 32 unstable spectral dims swamp 2 useful ones.

3. **The mics are too close, and the path is wrong.** Measured inter-mic lag spans about 3 samples
   total at 48kHz (~7mm of path difference per sample) with per-zone noise of comparable size.
   Sub-sample GCC-PHAT did not rescue it — it scored *worse*, and the reason is in the lag stats:
   session B left zones read **+0.02 samples, sd 0.05** across 40 taps. Both mics receive the tap
   simultaneously.

   Desk taps couple into the **laptop chassis** (structure-borne, ~5000 m/s) rather than travelling
   to the mics through air (~343 m/s). Structure-borne arrival is effectively instantaneous at both
   mics no matter where the desk was struck. There is no time-of-arrival difference to measure.

**A closer-spaced mic pair cannot be fixed in software.** This is not a tuning problem or a
better-classifier problem. Four-zone desk localization is not available on this hardware.

## Verdict

**Pivot to tap-count.** Single / double / triple tap anywhere on the desk. No localization, so the
mic geometry stops mattering and it works on any laptop. The temporal pattern is also a far
stronger rejection cue than any spectral signature: two impulses 100–300ms apart almost never
happen by accident, whereas one impulse happens constantly.

The parts of the PRD that survive intact: audio capture, onset detection, DSP, the tap/not-tap
rejection problem (which was always the genuinely hard and interesting part), calibration, the
action engine, the UI, the backend. What is deleted: zone classification and the 4-zone model.

## Microphone exclusivity — resolved, the constraint is gone

Exclusive mode was only ever needed for two *independent* channels (TDOA). With localization
dropped, tap-count needs only "did an impulse happen", which shared mode answers.

Measured in shared mode, 1ch:

| | Level (high-passed RMS) |
|---|---|
| Idle | **0.00e+00** — exactly zero, the APO noise gate closes completely |
| Taps | 1.7e-05, 6.6e-05, 1.1e-04, 5.8e-04 |
| Gate artifacts | <= 3.3e-06 |

**The noise suppression does not eat knuckle taps.** A threshold near **1e-5** separates taps from
artifacts with ~5x margin.

Verified: two simultaneous shared-mode streams open and read fine on this device. Another app can
hold the microphone while TapSense listens. **No lock, no conflict, no release-on-demand hack.**

The gate is an asset, not an obstacle: digital silence at rest means any non-zero energy is already
a candidate event, so detection is an absolute threshold rather than an adaptive noise floor.

Note the control changes with the mode:
- shared mode -> **absolute** level threshold (idle is 0, so ratio-to-floor is meaningless)
- exclusive mode -> ratio-to-noise-floor (`--k`), because idle sits at ~3e-3

### Caveat not yet tested

During an actual call the gate is held open by speech and the idle level stops being zero.
Tap detection under continuous speech is unmeasured, and false positives there could fire actions
mid-call. Test before trusting it during calls.

## False-positive rate

Still unmeasured, and now the **central** metric rather than a side one. Target <1/hour. With the
zone classifier gone, tap-vs-not-tap is the whole product.
