import { useEffect, useRef, useState } from "react";
import { request } from "../api";
import { Skeleton } from "../components";
import { useEndpoint } from "../useEndpoint";
import { useReadVisibility } from "../useReadVisibility";
import "./knowledgePanel.css";

type Revision = { id: string; version_label: string; state: string; content: string; content_hash: string; effective_from: string; expires_at: string | null };
type Document = { document_id: string; document_key: string; title: string; source_uri: string; kind: string; language: string; state: string; version_label: string | null; chunk_count: number; indexed_chunks: number; revisions: Revision[] };
type KnowledgeStatus = { storage: string; published_documents: number; active_chunks: number; indexed_chunks: number; semantic_ready: boolean; index_state?: string; last_retrieval_mode?: string; embedding_health?: { status: string; checked_at: string; model_revision: string | null; dimension: number | null; error_code: string | null }; worker?: { state: string; online: boolean; production: boolean; current_job_id?: string | null }; embedding: { configured?: boolean; model?: string; dimension?: number; status?: string; provider?: string; health?: string; fingerprint?: string }; jobs: { id: string; state: string; indexed_chunks: number; requested_chunks: number; error_code: string | null; model_revision?: string | null }[] };
type SearchResult = { mode: string; status: string; sources: { source_id: string; title: string; content: string; reference?: string; source_uri?: string; version?: string; content_hash?: string }[]; trace_id?: string; diagnostics?: unknown };
type Notice = { token: string; epoch: number; text: string; error?: boolean };
const BASE = "/api/v2/admin/knowledge";
const states: Record<string, string> = { DRAFT: "待审核", PUBLISHED: "已发布", RETIRED: "已停用", SUPERSEDED: "历史版本", QUEUED: "已排队", RUNNING: "正在索引", SUCCEEDED: "索引完成", FAILED: "索引失败", INTERRUPTED: "运行中断，旧记录保留", STALE: "来源已变化", UNCONFIGURED: "嵌入服务未配置", EMPTY: "无有效知识", READY: "已核验语义检索就绪", PENDING: "索引尚未就绪", ERROR: "当前不可语义检索" };

