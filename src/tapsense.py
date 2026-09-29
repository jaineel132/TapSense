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


TYPING_GUARD_MS = 250       # ignore impulses this soon after a key press or mouse action


def ms_since_input():
    """Milliseconds since the user last pressed a key or moved/clicked the mouse.

    The keyboard is bolted to the same chassis as the microphone, so a keystroke and
    a desk tap are almost the same physical event -- measured at ~50 false impulses
    per minute while typing normally. No amount of threshold tuning separates them.

    But a deliberate desk tap involves no keyboard and no mouse. So if Windows says
    you touched an input device a few milliseconds ago, that impulse was your typing,
    not your knuckle. Costs 1.4 microseconds per call.
    """
    import ctypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    li = LASTINPUTINFO()
    li.cbSize = ctypes.sizeof(li)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li))
    return ctypes.windll.kernel32.GetTickCount() - li.dwTime


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


def impulses(stream, threshold, on_impulse, stop_when, on_tick=None, on_event_end=None,
             guard_typing=True, on_suppressed=None):
    """Call on_impulse(time, level) once per tap, at the instant it starts.

    Impulses arriving right after keyboard or mouse activity are dropped -- see
    ms_since_input(). Disable with guard_typing=False (calibration does, since you
    press Enter to start it and the first tap would otherwise be swallowed).
    """
    trig = Trigger(threshold)
    was_armed = True
    while not stop_when():
        block, _ = stream.read(BLOCK)
        now = time.monotonic()
        lvl = level_of(block)
        if trig.update(now, lvl):
            if guard_typing and ms_since_input() < TYPING_GUARD_MS:
                if on_suppressed:
                    on_suppressed(now, lvl)
            else:
                on_impulse(now, lvl)
        if on_event_end and trig.armed and not was_armed:
            on_event_end(trig.onset_t, trig.peak)   # full strength, known only at the end
        was_armed = trig.armed
        if on_tick:
            on_tick(now)


def events(stream, threshold, on_event, stop_when):
    """Like impulses(), but hands back the actual audio of each sound.

    Keeps a rolling buffer so the window can start slightly BEFORE the trigger --
    the sharp leading edge is the most distinctive part, and it has already gone past
    by the time the level crosses the threshold.
    """
    pre, post = int(SR * 0.005), int(SR * 0.100)
    ring = np.zeros(pre + post + BLOCK * 2, np.float32)
    trig = Trigger(threshold)
    pending = None

    while not stop_when():
        block, _ = stream.read(BLOCK)
        now = time.monotonic()
        ring = np.concatenate([ring[len(block):], block[:, 0]])
        if trig.update(now, level_of(block)):
            pending = 0
        elif pending is not None:
            pending += BLOCK
            if pending >= post:
                start = len(ring) - pending - pre - BLOCK
                on_event(trig.peak, ring[start:start + pre + post].copy())
                pending = None


def shape_features(win):
    """Numbers describing the SHAPE of a sound, independent of how loud it is.

    Everything here is a ratio or a duration, so a hard tap and a soft tap look alike
    while a tap and a drum hit do not.
    """
    w = win.astype(np.float64)
    w = w - w.mean()
    env = np.abs(w)
    pk = env.max() + 1e-12
    spec = np.abs(np.fft.rfft(w * np.hanning(len(w)))) ** 2
    freq = np.fft.rfftfreq(len(w), 1 / SR)

    def band(lo, hi):
        return spec[(freq >= lo) & (freq < hi)].sum() + 1e-20

    peak_at = int(np.argmax(env))
    above = np.where(env > pk * 0.5)[0]
    return {
        # Laptop speakers physically cannot push low frequencies; a knuckle thumps
        # them straight into the desk and chassis. This is the main hypothesis.
        "low_vs_high": np.log10(band(80, 500) / band(2000, 12000)),
        "low_share": np.log10(band(80, 500) / spec.sum()),
        # A struck desk transfers energy almost instantly; a speaker cone cannot.
        "attack_ms": 1000.0 * peak_at / SR,
        "width_ms": 1000.0 * len(above) / SR,
        "centroid": np.log10((spec * freq).sum() / (spec.sum() + 1e-20) + 1),
        "crest": np.log10(pk / (np.sqrt((w ** 2).mean()) + 1e-12)),
    }


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

