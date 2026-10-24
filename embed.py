"""What a headline means, as a vector: a small static embedding model.

potion-base-8M (minishlab, MIT licence, 30 MB) gives each word piece a vector
and a text the mean of its pieces' vectors. That's no neural network at run
time, just a lookup and an average, so it costs the Pi almost nothing. It
knows "riots" and "unrest" are alike, and that "French" is about France,
which word counts can't. It's one signal among several in the learned model
(learn.py), which only uses it if it helps against your labels.

The image bakes the model in (see the Dockerfile); without it, this says so
and the app groups as it did before.
"""
import json
import os
import pathlib
import struct
from functools import lru_cache

MODEL_DIR = pathlib.Path(os.environ.get("EMBED_MODEL_DIR")
                         or pathlib.Path(__file__).resolve().parent / "models" / "potion-base-8M")


def _load_safetensors(path: pathlib.Path):
    """The one tensor in a .safetensors file, read with numpy alone."""
    import numpy as np
    with path.open("rb") as f:
        size = struct.unpack("<Q", f.read(8))[0]
        header = json.loads(f.read(size))
        data = f.read()
    (name, info), = ((k, v) for k, v in header.items() if k != "__metadata__")
    dtype = {"F32": np.float32, "F16": np.float16}[info["dtype"]]
    start, end = info["data_offsets"]
    return np.frombuffer(data[start:end], dtype=dtype).reshape(info["shape"]).astype(np.float32)


@lru_cache(maxsize=1)
def _model():
    """(tokenizer, embeddings, unknown token id), or None without the model."""
    if os.environ.get("EMBED", "1") == "0" or not (MODEL_DIR / "tokenizer.json").is_file():
        return None
    try:
        from tokenizers import Tokenizer
        tokenizer = Tokenizer.from_file(str(MODEL_DIR / "tokenizer.json"))
        vectors = _load_safetensors(MODEL_DIR / "model.safetensors")
    except (ImportError, OSError, ValueError, KeyError):
        return None
    return tokenizer, vectors, tokenizer.token_to_id("[UNK]")


def available() -> bool:
    return _model() is not None


def encode(texts: list[str]):
    """One unit-length vector per text (a numpy array, len(texts) × 256), or
    None without the model. A text with no known pieces gets zeros."""
    model = _model()
    if model is None:
        return None
    import numpy as np
    tokenizer, vectors, unk = model
    out = np.zeros((len(texts), vectors.shape[1]), dtype=np.float32)
    for i, enc in enumerate(tokenizer.encode_batch([t or "" for t in texts], add_special_tokens=False)):
        ids = [t for t in enc.ids if t != unk]
        if ids:
            v = vectors[ids].mean(axis=0)
            n = float(np.linalg.norm(v))
            out[i] = v / n if n else v
    return out


def text(article: dict) -> str:
    """What gets embedded: the headline and the start of the summary."""
    return f"{article['title']}. {' '.join((article.get('summary') or '').split()[:40])}"
