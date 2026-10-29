import {dateText, Skeleton} from "../components";
import {useEndpoint} from "../useEndpoint";
import "./operationsDiagnostics.css";

export type CapabilityHealth = {
  schema_version:"capability-health-v1"; observed_at:string; live_model_probe_performed:boolean;
  process:{status:string;database:string;worker:string}; observation_window_seconds:number;
  models:Record<string,{status:string;observed_at:string|null;http_attempts:number|null;error_codes:string[];correlation_id?:string;
    fallback?:boolean|null;result_adopted?:boolean|null;job_status?:string|null;additional_unknown_observations?:number;sample_truncated?:boolean}>;
  queues:Record<string,{by_state:Record<string,number>;oldest_pending_age_seconds:number|null}>;
  knowledge:{status:string;semantic_index_ready?:boolean;active_chunks?:number;indexed_chunks?:number;pending_index_jobs?:number;
    embedding?:{configuration_status:string;live_status:string};index_worker?:{state:string;online:boolean};
    last_retrieval?:{status:string;mode:string;observed_at:string}|null};
  recent_failures:{stage:string;error_type:string;code:string;correlation_id:string|null;observed_at:string}[];
};
const statusLabels:Record<string,string>={OBSERVED_OK:"近期输出已采纳",DEGRADED:"存在异常或回退",STALE:"观测已过期",NOT_OBSERVED:"尚无调用记录",UNKNOWN:"调用结果未完整记录",
  NOT_PROBED:"未实时探测",UNCONFIGURED:"未配置",INDEX_READY:"索引已就绪",INDEX_PENDING:"索引待完成",UNAVAILABLE:"暂不可用"};
export function CapabilityHealthContent({data}:{data:CapabilityHealth}) {
  return <section className="panel capability-health" aria-label="功能健康观测">
    <h2>功能健康观测</h2><p>进程在线与功能可用分别展示。最近观测窗口 {data.observation_window_seconds} 秒；此页面不主动调用模型。</p>
    <small>读取于 {dateText(data.observed_at,true)}</small>
    <div className="ops-health-grid">
      <article><h3>执行进程</h3><strong>{data.process.status === "ready" ? "进程已就绪" : "进程未就绪"}</strong><p>数据库：{data.process.database} · Worker：{data.process.worker}</p></article>
      {["understanding","consultation","narration"].map(key=>{const model=data.models[key]||{status:"UNKNOWN",observed_at:null,http_attempts:null,error_codes:[]};return <article key={key} data-capability={key} data-health={model.status}>
        <h3>{key==="understanding"?"需求理解":key==="consultation"?"知识咨询":key==="narration"?"工具后结果叙述":key}</h3><strong>{statusLabels[model.status]||"状态待核对"}</strong>
        <p>已记录 HTTP 尝试：{model.http_attempts??"未知"}</p><small>{model.observed_at?dateText(model.observed_at,true):"尚未记录时间"}</small>
        <p>模型输出：{model.result_adopted===true?"已采纳":model.result_adopted===false?"未采纳":"采纳状态未知"} · 回退：{model.fallback===true?"是":model.fallback===false?"否":"未知"}</p>
        {key==="narration"&&<small>作业状态 {model.job_status||"未记录"} 不等于模型成功；其他未知观测 {model.additional_unknown_observations??"未记录"}{model.sample_truncated?" · 采样已截断":""}</small>}
        {model.error_codes.length>0&&<p>错误类别：{model.error_codes.join("、")}</p>}{model.correlation_id&&<code>{model.correlation_id}</code>}
      </article>})}
      <article data-capability="knowledge"><h3>知识检索</h3><strong>{statusLabels[data.knowledge.status]||"状态待核对"}</strong>
        <p>已索引 / 有效片段：{data.knowledge.indexed_chunks??"未知"} / {data.knowledge.active_chunks??"未知"}</p>
        <p>待索引任务：{data.knowledge.pending_index_jobs??"未知"}</p>
        <p>Embedding：{data.knowledge.embedding?.configuration_status||"配置状态未知"} · {statusLabels[data.knowledge.embedding?.live_status||"NOT_PROBED"]}</p>
        {data.knowledge.index_worker&&<p>索引 Worker：{data.knowledge.index_worker.online?"在线":"离线或未观测"} · {data.knowledge.index_worker.state}</p>}
        {data.knowledge.last_retrieval&&<small>最近检索：{data.knowledge.last_retrieval.status} / {data.knowledge.last_retrieval.mode} · {dateText(data.knowledge.last_retrieval.observed_at,true)}</small>}
      </article>
    </div>
    <h3>队列等待</h3><div className="ops-health-grid">{Object.entries(data.queues).map(([name,queue])=><article key={name}>
      <strong>{{chat:"对话理解",narration:"结果叙述",images:"图片识别"}[name]||name}</strong>
      <p>{Object.entries(queue.by_state).map(([state,count])=>`${state}: ${count}`).join(" · ")||"暂无作业"}</p>
      <small>{queue.oldest_pending_age_seconds===null?"当前无排队记录":`最早排队已等待 ${queue.oldest_pending_age_seconds} 秒`}</small>
    </article>)}</div>
    <h3>最近错误阶段</h3>{data.recent_failures.length?<div className="admin-table-scroll"><table className="admin-table"><thead><tr><th>时间</th><th>处理阶段</th><th>错误类型 / 代码</th><th>关联记录</th></tr></thead><tbody>
      {data.recent_failures.map((row,i)=><tr key={`${row.correlation_id}:${i}`}><td>{dateText(row.observed_at,true)}</td><td>{row.stage}</td><td>{row.error_type} / {row.code}</td><td><code>{row.correlation_id||"未记录"}</code></td></tr>)}
    </tbody></table></div>:<p>当前采样中没有已记录的处理异常。</p>}
  </section>;
}
export function CapabilityHealthPanel({token,role}:{token:string;role:string}) {
  const result=useEndpoint<CapabilityHealth>(role==="ADMIN"?"/api/v2/admin/capability-health":null,token);
  if(role!=="ADMIN")return null;
  return <>{result.loading&&!result.data&&<Skeleton lines={4}/>}{result.error&&<p role="alert">功能健康信息暂未读到：{result.error}</p>}
    {result.data&&<CapabilityHealthContent data={result.data}/>}<button type="button" className="text-button" disabled={result.loading} onClick={result.refresh}>刷新功能健康观测</button></>;
}
