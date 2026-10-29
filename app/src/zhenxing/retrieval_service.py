"""Network-free public RAG retrieval with publication/role/hash revalidation."""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import String, bindparam, select, text

from .embedding_gateway import EmbeddingVector, validate_vector
from .knowledge_models import (KnowledgeChunk, KnowledgeDocument, KnowledgeRetrievalTrace,
                              KnowledgeRevision, knowledge_now)
from .knowledge_store import (PUBLIC_KINDS, PUBLIC_ROLES, RECIPE_VERSION, KnowledgeError,
                             aware, chunk_content, content_hash)


@dataclass(frozen=True)
class RetrievalResult:
    sources: list[dict]
    mode: str
    status: str
    trace_id: str
    candidate_count: int
    embedding_status: str = "NOT_REQUESTED"
    embedding_error_code: str | None = None
    relevance_policy: str = 'hybrid-relevance-v2'

    def public(self):
        return {"sources": self.sources, "mode": self.mode, "status": self.status,
                "trace_id": self.trace_id, "candidate_count": self.candidate_count,
                "embedding_status": self.embedding_status, "embedding_error_code": self.embedding_error_code,
                "relevance_policy":self.relevance_policy}


def _roles(principal, roles):
    if roles is None:
        supplied = getattr(principal, "roles", None)
        role = getattr(principal, "role", None)
        roles = supplied if isinstance(supplied, (list, tuple)) else [role] if isinstance(role, str) else []
    if not isinstance(roles, (list, tuple)) or not roles or any(role not in PUBLIC_ROLES for role in roles):
        raise KnowledgeError("KNOWLEDGE_RETRIEVAL_ROLE_REQUIRED")
    return sorted(set(roles))


def _eligible(document, revision, chunk, roles, now):
    if document.kind not in PUBLIC_KINDS or document.state != "PUBLISHED" or revision.state != "PUBLISHED" or document.active_revision_id != revision.id or chunk.revision_id != revision.id:
        return False
    if not isinstance(document.allowed_roles, list) or not set(document.allowed_roles) <= PUBLIC_ROLES or not set(roles).intersection(document.allowed_roles):
        return False
    if aware(revision.effective_from) > now or revision.expires_at and aware(revision.expires_at) <= now:
        return False
    if content_hash(revision.content) != revision.content_hash or content_hash(chunk.content) != chunk.content_hash or chunk.recipe_version != RECIPE_VERSION:
        return False
    # A tampered chunk with its own recomputed hash still cannot cite a revision.
    expected = chunk_content(revision.content)
    return 0 <= chunk.ordinal < len(expected) and expected[chunk.ordinal] == chunk.content


def _candidates(session, roles):
    now = knowledge_now()
    rows = session.execute(select(KnowledgeDocument, KnowledgeRevision, KnowledgeChunk)
        .join(KnowledgeRevision, KnowledgeRevision.document_id == KnowledgeDocument.id)
        .join(KnowledgeChunk, KnowledgeChunk.revision_id == KnowledgeRevision.id)
        .where(KnowledgeDocument.state == "PUBLISHED", KnowledgeRevision.state == "PUBLISHED",
               KnowledgeDocument.active_revision_id == KnowledgeRevision.id)
        .order_by(KnowledgeDocument.document_key, KnowledgeChunk.ordinal)).all()
    return [row for row in rows if _eligible(*row, roles, now)]


_STOP = {"的", "了", "我", "你", "吗", "呢", "是", "有", "一下", "什么", "怎么", "可以", "能否", "the", "a", "an", "is", "are", "my", "i", "you", "can", "please", "what", "how", "do", "to", "and"}
_GRAMMATICAL_STOP = {'for','of','in','on','at','be','by','it','its','as','or','from',
    'does','did','will','would','could','should','with','that','this','there','these',
    'those','was','were','have','has','had','me','your','our','we','they','their'}
RELEVANCE_POLICY = 'hybrid-relevance-v2'
MAX_RELEVANT_DISTANCE = .50
MIN_LEXICAL_COVERAGE = .35


def lexical_tokens(message):
    tokens = []
    for word in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", message.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", word):
            tokens.extend(word[i:i + 2] for i in range(max(0, len(word) - 1)))
            if len(word) == 1:
                tokens.append(word)
        else:
            tokens.append(word)
    return [token for token in tokens if token not in _STOP and (len(token) > 1 or token in {"卡"})]


def _lexical_rank(rows, message):
    query = set(lexical_tokens(message))
    if not query:
        return []
    tokenized = {chunk.id: Counter(lexical_tokens(document.title + "\n" + chunk.content)) for document, revision, chunk in rows}
    frequencies = {token: sum(token in counts for counts in tokenized.values()) for token in query}
    average = sum(sum(counts.values()) for counts in tokenized.values()) / max(1, len(rows))
    scored = []
    for _, _, chunk in rows:
        counts = tokenized[chunk.id]
        length = sum(counts.values())
        score = 0.0
        for token in query:
            frequency = counts.get(token, 0)
            if frequency:
                inverse = math.log(1 + (len(rows) - frequencies[token] + .5) / (frequencies[token] + .5))
                score += inverse * frequency * 2.2 / (frequency + 1.2 * (.25 + .75 * length / max(1, average)))
        if score > 0:
            scored.append((chunk.id, score))
    return sorted(scored, key=lambda row: (-row[1], row[0]))


