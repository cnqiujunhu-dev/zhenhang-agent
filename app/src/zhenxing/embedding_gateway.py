"""Explicit cloud or allowlisted local embedding HTTP, never a chat model."""
from __future__ import annotations

import math
import os
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx


@dataclass(frozen=True)
class EmbeddingVector:
    values: tuple[float, ...]
    model: str
    model_revision: str = "unspecified"

    @property
    def dimension(self):
        return len(self.values)


@dataclass(frozen=True)
class EmbeddingResult:
    status: str
    vectors: tuple[EmbeddingVector, ...] = field(default_factory=tuple)
    model: str | None = None
    dimension: int | None = None
    error_code: str | None = None
    model_revision: str | None = None
    max_tokens: int | None = None

    def public(self):
        return {"status": self.status, "model": self.model, "dimension": self.dimension,
                "vector_count": len(self.vectors), "error_code": self.error_code,
                "model_revision": self.model_revision, "max_tokens": self.max_tokens}


def validate_vector(values, model, model_revision="unspecified"):
    if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9._:/-]{1,160}", model):
        raise ValueError("EMBEDDING_MODEL_INVALID")
    if not isinstance(model_revision, str) or not re.fullmatch(r"[A-Za-z0-9._:/-]{1,160}", model_revision):
        raise ValueError("EMBEDDING_MODEL_REVISION_INVALID")
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 4096:
        raise ValueError("EMBEDDING_DIMENSION_INVALID")
    if any(type(value) not in {int, float} or abs(value) > 1e30 or not math.isfinite(value) for value in values):
        raise ValueError("EMBEDDING_VALUES_INVALID")
    if not any(value != 0 for value in values):
        raise ValueError("EMBEDDING_ZERO_VECTOR")
    return EmbeddingVector(tuple(float(value) for value in values), model, model_revision)


def embedding_config_status():
    provider = os.getenv("ZX_EMBEDDING_PROVIDER", "cloud_api").lower()
    keys = ("ZX_EMBEDDING_BASE_URL", "ZX_EMBEDDING_MODEL") + (() if provider == "local_http" else ("ZX_EMBEDDING_API_KEY",))
    configured = provider in {"cloud_api", "local_http"} and all(os.getenv(key, "").strip() for key in keys)
    model = os.getenv("ZX_EMBEDDING_MODEL", "").strip()
    revision = os.getenv("ZX_EMBEDDING_MODEL_REVISION", "").strip()
    dimension = os.getenv("ZX_EMBEDDING_DIMENSION", "").strip()
    return {"status": "CONFIGURED" if configured else "UNCONFIGURED",
            "provider": "LOCAL_HTTP" if provider == "local_http" else "CLOUD_API", "local_model": False,
            "local_embedding": provider == "local_http",
            "max_tokens": 128 if provider == "local_http" else None,
            "expected_model_revision": revision if re.fullmatch(r"[A-Za-z0-9._:/-]{1,160}", revision) else None,
            "expected_dimension": int(dimension) if dimension.isdigit() and 1 <= int(dimension) <= 4096 else None,
            "model": model if re.fullmatch(r"[A-Za-z0-9._:/-]{1,160}", model) else None}


