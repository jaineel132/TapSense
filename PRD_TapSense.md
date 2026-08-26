# Product Requirements Document — TapSense

**Status:** Revision 2 — rewritten after Phase 0 feasibility testing
**Scope:** Local-only, Windows-first
**Supersedes:** Revision 1 (four-zone desk localization), which Phase 0 disproved

---

## 1. What changed, and why

Revision 1 specified **four desk zones** — tap the left-front corner, tap the right-rear corner,
each triggers a different action. It was modelled on Holo, a macOS project.

Phase 0 built a throwaway measurement spike (`spike/spike.py`) and tested that assumption on the
target hardware before any product code was written. **It failed.**

| Measurement | Result |
|---|---|
| 4-zone accuracy, same session | 68.7% |
| 4-zone accuracy, **cross-session** (laptop moved 3cm) | **30.6%** (chance 25%) |
| Best variant, any feature set | 40.6% |
| Required to proceed | 85% |

The 38-point gap between same-session and cross-session is the whole story: the classifier was
memorizing where the laptop sat, not learning where the desk was struck.

**Root cause (physical, not fixable in software):** desk taps couple into the laptop *chassis* and
travel at roughly 5000 m/s, not through air at 343 m/s. Structure-borne sound arrives at both
microphones essentially simultaneously — measured inter-microphone delay was **+0.02 samples with a
standard deviation of 0.05** across 40 taps. There is no time-of-arrival difference to measure.
A microphone pair centimetres apart cannot recover one. Sub-sample GCC-PHAT estimation was tried and
scored *worse*.

Full evidence: [`spike/FINDINGS.md`](spike/FINDINGS.md).

**Revision 2 drops localization entirely and uses the temporal pattern of taps instead.** This turns
out to be the stronger design regardless — see §4.

---

## 2. Product definition

### 2.1 One line

TapSense is a Windows desktop application that listens through the laptop microphone for deliberate
tap *patterns* on the desk and triggers a configured action.

### 2.2 The interaction

| Gesture | Behaviour |
|---|---|
| One impulse | **Ignored.** This is the noise rejection doing its job. |
| Two taps within 350ms | Fires the double-tap action |
| Three taps within 700ms | Fires the triple-tap action |

Tap anywhere on the desk. Position is irrelevant — only the rhythm matters.

### 2.3 Why single tap is deliberately unusable

A single impulse is indistinguishable from a mug being set down, a drawer closing, or a dropped pen.
Binding an action to it guarantees false triggers, and a gesture system that fires when you did not
gesture gets uninstalled within an hour.

Two impulses at a controlled interval almost never occur by accident. **The rhythm is the signal,
not the sound.** Discarding single taps is what makes the rest trustworthy.

---

## 3. Measured hardware constraints

Established in Phase 0 on the development machine (Lenovo 83DV, Realtek microphone array). These are
measurements, not assumptions — re-run `spike/spike.py devices` on any new machine.

### 3.1 Use shared mode, not exclusive

| Mode | Idle level (high-passed RMS) | Channels | Mic locked? |
|---|---|---|---|
| Shared | **0.00e+00** (gate fully closed) | mono, duplicated | No |
| Exclusive | 3.1e-03 | 2 independent | **Yes** |

Revision 1 would have required exclusive mode to reach the two independent microphones for
time-delay measurement. That locks the microphone: no Zoom, no Discord, no voice input while
TapSense runs.

Localization is gone, so **the lock is gone with it.** Shared mode is sufficient and polite.
Verified: two simultaneous shared-mode streams open and read on this device.

### 3.2 The Windows noise gate is an asset

Windows/Realtek audio processing gates to **digital silence** at rest, and knuckle taps pass through
it cleanly:

| | Level |
|---|---|
| Idle | 0.00e+00 |
| Taps | 1.7e-05 to 5.8e-04 |
| Gate artifacts | <= 3.3e-06 |

Roughly 5x margin between the quietest tap and the loudest artifact.

Consequence: **detection is an absolute threshold, not an adaptive noise floor.** Any non-zero
energy is already a candidate event. This is simpler and more robust than the adaptive scheme
Revision 1 implied.

### 3.3 Detection operates on a high-passed signal

Raw microphone RMS is dominated by sub-200Hz rumble — 89% of idle energy on this machine. A level
detector on the raw signal measures the fan and the desk, not taps. Detection runs on a high-passed
signal; a first difference (`np.diff`, +6dB/octave) was sufficient in Phase 0.

