#!/usr/bin/env python3

"""TapSense Phase 0 feasibility spike -- FROZEN.

This script produced FINDINGS.md and is kept as evidence for those measurements.
Do not repurpose it as product code; Phase 1 starts fresh in src/.

It answered one question: can this laptop's microphone separate four desk zones,
and does that survive the laptop being moved between sessions?  Answer: no.
30.6% cross-session against a 25% chance baseline. See FINDINGS.md.

Still useful as measurement tools:
    python spike.py devices               # capture paths, channel independence
    python spike.py monitor               # live level meter, pick a threshold
    python spike.py fpr --minutes 10      # false triggers during normal work

Dead, kept only so the reported numbers stay reproducible:
    python spike.py record --zone LF --session A
    python spike.py eval                  # cross-session confusion matrix
    python spike.py selftest              # sanity check for the dead kNN path
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np

SR = 48000
WIN = SR * 40 // 1000       # 40 ms analysis window
PRE = SR * 5 // 1000        # keep 5 ms before the onset so the attack is not clipped
BLOCK = 256                 # ~5.3 ms
REFRACTORY_S = 0.15
N_BANDS = 32
MAX_LAG = 64                # +/- samples for inter-channel cross-correlation
ZONES = ["LF", "LR", "RF", "RR"]
DATA = Path(__file__).parent / "data"


# ---------------------------------------------------------------- features

def _bands(nfft):
    """Log-spaced band edges (as rfft bin indices) from 100 Hz to Nyquist."""
    edges = np.geomspace(100.0, SR / 2, N_BANDS + 1)
    return np.clip((edges / (SR / 2) * (nfft // 2)).astype(int), 0, nfft // 2)


def features(win):
    """win: (WIN, ch) float32 -> 1-D feature vector.

    Spectral coloration (always) + inter-channel TDOA/level cues (if stereo).
    """
    mono = win[:, 0].astype(np.float64)
    mono = mono - mono.mean()
    spec = np.abs(np.fft.rfft(mono * np.hanning(len(mono)))) ** 2

    idx = _bands(len(mono))
    band = np.array([spec[idx[i]:max(idx[i + 1], idx[i] + 1)].sum() for i in range(N_BANDS)])
    band = np.log10(band + 1e-12)
    band = band - band.mean()               # kill overall loudness, keep spectral shape

    if win.shape[1] < 2:
        return band

    a, b = win[:, 0].astype(np.float64), win[:, 1].astype(np.float64)
    a, b = a - a.mean(), b - b.mean()
    xc = np.correlate(a, b, mode="full")
    mid = len(xc) // 2
    lag = int(np.argmax(xc[mid - MAX_LAG:mid + MAX_LAG + 1])) - MAX_LAG
    ratio = 20 * np.log10((np.sqrt((a ** 2).mean()) + 1e-9) / (np.sqrt((b ** 2).mean()) + 1e-9))
    return np.concatenate([band, [float(lag), float(ratio)]])


def feature_slices(dim):
    """Named views into the feature vector, so we can see where the signal lives."""
    s = {"spectral": slice(0, N_BANDS)}
    if dim > N_BANDS:
        s["tdoa"] = slice(N_BANDS, dim)
        s["combined"] = slice(0, dim)
    return s


# ---------------------------------------------------------------- classifier

def knn(Xtr, ytr, Xte, k=5):
    """Hand-rolled kNN. z-scores with TRAIN stats only, then euclidean.

    z-scoring matters: log-band features and a sample-count lag live on wildly
    different scales, and raw distance would ignore the lag entirely.
    """
    mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-9
    A, B = (Xtr - mu) / sd, (Xte - mu) / sd
    d = ((B[:, None, :] - A[None, :, :]) ** 2).sum(-1)
    nn = np.argsort(d, axis=1)[:, :k]
    return np.array([np.bincount(ytr[v], minlength=len(ZONES)).argmax() for v in nn])


def confusion(y_true, y_pred, labels):
    m = np.zeros((len(labels), len(labels)), int)
    for t, p in zip(y_true, y_pred):
        m[t, p] += 1
    return m


def report(m, labels, title):
    print("\n  " + title)
    acc = np.trace(m) / max(m.sum(), 1)
    print("  accuracy: {:.1%}   (chance = {:.0%})".format(acc, 1 / len(labels)))
    print("           " + "".join("{:>7}".format(l) for l in labels) + "     recall")
    for i, l in enumerate(labels):
        rec = m[i, i] / max(m[i].sum(), 1)
        print("   true {:<4}".format(l) + "".join("{:>7}".format(v) for v in m[i])
              + "   {:>7.1%}".format(rec))
    print("   precision " + "".join("{:>6.0%} ".format(m[i, i] / max(m[:, i].sum(), 1))
                                    for i in range(len(labels))))
    return acc


# ---------------------------------------------------------------- capture

def find_device():
    """First WASAPI microphone input.

    Measured on this laptop: the shared-mode stream is beamformed to mono and
    duplicated across both channels (bit-identical, corr 1.0) with the Realtek/
    Nahimic APOs crushing the level ~4 orders of magnitude. WASAPI *exclusive*
    mode bypasses the audio engine and yields two genuinely independent mics.
    That is the only path with a usable inter-mic time delay, so it is default.
    """
    import sounddevice as sd
    wasapi = [i for i, h in enumerate(sd.query_hostapis()) if "WASAPI" in h["name"]]
    for i, d in enumerate(sd.query_devices()):
        if d["hostapi"] in wasapi and d["max_input_channels"] >= 2 and "icrophone" in d["name"]:
            return i
    return sd.default.device[0]


def open_stream(ch, device=None, exclusive=True):
    import sounddevice as sd
    if device is None:
        device = find_device()
    extra = sd.WasapiSettings(exclusive=True) if exclusive else None
    return sd.InputStream(samplerate=SR, channels=ch, blocksize=BLOCK,
                          dtype="float32", device=device, extra_settings=extra)


def detect_loop(stream, k, on_tap, stop_when):
    """Shared onset detector. Calls on_tap(window, peak, snr_db) per accepted event."""
    state = {"tail": np.zeros((WIN * 3, stream.channels), np.float32)}
    hist = []
    last = 0.0

    def push(block):
        state["tail"] = np.concatenate([state["tail"][len(block):], block])

    def rms_of(block):
        """RMS of the high-passed block.

        Raw RMS is dominated by sub-200Hz rumble (~89% of idle energy on this
        machine), so it measures the desk and the fan, not taps. np.diff is a
        free +6dB/octave high-pass; a tap is broadband and survives it, rumble
        does not. The stored window stays raw so features are unaffected.
        """
        hp = np.diff(block[:, 0].astype(np.float64))
        return float(np.sqrt((hp ** 2).mean()))

    while not stop_when():
        block, _ = stream.read(BLOCK)
        push(block)
        rms = rms_of(block)
        # ~1s warm-up: the stream's first blocks are startup transients and would
        # otherwise both fire spuriously and poison the noise floor.
        floor = max(float(np.median(hist)), 1e-6) if len(hist) >= 190 else None
        hist.append(rms)
        if len(hist) > 200:
            hist.pop(0)

        if floor is None or rms < k * floor or (time.time() - last) < REFRACTORY_S:
            continue

        # Onset sits at the start of the block just pushed; pull in the rest of the window.
        from_end = BLOCK
        while from_end < WIN - PRE:
            more, _ = stream.read(BLOCK)
            push(more)
            from_end += BLOCK
            hist.append(rms_of(more))
        start = len(state["tail"]) - from_end - PRE
        window = state["tail"][start:start + WIN].copy()

        # Do not let the tap itself raise the noise floor for the next detection.
        drop = (WIN // BLOCK) + 2
        del hist[-drop:]

        last = time.time()
        on_tap(window, float(np.abs(window[:, 0]).max()), 20 * np.log10(rms / floor))


def cmd_monitor(a):
    """Live meter of (high-passed level / noise floor).

    Pick --k from this instead of guessing: watch the idle ratio, then tap and
    watch the peak. Set k roughly halfway between them, nearer the idle side.
    """
    print("\n  Tap the desk a few times, then sit still. Ctrl+C to stop.")
    print("  Choose --k between the idle ratio and the tap ratio.\n")
    hist, peak_idle, peak_tap = [], 0.0, 0.0
    abs_idle, abs_peak = 0.0, 0.0
    with open_stream(a.channels, a.device, not a.shared) as s:
        try:
            t0 = time.time()
            while True:
                block, _ = s.read(BLOCK)
                hp = np.diff(block[:, 0].astype(np.float64))
                rms = float(np.sqrt((hp ** 2).mean()))
                hist.append(rms)
                if len(hist) > 200:
                    hist.pop(0)
                if len(hist) < 190:
                    continue
                # Shared mode gates to literal zero at rest, so a ratio against the
                # noise floor is meaningless there -- absolute level is what counts.
                ratio = rms / max(float(np.median(hist)), 1e-6)
                peak_tap = max(peak_tap, ratio)
                abs_peak = max(abs_peak, rms)
                if ratio < 4:
                    peak_idle = max(peak_idle, ratio)
                    abs_idle = max(abs_idle, rms)
                if time.time() - t0 > 0.25:
                    t0 = time.time()
                    bar = "#" * min(int(ratio * 2), 60)
                    print("   level {:.2e}  ratio {:>7.1f}  idle-max {:>5.1f}  peak {:>7.1f}  {}".format(
                        rms, ratio, peak_idle, peak_tap, bar))
        except KeyboardInterrupt:
            print("\n  idle : ratio <{:.1f}x   level <{:.2e}".format(peak_idle, abs_idle))
            print("  taps : ratio {:.1f}x   level {:.2e}".format(peak_tap, abs_peak))
            if abs_idle < 1e-6:
                # Shared mode gates to digital silence at rest, so ratio-to-floor is
                # meaningless (anything / ~0 is huge). Absolute level is the control.
                print("\n  Idle gates to DIGITAL SILENCE and taps get through.")
                print("  Use an absolute threshold, not --k. Suggested: {:.1e}".format(
                    max(abs_peak / 20, 1e-5)))
                print("  Shared mode -> no exclusive lock, other apps keep the mic.")
            elif peak_tap > peak_idle * 2:
                print("\n  suggested:  --k {:.0f}".format(max(peak_idle * 1.5, peak_tap * 0.4)))
            else:
                print("\n  taps are not standing out from the noise floor -- tap harder,")
                print("  or the mic is not picking up desk vibration well from here.")


def cmd_record(a):
    DATA.mkdir(parents=True, exist_ok=True)
    caught = []
    print("\n  Recording zone {}, session {} -- {} taps, {}ch @ {}Hz".format(
        a.zone, a.session, a.n, a.channels, SR))
    print("  threshold k={}.  Tap ~20cm out from the {} corner. Ctrl+C to stop early.\n".format(
        a.k, a.zone))

    def got(w, pk, snr):
        caught.append(w)
        print("   tap {:>3}/{}   peak {:.3f}   snr {:>5.1f} dB".format(len(caught), a.n, pk, snr))

    with open_stream(a.channels, a.device, not a.shared) as s:
        try:
            detect_loop(s, a.k, got, lambda: len(caught) >= a.n)
        except KeyboardInterrupt:
            print("\n  stopped early")

    if not caught:
        print("  nothing captured -- lower --k or tap harder")
        return
    path = DATA / "{}_{}.npz".format(a.session, a.zone)
    old = list(np.load(path)["w"]) if path.exists() else []
    np.savez_compressed(path, w=np.array(old + caught, np.float32))
    print("\n  saved {} taps -> {}  (total {})".format(len(caught), path, len(old) + len(caught)))


def cmd_fpr(a):
    """False-positive rate: how often does the detector fire during normal work?"""
    hits = []
    t0 = time.time()
    end = t0 + a.minutes * 60
    print("\n  Counting false triggers for {} min. Type and use the mouse normally.".format(a.minutes))
    print("  Do NOT tap the desk. Ctrl+C to stop.\n")

    def got(w, pk, snr):
        hits.append(time.time())
        print("   false trigger {:>3}  at {:>6.0f}s  peak {:.3f}".format(len(hits), time.time() - t0, pk))

    with open_stream(a.channels, a.device, not a.shared) as s:
        try:
            detect_loop(s, a.k, got, lambda: time.time() > end)
        except KeyboardInterrupt:
            pass
    mins = (time.time() - t0) / 60
    print("\n  {} triggers in {:.1f} min = {:.1f}/hour".format(
        len(hits), mins, len(hits) / max(mins, 0.01) * 60))
    print("  target: <1/hour. Above that, the action engine fires while you work.")


# ---------------------------------------------------------------- commands

def cmd_devices(a):
    import sounddevice as sd
    print("\n  INPUT DEVICES\n")
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            api = sd.query_hostapis(d["hostapi"])["name"]
            print("   [{:>2}] {:<48} {}ch  {:.0f}Hz  ({})".format(
                i, d["name"][:48], d["max_input_channels"], d["default_samplerate"], api))

    print("\n  WHAT ACTUALLY OPENS  (the number that decides this project)\n")
    dev = a.device if a.device is not None else find_device()
    print("\n  probing device [{}] {}".format(dev, sd.query_devices(dev)["name"]))
    for label, excl in [("shared   ", False), ("exclusive", True)]:
        got = []
        for ch in (1, 2, 4):
            try:
                with open_stream(ch, dev, excl):
                    got.append(ch)
            except Exception:
                pass
        print("   {}: {}".format(label, got if got else "nothing opened"))

    print("\n  ARE THE 2 CHANNELS REAL?  (a duplicated mono stream gives no time delay)\n")
    for label, excl in [("shared   ", False), ("exclusive", True)]:
        try:
            with open_stream(2, dev, excl) as s:
                buf = [s.read(1024)[0] for _ in range(45)]
            x = np.concatenate(buf).astype(np.float64)
            l, r = x[:, 0], x[:, 1]
            # Judge by correlation, not raw difference: a resampling host API adds
            # tiny rounding noise to a duplicated stream and fakes "independent".
            corr = np.corrcoef(l, r)[0, 1] if l.std() > 0 and r.std() > 0 else 1.0
            print("   {}: rms {:.6f}  corr {:.5f}  ->  {}".format(
                label, np.sqrt((l ** 2).mean()), corr,
                "DUPLICATED MONO (no TDOA)" if corr > 0.999 else "INDEPENDENT MICS (TDOA available)"))
        except Exception as e:
            print("   {}: FAIL {}".format(label, str(e)[:50]))

    print("\n   Independent mics -> inter-mic time delay available, 4 zones plausible.")
    print("   Duplicated mono  -> spectral coloration only; expect the 2-zone or tap-count fallback.\n")


def cmd_eval(a):
    X, y, sess = [], [], []
    for si in ("A", "B"):
        for zi, z in enumerate(ZONES):
            p = DATA / "{}_{}.npz".format(si, z)
            if not p.exists():
                continue
            for w in np.load(p)["w"]:
                X.append(features(w))
                y.append(zi)
                sess.append(si)
    if not X:
        print("  no data in {} -- run `record` first".format(DATA))
        return
    X, y, sess = np.array(X), np.array(y), np.array(sess)
    A, B = sess == "A", sess == "B"
    print("\n  {} taps in session A, {} in session B, {} features".format(
        A.sum(), B.sum(), X.shape[1]))

    for name, sl in feature_slices(X.shape[1]).items():
        print("\n" + "=" * 62 + "\n  FEATURES: {}\n".format(name) + "=" * 62)
        cross = None
        if A.sum() and B.sum():
            cross = report(confusion(y[B], knn(X[A][:, sl], y[A], X[B][:, sl], a.k), ZONES),
                           ZONES, "CROSS-SESSION  (train A -> test B)  <-- the honest number")
        else:
            print("\n  need both sessions for the cross-session number")

        same_t, same_p = [], []
        for mask in (A, B):
            if mask.sum() < 8:
                continue
            Xi, yi = X[mask][:, sl], y[mask]
            half = np.arange(len(yi)) % 2 == 0
            same_t += list(yi[~half])
            same_p += list(knn(Xi[half], yi[half], Xi[~half], a.k))
        if same_t:
            same = report(confusion(np.array(same_t), np.array(same_p), ZONES), ZONES,
                          "same-session (optimistic -- this is the number that lies)")
            if cross is not None:
                print("\n  gap: {:+.1%}  <- how much of that is just 'the laptop has not moved'".format(
                    same - cross))

    # Which axis actually carries the signal? Two mics sit side by side, so
    # left/right and front/rear are not equally learnable -- and the fallback
    # plan is "drop to 2 zones", so it matters which pair to drop to.
    if A.sum() and B.sum():
        yt, yp, split = y[B], knn(X[A], y[A], X[B], a.k), "cross-session"
    else:
        half = np.arange(len(y)) % 2 == 0
        yt, yp, split = y[~half], knn(X[half], y[half], X[~half], a.k), "same-session"
    print("\n" + "=" * 62 + "\n  AXIS BREAKDOWN ({})\n".format(split) + "=" * 62)
    print("  4-zone           : {:.1%}".format((yt == yp).mean()))
    for name, group in [("left/right (L=LF,LR)", {0: 0, 1: 0, 2: 1, 3: 1}),
                        ("front/rear (F=LF,RF)", {0: 0, 1: 1, 2: 0, 3: 1})]:
        g = np.vectorize(group.get)
        print("  {} : {:.1%}  (chance 50%)".format(name, (g(yt) == g(yp)).mean()))

    if A.sum() and B.sum():
        acc = np.trace(confusion(y[B], knn(X[A], y[A], X[B], a.k), ZONES)) / B.sum()
        verdict = ("BUILD the 4-zone product as specced" if acc >= 0.85 else
                   "DROP to 2 zones (left/right)" if acc >= 0.60 else
                   "PIVOT to tap-count (single/double/triple, no localization)")
        print("\n" + "=" * 62)
        print("  GATE: {:.1%} cross-session -> {}".format(acc, verdict))
        print("=" * 62 + "\n")


def cmd_selftest(a):
    """Synthetic zones with known separability. Fails loudly if the pipeline is broken,
    so that a bad real-world number can be trusted as a real-world number."""
    rng = np.random.default_rng(0)
    freqs = np.fft.rfftfreq(WIN, 1 / SR)
    X, y = [], []
    for zi, f0 in enumerate([800, 1600, 3200, 6400]):
        for _ in range(30):
            spec = np.fft.rfft(rng.standard_normal(WIN))
            c = f0 * rng.uniform(0.92, 1.08)
            spec = spec * np.exp(-((np.log(freqs + 1) - np.log(c)) ** 2) / 0.08)
            sig = np.fft.irfft(spec, WIN) * np.exp(-np.arange(WIN) / (SR * 0.006))
            X.append(features((sig[:, None] * rng.uniform(0.5, 2.0)).astype(np.float32)))
            y.append(zi)
    X, y = np.array(X), np.array(y)
    half = np.arange(len(y)) % 2 == 0
    acc = (knn(X[half], y[half], X[~half]) == y[~half]).mean()
    print("\n  selftest: {:.1%} on synthetic separable zones ({} features)".format(acc, X.shape[1]))
    assert acc > 0.9, "pipeline broken -- expected >90% on synthetic data, got {:.1%}".format(acc)
    assert features(np.zeros((WIN, 2), np.float32)).shape[0] == N_BANDS + 2, "stereo feature dim wrong"
    print("  OK -- feature extraction and kNN work. Real-world numbers can be trusted.\n")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def audio_args(sp):
        sp.add_argument("--channels", type=int, default=2)
        sp.add_argument("--device", type=int, default=None)
        sp.add_argument("--shared", action="store_true",
                        help="use shared mode (mono, APO-processed) instead of WASAPI exclusive")
        sp.add_argument("--k", type=float, default=8.0, help="onset threshold, x noise floor")

    d = sub.add_parser("devices")
    d.add_argument("--device", type=int, default=None)
    d.set_defaults(f=cmd_devices)

    r = sub.add_parser("record")
    audio_args(r)
    r.add_argument("--zone", required=True, choices=ZONES)
    r.add_argument("--session", required=True, choices=["A", "B"])
    r.add_argument("--n", type=int, default=40)
    r.set_defaults(f=cmd_record)

    m = sub.add_parser("monitor")
    audio_args(m)
    m.set_defaults(f=cmd_monitor)

    fp = sub.add_parser("fpr")
    audio_args(fp)
    fp.add_argument("--minutes", type=float, default=10)
    fp.set_defaults(f=cmd_fpr)

    e = sub.add_parser("eval")
    e.add_argument("--k", type=int, default=5)
    e.set_defaults(f=cmd_eval)

    sub.add_parser("selftest").set_defaults(f=cmd_selftest)

    a = p.parse_args()
    a.f(a)


if __name__ == "__main__":
    sys.exit(main())
