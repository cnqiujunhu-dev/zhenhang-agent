import { useState } from "react";
import { request } from "../api";
import { money } from "../components";
import { useEndpoint } from "../useEndpoint";
import type { CreditApplication } from "../types";

export function CreditReviewPanel({ token }: {token: string}) {
  const read = useEndpoint<{applications: (CreditApplication & {username?: string})[]}>("/api/v2/admin/credit/applications",token);
  const [adult, setAdult] = useState<Record<string,boolean>>({});
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  async function decide(row: CreditApplication, approved: boolean) {
    if (busy || row.status !== "PENDING_REVIEW" || approved && !adult[row.application_id]) return;
    setBusy(row.application_id);setError("");setNotice("");
    try {
      const result = await request<{status:string;requires_activation?:boolean}>("/api/v2/admin/credit/applications/"+encodeURIComponent(row.application_id)+"/decision",token,"POST",
        {approved,adult_eligible:adult[row.application_id] === true,operation_key:crypto.randomUUID()});
      setNotice(result.status === "APPROVED" ? result.requires_activation ? "已审核发卡，等待客户本人激活。" : "调额审核已完成。" : "申请已拒绝。");
      read.refresh();
    } catch(reason) { setError(reason instanceof Error ? reason.message : "审核未完成，请先刷新核对原申请状态。"); }
    finally { setBusy(""); }
  }
  return <section className="panel"><div className="panel-heading"><h2>信用卡申请审核</h2>
    <button type="button" className="button secondary compact" disabled={read.loading || !!busy} onClick={() => read.refresh()}>刷新申请</button></div>
    <p>审核只决定发卡或授信申请，不代客户执行还款、消费或激活。</p>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {read.loading ? <p role="status">正在读取申请…</p> : read.error ? <p role="alert">{read.error}</p> : read.data && !read.data.applications.length ? <p>暂无申请。</p> : null}
    {read.data?.applications.map(row => <article className="admin-change-review" key={row.application_id}>
      <h3>{row.kind === "CARD" ? "信用卡申请" : "授信调额申请"} · {row.username || row.user_id || "所属客户待核验"}</h3>
      <p>{({PENDING_REVIEW:"待审核",APPROVED:"已批准",REJECTED:"已拒绝"} as Record<string,string>)[row.status] || "状态待核验"}
        {row.kind === "LIMIT" && typeof row.requested_limit_minor === "number" ? " · 申请共享授信 "+money(row.requested_limit_minor,{currency:"CNY"}) : ""}</p>
      {row.status === "PENDING_REVIEW" && <><label><input type="checkbox" checked={adult[row.application_id] === true} disabled={!!busy}
        onChange={event => setAdult({...adult,[row.application_id]:event.target.checked})}/>已核验申请人符合成年样例条件</label>
        <div className="admin-actions"><button type="button" className="button primary" disabled={!!busy || !adult[row.application_id]} onClick={() => void decide(row,true)}>批准申请</button>
          <button type="button" className="button secondary" disabled={!!busy} onClick={() => void decide(row,false)}>拒绝申请</button></div></>}
    </article>)}
  </section>;
}