### 3.4 Open question — behaviour during calls

While someone is speaking, the noise gate is held open and idle stops being zero. Tap detection
under continuous speech is **unmeasured**. Phase 2 must test it; false triggers mid-call would be a
serious defect.

---

## 4. Why this design is better than Revision 1

Not a consolation prize. Concretely:

1. **It runs on any laptop.** No microphone geometry dependency, so no per-device model. Revision 1
   §5 conceded it was "not a universal guarantee for every laptop" — that limitation disappears.
2. **Calibration is one number, not a trained model.** Tap a few times, store a threshold.
   Revision 1 needed labelled collection, training, held-out evaluation, model versioning, and
   quality gates — for a model that decayed when the laptop moved.
3. **Rejection is far stronger.** Temporal pattern matching beats spectral fingerprinting for
   distinguishing intent, and intent-detection was always the genuinely hard part.
4. **No microphone lock** (§3.1).
5. **Light taps work.** The reason finger taps did not register in Phase 0 was the exclusive-mode
   noise floor drowning them. Against a zero floor, a gentle tap at 1e-05 is plainly visible.

### 4.1 What it costs

Stated plainly, because these are real:

- **Two actions, not four.** Possibly three if quadruple-tap proves reproducible — to be measured,
  not assumed.
- **Latency.** A double-tap cannot fire the instant the second tap lands; the system must wait to
  confirm a third is not coming. See §6.
- **It is not a hotkey replacement.** Its niche is when hands are away from the keyboard — pausing
  music, muting a call.

---

## 5. Non-goals

TapSense is not: a voice assistant; a cloud service; a keyboard or mouse replacement; a security or
authentication mechanism; a tap *localization* system (Phase 0 disproved it); a macOS or Linux
product in v1; a deployed or hosted system.

---

## 6. Gesture timing and latency budget

```
   tap 1      tap 2                      action
     |          |                           |
     +----------+---------------------------+
       <=350ms        350ms confirm window
                  (waiting for a possible tap 3)
```

| Parameter | Value | Rationale |
|---|---|---|
| Refractory after each impulse | 80ms | Suppresses one tap's ringing counting twice; humans cannot deliberately tap faster than ~125ms apart |
| Max gap between taps in a gesture | 350ms | Comfortable deliberate double-tap |
| Confirm window after final tap | 350ms | Distinguishes double from triple |
| **Total latency, final tap to action** | **<=400ms (p95)** | Acceptable for media control |

### 6.1 Latency optimisation worth building

**If no triple-tap gesture is bound, the double-tap fires immediately on the second impulse** — no
confirm window, near-zero added latency. Only pay the 350ms wait when a longer gesture is actually
configured. Cheap to implement, materially better feel.

---

## 7. Success criteria

Measured, not asserted. The project succeeds when all hold on the development machine:

| # | Criterion | Target |
|---|---|---|
| SC-1 | Detection rate on deliberate double-taps | >= 95% of 100 attempts |
| SC-2 | **False triggers during normal desk work** | **<= 1 per hour over 4 hours** |
| SC-3 | Latency, final tap to action fired | <= 400ms at p95 |
| SC-4 | Gesture confusion (double read as triple, or vice versa) | <= 2% |
| SC-5 | Runs on a second, different laptop with only threshold recalibration | works |
| SC-6 | Idle CPU while listening | <= 2% of one core |
| SC-7 | Automated tests cover detection, gesture assembly, and action dispatch | present and passing |

**SC-2 is the make-or-break criterion.** With localization gone, tap-versus-not-tap is the entire
product. No accuracy claim goes in the README without a measurement behind it.

---

## 8. Architecture

```
   Microphone (shared mode, 48kHz mono)
        |
        v
   Ring buffer  ---------------------------- Phase 1
        |
        v
   High-pass + absolute threshold
        |
        v
   Impulse events (timestamp, level, window)
        |
        v
   Tap / not-tap filter ------------------- Phase 2
        |
        v
   Gesture assembler (count within window)
        |
        v
   Gesture (DOUBLE | TRIPLE)
        |
        v
   Action dispatcher ---------------------- Phase 3
        |
        v
   Windows action

   Desktop UI (system tray) --------------- Phase 4
   SQLite config + history ---------------- Phase 5
   Optional Node/Postgres learning layer -- Phase 6
```