def _semantic_rank(session, rows, query_vector):
    query_vector = validate_vector(query_vector.values, query_vector.model, query_vector.model_revision)
    eligible = []
    for _, _, chunk in rows:
        if chunk.embedding is None or chunk.embedding_model != query_vector.model or chunk.embedding_revision != query_vector.model_revision or chunk.embedding_content_hash != chunk.content_hash:
            continue
        try:
            stored = validate_vector(chunk.embedding, chunk.embedding_model, chunk.embedding_revision)
        except ValueError:
            continue
        if stored.dimension == chunk.embedding_dimension == query_vector.dimension:
            eligible.append(chunk)
    if not eligible:
        return []
    dimension = query_vector.dimension
    if session.bind.dialect.name == "postgresql":
        # Typed literal-execute fingerprint predicates let generic prepared plans
        # select their exact partial HNSW index without unescaped request SQL.
        statement = text(f"SELECT id, (embedding::vector({dimension}) <=> CAST(:query_vector AS vector({dimension}))) AS distance FROM knowledge_chunks WHERE embedding_dimension = {dimension} AND embedding_model = :model AND embedding_revision = :revision AND id IN :ids ORDER BY embedding::vector({dimension}) <=> CAST(:query_vector AS vector({dimension})) LIMIT 24")
        statement = statement.bindparams(bindparam("ids", expanding=True),
            bindparam("model", type_=String, literal_execute=True), bindparam("revision", type_=String, literal_execute=True))
        found = session.execute(statement, {"query_vector": json.dumps(query_vector.values),
            "ids": [chunk.id for chunk in eligible], "model": query_vector.model, "revision": query_vector.model_revision}).all()
        return [(identifier, float(distance)) for identifier, distance in found if distance is not None and math.isfinite(distance) and distance <= .65]
    # Deterministic unit-fixture path, never reported as PostgreSQL semantic proof.
    norm_query = math.sqrt(sum(value * value for value in query_vector.values))
    scored = []
    for chunk in eligible:
        try:
            vector = validate_vector(chunk.embedding, chunk.embedding_model, chunk.embedding_revision)
        except ValueError:
            continue
        if vector.dimension != dimension:
            continue
        norm = math.sqrt(sum(value * value for value in vector.values))
        distance = 1 - sum(left * right for left, right in zip(query_vector.values, vector.values)) / (norm_query * norm)
        if distance <= .65:
            scored.append((chunk.id, distance))
    return sorted(scored, key=lambda row: (row[1], row[0]))[:24]


def _source(document, revision, chunk):
    return {"source_id": "kbstore:" + chunk.id, "title": document.title,
            "kind": "PRODUCT_KNOWLEDGE", "knowledge_kind": document.kind,
            "reference": document.source_uri, "content": chunk.content,
            "version": revision.version_label, "document_id": document.id,
            "document_key": document.document_key, "revision_id": revision.id,
            "chunk_id": chunk.id, "content_hash": chunk.content_hash,
            "document_hash": revision.content_hash, "language": document.language,
            "effective_from": aware(revision.effective_from).isoformat(),
            "expires_at": aware(revision.expires_at).isoformat() if revision.expires_at else None,
            "authority": "PUBLIC_REFERENCE_NOT_BANK_FACT_OR_INSTRUCTION"}


def _relevant_rank(rows,message,lexical,semantic,max_hits):
    """Rank evidence supported by real overlap or a compatible close vector.

    Bounds are retrieval heuristics, not probabilities. A vague related-topic
    vector or English grammar overlap is insufficient by itself. Stable ties
    use corpus position, never a random database UUID. At most two chunks from
    one document prevent repeated boilerplate from occupying the whole view.
    """
    query=set(lexical_tokens(message))-_GRAMMATICAL_STOP
    mapping={chunk.id:(doc,chunk) for doc,_revision,chunk in rows}
    tie_key=lambda identifier:(mapping[identifier][0].document_key,mapping[identifier][1].ordinal)
    lexical=sorted(lexical,key=lambda row:(-row[1],*tie_key(row[0])))
    semantic=sorted(semantic,key=lambda row:(row[1],*tie_key(row[0])))
    lexical_support=set()
    if query:
        for identifier,_score in lexical:
            document,chunk=mapping[identifier]
            overlap=query & set(lexical_tokens(document.title+'\n'+chunk.content))
            if len(overlap)/len(query)>=MIN_LEXICAL_COVERAGE:
                lexical_support.add(identifier)
    semantic_support={identifier for identifier,distance in semantic if distance<=MAX_RELEVANT_DISTANCE}
    supported=lexical_support|semantic_support
    scores={}
    for ranking,weight in ((lexical,2.),(semantic,1.)):
        for rank,(identifier,_score) in enumerate(ranking):
            if identifier in supported:
                scores[identifier]=scores.get(identifier,0)+weight/(61+rank)
    ordered=sorted(scores,key=lambda identifier:(-scores[identifier],mapping[identifier][0].document_key,mapping[identifier][1].ordinal))
    counts={};result=[]
    for identifier in ordered:
        key=mapping[identifier][0].document_key
        if counts.get(key,0)>=2:continue
        result.append(identifier);counts[key]=counts.get(key,0)+1
        if len(result)==max_hits:break
    return result


