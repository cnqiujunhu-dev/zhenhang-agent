"""Explicit upgrade of exact stock policy revisions; never a restart overwrite."""
from sqlalchemy import select

from .feedback_public_policy import POLICIES, VERSION, stock_predecessors, is_exact_historical_stock
from .knowledge_models import KnowledgeDocument, KnowledgeRevision
from .knowledge_store import content_hash, create_revision, publish_revision, record_knowledge_audit
from .product_knowledge import FEEDBACK_POLICY_PREDECESSORS


def upgrade_feedback_policies(session, *, actor_account_id):
    results = []; published = []
    for identifier, bodies in POLICIES.items():
        predecessors = stock_predecessors(identifier, FEEDBACK_POLICY_PREDECESSORS[identifier])
        for language, body in zip(("zh-CN", "en"), bodies):
            key = f"policy:{identifier}:{language}"
            document = session.scalar(select(KnowledgeDocument).where(
                KnowledgeDocument.document_key == key).with_for_update().execution_options(populate_existing=True))
            status = "NOT_PRESENT"
            if document is not None:
                active = session.scalar(select(KnowledgeRevision).where(KnowledgeRevision.id == document.active_revision_id)
                    .with_for_update().execution_options(populate_existing=True)) if document.active_revision_id else None
                expected_hash = content_hash(body)
                if active and active.state == document.state == "PUBLISHED" and active.version_label == VERSION \
                        and active.content_hash == expected_hash and content_hash(active.content) == expected_hash:
                    status = "ALREADY_CURRENT"
                elif not active or document.state != "PUBLISHED" or active.state != "PUBLISHED" \
                        or not (any(active.version_label == old["version"] and active.content == old[language]
                            and active.content_hash == content_hash(old[language]) for old in predecessors)
                            or document.source_uri == 'knowledge:policy/' + identifier and document.kind == 'PUBLIC_POLICY'
                            and is_exact_historical_stock(document_key=key, language=document.language,
                                version=active.version_label, body=active.content, stored_hash=active.content_hash,
                                expected_body=FEEDBACK_POLICY_PREDECESSORS[identifier][language])):
                    status = "PRESERVED_NONSTOCK_OR_UNPUBLISHED"
                elif session.scalar(select(KnowledgeRevision.id).where(
                        KnowledgeRevision.document_id == document.id, KnowledgeRevision.version_label == VERSION)):
                    status = "PRESERVED_EXISTING_REVISION"
                else:
                    revision = create_revision(session, document.id, body, VERSION)
                    record_knowledge_audit(session, actor_account_id=actor_account_id, action="CREATE_REVISION",
                        document_id=document.id, revision_id=revision.id, content_hash=revision.content_hash,
                        reason="0.21 exact stock policy upgrade; original revision retained")
                    publish_revision(session, revision.id, revision.content_hash)
                    published.append(revision.id)
                    record_knowledge_audit(session, actor_account_id=actor_account_id, action="PUBLISH",
                        document_id=document.id, revision_id=revision.id, content_hash=revision.content_hash,
                        reason="0.21 exact stock policy upgrade; requires normal index worker")
                    status = "PUBLISHED_STOCK_UPGRADE"
            results.append({"document_key": key, "status": status})
    index_job_id = None
    if published:
        from .knowledge_ingest import create_index_job
        index_job_id = create_index_job(session, revision_ids=published).id
    return {"version": VERSION, "documents": results, "automatic_restart_upgrade": False,
            "old_revisions_deleted": 0, "committed_by_caller": True,
            "index_job_id": index_job_id, "index_revision_ids": published}