### 8.1 Stack

**Product:**
- Python 3.13, `numpy`, `sounddevice`
- `PySide6` for the tray application
- `sqlite3` (standard library) for configuration and history
- `pytest`

`scipy` and `scikit-learn` are **not** dependencies until Phase 2 demonstrates that heuristics are
insufficient. Phase 0 ran entirely on numpy.

**Phase 6 (optional, learning only):** Node.js, TypeScript, Express, Prisma, PostgreSQL, Redis, JWT.

### 8.2 A note on the backend

Revision 1 specified Node + Express + PostgreSQL + Redis + JWT + OpenAPI for a **single-user
application talking to localhost**. Revision 1 §20 Rule 5 said "do not add technologies simply to
make the project look advanced" — and then the stack section did exactly that.

Revision 2 resolves the contradiction honestly: **SQLite is the product's persistence.** The
Node/PostgreSQL/Redis/auth layer is retained as **Phase 6, explicitly labelled a learning
exercise**, not a product requirement.

This is the stronger position to defend. "I used SQLite because one local user does not need a
database server, and built a REST layer separately to learn backend engineering" survives scrutiny.
"My single-user desktop app needs JWT refresh tokens" does not.

---

## 9. Development phases

Phased, with a **measurable exit gate on every phase.** Nothing proceeds on a phase whose gate has
not been met. This discipline is what caught the Revision 1 failure in one evening instead of month
three.

---

### Phase 0 — Feasibility **[COMPLETE]**

**Goal:** Determine whether the sensing concept works at all before building anything.

**Delivered:** `spike/spike.py`, `spike/FINDINGS.md`.

**Outcome:** Four-zone localization disproved (30.6% cross-session against an 85% gate). Hardware
capture path characterized. Product redefined around temporal patterns.

**What this phase established that the rest depends on:**
- Cross-session evaluation is mandatory; same-session numbers lie by ~38 points
- Shared mode is sufficient and avoids the microphone lock
- Absolute threshold, not adaptive floor
- Detection must run high-passed

---

### Phase 1 — Detection core

**Goal:** A command-line program that prints `DOUBLE TAP` / `TRIPLE TAP` reliably.

**Build:**
- Shared-mode capture, 48kHz mono, ring buffer
- High-pass, absolute-threshold onset detection, 80ms refractory
- Gesture assembler: count impulses within the §6 timing windows
- Threshold calibration: user taps 5 times, threshold is set from the observed levels
- Reuse: `detect_loop`, `monitor`, and `fpr` in `spike/spike.py` are the working starting points

**Gate:**
- SC-1 detection rate >= 95% on 100 deliberate double-taps
- SC-2 preliminary: <= 5 false triggers per hour over 1 hour of normal work
- SC-3 latency <= 400ms p95

**If SC-2 preliminary cannot reach 5/hour with threshold tuning alone, stop and go to Phase 2
before building anything else.**

---

### Phase 2 — Rejection quality

**Goal:** Reach SC-2 (<= 1 false trigger/hour) and close the §3.4 unknown.

**Build:**
- Collect a labelled negative dataset: typing, mouse clicks, mug placement, drawer, door, speech,
  music, keyboard tray knocks
- Collect a labelled positive dataset: deliberate taps, varied force and position
- Measure the Phase 1 heuristic against both; produce a confusion matrix
- **Only if the heuristic misses the gate:** add features (attack time, spectral centroid, decay
  shape, band ratios) and a binary tap/not-tap classifier. This is where `scikit-learn` earns its
  place, if it does.
- Test detection during an active call (§3.4)

**Gate:**
- SC-2: <= 1 false trigger/hour over 4 hours of real work
- SC-4: <= 2% gesture confusion
- Call behaviour characterized and documented — either it works, or TapSense pauses during calls

**Evaluation rule:** train and test on data collected in **different sessions**. The Phase 0 failure
came from ignoring exactly this.

---

### Phase 3 — Action engine

**Goal:** Gestures do something useful.

**Build:**
- Action registry: media keys (play/pause, next, previous), launch application, open URL, run a
  configured script, copy text
- Validation at the configuration boundary — a configured action is a trust boundary. Script and
  application actions must not become arbitrary command execution through unvalidated config.
- Failure reporting: an action that fails is surfaced, not swallowed
- Rejected gestures must be structurally incapable of reaching the dispatcher