def embed_texts(texts, *, client=None, input_type="passage"):
    """Invoke outside a database transaction; bounded, no chat fallback."""
    if not isinstance(texts, (list, tuple)) or not 1 <= len(texts) <= 32 or any(
        not isinstance(text, str) or not text.strip() or len(text) > 8000 for text in texts
    ) or sum(len(text) for text in texts) > 64000:
        return EmbeddingResult("ERROR", error_code="EMBEDDING_INPUT_INVALID")
    if input_type not in {"query", "passage"}:
        return EmbeddingResult("ERROR", error_code="EMBEDDING_INPUT_TYPE_INVALID")
    if any(re.search(r"(?i)sk-[a-z0-9_-]{8,}|Bearer\s+\S+|(?:zhenhang|xiaozhao)[a-f0-9]{8}|(?:密码|口令|验证码|password|api[_ -]?key|access[_ -]?token)\s*[:：=]\s*\S+", text) for text in texts):
        return EmbeddingResult("ERROR", error_code="EMBEDDING_PRIVATE_INPUT_FORBIDDEN")
    if embedding_config_status()["status"] != "CONFIGURED":
        return EmbeddingResult("UNCONFIGURED", error_code="EMBEDDING_UNCONFIGURED")
    base = os.environ["ZX_EMBEDDING_BASE_URL"].strip().rstrip("/")
    model = os.environ["ZX_EMBEDDING_MODEL"].strip()
    parsed = urlparse(base)
    provider = os.getenv("ZX_EMBEDDING_PROVIDER", "cloud_api").lower()
    try:
        port = parsed.port
    except ValueError:
        return EmbeddingResult("ERROR", error_code="EMBEDDING_ENDPOINT_INVALID")
    local = provider == "local_http"
    allowed_local = parsed.hostname in {"localhost", "127.0.0.1", "::1"} or parsed.hostname == "embedding" and port == 8000
    valid_endpoint = parsed.scheme == "http" and allowed_local if local else parsed.scheme == "https" and parsed.hostname and parsed.hostname.lower() not in {"localhost", "127.0.0.1", "::1", "0.0.0.0", "embedding"}
    if not valid_endpoint or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path.rstrip("/") not in {"", "/v1", "/embeddings", "/v1/embeddings"}:
        return EmbeddingResult("ERROR", error_code="EMBEDDING_ENDPOINT_INVALID")
    if not re.fullmatch(r"[A-Za-z0-9._:/-]{1,160}", model):
        return EmbeddingResult("ERROR", error_code="EMBEDDING_MODEL_INVALID")
    url = base if base.endswith("/embeddings") else base + "/embeddings"
    owned_client = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(25, connect=8), follow_redirects=False)
    deadline = time.monotonic() + 35
    try:
        headers = {"Content-Type": "application/json"}
        if not local:
            headers["Authorization"] = "Bearer " + os.environ["ZX_EMBEDDING_API_KEY"]
        payload_request = {"model": model, "input": list(texts), "encoding_format": "float"}
        if local:
            payload_request["input_type"] = input_type
        with client.stream("POST", url, headers=headers, json=payload_request) as response:
            if response.status_code != 200:
                code = "EMBEDDING_AUTH" if response.status_code in {401, 403} else "EMBEDDING_RATE_LIMIT" if response.status_code == 429 else "EMBEDDING_INPUT_REJECTED" if response.status_code == 422 else "EMBEDDING_HTTP_ERROR"
                return EmbeddingResult("ERROR", model=model, error_code=code)
            chunks, length = [], 0
            for chunk in response.iter_bytes():
                if time.monotonic() > deadline:
                    return EmbeddingResult("ERROR", model=model, error_code="EMBEDDING_TIMEOUT")
                length += len(chunk)
                if length > 2_000_000:
                    return EmbeddingResult("ERROR", model=model, error_code="EMBEDDING_RESPONSE_TOO_LARGE")
                chunks.append(chunk)
            import json
            payload = json.loads(b"".join(chunks))
        if not isinstance(payload, dict):
            raise ValueError("EMBEDDING_PAYLOAD_INVALID")
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != len(texts):
            raise ValueError("EMBEDDING_COUNT_INVALID")
        if payload.get("model") is not None and payload["model"] != model:
            raise ValueError("EMBEDDING_MODEL_MISMATCH")
        metadata = payload.get("metadata", {})
        model_revision = metadata.get("model_revision", "unspecified") if isinstance(metadata, dict) else "unspecified"
        if local and (not isinstance(model_revision, str) or not re.fullmatch(r"[a-f0-9]{64}", model_revision)):
            raise ValueError("EMBEDDING_MODEL_REVISION_REQUIRED")
        if local and (not isinstance(metadata, dict) or metadata.get("max_tokens") != 128 or
                      metadata.get("local_embedding") is not True or metadata.get("chat_model") is not False):
            raise ValueError("EMBEDDING_LOCAL_METADATA_INVALID")
        expected_revision = os.getenv("ZX_EMBEDDING_MODEL_REVISION", "").strip()
        if expected_revision and model_revision != expected_revision:
            raise ValueError("EMBEDDING_MODEL_REVISION_MISMATCH")
        rows = {}
        for row in data:
            if not isinstance(row, dict) or type(row.get("index")) is not int or not 0 <= row["index"] < len(texts) or row["index"] in rows:
                raise ValueError("EMBEDDING_INDEX_INVALID")
            rows[row["index"]] = validate_vector(row.get("embedding"), model, model_revision)
        vectors = tuple(rows[index] for index in range(len(texts)))
        dimension = vectors[0].dimension
        if any(vector.dimension != dimension for vector in vectors):
            raise ValueError("EMBEDDING_DIMENSION_MISMATCH")
        if local and (type(metadata.get("dimension")) is not int or metadata["dimension"] != dimension):
            raise ValueError("EMBEDDING_METADATA_DIMENSION_MISMATCH")
        configured_dimension = os.getenv("ZX_EMBEDDING_DIMENSION", "").strip()
        if configured_dimension and (not configured_dimension.isdigit() or int(configured_dimension) != dimension):
            raise ValueError("EMBEDDING_DIMENSION_MISMATCH")
        return EmbeddingResult("READY", vectors=vectors, model=model, dimension=dimension, model_revision=model_revision,
                               max_tokens=metadata.get("max_tokens") if local else None)
    except (ValueError, TypeError, KeyError):
        return EmbeddingResult("ERROR", model=model, error_code="EMBEDDING_RESPONSE_INVALID")
    except httpx.TimeoutException:
        return EmbeddingResult("ERROR", model=model, error_code="EMBEDDING_TIMEOUT")
    except httpx.HTTPError:
        return EmbeddingResult("ERROR", model=model, error_code="EMBEDDING_TRANSPORT")
    finally:
        if owned_client:
            client.close()


def get_query_embedding(message, *, client=None):
    return embed_texts([message], client=client, input_type="query")
