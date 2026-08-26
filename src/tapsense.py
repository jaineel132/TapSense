#!/usr/bin/env python3
"""TapSense Phase 1 -- detection core.

Prints DOUBLE TAP / TRIPLE TAP when you tap the desk. Nothing else yet.

    python src/tapsense.py calibrate    # tap 5 times, learns how loud your taps are
    python src/tapsense.py listen       # prints gestures as you tap
    python src/tapsense.py selftest     # checks the gesture logic, no mic needed

Why this design (see PRD sections 2.3 and 6):
  A single impulse is thrown away. One thud could be a mug, a door, a dropped pen,
  and we can never tell which -- so binding anything to it guarantees misfires.
  Two impulses at a deliberate interval essentially never happen by accident.
  We match the RHYTHM, not the sound.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

SR = 48000
BLOCK = 256                 # ~5.3ms of audio per read

# Timing (PRD section 6). Tuned to human tapping, not guessed at random:
MAX_GAP_S = 0.35            # impulses further apart than this belong to different gestures
MIN_LEVEL = 1e-5            # never go below this: measured desk noise artifacts sit under it

CONFIG = Path(__file__).parent / "config.json"


# ---------------------------------------------------------------- audio

def find_device():
    """The WASAPI microphone input.

    Shared mode on purpose -- Phase 0 showed exclusive mode locks the mic away from
    every other app, and we only ever needed exclusive mode for the corner detection
    that got cut. Shared mode lets Zoom and Discord keep working alongside us.
    """
    import sounddevice as sd
    wasapi = [i for i, h in enumerate(sd.query_hostapis()) if "WASAPI" in h["name"]]
    for i, d in enumerate(sd.query_devices()):
        if d["hostapi"] in wasapi and d["max_input_channels"] > 0 and "icrophone" in d["name"]:
            return i
    return sd.default.device[0]


def open_stream(device=None):
    import sounddevice as sd
    return sd.InputStream(samplerate=SR, channels=1, blocksize=BLOCK,
                          dtype="float32", device=device if device is not None else find_device())


def level_of(block):
    """How much sudden, sharp sound is in this block of audio.

    np.diff is a cheap high-pass filter: it reports how fast the signal is CHANGING
    rather than how loud it is. That matters because most of what a laptop mic picks
    up at rest is low, slow rumble -- fans, the desk, the building. A tap is a sharp
    spike. Measuring change instead of loudness makes the tap stand out and the
    rumble disappear.
    """
    return float(np.sqrt((np.diff(block[:, 0].astype(np.float64)) ** 2).mean()))


class Trigger:
    """Fires once per sound event, at the moment it starts.

    Why not just "any block louder than the threshold": a knuckle tap RINGS. Measured
    on this desk, one tap stays above the threshold for ~85ms. Firing on every loud
    block would report a single tap as a dozen impulses.

    A fixed cooldown does not fix it either -- the ring-out lasted longer than any
    cooldown short enough to still allow a deliberate fast double-tap.

    So: fire on the rising edge, then refuse to fire again until the sound has
    genuinely died away (dropped to RELEASE of the threshold). Long ring-out, one
    impulse. Two real taps, two impulses. This is a Schmitt trigger.
    """

    RELEASE = 0.3           # "quiet" means below 30% of the trigger threshold
    RELEASE_HOLD_S = 0.02   # and it must STAY quiet this long before we re-arm
    REARM_S = 0.04          # minimum time between two separate taps

    def __init__(self, threshold):
        self.threshold = threshold
        self.armed = True
        self.onset_t = 0.0
        self.peak = 0.0
        self.quiet_since = None

    def update(self, t, level):
        """Returns True exactly once per sound event, on its first loud block."""
        if self.armed:
            if level >= self.threshold:
                self.armed = False
                self.onset_t, self.peak = t, level
                self.quiet_since = None
                return True
            return False

        self.peak = max(self.peak, level)
        if level < self.threshold * self.RELEASE:
            # Ringing does not fade smoothly, it wobbles -- a real tap dips quiet for
            # one block then comes back. Requiring a SUSTAINED quiet stretch is what
            # stops one tap being counted as two.
            if self.quiet_since is None:
                self.quiet_since = t
            elif (t - self.quiet_since) >= self.RELEASE_HOLD_S and (t - self.onset_t) >= self.REARM_S:
                self.armed = True
        else:
            self.quiet_since = None     # loud again: the tap is still ringing
        return False


def impulses(stream, threshold, on_impulse, stop_when, on_tick=None, on_event_end=None):
    """Call on_impulse(time, level) once per tap, at the instant it starts."""
    trig = Trigger(threshold)
    was_armed = True
    while not stop_when():
        block, _ = stream.read(BLOCK)
        now = time.monotonic()
        lvl = level_of(block)
        if trig.update(now, lvl):
            on_impulse(now, lvl)
        if on_event_end and trig.armed and not was_armed:
            on_event_end(trig.onset_t, trig.peak)   # full strength, known only at the end
        was_armed = trig.armed
        if on_tick:
            on_tick(now)


# ---------------------------------------------------------------- gestures

class Gestures:
    """Turns a stream of impulse timestamps into gestures.

    Collects impulses that arrive close together, then waits MAX_GAP_S of quiet to
    decide the gesture is finished. Waiting is unavoidable: the moment the second tap
    lands we cannot yet know whether a third is coming.

    1 impulse  -> ignored (this is the noise rejection)
    2 impulses -> DOUBLE TAP
    3 impulses -> TRIPLE TAP
    4+         -> ignored (nobody taps four times on purpose; likely a rattle)
    """

    NAMES = {2: "DOUBLE TAP", 3: "TRIPLE TAP"}

    def __init__(self, on_gesture, max_gap=MAX_GAP_S):
        self.on_gesture = on_gesture
        self.max_gap = max_gap
        self.count = 0
        self.last = 0.0

    def impulse(self, t, level=0.0):
        self.count += 1
        self.last = t

    def tick(self, now):
        """Call regularly. Fires the gesture once the quiet window has passed."""
        if self.count and (now - self.last) >= self.max_gap:
            count, self.count = self.count, 0
            # name is None for 1 tap or 4+: reported so --verbose can show what was
            # thrown away, which is how you tell "missed my tap" from "heard noise".
            self.on_gesture(self.NAMES.get(count), count)


# ---------------------------------------------------------------- config

def load_threshold():
    if CONFIG.exists():
        return json.loads(CONFIG.read_text())["threshold"]
    return None


# ---------------------------------------------------------------- commands

def cmd_calibrate(a):
    """Measure this desk, then set a threshold that sits between noise and taps.

    Two steps, because one is not enough: we need to know how loud the room is when
    nothing is happening AND how loud a tap is. The threshold goes between them.
    Guessing either one is how the first version got a threshold that missed taps.
    """
    with open_stream(a.device) as s:
        # Step 1 -- what does this desk sound like when nothing happens?
        print("\n  Step 1 of 2: sit still and stay quiet for 3 seconds...\n")
        bg = []
        t_end = time.monotonic() + 3.0
        while time.monotonic() < t_end:
            bg.append(level_of(s.read(BLOCK)[0]))
        noise = float(np.percentile(bg, 99))
        print("   background noise: {:.1e}".format(noise))

        # Threshold well clear of the noise. Taps measured on this hardware run
        # 10-100x above background, so 4x leaves room for gentler taps.
        threshold = max(noise * 4, MIN_LEVEL)

        # Step 2 -- how strong is an actual tap? Uses the full event strength
        # (its peak), not the first block, which is only a fraction of the way up.
        print("\n  Step 2 of 2: tap the desk 5 times, normal strength, ~1s apart.\n")
        peaks = []
        try:
            impulses(s, threshold, lambda t, lvl: None, lambda: len(peaks) >= 5,
                     on_event_end=lambda t, pk: (peaks.append(pk),
                                                 print("   tap {}/5   strength {:.1e}".format(len(peaks), pk))))
        except KeyboardInterrupt:
            print("\n  stopped early")

    if len(peaks) < 3:
        print("\n  Only caught {} taps.".format(len(peaks)))
        print("  Either tap harder, or the mic is not picking up this desk well.")
        print("  Run 'debug' to see what is actually reaching the mic.")
        return 1

    # Use the MEDIAN tap, not the softest. Calibration inevitably catches a couple of
    # ring fragments alongside the real taps, and those junk readings are always the
    # quietest -- taking the minimum would drag the threshold down to their level,
    # which is exactly how it ended up pinned to the floor and counting echoes as taps.
    typical = float(np.median(peaks))

    # A tap's first moment is only a few percent of its peak (the sound ramps up),
    # so the threshold has to sit well under the peak to catch the onset at all.
    threshold = max(typical * 0.05, noise * 4, MIN_LEVEL)

    CONFIG.write_text(json.dumps({"threshold": threshold, "noise": noise,
                                  "typical_tap": typical}, indent=2))
    print("\n  background   {:.1e}".format(noise))
    print("  typical tap  {:.1e}".format(typical))
    print("  threshold    {:.1e}   <- saved".format(threshold))
    print("\n  Your typical tap is {:.0f}x above the threshold.".format(typical / threshold))
    if typical / threshold < 5:
        print("  That is tight -- gentle taps may be missed and noise may slip through.")
        print("  Tapping a little harder, or on a harder part of the desk, will help.")
    else:
        print("  Good margin.")
    print("\n  Now run:  python src/tapsense.py listen")


def cmd_listen(a):
    threshold = a.threshold or load_threshold()
    if threshold is None:
        print("\n  No threshold yet. Run this first:")
        print("    python src/tapsense.py calibrate\n")
        return 1

    print("\n  Listening. Double-tap or triple-tap the desk. Ctrl+C to stop.")
    print("  (single taps are ignored on purpose)")
    if not a.verbose:
        print("  Add --verbose to see every sound it hears.")
    print()

    stats = {"impulses": 0, "gestures": 0, "ignored": 0}

    def fired(name, count):
        if name:
            stats["gestures"] += 1
            # Clock time, so an hour-long unattended run can be read back afterwards
            # and lined up against what you were doing when it fired.
            print("   {}   >>> {}".format(time.strftime("%H:%M:%S"), name))
        else:
            stats["ignored"] += 1
            if a.verbose:
                print("   -   ignored ({} tap{})".format(count, "s" if count > 1 else ""))

    def saw(t, lvl):
        stats["impulses"] += 1
        if a.verbose:
            print("   .   tap heard, strength {:.1e}".format(lvl))

    g = Gestures(fired)
    started = time.monotonic()
    with open_stream(a.device) as s:
        try:
            impulses(s, threshold, lambda t, lvl: (saw(t, lvl), g.impulse(t, lvl)),
                     lambda: False, on_tick=g.tick)
        except KeyboardInterrupt:
            hours = (time.monotonic() - started) / 3600
            print("\n  ran {:.1f} min".format(hours * 60))
            print("  {} taps heard -> {} gestures, {} ignored".format(
                stats["impulses"], stats["gestures"], stats["ignored"]))
            print("  Ignored ones are single taps and stray noise, thrown away on purpose.")
            if hours > 0:
                # The Phase 1 / Phase 2 gate: if you did NOT tap during this run,
                # every gesture above was a false fire, and this is the number to beat.
                print("\n  If you never tapped on purpose: {:.1f} false fires per hour".format(
                    stats["gestures"] / hours))
                print("  Phase 1 target: under 5/hour.   Phase 2 target: under 1/hour.\n")


def cmd_debug(a):
    """Show every sound the mic delivers, with no threshold filtering.

    Use this when taps are not being detected: it answers whether they are arriving
    at all, and at what strength, instead of guessing at threshold values.
    """
    thr = a.threshold or load_threshold() or MIN_LEVEL
    print("\n  Threshold: {:.1e}.  Each line below is ONE detected sound event.".format(thr))
    print("  'rings' is how long it stayed loud -- a tap keeps sounding after you hit it.")
    print("  Tap 5 times normally, then 5 harder, then sit still. Ctrl+C to stop.\n")

    events, bg = [], []
    trig = Trigger(thr)
    was_armed = True
    t0 = time.monotonic()
    with open_stream(a.device) as s:
        try:
            while True:
                block, _ = s.read(BLOCK)
                now = time.monotonic()
                lvl = level_of(block)
                if trig.armed:
                    bg.append(lvl)
                trig.update(now, lvl)
                if trig.armed and not was_armed:
                    dur = (now - trig.onset_t) * 1000
                    events.append((trig.peak, dur))
                    print("   {:>6.1f}s   strength {:.2e}   rings {:>5.0f} ms   {}".format(
                        trig.onset_t - t0, trig.peak, dur,
                        "#" * min(int(trig.peak / thr), 40)))
                was_armed = trig.armed
        except KeyboardInterrupt:
            pass

    if not events:
        print("\n  No sound ever crossed the threshold.")
        if bg:
            print("  Loudest thing heard was {:.1e}, threshold is {:.1e}.".format(max(bg), thr))
            print("  Threshold is too high -- rerun calibrate, or try --threshold {:.0e}".format(
                max(bg) * 0.5))
        return

    pk = np.array([e[0] for e in events])
    dur = np.array([e[1] for e in events])
    print("\n  {} events.  strength: softest {:.1e}  loudest {:.1e}".format(len(pk), pk.min(), pk.max()))
    print("  ring time: shortest {:.0f} ms   median {:.0f} ms   longest {:.0f} ms".format(
        dur.min(), np.median(dur), dur.max()))
    if bg:
        print("  background between events: {:.1e}".format(np.percentile(bg, 99)))
        print("  margin, softest event over background: {:.0f}x".format(
            pk.min() / max(np.percentile(bg, 99), 1e-12)))
    print("\n  One line per tap = detection is working.")
    print("  Several lines from one tap = it is still double-counting; tell me.\n")


def cmd_selftest(a):
    """Check the gesture logic without needing a microphone.

    Feeds made-up impulse times through the real Gestures class and checks the right
    gestures come out. If this breaks, `listen` is broken too.
    """
    def run(times):
        out = []
        g = Gestures(lambda name, n: out.append(name) if name else None)
        for t in times:
            g.tick(t)
            g.impulse(t)
        g.tick(times[-1] + MAX_GAP_S + 0.01)    # let the final gesture settle
        return out

    cases = [
        ("single tap ignored",          [0.0],                    []),
        ("double tap",                  [0.0, 0.15],              ["DOUBLE TAP"]),
        ("triple tap",                  [0.0, 0.15, 0.30],        ["TRIPLE TAP"]),
        ("two taps far apart",          [0.0, 2.0],               []),
        ("four taps ignored",           [0.0, .15, .30, .45],     []),
        ("double then double",          [0.0, 0.15, 2.0, 2.15],   ["DOUBLE TAP", "DOUBLE TAP"]),
        ("double then single",          [0.0, 0.15, 2.0],         ["DOUBLE TAP"]),
    ]
    for name, times, want in cases:
        got = run(times)
        assert got == want, "{}: expected {}, got {}".format(name, want, got)
        print("   ok  {:<22} -> {}".format(name, got or "(ignored)"))

    assert MAX_GAP_S > 0.125, "gesture window must allow deliberate fast taps"

    # --- Trigger: one tap must produce exactly one impulse, however long it rings.
    def fire(levels, threshold=2e-5, dt=0.00533):
        """Feed a sequence of block levels through Trigger, count the impulses."""
        trig, n = Trigger(threshold), 0
        for i, lv in enumerate(levels):
            if trig.update(i * dt, lv):
                n += 1
        return n

    quiet = [1e-6] * 20
    # A real tap measured on this desk: sharp onset, then ~85ms of ring-out.
    tap = [2.4e-5, 3.1e-5, 3.7e-4, 1.0e-4, 1.2e-4, 1.8e-4, 7.1e-5, 4.0e-5,
           9.7e-5, 1.4e-5, 6.1e-5, 1.7e-5, 2.8e-5, 5.9e-6, 2.7e-5, 4.2e-6, 1.9e-5]

    trigger_cases = [
        ("silence fires nothing",        quiet,                          0),
        ("one tap, one impulse",         quiet + tap + quiet,            1),
        ("two taps, two impulses",       quiet + tap + quiet + tap + quiet, 2),
        ("quiet blip below threshold",   quiet + [5e-6] * 5 + quiet,     0),
    ]
    for name, levels, want in trigger_cases:
        got = fire(levels)
        assert got == want, "{}: expected {} impulse(s), got {}".format(name, want, got)
        print("   ok  {:<22} -> {} impulse(s)".format(name, got))

    # The bug this replaced: a fixed cooldown shorter than the ring-out split one
    # tap into several impulses, so a single tap looked like a double tap.
    assert fire(quiet + tap + quiet) == 1, "one tap must never produce two impulses"

    print("\n  All gesture and trigger checks passed.\n")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("calibrate", help="learn how loud your taps are")
    c.add_argument("--device", type=int, default=None)
    c.set_defaults(f=cmd_calibrate)

    l = sub.add_parser("listen", help="print gestures as you tap")
    l.add_argument("--device", type=int, default=None)
    l.add_argument("--threshold", type=float, default=None)
    l.add_argument("--verbose", "-v", action="store_true", help="show every sound heard")
    l.set_defaults(f=cmd_listen)

    d = sub.add_parser("debug", help="show every sound, unfiltered -- use when taps are missed")
    d.add_argument("--device", type=int, default=None)
    d.add_argument("--threshold", type=float, default=None)
    d.set_defaults(f=cmd_debug)

    sub.add_parser("selftest", help="check gesture logic, no mic needed").set_defaults(f=cmd_selftest)

    a = p.parse_args()
    return a.f(a)


if __name__ == "__main__":
    sys.exit(main())
