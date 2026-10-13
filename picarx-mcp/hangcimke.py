#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hangcimke.py — YAMNet sound event classifier (AudioSet, 521 classes).

RUNTIME: the separate python 3.12 venv (.yamnet-venv), because ai-edge-litert (LiteRT,
the successor of tflite-runtime) only ships aarch64 wheels for <=cp312, there is NONE for
the system python 3.13. The system-side listener (robot_listen_loop.py, python 3.13)
calls it as a SUBPROCESS (--json), so the python version difference is no problem.

Input: 16 kHz mono WAV (exactly what the listener records -> zero resampling).
Output: top-k labels + scores. The "hu" field / display name comes from
hangkep_labels.magyar() (Hungarian by default, English where there is no translation).

Usage:
  python hangcimke.py <wav> [topk]      # human-readable: "<display name>  0.72  [English]"
  python hangcimke.py --json <wav>      # one JSON line: {"labels":[{en,hu,score}...], ...}
  python hangcimke.py --serve           # reads wav paths from STDIN line by line, one JSON
                                        # per line -> the model is loaded ONCE (batch/daemon)

The model expects 15600 samples (0.975 s @ 16k) at a time; a longer clip is cut into
15600-sample windows (half overlap), and the per-window scores are combined (max, see scores()).
"""
import sys, os, json, time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.path.join(HERE, "models", "yamnet.tflite")
CLASSMAP = os.path.join(HERE, "models", "yamnet_class_map.csv")
SR = 16000
WIN = 15600            # YAMNet's fixed input (0.975 s @ 16k)
HOP = WIN // 2         # half overlap

# localised display names (pure stdlib module in the same folder)
try:
    from hangkep_labels import magyar
except Exception:
    def magyar(en):
        return en


def load_classmap(path):
    import csv
    names = []
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.reader(f)
        next(r, None)  # header
        for row in r:
            if len(row) >= 3:
                names.append(row[2])
    return names


def read_wav_16k_mono(path):
    """WAV -> float32 [-1,1], 16 kHz mono. Stereo -> mean; other sample rate -> linear resample."""
    import soundfile as sf
    data, sr = sf.read(path, dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != SR and len(data) > 1:
        # simple linear resampling (the ear records 16k anyway, this is only a safeguard)
        n_out = int(round(len(data) * SR / sr))
        if n_out > 1:
            xp = np.linspace(0.0, 1.0, num=len(data), endpoint=False)
            xq = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
            data = np.interp(xq, xp, data).astype(np.float32)
    return np.ascontiguousarray(data, dtype=np.float32)


class Yamnet:
    def __init__(self, model_path=MODEL, threads=2):
        from ai_edge_litert.interpreter import Interpreter
        self.it = Interpreter(model_path=model_path, num_threads=threads)
        self.it.allocate_tensors()
        self.inp = self.it.get_input_details()[0]
        self.out = self.it.get_output_details()[0]
        self.names = load_classmap(CLASSMAP)

    def _frames(self, wav):
        if len(wav) < WIN:
            wav = np.pad(wav, (0, WIN - len(wav)))
        starts = list(range(0, max(1, len(wav) - WIN + 1), HOP))
        if not starts:
            starts = [0]
        for s in starts:
            seg = wav[s:s + WIN]
            if len(seg) < WIN:
                seg = np.pad(seg, (0, WIN - len(seg)))
            yield seg.astype(np.float32)

    def scores(self, wav):
        """MAXIMUM of the per-window scores per class -> [521] vector.
        (max instead of mean — in a long segment the window average dilutes a short
        event, e.g. two meows between silences; live test: cat 0.155 with the mean vs
        ~0.7 in the meow window.)"""
        acc = None
        for seg in self._frames(wav):
            self.it.set_tensor(self.inp["index"], seg)
            self.it.invoke()
            y = self.it.get_tensor(self.out["index"])[0]
            acc = y.copy() if acc is None else np.maximum(acc, y)
        return acc

    def topk(self, wav, k=5):
        sc = self.scores(wav)
        idx = np.argsort(sc)[::-1][:k]
        out = []
        for i in idx:
            en = self.names[i] if i < len(self.names) else str(i)
            out.append({"en": en, "hu": magyar(en), "score": round(float(sc[i]), 4)})
        return out


def classify_file(model, path, k=5):
    t0 = time.time()
    wav = read_wav_16k_mono(path)
    labels = model.topk(wav, k)
    ms = int(1000 * (time.time() - t0))
    return {"labels": labels, "dur_s": round(len(wav) / SR, 2), "ms": ms}


def main():
    args = sys.argv[1:]
    if not args:
        print("usage: hangcimke.py <wav> [topk] | --json <wav> | --serve", file=sys.stderr)
        sys.exit(2)

    if args[0] == "--serve":
        # daemon mode: the model loads once, wav paths arrive on STDIN line by line
        model = Yamnet()
        print(json.dumps({"ready": True}), flush=True)
        for line in sys.stdin:
            p = line.strip()
            if not p:
                continue
            try:
                print(json.dumps(classify_file(model, p), ensure_ascii=False), flush=True)
            except Exception as e:
                print(json.dumps({"labels": [], "error": str(e)}), flush=True)
        return

    if args[0] == "--json":
        path = args[1]
        k = int(args[2]) if len(args) > 2 else 5
        model = Yamnet()
        print(json.dumps(classify_file(model, path, k), ensure_ascii=False), flush=True)
        return

    # human-readable mode
    path = args[0]
    k = int(args[1]) if len(args) > 1 else 5
    model = Yamnet()
    res = classify_file(model, path, k)
    print(f"# {path}  ({res['dur_s']} s, {res['ms']} ms)")
    for d in res["labels"]:
        en = d["en"]; hu = d["hu"]
        tag = hu if hu != en else en
        extra = f"  [{en}]" if hu != en else ""
        print(f"  {tag:<22} {d['score']:.3f}{extra}")


if __name__ == "__main__":
    main()
