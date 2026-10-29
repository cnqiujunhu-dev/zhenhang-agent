"""Local text embeddings only. No chat, generation, bank tools or API secrets."""
import hashlib
import json
import math
import os
from pathlib import Path
import threading
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
CACHE = Path(os.getenv("ZX_LOCAL_EMBEDDING_CACHE", "/models/minilm-multilingual"))
app = FastAPI(title="Local knowledge embeddings", version="1.0")
state = {"status": "STARTING", "model": MODEL, "dimension": 384,
    "provider": "ONNX_CPU", "model_revision": None, "error_code": None,
    "chat_model": False, "generation_supported": False}
encoder = None
coverage_tokenizer = None
max_tokens = None
model_lock = threading.Lock()


def load_model():
    global encoder, coverage_tokenizer, max_tokens
    try:
        from fastembed import TextEmbedding
        CACHE.mkdir(parents=True, exist_ok=True)
        instance = TextEmbedding(model_name=MODEL, cache_dir=str(CACHE), threads=4,
            providers=["CPUExecutionProvider"])
        from tokenizers import Tokenizer
        tokenizer = getattr(getattr(instance, "model", None), "tokenizer", None)
        truncation = tokenizer.truncation if tokenizer is not None else None
        if not truncation or not isinstance(truncation.get("max_length"), int):
            raise ValueError("MODEL_TOKENIZER_BUDGET_UNKNOWN")
        budget = truncation["max_length"]
        untruncated = Tokenizer.from_str(tokenizer.to_str())
        untruncated.no_truncation()
        untruncated.no_padding()
        artifacts = sorted(p for p in CACHE.rglob("*") if p.is_file()
            and p.suffix in {".onnx", ".json", ".txt"} and p.name != "embedding-manifest.json")
        if not any(p.suffix == ".onnx" for p in artifacts):
            raise ValueError("MODEL_ARTIFACTS_MISSING")
        digest = hashlib.sha256()
        hashes = {}
        for path in artifacts:
            item = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024*1024), b""):
                    item.update(block)
            relative = path.relative_to(CACHE).as_posix()
            hashes[relative] = item.hexdigest()
            digest.update((relative + ":" + item.hexdigest() + "\n").encode())
        revision = digest.hexdigest()
        manifest = CACHE / "embedding-manifest.json"
        if manifest.exists():
            pinned = json.loads(manifest.read_text(encoding="utf-8"))
            if pinned.get("model") != MODEL or pinned.get("model_revision") != revision:
                raise ValueError("MODEL_ARTIFACT_CHANGED")
        else:
            with manifest.open("x", encoding="utf-8") as stream:
                json.dump({"model": MODEL, "model_revision": revision, "sha256": hashes,
                    "runtime": "fastembed-0.8.1-onnx-cpu", "dimension": 384}, stream, indent=2)
        # Verify real numeric model output rather than treating download as ready.
        probe = list(instance.embed(["银行卡与账户余额", "bank cards and account balances"]))
        if len(probe) != 2 or any(len(row) != 384 or not all(math.isfinite(float(x)) for x in row) for row in probe):
            raise ValueError("MODEL_PROBE_INVALID")
        encoder, coverage_tokenizer, max_tokens = instance, untruncated, budget
        state.update(status="READY", model_revision=revision, max_tokens=budget, error_code=None)
    except Exception as error:
        state.update(status="ERROR", error_code=str(error) if isinstance(error, ValueError)
            and str(error) in {"MODEL_ARTIFACTS_MISSING", "MODEL_ARTIFACT_CHANGED", "MODEL_PROBE_INVALID", "MODEL_TOKENIZER_BUDGET_UNKNOWN"}
            else "MODEL_DOWNLOAD_OR_LOAD_FAILED")


@app.on_event("startup")
def startup():
    threading.Thread(target=load_model, daemon=True).start()


@app.get("/health")
def health():
    return dict(state)


class EmbeddingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model: str
    input: list[str] = Field(min_length=1, max_length=32)
    encoding_format: Literal["float"] = "float"
    input_type: Literal["query", "passage"] = "passage"


@app.post("/v1/embeddings")
def embeddings(payload: EmbeddingsInput):
    if payload.model != MODEL:
        raise HTTPException(422, "MODEL_NOT_REGISTERED")
    if state["status"] != "READY" or encoder is None:
        raise HTTPException(503, "EMBEDDING_MODEL_NOT_READY")
    if any(not value.strip() or len(value) > 2000 for value in payload.input) or sum(map(len, payload.input)) > 16000:
        raise HTTPException(422, "EMBEDDING_INPUT_TOO_LARGE")
    with model_lock:
        # FastEmbed's real tokenizer determines coverage. Reject silent model
        # truncation: chunking belongs to the knowledge ingestion pipeline.
        if coverage_tokenizer is None or max_tokens is None:
            raise HTTPException(503, "MODEL_TOKENIZER_BUDGET_UNKNOWN")
        tokenized = coverage_tokenizer.encode_batch(payload.input)
        if any(len(row.ids) > max_tokens for row in tokenized):
            raise HTTPException(422, "EMBEDDING_TOKEN_BUDGET_EXCEEDED")
        values = list(encoder.embed(payload.input))
    data = []
    for index, row in enumerate(values):
        vector = [float(x) for x in row]
        if len(vector) != 384 or not all(math.isfinite(x) for x in vector):
            raise HTTPException(503, "EMBEDDING_OUTPUT_INVALID")
        data.append({"object": "embedding", "index": index, "embedding": vector})
    return {"object": "list", "model": MODEL, "data": data,
        "metadata": {"model_revision": state["model_revision"], "runtime": "ONNX_CPU",
            "dimension": 384, "max_tokens": max_tokens, "local_embedding": True, "chat_model": False}}