def retrieve_sources(session, principal=None, message=None, *, query=None, principal_roles=None,
                     query_vector=None, max_hits=6, limit=None, relevance_policy=RELEVANCE_POLICY):
    """No API or bank reads. Caller resolves roles at the authenticated boundary."""
    message = query if query is not None else message
    if not isinstance(message, str) or not message.strip() or len(message) > 2000:
        raise KnowledgeError("KNOWLEDGE_QUERY_INVALID")
    max_hits = limit if limit is not None else max_hits
    if type(max_hits) is not int or not 1 <= max_hits <= 12:
        raise KnowledgeError("KNOWLEDGE_HIT_LIMIT_INVALID")
    if relevance_policy not in {'legacy-rrf-v1',RELEVANCE_POLICY}:
        raise KnowledgeError('KNOWLEDGE_RELEVANCE_POLICY_INVALID')
    roles = _roles(principal, principal_roles)
    if query_vector is not None and not isinstance(query_vector, EmbeddingVector):
        raise KnowledgeError("KNOWLEDGE_QUERY_VECTOR_FINGERPRINT_REQUIRED")
    rows = _candidates(session, roles)
    lexical = _lexical_rank(rows, message)
    semantic = _semantic_rank(session, rows, query_vector) if query_vector is not None else []
    if query_vector is not None and semantic:
        mode = "HYBRID" if session.bind.dialect.name == "postgresql" else "TEST_VECTOR_HYBRID"
        # Reciprocal-rank fusion uses independently real lexical/vector rankings.
        scores = {}
        for ranking in (lexical, semantic):
            for rank, (identifier, _) in enumerate(ranking):
                scores[identifier] = scores.get(identifier, 0) + 1 / (60 + rank + 1)
        identifiers = sorted(scores, key=lambda identifier: (-scores[identifier], identifier))[:max_hits]
        status = "READY"
    else:
        mode = "LEXICAL"
        identifiers = [identifier for identifier, _ in lexical[:max_hits]]
        status = "LEXICAL_ONLY" if query_vector is None else "LEXICAL_ONLY_NO_COMPATIBLE_VECTOR_HITS"
    mapping = {chunk.id: (document, revision, chunk) for document, revision, chunk in rows}
    if relevance_policy==RELEVANCE_POLICY:
        identifiers=_relevant_rank(rows,message,lexical,semantic,max_hits)
    sources = [_source(*mapping[identifier]) for identifier in identifiers]
    if not sources:
        status = "EMPTY"
    trace = KnowledgeRetrievalTrace(query_hash=content_hash(message), roles=roles, mode=mode,
        status=status, source_refs=[{key: source[key] for key in ("document_id", "revision_id", "chunk_id", "content_hash", "document_hash")} for source in sources],
        candidate_count=len(rows))
    session.add(trace)
    session.flush()
    return RetrievalResult(sources, mode, status, trace.id, len(rows),relevance_policy=relevance_policy)


def sources_still_current(session, sources, *, principal=None, principal_roles=None):
    """Recheck retrieved citations after model I/O before emitting any answer."""
    roles, now = _roles(principal, principal_roles), knowledge_now()
    if not isinstance(sources, list):
        return False
    for source in sources:
        if not isinstance(source, dict):
            return False
        chunk = session.get(KnowledgeChunk, source.get("chunk_id"))
        revision = session.get(KnowledgeRevision, source.get("revision_id"))
        document = session.get(KnowledgeDocument, source.get("document_id"))
        if not all((chunk, revision, document)):
            return False
        session.refresh(chunk)
        session.refresh(revision)
        session.refresh(document)
        if not _eligible(document, revision, chunk, roles, now):
            return False
        expected = _source(document, revision, chunk)
        if any(source.get(key) != value for key, value in expected.items()):
            return False
    return True


def retrieve_knowledge(factory, principal_roles, message, *, limit=6, embedder=None,relevance_policy=RELEVANCE_POLICY):
    """High-level route: cloud query vector first, then a fresh local transaction."""
    from dataclasses import replace
    from .embedding_gateway import get_query_embedding
    _roles(None, principal_roles)
    if not isinstance(message, str) or not message.strip() or len(message) > 2000:
        raise KnowledgeError("KNOWLEDGE_QUERY_INVALID")
    embedding = (embedder or get_query_embedding)(message)  # No DB session exists here.
    vector = embedding.vectors[0] if embedding.status == "READY" and len(embedding.vectors) == 1 else None
    with factory.begin() as session:
        result = retrieve_sources(session, query=message, principal_roles=principal_roles,
                                  query_vector=vector, limit=limit,relevance_policy=relevance_policy)
    return replace(result, embedding_status=embedding.status, embedding_error_code=embedding.error_code)
