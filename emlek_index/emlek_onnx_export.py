# -*- coding: utf-8 -*-
"""Optional, one-off: exports the embedding model to ONNX (fp32 + int8), so the recall server can run
with onnxruntime instead of torch — about half the memory (0.9 GB instead of 2.1 GB).

Run it once from the venv that has torch + transformers + onnxruntime:
  python emlek_onnx_export.py                  # into ~/.cache/emlek_onnx (or EMLEK_ONNX_DIR)
  python emlek_onnx_export.py --cel <folder>   ("cél" = target)
It writes e5_small.onnx, e5_small_int8.onnx and tok/tokenizer.json, then checks the vectors against
sentence-transformers (fp32 should be ~1.0000, int8 ~0.99). Without these files emlek.py simply falls
back to sentence-transformers.
"""
import argparse
import os
import sys

import numpy as np
import torch
from transformers import AutoModel, AutoTokenizer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from emlek_epito import ALAP_MODELL  # noqa: E402


class Pooled(torch.nn.Module):
    """The model + mean pooling + L2 normalisation, so the ONNX graph outputs the final sentence vector."""
    def __init__(self, m):
        super().__init__()
        self.m = m

    def forward(self, input_ids, attention_mask):
        h = self.m(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state
        mask = attention_mask.unsqueeze(-1).to(h.dtype)
        v = (h * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
        return torch.nn.functional.normalize(v, p=2, dim=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cel", "--target", dest="cel",
                    default=os.path.expanduser(os.environ.get("EMLEK_ONNX_DIR") or os.path.join("~", ".cache", "emlek_onnx")))
    ap.add_argument("--modell", "--model", dest="modell", default=ALAP_MODELL)
    a = ap.parse_args()
    os.makedirs(os.path.join(a.cel, "tok"), exist_ok=True)

    tok = AutoTokenizer.from_pretrained(a.modell)
    tok.save_pretrained(os.path.join(a.cel, "tok"))          # tok/tokenizer.json is what emlek.py reads
    model = Pooled(AutoModel.from_pretrained(a.modell)).eval()
    minta = tok(["query: a sample sentence"], return_tensors="pt")
    fp32 = os.path.join(a.cel, "e5_small.onnx")
    with torch.no_grad():
        torch.onnx.export(model, (minta["input_ids"], minta["attention_mask"]), fp32,
                          input_names=["input_ids", "attention_mask"], output_names=["embedding"],
                          dynamic_axes={"input_ids": {0: "b", 1: "n"}, "attention_mask": {0: "b", 1: "n"},
                                        "embedding": {0: "b"}},
                          opset_version=17, dynamo=False)
    from onnxruntime.quantization import QuantType, quantize_dynamic
    int8 = os.path.join(a.cel, "e5_small_int8.onnx")
    quantize_dynamic(fp32, int8, weight_type=QuantType.QInt8)

    # check against sentence-transformers
    import onnxruntime as ort
    from sentence_transformers import SentenceTransformer
    st = SentenceTransformer(a.modell, device="cpu")
    mondatok = ["query: the cat sleeps on the windowsill", "passage: a teáskanna a tűzhelyen fütyül"]
    ref = st.encode(mondatok, normalize_embeddings=True)
    for nev, ut in (("fp32", fp32), ("int8", int8)):
        s = ort.InferenceSession(ut, providers=["CPUExecutionProvider"])
        for i, t in enumerate(mondatok):
            e = tok([t], return_tensors="np")
            v = s.run(None, {"input_ids": e["input_ids"].astype(np.int64),
                             "attention_mask": e["attention_mask"].astype(np.int64)})[0][0]
            print(f"{nev} sentence {i + 1}: cosine with sentence-transformers = {float(v @ ref[i]):.4f}")
    print(f"done: {a.cel}")


if __name__ == "__main__":
    main()
