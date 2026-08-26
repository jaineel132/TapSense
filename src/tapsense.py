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
REFRACTORY_S = 0.08         # ignore new impulses this soon after one -- stops a single
                            # tap's ringing being counted twice. Humans cannot
                            # deliberately tap faster than ~125ms apart.
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


def impulses(stream, threshold, on_impulse, stop_when, on_tick=None):
    """Call on_impulse(time, level) for each sharp sound above `threshold`."""
    last = 0.0
    while not stop_when():
        block, _ = stream.read(BLOCK)
        now = time.monotonic()
        lvl = level_of(block)
        if lvl >= threshold and (now - last) >= REFRACTORY_S:
            last = now
            on_impulse(now, lvl)
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
            name = self.NAMES.get(self.count)
            count, self.count = self.count, 0
            if name:
                self.on_gesture(name, count)


# ---------------------------------------------------------------- config

def load_threshold():
    if CONFIG.exists():
        return json.loads(CONFIG.read_text())["threshold"]
    return None


# ---------------------------------------------------------------- commands

def cmd_calibrate(a):
    """Learn how loud this person's taps are on this desk."""
    print("\n  Tap the desk 5 times, at the strength you would normally use.")
    print("  Leave about a second between them.\n")
    seen = []
    with open_stream(a.device) as s:
        try:
            impulses(s, MIN_LEVEL,
                     lambda t, lvl: (seen.append(lvl),
                                     print("   tap {}/5   strength {:.1e}".format(len(seen), lvl))),
                     lambda: len(seen) >= 5)
        except KeyboardInterrupt:
            print("\n  stopped early")

    if len(seen) < 3:
        print("\n  Only got {} taps -- not enough to judge. Tap a bit harder and rerun.".format(len(seen)))
        return

    # Sit well below the softest tap so slightly gentler ones still register, but
    # never below the level where ordinary desk noise lives.
    threshold = max(min(seen) * 0.4, MIN_LEVEL)
    CONFIG.write_text(json.dumps({"threshold": threshold}, indent=2))
    print("\n  Softest tap: {:.1e}".format(min(seen)))
    print("  Threshold set to {:.1e}, saved to {}".format(threshold, CONFIG.name))
    print("\n  Now run:  python src/tapsense.py listen")


def cmd_listen(a):
    threshold = a.threshold or load_threshold()
    if threshold is None:
        print("\n  No threshold yet. Run this first:")
        print("    python src/tapsense.py calibrate\n")
        return 1

    print("\n  Listening. Double-tap or triple-tap the desk. Ctrl+C to stop.")
    print("  (single taps are ignored on purpose)\n")

    stats = {"impulses": 0, "gestures": 0}

    def fired(name, count):
        stats["gestures"] += 1
        print("   >>> {}".format(name))

    def saw(t, lvl):
        stats["impulses"] += 1
        print("   .  impulse   strength {:.1e}".format(lvl))

    g = Gestures(fired)
    with open_stream(a.device) as s:
        try:
            impulses(s, threshold, lambda t, lvl: (saw(t, lvl), g.impulse(t, lvl)),
                     lambda: False, on_tick=g.tick)
        except KeyboardInterrupt:
            print("\n  {} impulses -> {} gestures".format(stats["impulses"], stats["gestures"]))
            print("  Impulses with no gesture are single taps and noise, correctly ignored.\n")


def cmd_selftest(a):
    """Check the gesture logic without needing a microphone.

    Feeds made-up impulse times through the real Gestures class and checks the right
    gestures come out. If this breaks, `listen` is broken too.
    """
    def run(times):
        out = []
        g = Gestures(lambda name, n: out.append(name))
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

    # A tap's own ringing must not count as a second tap.
    assert REFRACTORY_S < 0.125, "refractory must allow deliberate fast taps"
    assert MAX_GAP_S > REFRACTORY_S, "gesture window must be longer than the refractory period"
    print("\n  All gesture logic checks passed.\n")


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
    l.set_defaults(f=cmd_listen)

    sub.add_parser("selftest", help="check gesture logic, no mic needed").set_defaults(f=cmd_selftest)

    a = p.parse_args()
    return a.f(a)


if __name__ == "__main__":
    sys.exit(main())
