# TapSense — What we built, phase by phase

Plain-English progress log. The PRD says what to build and why; this says what is actually done.

**Status key:** DONE · IN PROGRESS · NOT STARTED

---

## The project in three sentences

TapSense lets you tap your desk to control your laptop — double-tap to pause music, that kind of
thing. It listens through the laptop microphone and watches for the *rhythm* of your taps, not
where you tapped. Everything runs on your own machine; nothing is sent anywhere.

---

## Phase 0 — Does this idea even work? · **DONE**

**The question:** The original plan was to detect *which corner* of the desk you tapped, giving four
separate buttons. Before building anything, we tested whether that was possible.

**What we built:** A small throwaway test program (`spike/spike.py`) that recorded taps at each
corner and checked whether the computer could tell them apart.

**What happened:** It failed. The computer could tell the corners apart only while the laptop sat in
exactly one spot. Move the laptop a few centimetres and it was back to guessing.

**Why it failed:** Physics, not code. Tap sounds travel through the laptop's *body* rather than
through the air, and the body carries sound extremely fast. So both microphones hear the tap at the
same instant, and there's no difference in timing to measure. Direction is unrecoverable. Two
microphones sitting centimetres apart cannot fix this, and neither can better software.

**What else we learned:**
- The microphone does **not** have to be locked to TapSense. It can be shared, so Zoom and Discord
  keep working while TapSense listens. (The four-corner design would have required locking it.)
- When your desk is quiet, Windows cleans the microphone stream down to pure silence. That is
  genuinely helpful — anything that isn't silence is worth looking at.
- Taps do survive Windows' noise cleanup. They come through clearly.

**The decision:** Drop corner detection. Use tap *rhythm* instead — double-tap and triple-tap. This
works on any laptop, not just this one.

**Files:** `spike/spike.py` (frozen), `spike/FINDINGS.md` (the evidence)

> **Why this phase mattered:** It cost one evening and saved weeks. Without it we'd have built the
> whole app on top of a feature that only worked while nobody moved the laptop.

---

## Phase 1 — Can we detect a deliberate double-tap? · **IN PROGRESS**

**The question:** Can the computer reliably tell "you tapped the desk twice on purpose" apart from
"you were just working"?

**What we're building:** A terminal program that prints `DOUBLE TAP` or `TRIPLE TAP` when you tap.
Nothing else — no window, no buttons, no actions, no saved data.

**How it works:**
1. Listen to the microphone continuously.
2. Watch for sudden bursts of sound. Each burst means something hit the desk.
3. **Throw away lone bursts.** One burst could be a mug, a door, a dropped pen — we can never
   trust it.
4. Two bursts close together is not an accident. That's a deliberate double-tap.
5. Three close together is a triple-tap.

**The key idea:** We are not identifying the *sound* of a tap. We're identifying the *rhythm*.
It doesn't matter what your tap sounded like — it matters that there were two of them, close
together, on purpose. Rhythm is far harder to trigger by accident than sound.

**Done when:**
- It catches nearly every deliberate double-tap
- It stays quiet while you work normally
- It reacts fast enough to feel responsive

**Files:** `src/tapsense.py`

---

## Phase 2 — Does it stay quiet while you actually work? · NOT STARTED

**The question:** Phase 1 is tested by deliberately tapping. This phase tests the opposite — does it
misfire during a normal working day?

**What we'll build:** A recording of ordinary desk noise (typing, mouse clicks, mug, drawer, door,
talking, music) and a measurement of how often TapSense wrongly thinks that was a tap.

If it misfires too often, we teach it the difference between a real tap and desk noise properly,
using examples of both.

**Also settles one open question:** during a video call, someone is talking constantly, so the
microphone is never silent. We don't yet know how tap detection behaves then.

**Done when:** it almost never misfires during a normal working day.

> **This is the make-or-break phase.** A gesture system that fires when you didn't gesture gets
> uninstalled the same day. Now that corner detection is gone, "is this a real tap or not" *is* the
> entire product.

---

## Phase 3 — Make the taps do something · NOT STARTED

**What we'll build:** Connect gestures to real actions — pause/play music, skip track, open an app,
open a website, run a script.

**Careful bit:** letting a config file run programs is exactly how software gets exploited. Anything
the user configures gets validated before it runs.

**Done when:** double-tapping your desk actually pauses your music.

---

## Phase 4 — Turn it into a real app · NOT STARTED

**What we'll build:** A small system-tray application. Turn listening on and off, set up your taps,
choose your actions, see what it detected — all without a terminal.

**Done when:** you can use it all day without opening a terminal, and it doesn't drain your battery.

---

## Phase 5 — Remember settings · NOT STARTED

**What we'll build:** A small local database so your settings survive a restart, plus a history of
what fired and when.

**Done when:** you set it up once and it stays set up.

---

## Phase 6 — Backend layer · NOT STARTED · *optional*

**Not needed for TapSense to work.** This phase exists to learn backend engineering: a web API,
a proper database server, login and accounts.

**Done when:** the desktop app still works perfectly with the backend switched off. If turning the
backend off breaks the app, the design is wrong.

**How to describe it honestly:** "I used a small local database for the app itself, because one
person on one machine doesn't need a database server. I built the backend separately to learn how
backend systems are structured." That's defensible. Claiming a single-user desktop app *needs*
login tokens is not.

---

## The rule we follow

**Every phase has to prove it works before the next one starts.**

That's what caught the Phase 0 failure in one evening instead of month three. It's also the most
useful thing to say about this project: the four-corner idea was tested, disproved, and replaced
with something that actually works — rather than built, shipped, and quietly broken.
