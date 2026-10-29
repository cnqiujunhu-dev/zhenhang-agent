import {useRef,useState} from "react";
import {dateText,Icon,Skeleton,useDialogFocus} from "../components";
import {useEndpoint} from "../useEndpoint";
import "./operationsDiagnostics.css";

export type ImageDiagnosticRow={attachment_id:string;purpose:string|null;state:string|null;version:number;created_at:string;updated_at:string;expires_at:string;error_code:string|null};
export type ImageDiagnostic=ImageDiagnosticRow & {schema_version:"admin-image-diagnostic-v1";read_only:true;image_content_included:false;access_audit_id:string;
  extraction_available:boolean;provider:string|null;model:string|null;http_attempts:number|null;usage:Record<string,number|null>;
  field_observations:{field:string;raw_score:number|null;position:number[]|null}[];scores_are_calibrated_safety_confidence:false;
  job:{job_id:string;status:string;attempts:number;last_error:string|null;completed_at:string|null}|null;
  events:{sequence:number;type:string;observed_at:string}[]};
export function ImageDiagnosticContent({data}:{data:ImageDiagnostic}) {
  return <div className="image-diagnostic-content"><p>只读处理诊断。原图、识别原文和客户修正内容不在此展示；查看行为已留运维访问记录。</p>
    <dl className="structured-data"><div><dt>处理状态</dt><dd>{data.state||"未知"} · v{data.version}</dd></div>
      <div><dt>提供方 / 模型</dt><dd>{data.provider??"未记录"} / {data.model??"未记录"}</dd></div>
      <div><dt>HTTP 尝试</dt><dd>{data.http_attempts??"未知"}</dd></div>
      <div><dt>审计关联</dt><dd><code>{data.access_audit_id}</code></dd></div>
      <div><dt>令牌用量</dt><dd>{Object.entries(data.usage).map(([key,count])=>`${key}: ${count??"未知"}`).join(" · ")}</dd></div>
    </dl>
    {!data.extraction_available&&<p>没有可读取的提取诊断，可能尚未完成或材料已过保留期。</p>}
    <h3>字段识别观测</h3><p>原始分数未经安全置信度校准，不代表金额准确、已经到账或可以执行业务。</p>
    {data.field_observations.length?<table className="admin-table"><thead><tr><th>字段</th><th>原始分数</th><th>归一化原图位置</th></tr></thead><tbody>{data.field_observations.map((field,index)=><tr key={`${field.field}:${index}`}><td>{field.field}</td><td>{field.raw_score??"未返回"}</td><td>{field.position?.join(", ")??"未返回"}</td></tr>)}</tbody></table>:<p>暂无字段识别观测。</p>}
    <h3>处理作业</h3>{data.job?<p>{data.job.status} · 已尝试 {data.job.attempts} 次{data.job.last_error?` · ${data.job.last_error}`:""}</p>:<p>暂无作业记录。</p>}
    <h3>材料事件</h3><ol className="ops-diagnostic-events">{data.events.map(event=><li key={event.sequence}>{event.type} · {dateText(event.observed_at,true)}</li>)}</ol>
  </div>;
}
function ImageDiagnosticDrawer({token,id,onClose}:{token:string;id:string;onClose:()=>void}) {
  const ref=useRef<HTMLElement>(null);useDialogFocus(true,{dialogRef:ref,onDismiss:onClose});
  const result=useEndpoint<ImageDiagnostic>(`/api/v2/admin/image-diagnostics/${encodeURIComponent(id)}`,token);
  return <div className="drawer-backdrop" onClick={onClose}><aside ref={ref} className="evidence-drawer admin-trace-drawer" role="dialog" aria-modal="true" aria-labelledby="image-diagnostic-title" onClick={event=>event.stopPropagation()}>
    <header><h2 id="image-diagnostic-title">图片处理诊断</h2><button type="button" className="icon-button" aria-label="关闭图片诊断" onClick={onClose}><Icon name="close"/></button></header>
    <div className="drawer-content">{result.loading&&<Skeleton lines={5}/>}{result.error&&<p role="alert">{result.error}</p>}{result.data&&<ImageDiagnosticContent data={result.data}/>}</div>
  </aside></div>;
}
export function ImageDiagnosticsPanel({token,role}:{token:string;role:string}) {
  const [page,setPage]=useState(1),[selected,setSelected]=useState<{token:string;id:string}|null>(null);
  const result=useEndpoint<{items:ImageDiagnosticRow[];total:number;page:number;page_size:number}>(role==="ADMIN"?`/api/v2/admin/image-diagnostics?page=${page}&page_size=20`:null,token);
  if(role!=="ADMIN")return null;
  return <section className="panel"><div className="panel-heading"><h2>图片处理诊断</h2><button type="button" className="text-button" onClick={result.refresh} disabled={result.loading}>刷新</button></div>
    <p>仅检查处理与识别质量，不代客户核对材料，不开放原图访问或银行操作。</p>
    {result.loading&&!result.data&&<Skeleton lines={4}/>}{result.error&&<p role="alert">{result.error}</p>}
    {result.data&&<><div className="admin-table-scroll"><table className="admin-table"><thead><tr><th>材料标识</th><th>用途</th><th>状态</th><th>更新于</th><th>检查</th></tr></thead><tbody>
      {result.data.items.map(row=><tr key={row.attachment_id}><td><code>{row.attachment_id}</code></td><td>{row.purpose==="FRAUD"?"风险截图":"账单截图"}</td><td>{row.state}</td><td>{dateText(row.updated_at,true)}</td><td><button type="button" className="text-button" onClick={()=>setSelected({token,id:row.attachment_id})}>查看诊断</button></td></tr>)}
    </tbody></table></div>{!result.data.items.length&&<p>暂无图片处理记录。</p>}
      <div className="ops-pagination"><button type="button" className="text-button" disabled={page<=1||result.loading} onClick={()=>setPage(value=>value-1)}>上一页</button><span>第 {page} 页 · 共 {result.data.total} 项</span><button type="button" className="text-button" disabled={page*20>=result.data.total||result.loading} onClick={()=>setPage(value=>value+1)}>下一页</button></div></>}
    {selected?.token===token&&<ImageDiagnosticDrawer key={`${token}:${selected.id}`} token={token} id={selected.id} onClose={()=>setSelected(null)}/>}
  </section>;
}