def load_config():
    return json.loads(CONFIG.read_text()) if CONFIG.exists() else {}


def load_threshold():
    return load_config().get("threshold")


DEFAULT_ACTIONS = {
    "DOUBLE TAP": {"do": "media", "key": "play_pause"},
    "TRIPLE TAP": {"do": "media", "key": "next"},
}


# ---------------------------------------------------------------- actions

# Windows virtual key codes for the media keys every music player already listens to.
# Using these instead of talking to Spotify/VLC/YouTube directly means one code path
# controls whatever is playing, with no per-app integration and no extra dependency.
MEDIA_KEYS = {
    "play_pause": 0xB3,
    "next": 0xB0,
    "prev": 0xB1,
    "mute": 0xAD,
    "volume_up": 0xAF,
    "volume_down": 0xAE,
}


def press_media_key(vk):
    import ctypes
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)   # key down
    ctypes.windll.user32.keybd_event(vk, 0, 2, 0)   # key up


def resolve(spec):
    """Validate one action from the config file. Returns (description, run).

    Raises ValueError on anything malformed. Config is a trust boundary -- it decides
    what gets launched -- so it is checked before anything runs, never while running.
    Nothing here is eval'd or shelled out as a string.
    """
    if not isinstance(spec, dict) or "do" not in spec:
        raise ValueError("action must be an object with a 'do' field")

    kind = spec["do"]

    if kind == "media":
        key = spec.get("key")
        if key not in MEDIA_KEYS:
            raise ValueError("unknown media key {!r}; choose from: {}".format(
                key, ", ".join(sorted(MEDIA_KEYS))))
        return "media key: " + key, lambda: press_media_key(MEDIA_KEYS[key])

    if kind == "url":
        target = spec.get("target", "")
        if not isinstance(target, str) or not target.startswith(("http://", "https://")):
            raise ValueError("url target must start with http:// or https://")
        import webbrowser
        return "open url: " + target, lambda: webbrowser.open(target)

    if kind == "open":
        target = spec.get("target", "")
        if not isinstance(target, str) or not target:
            raise ValueError("open target must be a path to a file or application")
        if not Path(target).exists():
            raise ValueError("open target does not exist: " + target)
        import os
        return "open: " + target, lambda: os.startfile(target)

    raise ValueError("unknown action type {!r}; choose from: media, url, open".format(kind))


def load_actions():
    """Validate every configured action up front, so a typo fails now, not mid-gesture."""
    configured = load_config().get("actions", DEFAULT_ACTIONS)
    out, errors = {}, []
    for gesture, spec in configured.items():
        try:
            out[gesture] = resolve(spec)
        except ValueError as e:
            errors.append("  {}: {}".format(gesture, e))
    return out, errors


# ---------------------------------------------------------------- commands