/** Only public knowledge is edited here; no bank money or customer authority. */
export function KnowledgePanel({ token }: { token: string }) {
  const visibility = useReadVisibility();
  const scope = `${token}:${visibility.epoch}:${visibility.visible}`;
  const scopeRef = useRef(scope);
  scopeRef.current = scope;
  const controller = useRef<AbortController | null>(null);
  const mutationSequence = useRef(0);
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState("");
  const [create, setCreate] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [key, setKey] = useState("");
  const [title, setTitle] = useState("");
  const [source, setSource] = useState("knowledge:admin/");
  const [kind, setKind] = useState("PUBLIC_POLICY");
  const [language, setLanguage] = useState("zh-CN");
  const [version, setVersion] = useState("");
  const [content, setContent] = useState("");
  const [effective, setEffective] = useState("");
  const [expiry, setExpiry] = useState("");
  const [reason, setReason] = useState("");
  const [review, setReview] = useState<Revision | null>(null);
  const [approved, setApproved] = useState(false);
  const [query, setQuery] = useState("");
  const [auditOpen, setAuditOpen] = useState(false);
  const [search, setSearch] = useState<{ scope: string; result: SearchResult } | null>(null);
  const status = useEndpoint<KnowledgeStatus>(`${BASE}/status`, token);
  const documents = useEndpoint<{ items: Document[]; total: number; page_size: number }>(`${BASE}/documents?page=${page}&page_size=20`, token);
  const detail = useEndpoint<Document>(selected ? `${BASE}/documents/${encodeURIComponent(selected)}` : null, token);
  const audits = useEndpoint<{ items: { audit_id: string; action: string; document_id: string | null; content_hash: string | null; reason: string; created_at: string }[]; total: number }>(auditOpen ? `${BASE}/audit?page_size=20` : null, token);
  const currentNotice = visibility.visible && notice?.token === token && notice.epoch === visibility.epoch ? notice : null;
  const currentSearch = search?.scope === scope && visibility.visible ? search.result : null;
  const verifiedSemanticReady = status.data?.storage === "PGVECTOR" && status.data.semantic_ready === true && status.data.index_state === "READY"
    && status.data.embedding_health?.status === "READY" && /^[a-f0-9]{64}$/.test(status.data.embedding_health.model_revision || "");
  useEffect(() => {
    if (!visibility.visible || status.loading || !status.data?.jobs.some(job => ["QUEUED", "RUNNING"].includes(job.state))) return;
    const captured = scope;
    const timer = setTimeout(() => { if (scopeRef.current === captured) status.refresh({ automatic: true }); }, 2500);
    return () => clearTimeout(timer);
  }, [scope, visibility.visible, status.loading, status.data]);

  useEffect(() => {
    controller.current?.abort();
    mutationSequence.current += 1;
    setBusy(false); setNotice(null); setSearch(null); setReview(null); setApproved(false);
    setCreate(false); setSelected(""); setContent(""); setVersion(""); setReason(""); setAuditOpen(false);
    return () => { controller.current?.abort(); mutationSequence.current += 1; };
  }, [token, visibility.epoch, visibility.visible]);

  function refresh() { status.refresh(); documents.refresh(); detail.refresh(); audits.refresh(); }
  async function action<T>(path: string, body: unknown, message: string, onSuccess?: (value: T) => void) {
    if (busy || !visibility.visible) return;
    const captured = scope;
    const sequence = ++mutationSequence.current;
    const abort = new AbortController(); controller.current = abort;
    setBusy(true); setNotice(null);
    try {
      const value = await request<T>(path, token, "POST", body, undefined, { signal: abort.signal, timeoutMs: 60000 });
      if (scopeRef.current !== captured || abort.signal.aborted || sequence !== mutationSequence.current) return;
      setNotice({ token, epoch: visibility.epoch, text: message });
      onSuccess?.(value); refresh();
    } catch (error) {
      if (scopeRef.current !== captured || abort.signal.aborted || sequence !== mutationSequence.current) return;
      setNotice({ token, epoch: visibility.epoch, text: error instanceof Error ? error.message : "请求未完成，请核对当前状态。", error: true });
    } finally {
      if (scopeRef.current === captured && sequence === mutationSequence.current) setBusy(false);
    }
  }

  function startCreate() {
    setSelected(""); setCreate(true); setContent(""); setVersion(""); setKey(""); setTitle("");
    setSource("knowledge:admin/"); setEffective(""); setExpiry(""); setReview(null); setApproved(false);
  }
  function selectDocument(id: string) {
    setSelected(id); setCreate(false); setContent(""); setVersion(""); setEffective(""); setExpiry("");
    setReview(null); setApproved(false); setReason("");
  }
  async function importText(file: File | undefined) {
    if (!file || busy || file.size > 200000) return;
    const captured = scope;
    try {
      const text = await file.text();
      if (scopeRef.current !== captured) return;
      if (file.name.toLowerCase().endsWith(".json")) {
        const value: unknown = JSON.parse(text);
        if (!value || typeof value !== "object" || !("content" in value) || typeof value.content !== "string") throw new Error("JSON 必须包含字符串 content 字段。");
        if (value.content.length > 60000) throw new Error("文件正文过长。");
        setContent(value.content);
      } else {
        if (text.length > 60000) throw new Error("文件正文过长。");
        setContent(text);
      }
    } catch {
      if (scopeRef.current === captured) setNotice({ token, epoch: visibility.epoch, text: "文件未通过读取，请使用 UTF-8 文本、Markdown 或包含 content 的 JSON。", error: true });
    }
  }
  function saveDraft() {
    const dates = { ...(effective ? { effective_from: new Date(effective).toISOString() } : {}), ...(expiry ? { expires_at: new Date(expiry).toISOString() } : {}) };
    const body = { content, version_label: version.trim(), ...dates };
    const path = create ? `${BASE}/documents` : `${BASE}/documents/${encodeURIComponent(selected)}/revisions`;
    void action<Document>(path, create ? { ...body, document_key: key.trim(), title: title.trim(), source_uri: source.trim(), kind, language } : body,
      "新版本已保存为待审核，尚未对客户发布。", (value) => { setCreate(false); setSelected(value.document_id); setContent(""); setVersion(""); });
  }

  return <div className="knowledge-panel">
    <section className="panel knowledge-status" aria-labelledby="knowledge-status-title">
      <h2 id="knowledge-status-title">本机知识与检索</h2>
      <p>公开知识与银行账户数据分开存储。资料用于解释，不授予付款权限，也不替代实时价格和余额。</p>
      {status.loading && !status.data && <Skeleton lines={3} />}
      {status.error && <p className="login-error" role="alert">{status.error}</p>}
      {status.data && <>
        <dl><div><dt>存储</dt><dd>{status.data.storage}</dd></div><div><dt>已发布资料</dt><dd>{status.data.published_documents}</dd></div><div><dt>向量覆盖</dt><dd>{status.data.indexed_chunks} / {status.data.active_chunks}</dd></div><div><dt>语义索引</dt><dd>{verifiedSemanticReady ? states.READY : status.data.index_state && status.data.index_state !== "READY" ? states[status.data.index_state] || status.data.index_state : "尚未完整覆盖，不能视为语义检索就绪"}</dd></div></dl>
        <p>Embedding：{["LOCAL_EMBEDDING", "LOCAL_HTTP"].includes(status.data.embedding.provider || "") ? "本机嵌入服务" : status.data.embedding.provider === "CLOUD_API" ? "云端嵌入接口" : "独立嵌入服务"} · {status.data.embedding.status || "未返回状态"}{status.data.embedding.model ? ` · ${status.data.embedding.model}` : ""}。配置存在不代表健康检查或实际索引成功；对话与业务规划仍使用云 API。</p>
        {status.data.embedding.health && <p>服务健康：{status.data.embedding.health}</p>}
        {status.data.embedding.fingerprint && <code className="knowledge-hash">模型指纹 {status.data.embedding.fingerprint}</code>}
        <p>本机服务实际探活：{status.data.embedding_health?.status || "尚未返回探活证据"}{status.data.embedding_health?.error_code ? ` · ${status.data.embedding_health.error_code}` : ""}{status.data.embedding_health?.checked_at ? ` · ${status.data.embedding_health.checked_at}` : ""}</p>
        {status.data.embedding_health?.model_revision && <code className="knowledge-hash">实际加载模型指纹 {status.data.embedding_health.model_revision} · {status.data.embedding_health.dimension} 维</code>}
        <p>独立索引 Worker：{status.data.worker?.online && status.data.worker.production ? "心跳和独占锁均已核验，正在运行" : status.data.worker?.state || "尚未观察到运行记录"}。排队不会在 API 进程内执行；失败不会自动无限重试。</p>
        <p>最近检索模式：{status.data.last_retrieval_mode || "尚未记录"}；关键词降级不等于语义检索成功。</p>
        <button className="button secondary" disabled={busy} onClick={() => void action(`${BASE}/index-jobs`, {}, "已提交索引任务，请刷新核对结果；排队不代表已完成。")}>重建公开知识索引</button>
        <button className="text-button" disabled={busy} onClick={refresh}>刷新知识状态</button>
        {status.data.jobs.length > 0 && <details><summary>最近索引任务与模型版本</summary>{status.data.jobs.map(job => <div key={job.id}><p>{states[job.state] || job.state} · {job.indexed_chunks}/{job.requested_chunks}{job.model_revision ? ` · 模型指纹 ${job.model_revision}` : " · 模型版本尚未记录"}{job.error_code ? ` · ${job.error_code}` : ""}</p>{["FAILED", "UNCONFIGURED", "INTERRUPTED", "STALE"].includes(job.state) && <button className="text-button" disabled={busy} onClick={() => void action(`${BASE}/index-jobs/${encodeURIComponent(job.id)}/retry`, {}, "已创建新的索引任务；原失败记录和尝试证据保留。")}>重新排队此任务</button>}</div>)}</details>}
      </>}
    </section>
    {currentNotice && <p className={currentNotice.error ? "login-error" : "knowledge-notice"} role={currentNotice.error ? "alert" : "status"}>{currentNotice.text}</p>}
    <div className="knowledge-workspace">
      <section className="panel knowledge-list" aria-labelledby="knowledge-documents-title">
        <header><h2 id="knowledge-documents-title">公开资料</h2><button className="button secondary" disabled={busy} onClick={startCreate}>新增资料</button></header>
        {documents.loading && !documents.data && <Skeleton lines={5} />}
        {documents.error && <p className="login-error" role="alert">{documents.error}</p>}
        {documents.data?.items.map(doc => <button key={doc.document_id} className={`knowledge-document ${selected === doc.document_id ? "selected" : ""}`} disabled={busy} onClick={() => selectDocument(doc.document_id)}><strong>{doc.title}</strong><span>{doc.language} · {states[doc.state] || doc.state} · {doc.version_label || "未发布"}</span><small>{doc.document_key}</small></button>)}
        {documents.data && <div className="knowledge-pagination"><button className="text-button" disabled={page <= 1 || busy} onClick={() => setPage(page - 1)}>上一页</button><span>{page} 页 · {documents.data.total} 条</span><button className="text-button" disabled={page * documents.data.page_size >= documents.data.total || busy} onClick={() => setPage(page + 1)}>下一页</button></div>}
      </section>
      <section className="panel knowledge-editor" aria-labelledby="knowledge-editor-title">
        <h2 id="knowledge-editor-title">{create ? "新增公开资料" : detail.data ? detail.data.title : "选择资料查看与修订"}</h2>
        {detail.loading && <Skeleton lines={3} />}{detail.error && <p className="login-error" role="alert">{detail.error}</p>}
        {detail.data && <>
          <p>来源：{detail.data.source_uri}</p><p>{detail.data.kind} · {states[detail.data.state] || detail.data.state} · 当前版本 {detail.data.version_label || "未发布"}</p>
          {detail.data.revisions.map(revision => <details key={revision.id}><summary>{revision.version_label} · {states[revision.state] || revision.state}</summary><p>生效：{revision.effective_from} · 到期：{revision.expires_at || "未指定"}</p><pre>{revision.content}</pre><code className="knowledge-hash">SHA256 {revision.content_hash}</code>{revision.state === "DRAFT" && <button className="button secondary" disabled={busy} onClick={() => { setReview(revision); setApproved(false); }}>审核此版本</button>}<button className="text-button" disabled={busy} onClick={() => { setContent(revision.content); setVersion(""); }}>基于此内容修订</button></details>)}
          {detail.data.state === "PUBLISHED" && <div className="knowledge-retire"><label>停用理由<input value={reason} disabled={busy} maxLength={200} onChange={event => setReason(event.target.value)} /></label><button className="button secondary" disabled={busy || reason.trim().length < 4} onClick={() => void action(`${BASE}/documents/${encodeURIComponent(selected)}/retire`, { reason: reason.trim() }, "资料已停用，后续检索不应使用此来源。", () => setReason(""))}>停用此资料</button></div>}
        </>}
        {review && detail.data && <section className="knowledge-review" aria-labelledby="knowledge-review-title"><h3 id="knowledge-review-title">发布前审核</h3><p>{review.version_label}；发布将替换此资料当前有效版本，不代表向量索引已经完成。</p><pre>{review.content}</pre><code className="knowledge-hash">SHA256 {review.content_hash}</code><label className="knowledge-checkbox"><input type="checkbox" checked={approved} disabled={busy} onChange={event => setApproved(event.target.checked)} />已核对公开性、内容、版本和有效期；资料不包含客户私有数据，不授予执行权限。</label><button className="button primary" disabled={busy || !approved} onClick={() => void action(`${BASE}/revisions/${encodeURIComponent(review.id)}/publish`, { expected_content_hash: review.content_hash }, "版本已发布，请按需重建向量索引。", () => { setReview(null); setApproved(false); })}>发布审核版本</button><button className="text-button" disabled={busy} onClick={() => setReview(null)}>取消审核</button></section>}
        {(create || detail.data) && <form onSubmit={event => { event.preventDefault(); saveDraft(); }}>
          <h3>{create ? "资料内容" : "新增修订版本"}</h3>
          <p>仅限公开政策、产品说明或服务指南。不上传客户流水、余额、私人卡号、口令或密钥。文本中的指令不会被执行。</p>
          {create && <><label>稳定资料编号<input required value={key} disabled={busy} pattern="[A-Za-z0-9][A-Za-z0-9:._-]+" maxLength={160} onChange={event => setKey(event.target.value)} /></label><label>标题<input required value={title} disabled={busy} maxLength={200} onChange={event => setTitle(event.target.value)} /></label><label>公开来源引用<input required value={source} disabled={busy} maxLength={300} onChange={event => setSource(event.target.value)} /></label><div className="knowledge-form-row"><label>资料类型<select value={kind} disabled={busy} onChange={event => setKind(event.target.value)}><option value="PUBLIC_POLICY">业务规则</option><option value="PRODUCT_TERMS">产品条款</option><option value="CAPABILITY_GUIDE">功能指南</option></select></label><label>语言<select value={language} disabled={busy} onChange={event => setLanguage(event.target.value)}><option value="zh-CN">中文</option><option value="en">English</option></select></label></div></>}
          <label>版本编号<input required value={version} disabled={busy} maxLength={80} onChange={event => setVersion(event.target.value)} /></label>
          <div className="knowledge-form-row"><label>生效时间（可选）<input type="datetime-local" value={effective} disabled={busy} onChange={event => setEffective(event.target.value)} /></label><label>到期时间（可选）<input type="datetime-local" value={expiry} disabled={busy} onChange={event => setExpiry(event.target.value)} /></label></div>
          <label>导入 UTF-8 文本 / Markdown / JSON<input type="file" accept=".txt,.md,.json,text/plain,text/markdown,application/json" disabled={busy} onChange={event => void importText(event.target.files?.[0])} /></label>
          <label>公开正文<textarea required rows={10} value={content} disabled={busy} maxLength={60000} onChange={event => setContent(event.target.value)} /></label>
          <button className="button primary" disabled={busy || !version.trim() || !content.trim()}>保存待审核版本</button>
        </form>}
      </section>
    </div>
    <section className="panel knowledge-search" aria-labelledby="knowledge-search-title"><h2 id="knowledge-search-title">检索诊断</h2><p>实际检索来源与模式，不把关键词降级称为向量检索。诊断输入仅用于公开问题，不包含客户数据。</p><form onSubmit={event => { event.preventDefault(); void action<SearchResult>(`${BASE}/search`, { query: query.trim(), language, limit: 6 }, "检索完成，请核对来源与检索模式。", result => setSearch({ scope, result })); }}><label>公开问题<input required maxLength={1000} value={query} disabled={busy} onChange={event => setQuery(event.target.value)} /></label><button className="button secondary" disabled={busy || !query.trim()}>检索公开知识</button></form>{currentSearch && <div><p>模式：{currentSearch.mode} · 状态：{currentSearch.status}</p>{currentSearch.sources.length === 0 && <p>未找到可用来源，不代表问题已被回答。</p>}{currentSearch.sources.map(item => <article key={item.source_id}><h3>{item.title}</h3><p>{item.content}</p><small>{item.source_uri || item.reference} · {item.version}</small>{item.content_hash && <code className="knowledge-hash">{item.content_hash}</code>}</article>)}</div>}</section>
    <section className="panel"><details onToggle={event => setAuditOpen(event.currentTarget.open)}><summary>知识变更审计</summary><p>最近 20 条公开知识管理记录，不包含客户银行流水。</p>{audits.loading && <Skeleton lines={3} />}{audits.error && <p className="login-error" role="alert">{audits.error}</p>}{audits.data?.items.map(row => <article key={row.audit_id}><p>{row.created_at} · {row.action} · {row.document_id || "全量索引"}</p>{row.reason && <p>{row.reason}</p>}{row.content_hash && <code className="knowledge-hash">{row.content_hash}</code>}</article>)}{audits.data && <p>已记录 {audits.data.total} 次变更。</p>}</details></section>
  </div>;
}