**Gate:** double-tap pauses and resumes music reliably; a deliberately malformed action
configuration is rejected rather than executed.

---

### Phase 4 — Desktop application

**Goal:** Something usable without a terminal.

**Build:**
- PySide6 system tray application
- Listening on/off, current state visible
- Threshold calibration flow
- Action configuration per gesture
- Live event log for debugging and trust
- Error surfacing: microphone unavailable, device changed, action failed

**Gate:** SC-6 (idle CPU <= 2% of one core). Usable for a full working day without touching a
terminal.

---

### Phase 5 — Persistence

**Goal:** Settings survive restart; history is queryable.

**Build:**
- SQLite schema: settings, gesture-to-action bindings, calibration values, event history
- Migration approach for schema changes
- History view: what fired, when, at what level — this is also the evidence trail for SC-2

**Gate:** configuration survives restart; event history queryable; SC-2 re-measured from stored
history rather than by hand.

---

### Phase 6 — Backend layer **[OPTIONAL — learning exercise, not a product requirement]**

**Goal:** Learn backend engineering. Explicitly not required for TapSense to work.

**Build:**
- Node.js + TypeScript + Express, layered routes/controllers/services/repositories
- PostgreSQL via Prisma; migrate the Phase 5 schema
- Zod request validation, consistent error contracts
- JWT authentication and authorization
- Redis **only** where a measured benefit is shown — otherwise document the decision to omit it
- OpenAPI documentation
- Jest + Supertest

**Gate:** the desktop application keeps working with the backend **switched off**. If it does not,
the layering is wrong.

**Framing:** present this as a deliberate learning exercise. Do not present it as necessary
architecture for a single-user local application.

---

## 10. Engineering questions to be able to answer

Answerable without notes, per Revision 1 §20 Rule 1.

**Audio:** How does a microphone produce samples? Why does sample rate matter? Why buffer? Why does
a high-pass filter improve tap detection when the tap is not high-frequency?

**Detection:** Why absolute threshold here rather than adaptive? What is a refractory period and why
does its length matter? What is the trade-off between detection rate and false-positive rate, and
who chooses where to sit on it?

**Gesture:** Why is single tap deliberately unusable? Why is a confirm window unavoidable when
distinguishing double from triple, and when can it be skipped?

**Evaluation:** Why did Phase 0's same-session accuracy exceed cross-session by 38 points? Why is
false-positive rate per hour a better metric than accuracy? What makes a negative dataset good?

**Architecture:** Why is the real-time loop local? Why does a single-user local app not need
PostgreSQL? What should happen when the microphone disappears mid-session?

**Physics:** Why did four-zone localization fail? What is structure-borne versus air-borne
propagation, and why does it eliminate time-of-arrival difference? What would the hardware need to
look like for localization to work?

---

## 11. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| SC-2 unreachable — false positives stay too high | **Project-ending** | Phase 2 gate. If unreachable with a classifier, require a modifier key held while tapping, or abandon. |
| Detection fails during calls (§3.4) | Medium | Measure in Phase 2. Fallback: pause TapSense while a call application is running. |
| Windows audio processing differs on other hardware | Medium | `spike.py devices` is the portable probe. SC-5 tests a second machine. |
| Two gestures is too few to be useful | Low | Accepted and stated in §4.1. Evaluate quadruple-tap in Phase 2. |
| Latency feels sluggish | Low | §6.1 optimisation removes it for the common case. |

---

## 12. Repository layout

```
tapsense/
  PRD_TapSense.md          this document
  spike/
    spike.py               FROZEN Phase 0 artifact - produced FINDINGS.md
    FINDINGS.md            measured hardware evidence
    data/                  Phase 0 recordings (gitignored, ~4MB)
  src/                     Phase 1+ product code
  tests/                   pytest
```

`spike/` is frozen evidence, not product code. Phase 1 starts fresh in `src/`. The `devices`,
`monitor`, and `fpr` subcommands of `spike.py` remain useful as measurement tools throughout.

---

## 13. Immediate next step

**Phase 1, detection core.** Reuse `detect_loop` from `spike/spike.py`, switch it to shared mode
with an absolute threshold, and add the gesture assembler.

First deliverable: a terminal window that prints `DOUBLE TAP` when you double-tap the desk, and
prints nothing at all when you work normally for an hour.