def cmd_calibrate(a):
    """Measure this desk, then set a threshold that sits between noise and taps.

    Two steps, because one is not enough: we need to know how loud the room is when
    nothing is happening AND how loud a tap is. The threshold goes between them.
    Guessing either one is how the first version got a threshold that missed taps.
    """
    with open_stream(a.device) as s:
        # Step 1 -- what does this desk sound like when you are NOT tapping?
        #
        # Music through laptop speakers feeds back into the mic, and drum hits look
        # very like taps. Measured on this machine, speaker feedback reached 1.6e-5
        # while a real tap peaks around 2.4e-4. If music is playing during this step
        # its level lands in the background measurement, and the threshold rises above
        # it automatically -- self-calibrating to whatever volume you actually use.
        print("\n  Step 1 of 2: don't tap for 3 seconds.")
        print("  If you listen through SPEAKERS, start your music now at your usual volume.")
        print("  (that way the threshold learns to ignore it)\n")
        time.sleep(1.0)
        bg = []
        t_end = time.monotonic() + 3.0
        while time.monotonic() < t_end:
            bg.append(level_of(s.read(BLOCK)[0]))
        # 99.9th percentile, not 99th. Interference from music is IMPULSIVE -- a drum
        # hit occupies two or three blocks out of hundreds, so an ordinary percentile
        # steps right over it and reports a floor far below what actually triggers.
        # We need to sit above the loudest non-tap thing, not the typical one.
        noise = float(np.percentile(bg, 99.9))
        print("   background: {:.1e}  (loudest non-tap sound)".format(noise))

        threshold = max(noise * 2, MIN_LEVEL)

        # Step 2 -- how strong is an actual tap? Uses the full event strength
        # (its peak), not the first block, which is only a fraction of the way up.
        print("\n  Step 2 of 2: tap the desk 5 times, normal strength, ~1s apart.\n")
        peaks = []
        try:
            impulses(s, threshold, lambda t, lvl: None, lambda: len(peaks) >= 5,
                     guard_typing=False,
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

    # 0.15, not the 0.05 this started at. 0.05 sat below speaker feedback from music,
    # so a drum beat landed as an extra impulse and turned a double-tap into a triple
    # -- which fired "next track" instead of "pause". Raising it does not lose taps:
    # the trigger fires on the way UP through the threshold, so a louder bar just
    # fires a few milliseconds later. It only drops taps whose whole peak is quieter
    # than 15% of a normal one.
    threshold = max(typical * 0.15, noise * 2, MIN_LEVEL)

    CONFIG.write_text(json.dumps({"threshold": threshold, "noise": noise,
                                  "typical_tap": typical}, indent=2))
    print("\n  background   {:.1e}".format(noise))
    print("  typical tap  {:.1e}".format(typical))
    print("  threshold    {:.1e}   <- saved".format(threshold))
    print("\n  Your typical tap is {:.0f}x above the threshold.".format(typical / threshold))
    if typical / threshold < 4:
        print("  That is tight -- gentle taps may be missed and noise may slip through.")
        if noise * 2 >= typical * 0.15:
            print("\n  Your background is loud compared to your taps.")
            print("  If that was music through SPEAKERS: the mic hears it and drum hits")
            print("  look like taps, which turns a double-tap into a triple-tap.")
            print("  HEADPHONES fix this completely -- the mic then hears only you.")
        else:
            print("  Tap harder, or on a firmer part of the desk.")
    else:
        print("  Good margin.")
    print("\n  Now run:  python src/tapsense.py listen")


def cmd_listen(a):
    threshold = a.threshold or load_threshold()
    if threshold is None:
        print("\n  No threshold yet. Run this first:")
        print("    python src/tapsense.py calibrate\n")
        return 1

    actions, errors = load_actions()
    if errors:
        # Refuse to run with a broken config rather than silently skipping actions.
        print("\n  Bad action configuration in {}:".format(CONFIG.name))
        print("\n".join(errors))
        print("\n  Fix it, or delete the 'actions' section to fall back to defaults.\n")
        return 1

    print("\n  Listening. Double-tap or triple-tap the desk. Ctrl+C to stop.")
    print("  (single taps are ignored on purpose)")
    if a.act:
        print("\n  ACTIONS ARMED:")
        for gesture, (what, _) in sorted(actions.items()):
            print("    {:<12} -> {}".format(gesture, what))
    else:
        print("  Actions are NOT armed -- gestures only print. Add --act to run them.")
    if not a.verbose:
        print("  Add --verbose to see every sound it hears.")
    print()

    stats = {"impulses": 0, "gestures": 0, "ignored": 0, "typing": 0}

    def fired(name, count):
        if name:
            stats["gestures"] += 1
            # Clock time, so an hour-long unattended run can be read back afterwards
            # and lined up against what you were doing when it fired.
            print("   {}   >>> {}".format(time.strftime("%H:%M:%S"), name))
            if a.act and name in actions:
                what, run = actions[name]
                try:
                    run()
                except Exception as e:
                    # A failing action must never take the listener down with it.
                    print("       action failed ({}): {}".format(what, e))
        else:
            stats["ignored"] += 1
            if a.verbose:
                print("   -   ignored ({} tap{})".format(count, "s" if count > 1 else ""))

    def saw(t, lvl):
        stats["impulses"] += 1
        if a.verbose:
            print("   .   tap heard, strength {:.1e}".format(lvl))

    def suppressed(t, lvl):
        stats["typing"] += 1
        if a.verbose:
            print("   x   suppressed (you were typing), strength {:.1e}".format(lvl))

    g = Gestures(fired)
    started = time.monotonic()
    with open_stream(a.device) as s:
        try:
            impulses(s, threshold, lambda t, lvl: (saw(t, lvl), g.impulse(t, lvl)),
                     lambda: False, on_tick=g.tick, on_suppressed=suppressed)
        except KeyboardInterrupt:
            hours = (time.monotonic() - started) / 3600
            print("\n  ran {:.1f} min".format(hours * 60))
            print("  {} taps heard -> {} gestures, {} ignored".format(
                stats["impulses"], stats["gestures"], stats["ignored"]))
            print("  {} suppressed as keyboard/mouse noise".format(stats["typing"]))
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


SAMPLES = Path(__file__).parent / "samples"


def cmd_collect(a):
    """Record the sound of each detected event, labelled, so we can compare them."""
    SAMPLES.mkdir(exist_ok=True)
    thr = a.threshold or load_threshold() or MIN_LEVEL
    got = []
    if a.label == "tap":
        print("\n  Recording YOUR TAPS. Music OFF, headphones off, room quiet.")
        print("  Tap the desk {} times, normally, about a second apart.\n".format(a.n))
    else:
        print("\n  Recording FALSE TRIGGERS. Start your music on SPEAKERS now.")
        print("  Do NOT tap. Just let it play until it has caught {}.\n".format(a.n))

    with open_stream(a.device) as s:
        try:
            events(s, thr,
                   lambda pk, win: (got.append(win),
                                    print("   {} {}/{}   strength {:.1e}".format(
                                        a.label, len(got), a.n, pk))),
                   lambda: len(got) >= a.n)
        except KeyboardInterrupt:
            print("\n  stopped early")

    if not got:
        print("\n  Caught nothing. Lower the threshold with --threshold, or tap harder.")
        return 1
    path = SAMPLES / "{}.npz".format(a.label)
    old = list(np.load(path)["w"]) if path.exists() else []
    np.savez_compressed(path, w=np.array(old + got, np.float32))
    print("\n  saved {} -> {}  (total {})".format(len(got), path.name, len(old) + len(got)))


def cmd_analyze(a):
    """Find which shape feature best separates real taps from speaker feedback."""
    sets = {}
    for label in ("tap", "noise"):
        p = SAMPLES / "{}.npz".format(label)
        if not p.exists():
            print("\n  Missing {}. Run:  python src/tapsense.py collect --label {}".format(
                p.name, label))
            return 1
        sets[label] = np.load(p)["w"]
    print("\n  {} taps, {} false triggers\n".format(len(sets["tap"]), len(sets["noise"])))

    feats = {k: [shape_features(w)[k] for w in sets[k2]]
             for k2 in sets for k in shape_features(sets[k2][0])}
    tapf = [shape_features(w) for w in sets["tap"]]
    noif = [shape_features(w) for w in sets["noise"]]

    print("  {:<14} {:>10} {:>10}   {:>9}".format("feature", "taps", "music", "separates"))
    best = []
    for k in tapf[0]:
        t = np.array([f[k] for f in tapf])
        n = np.array([f[k] for f in noif])
        # Best single cut-off between the two groups, and how often it is right.
        cuts = np.unique(np.concatenate([t, n]))
        acc, cut, sign = 0, 0, 1
        for c in cuts:
            for sg in (1, -1):
                a_ = (np.mean(sg * t >= sg * c) + np.mean(sg * n < sg * c)) / 2
                if a_ > acc:
                    acc, cut, sign = a_, c, sg
        best.append((acc, k, cut, sign))
        print("  {:<14} {:>10.2f} {:>10.2f}   {:>8.0%}".format(k, t.mean(), n.mean(), acc))

    best.sort(reverse=True)
    acc, k, cut, sign = best[0]
    print("\n  Best separator: {}  ({:.0%} correct)".format(k, acc))
    print("  Rule: a real tap has {} {} {:.3f}".format(k, ">=" if sign > 0 else "<=", cut))
    if acc < 0.85:
        print("\n  Under 85% -- not reliable enough to act on alone.")
        print("  Collect more samples, or headphones remain the practical answer.")
    else:
        print("\n  Good enough to use. Tell me and I will wire it into the detector.")


def cmd_actions(a):
    """Show what each gesture is wired to, and optionally fire one without tapping."""
    actions, errors = load_actions()
    using = "config.json" if "actions" in load_config() else "built-in defaults"
    print("\n  Actions ({}):\n".format(using))
    for gesture, (what, _) in sorted(actions.items()):
        print("    {:<12} -> {}".format(gesture, what))
    if errors:
        print("\n  Problems:")
        print("\n".join(errors))
    print("\n  Available: media ({}), url, open".format(", ".join(sorted(MEDIA_KEYS))))
    print("  Edit {} to change them.".format(CONFIG))

    if a.run:
        if a.run not in actions:
            print("\n  No action configured for {!r}".format(a.run))
            return 1
        what, run = actions[a.run]
        print("\n  Running {} -> {}".format(a.run, what))
        try:
            run()
            print("  Done.\n")
        except Exception as e:
            print("  Failed: {}\n".format(e))
            return 1
    else:
        print('\n  Try one:  python src/tapsense.py actions --run "DOUBLE TAP"\n')


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

    # --- Actions: a malformed config must be refused, never run.
    bad = [
        ("not an object",       "play_pause"),
        ("missing 'do'",        {"key": "play_pause"}),
        ("unknown type",        {"do": "shell", "cmd": "format c:"}),
        ("unknown media key",   {"do": "media", "key": "self_destruct"}),
        ("url not http",        {"do": "url", "target": "file:///C:/secrets.txt"}),
        ("open missing path",   {"do": "open", "target": "C:/definitely/not/here.exe"}),
        ("open with no target", {"do": "open"}),
    ]
    for name, spec in bad:
        try:
            resolve(spec)
        except ValueError:
            print("   ok  rejected: {}".format(name))
        else:
            raise AssertionError("{} should have been rejected: {!r}".format(name, spec))

    for name, spec, want in [
        ("media", {"do": "media", "key": "play_pause"}, "media key: play_pause"),
        ("url", {"do": "url", "target": "https://x.com"}, "open url: https://x.com"),
    ]:
        what, run = resolve(spec)
        assert what == want, "{}: expected {!r}, got {!r}".format(name, want, what)
        assert callable(run), name
        print("   ok  accepted: {:<7} -> {}".format(name, what))

    assert not load_actions()[1], "the built-in default actions must themselves be valid"
    print("   ok  default actions valid")

    print("\n  All gesture, trigger and action checks passed.\n")


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
    l.add_argument("--act", action="store_true", help="actually run the configured actions")
    l.set_defaults(f=cmd_listen)

    d = sub.add_parser("debug", help="show every sound, unfiltered -- use when taps are missed")
    d.add_argument("--device", type=int, default=None)
    d.add_argument("--threshold", type=float, default=None)
    d.set_defaults(f=cmd_debug)

    co = sub.add_parser("collect", help="record labelled sounds for comparison")
    co.add_argument("--label", required=True, choices=["tap", "noise"])
    co.add_argument("--n", type=int, default=25)
    co.add_argument("--device", type=int, default=None)
    co.add_argument("--threshold", type=float, default=None)
    co.set_defaults(f=cmd_collect)

    sub.add_parser("analyze", help="find what separates taps from music").set_defaults(f=cmd_analyze)

    ac = sub.add_parser("actions", help="show configured actions and test one")
    ac.add_argument("--run", metavar="GESTURE", default=None,
                    help='run one now without tapping, e.g. --run "DOUBLE TAP"')
    ac.set_defaults(f=cmd_actions)

    sub.add_parser("selftest", help="check gesture logic, no mic needed").set_defaults(f=cmd_selftest)

    a = p.parse_args()
    return a.f(a)


if __name__ == "__main__":
    sys.exit(main())


def average_interval(timestamps):
    """Mean gap between consecutive taps, in seconds."""
    gaps = []
    for i in range(len(timestamps)):
        gaps.append(timestamps[i + 1] - timestamps[i])
    return sum(gaps) / len(gaps)


def load_profile(path):
    """Read a saved profile from disk."""
    f = open(path)
    data = f.read()
    return data.strip().split(",")
