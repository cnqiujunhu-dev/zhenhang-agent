import { useEffect,useRef,useState } from "react";
import "./judgeEvidenceGuide.css";
import { dateText, Icon, Skeleton, StateBadge, StructuredData } from "../components";
import { useEndpoint } from "../useEndpoint";
import { webRelease } from "../useReleaseUpdate";
import type { AdminTask, AdminTrace, PageResult } from "./types";
import { filterEvidenceTasks, judgeEvidenceOutcomeLabels, judgeScenes, policySummaryData, safeHealthVersion, summarizeJudgeEvidence } from "./judgeEvidence";
import type { EvidenceStateFilter, JudgeEvidenceSampling, JudgeEvidenceSummary, JudgeRouteObservation, JudgeScene } from "./judgeEvidence";
import { blindAcceptanceTasks, exportAcceptanceSession, newAcceptanceSession, recordAcceptanceMeasurement, loadedUiEntryFromModulePaths, acceptanceTaskCoverage, acceptanceAttemptSummary } from "./expertAcceptance";
import type { AcceptanceHelp, AcceptanceParticipant, AcceptanceResult, AcceptanceSession, BlindTaskId } from "./expertAcceptance";

const known = (value: string | number | null | undefined) => value === null || value === undefined ? "未记录" : String(value);
function RouteFact({route}: {route: JudgeRouteObservation}) {
  return <div className="admin-readonly-boundary">
    <div>
      <p>计划 v{known(route.planVersion)} · {known(route.provider)} / {known(route.model)}</p>
      <p>Purpose：{known(route.purpose)} · 已记录出站请求尝试：{known(route.httpIssued)}</p>
      <small>尝试记录 {route.attemptRecords} 条；其中 HTTP 是否发出未知 {route.httpRecordsUnknown} 条。
        {route.countScope === "NOT_RECORDED" ? "没有尝试数组，不计作零调用。" : "仅统计返回的尝试记录。"}
        这里是本计划规划诊断，不是咨询、图片识别或所有 Purpose 的调用合计。</small>
    </div>
  </div>;
}
function SamplingFact({label, sampling}: {label: string; sampling: JudgeEvidenceSampling<unknown>}) {
  return <p>{label}：本页列出 {sampling.records.length} 条 · 服务端返回 {known(sampling.returned)} 条／总计 {known(sampling.total)} 条；
    {sampling.truncated === true ? "已截断，仅核查返回样本" : sampling.truncated === false ? "服务端标记未截断" : "是否截断未知"}。
    {sampling.samplingConflict && <span role="alert">采样字段类型或数量不一致，本导览不采信该组批准范围或软件证据。</span>}
  </p>;
}

/** Exported for SSR contract tests; it does not contain a network or bank mutation. */
export function JudgeEvidenceFacts({summary}: {summary: JudgeEvidenceSummary}) {
  return <>
    {(!summary.identityMatches || summary.versionConflict || !summary.projectionReadOnly) &&
      <p role="alert" className="login-error">任务标识、状态／版本或只读边界不一致。本导览不据此判断成功，请重新读取原任务。</p>}
    <section className="evidence-section">
      <h3>1. 核对任务与结局</h3>
      <p><StateBadge state={summary.state} /> {judgeEvidenceOutcomeLabels[summary.outcome]}</p>
      <p>任务数据版本 v{known(summary.taskVersion)} · 计划版本 v{known(summary.planVersion)} · 历史执行软件版本：{summary.executionVersionMixed ? "返回尝试跨多个发布版本，见逐次记录" : known(summary.executionSoftwareVersion)}{summary.executionSoftwareVersion ? "（仅当前返回尝试）" : ""}。</p>
      <small>任务／计划数字版本不是软件发布版本；当前页面和服务版本不能倒填历史执行版本。</small>
      <p>返回节点 {summary.nodeCount} 个 · 完成 {summary.successfulNodeCount} 个 · UNKNOWN {summary.unknownNodeCount} 个。</p>
      <p>UNKNOWN、部分失败、预约登记、AA 收款登记或提案保存都不能解释成全部资金到账。</p>
    </section>
    <section className="evidence-section">
      <h3>2. 模型实际用了什么</h3>
      <RouteFact route={summary.route} />
      {summary.historicalRoutes.length > 0 && <details className="admin-row-details">
        <summary>按各历史计划原记录检查（{summary.historicalRoutes.length} 项）</summary>
        {summary.historicalRoutes.map((route, index) => <RouteFact key={index} route={route} />)}
      </details>}
      <p>标准只读编译器可能不发规划模型请求；这不是 Laya 加速或模型准确率的证据。</p>
    </section>
    <section className="evidence-section">
      <h3>3. 策略与批准范围</h3>
      <p>返回策略决定 {summary.policyRecords.length} 条。
        {!summary.policyRecords.length && " 未返回不等于跳过策略。"}</p>
      {summary.policyRecords.length > 0 && <StructuredData data={policySummaryData(summary)} technical />}
      <p>本响应返回 ACTION_AUTHORIZED 事件 {summary.authorizationEventRecords} 条。事件证明存在授权记录，不独立证明批准范围或业务入账；未返回事件也不推断没有授权。</p>
      <SamplingFact label="当次授权证据采样" sampling={summary.authorizationEvidence} />
      {!summary.approvedScopeAvailable && <p>实际批准金额／币种／对象的只读授权记录：未返回可采信的范围。计划快照与策略 ALLOW 不独立证明客户已批准付款。</p>}
      {summary.authorizationEvidence.records.map((record, index) => <article className="admin-readonly-boundary" key={`${record.eventSeq}:${index}`}>
        <div>
          <p>授权事件 #{known(record.eventSeq)} · 记录于 {record.recordedAt ? dateText(record.recordedAt, true) : "未记录"}。</p>
          {record.status === "RECORDED" ? <>
            <p>已受理客户本次确认 · 动作 {record.action} · 操作 {record.operationId} · 计划 v{known(record.planVersion)} · 当次确认绑定的子任务数据版本 v{known(record.expectedTaskVersion)}。</p>
            <p>批准范围仅覆盖原记录的白名单字段；这是授权受理记录，不证明执行、资金入账或独立身份认证。</p>
            <dl className="structured-data">{record.fields.map((field, fieldIndex) => <div key={fieldIndex}><dt>{field.label}</dt><dd>{field.value}</dd></div>)}</dl>
            {!record.fields.length && <p>本记录未返回可展示的批准字段；不从当前计划或账户补造金额、币种和对象。</p>}
            <details className="admin-row-details"><summary>查看原记录绑定哈希</summary>
              <p>批准范围哈希：<code>{record.scopeHash}</code></p><p>计划哈希：<code>{record.planHash}</code></p><p>确认视图哈希：<code>{record.viewHash}</code></p>
            </details>
          </> : <p>{record.status === "NOT_RECORDED" ? "历史批准范围未记录；保留未知，不以当前计划补造。" : "批准证据的结构、类型或任务绑定未通过只读校验；不展示该范围。"}</p>}
        </div>
      </article>)}
      <small>管理员不代客确认；这里没有确认、付款、强制重试或调整数据按钮。</small>
    </section>
    <section className="evidence-section">
      <h3>4. 每次执行尝试的软件记录</h3>
      <SamplingFact label="执行尝试证据采样" sampling={summary.executionEvidence} />
      {summary.executionVersionMixed && <p>返回尝试跨发布版本：{summary.executionReleases.join("、")}。按每次记录核查，不能以一个汇总发布号覆盖历史。</p>}
      {new Set(summary.executionEvidence.records.flatMap(record => record.software ? [record.software.runtimeFingerprint] : [])).size > 1 &&
        <p>返回尝试包含不同运行时指纹；即使发布号相同，也不能认定当次加载的代码相同。</p>}
      {!summary.executionEvidence.records.length && <p>没有返回当次尝试的软件记录；不能从当前 health、页面发布号或计划版本倒填。</p>}
      {summary.executionEvidence.records.map((record, index) => <article className="admin-readonly-boundary" key={`${record.eventSeq}:${index}`}>
        <div>
          <p>执行事件 #{known(record.eventSeq)} · 记录于 {record.recordedAt ? dateText(record.recordedAt, true) : "未记录"}。</p>
          {record.status === "RECORDED" && record.software ? <>
            <p>{record.stage === "STARTED" ? "STARTED · 已派发本次尝试" : "FAILED_ATTEMPT · 本次尝试失败"} · 第 {known(record.attempt)} 次 · {record.dispatchStarted ? "派发已开始" : "未开始派发"}。</p>
            <p>动作 {record.action} · 操作 {record.operationId} · 计划 v{known(record.planVersion)} · 作业 {record.jobId}。</p>
            <p>当次安装包发布版本：{known(record.software.release)}；来源为当次安装包元数据，不是当前页面或任务数据版本。</p>
            <p>当次运行时指纹：<code style={{overflowWrap: "anywhere"}}>{record.software.runtimeFingerprint}</code></p>
            <details className="admin-row-details"><summary>当次已加载模块的 SHA256 代码指纹（{record.software.moduleHashes.length} 项）</summary>
              {record.software.moduleHashes.map(row => <p key={row.module}>{row.module}：<code style={{overflowWrap: "anywhere"}}>{row.hash}</code></p>)}
            </details>
            <small>仅覆盖列出的已加载模块代码；不是完整构建、数据库或外部服务版本证明。</small>
          </> : <p>{record.status === "NOT_RECORDED" ? "历史执行软件版本与指纹未记录，保留未知。" : "软件证据的结构、类型或任务绑定未通过只读校验，保留未知。"}</p>}
        </div>
      </article>)}
      <p>STARTED 与 FAILED_ATTEMPT 仅表示派发或尝试，均不声明结算。预约登记的软件版本不证明到期付款实际执行的版本；到期执行须核查其独立记录。</p>
    </section>
    <section className="evidence-section">
      <h3>5. 具体业务核验与证据完整性</h3>
      {summary.reconciliation.length ? summary.reconciliation.map((check, index) => <p key={index}>
        节点 {check.node} 的服务端对账记录：{check.balanced === true ? "balanced=true" : check.balanced === false ? "balanced=false" : "核验状态未记录"}；
        返回错误项 {known(check.recordedErrorCount)} 条。
      </p>) : <p>没有返回节点级 reconciliation 记录；不可从绿色状态、工具成功或哈希链补造入账核验。</p>}
      <p>审计链：{summary.auditChainValid === true ? "服务端记录校验通过" : summary.auditChainValid === false ? "服务端记录校验未通过" : "未记录"}。
        审计链校验不等于银行操作成功，也不证明外部收款人到账。</p>
      {summary.auditChainValid !== true && <p className="login-error" role="alert">审计链未校验通过；以上仅为返回的记录，不能据此确认历史记录完整性或业务入账。</p>}
      <p>工具记录：当前返回 {summary.toolRecords} 条／总计 {known(summary.toolTotal)} 条；
        {summary.toolsTruncated === true ? "已截断，不能视为完整调用全集" : summary.toolsTruncated === false ? "服务端标记未截断" : "是否截断未知"}。</p>
      <p>计划快照总计 {known(summary.snapshotsTotal)} 条；
        {summary.snapshotsTruncated === true ? "已截断" : summary.snapshotsTruncated === false ? "服务端标记未截断" : "是否截断未知"}。</p>
      <small>这里只解释返回的记录，完整参数、原始需求、逐节点回执和事实来源请打开原任务只读检查。</small>
    </section>
  </>;
}

export function ExpertAcceptanceCollector() {
  const [session, setSession] = useState<AcceptanceSession | null>(null);
  const [kind, setKind] = useState<AcceptanceParticipant>("DEVELOPER_SELF_TEST");
  const [consent, setConsent] = useState(false);
  const [taskId, setTaskId] = useState<BlindTaskId>(blindAcceptanceTasks[0].id);
  const [started, setStarted] = useState<number | null>(null);
  const [result, setResult] = useState<AcceptanceResult>("NOT_COMPLETED");
  const [help, setHelp] = useState<AcceptanceHelp>("NONE");
  const [errorObserved, setErrorObserved] = useState(false);
  const [clarity, setClarity] = useState<1 | 2 | 3 | 4 | 5 | null>(null);
  const task = blindAcceptanceTasks.find(item => item.id === taskId)!;
  const taskCoverage = acceptanceTaskCoverage(session || {measurements: []});
  const attemptSummary = acceptanceAttemptSummary(session || {measurements: [],attempts:[]});
  const [recordNotice,setRecordNotice] = useState("");
  const resetMeasurement = () => {setResult("NOT_COMPLETED"); setHelp("NONE"); setErrorObserved(false); setClarity(null);};
  const startSession = () => {
    if (!consent) return;
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    const loadedEntry = loadedUiEntryFromModulePaths(Array.from(document.querySelectorAll<HTMLScriptElement>('script[type="module"][src]'), script => script.getAttribute("src")));
    setSession(newAcceptanceSession(Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join(""), kind, webRelease, loadedEntry));
    setStarted(null); resetMeasurement();
    setRecordNotice("");
  };
  const finishMeasurement = () => {
    if (!session || started === null) return;
    try {
      setSession(recordAcceptanceMeasurement(session, {task_id: taskId, elapsed_ms: Math.max(0, Math.round(performance.now() - started)),
        result, assistance: help, interaction_error_observed: errorObserved, clarity}));
      setRecordNotice("");
    } catch {
      setRecordNotice("本次记录未保存，已有尝试保持不变。每个本地会话最多保留18次；请下载已有记录后新建会话。不会删除早先失败来腾出空间。");
    }
    setStarted(null);
  };
  const download = () => {
    if (!session || started !== null || !session.measurements.length) return;
    const url = URL.createObjectURL(new Blob([exportAcceptanceSession(session)], {type: "application/json"}));
    const link = document.createElement("a"); link.href = url; link.download = "zhenxing-anonymous-acceptance.json";
    link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 0);
  };
  return <section className="panel">
    <div className="panel-heading"><h2>独立使用者验收准备</h2></div>
    <div className="admin-panel-body acceptance-collector-body">
      <p className="acceptance-boundary">当前页面没有已核实的独立真人参与证据，核实人数为 0；实际总体参与人数未知。开发自测、代理和 fixtures 不算真人验收，也不换算为赛事分数。</p>
      <div className="acceptance-summary-grid" role="group" aria-label="最新任务覆盖与全部观察尝试，分母分别统计">
      <p>六项任务分母 {taskCoverage.task_denominator} · 完成 {taskCoverage.completed} · 部分完成 {taskCoverage.partial} · 未完成 {taskCoverage.not_completed} · 未尝试 {taskCoverage.unattempted}。未尝试不计为成功。</p>
      <p>上面按每项任务的最新结果汇总；所有重测另保留，不覆盖早先失败。尝试 {attemptSummary.recorded_attempts}／{attemptSummary.attempt_limit} 次 · 非完成 {attemptSummary.unsuccessful_attempts} 次 · 获得协助 {attemptSummary.assisted_attempts} 次 · 观察到交互错误 {attemptSummary.interaction_error_attempts} 次。这些是尝试数，不是参与人数。</p>
      </div>
      {attemptSummary.legacy_or_missing_history && <p>历史尝试未完整记录，只有最新结果；不补造早先成功或失败。</p>}
      {recordNotice && <p role="alert">{recordNotice}</p>}
      <p>以下任务包不自动操作客户账户。由观察者在专用测试账户记录选择项；不给参加者固定输入模板，不事先教他点哪里。
        需要写入的流程停在核对页。UNKNOWN 任务需预先存在，没有样本应记“未完成”。</p>
      <details className="admin-row-details"><summary>查看六项盲测任务说明</summary>
        <ol>{blindAcceptanceTasks.map(item => <li key={item.id}><strong>{item.label}</strong><p>{item.instruction}</p><small>{item.expected}</small></li>)}</ol>
      </details>
      {!session ? <>
        <label>本次观察来源 <select value={kind} onChange={event => setKind(event.target.value as AcceptanceParticipant)}>
          <option value="DEVELOPER_SELF_TEST">开发者自测</option><option value="AGENT_OR_FIXTURE">代理／固定样本</option>
          <option value="NONDEVELOPER_SELF_REPORTED">非开发者参加（观察者声明，未独立核实）</option>
        </select></label>
        <p><label><input type="checkbox" checked={consent} onChange={event => setConsent(event.target.checked)} />
          参加者知情，仅记录耗时和选择项，不记录输入内容、账号、金额、身份或联系方式。</label></p>
        <button type="button" className="button secondary" disabled={!consent} onClick={startSession}>新建本地匿名记录</button>
      </> : <>
        <p>本地会话来源：{session.participant_kind === "DEVELOPER_SELF_TEST" ? "开发者自测" : session.participant_kind === "AGENT_OR_FIXTURE" ? "代理／固定样本" : "观察者声明的非开发者参加，未独立核实"}。
          已填写 {session.measurements.length}／{blindAcceptanceTasks.length} 项。</p>
        <p>本次页面发布版本 {session.current_display_version} · DOM 入口路径 {session.loaded_ui_entry || "未记录"}。
          仅记录公开入口路径，不是完整构建已验证的证明。</p>
        <label>任务 <select disabled={started !== null} value={taskId} onChange={event => {setTaskId(event.target.value as BlindTaskId); resetMeasurement();}}>
          {blindAcceptanceTasks.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
        </select></label>
        <p>{task.instruction}</p><small>{task.expected}</small>
        {started === null ? <p><button type="button" className="button secondary" disabled={attemptSummary.recorded_attempts>=attemptSummary.attempt_limit} onClick={() => {resetMeasurement(); setStarted(performance.now());}}>开始观察计时（不发送请求）</button></p> : <>
          <p role="status">正在记录本次任务耗时。下面由观察者选择，不会自动判断用户是否成功。</p>
          <p><label>实际结果 <select value={result} onChange={event => setResult(event.target.value as AcceptanceResult)}>
            <option value="NOT_COMPLETED">未完成</option><option value="PARTIAL">部分完成</option><option value="COMPLETED">完成</option>
          </select></label></p>
          <p><label>获得协助 <select value={help} onChange={event => setHelp(event.target.value as AcceptanceHelp)}>
            <option value="NONE">没有协助</option><option value="PROMPT_ONLY">只得到提示</option><option value="HANDS_ON">有人代为操作</option>
          </select></label></p>
          <p><label><input type="checkbox" checked={errorObserved} onChange={event => setErrorObserved(event.target.checked)} />观察到交互错误／误解</label></p>
          <p><label>参加者对清楚程度的评价 <select value={clarity ?? ""} onChange={event => setClarity(event.target.value ? Number(event.target.value) as 1 | 2 | 3 | 4 | 5 : null)}>
            <option value="">未询问／未知</option>{[1, 2, 3, 4, 5].map(value => <option key={value} value={value}>{value} 分（1 不清楚，5 很清楚）</option>)}
          </select></label></p>
          <button type="button" className="button secondary" onClick={finishMeasurement}>结束观察并记录选择项</button>
        </>}
        {session.attempts?.length ? <details className="admin-row-details acceptance-attempt-history"><summary>查看保留的全部观察尝试（含早先失败）</summary>
          <ol>{session.attempts.map(row=><li key={row.attempt_sequence}>第{row.attempt_sequence}次 · {blindAcceptanceTasks.find(item=>item.id===row.task_id)?.label} · {row.result==="COMPLETED"?"完成":row.result==="PARTIAL"?"部分完成":"未完成"} · {row.elapsed_ms}ms · {row.assistance==="NONE"?"无协助":row.assistance==="PROMPT_ONLY"?"有提示":"有人代操作"}{row.interaction_error_observed?" · 观察到错误":""}</li>)}</ol>
        </details> : null}
        <p><button type="button" className="button secondary" disabled={started !== null || !session.measurements.length} onClick={download}>下载本地脱敏 JSON</button>{" "}
          <button type="button" className="text-button" onClick={() => {setSession(null); setStarted(null); setConsent(false);}}>清空本地记录</button></p>
      </>}
      <p className="admin-small-note">记录只存在本页面内存，离开导览后清除。不使用自动保存、剪贴板、网络上传或真实对话日志。
        只有明确点击下载才生成文件；自行保管，来源声明不等于独立真人核验。</p>
    </div>
  </section>;
}

function AuthorizedJudgeEvidenceGuide({token, onTaskOpen}: {token: string; onTaskOpen: (id: string) => void}) {
  const [page, setPage] = useState(1), [scene, setScene] = useState<JudgeScene | "ALL">("ALL");
  const [stateFilter, setStateFilter] = useState<EvidenceStateFilter>("ALL"), [selected, setSelected] = useState("");
  const list = useEndpoint<PageResult<AdminTask>>(`/api/v2/admin/tasks?page=${page}&page_size=100`, token);
  const health = useEndpoint<unknown>("/api/v1/health/live", token);
  const detail = useEndpoint<AdminTrace>(selected ? `/api/v2/admin/tasks/${encodeURIComponent(selected)}/trace` : null, token);
  const rows = filterEvidenceTasks(list.data?.items || [], scene, stateFilter);
  const summary = detail.data ? summarizeJudgeEvidence(detail.data, selected) : null;
  const refresh = () => {list.refresh(); health.refresh(); if (selected) detail.refresh();};
  const detailHeading=useRef<HTMLHeadingElement>(null);
  const selectionIntent=useRef<{id:string;trigger:Element|null}|null>(null);
  const selectionTrigger=useRef<HTMLElement|null>(null);
  function focusRequestedEvidence() {
    const intent=selectionIntent.current,target=detailHeading.current;
    if(!intent||intent.id!==selected||!target||document.visibilityState!=="visible"
      || document.activeElement!==intent.trigger&&document.activeElement!==document.body)return;
    target.focus({preventScroll:true});
    target.scrollIntoView({block:"start",behavior:window.matchMedia("(prefers-reduced-motion:reduce)").matches?"auto":"smooth"});
    selectionIntent.current=null;
  }
  function openEvidence(id:string) {
    selectionIntent.current={id,trigger:document.activeElement};
    selectionTrigger.current=document.activeElement instanceof HTMLElement?document.activeElement:null;
    if(id===selected)focusRequestedEvidence();else setSelected(id);
  }
  function closeEvidence() {
    const trigger=selectionTrigger.current;
    selectionIntent.current=null;setSelected("");
    requestAnimationFrame(()=>{
      if(document.visibilityState!=="visible"||document.activeElement!==document.body
        ||!trigger?.isConnected||trigger.closest("[hidden],[inert]"))return;
      trigger.focus({preventScroll:true});trigger.scrollIntoView({block:"nearest",behavior:"auto"});
    });
  }
  useEffect(()=>{focusRequestedEvidence();},[selected]);
  return <>
    <section className="panel">
      <div className="panel-heading"><h2>专家只读证据导览</h2><button type="button" className="button secondary" onClick={refresh}><Icon name="refresh" size={16} />刷新只读记录</button></div>
      <div className="admin-panel-body">
        <p>从真实服务端任务进入，按“需求与版本 → 实际路由 → 策略边界 → 业务核验”核查，不预置演示任务、不隐藏失败或 UNKNOWN。</p>
        <p>当前页面发布版本：{webRelease} · 当前服务发布版本：{known(safeHealthVersion(health.data))} · 历史执行版本以所选任务的逐次原记录为准。</p>
        {health.error && <p className="error-inline" role="alert">当前服务版本暂未读取；不影响任务本身，也不以页面版本替代。</p>}
        <label>登记场景 <select value={scene} onChange={event => {setScene(event.target.value as JudgeScene | "ALL"); setSelected("");}}>
          <option value="ALL">全部登记意图</option>{judgeScenes.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
        </select></label>{" "}
        <label>返回任务状态 <select value={stateFilter} onChange={event => {setStateFilter(event.target.value as EvidenceStateFilter); setSelected("");}}>
          <option value="ALL">全部（包含失败与未知）</option><option value="SUCCEEDED">完成记录</option><option value="UNKNOWN">UNKNOWN 对照</option><option value="PENDING">待补充／确认／等待</option>
        </select></label>
        <p className="admin-small-note">场景筛选仅依据服务端登记意图，不能证明节点覆盖完整。筛选只作用于当前页，不代表全部历史；无样本不表示能力不存在。</p>
        {list.loading && !list.data && <Skeleton lines={4} />}
        {list.error && <p className="login-error" role="alert">{list.error}；本页不把读取失败解释为没有任务。</p>}
        {list.data && <>
          <p>当前页返回 {list.data.items.length} 条 · 全部任务 {list.data.total} 条 · 筛选后 {rows.length} 条。</p>
          <div className="admin-table-scroll judge-evidence-picker" tabIndex={0} role="region" aria-label="返回任务列表，可滚动查看全部本页记录"><table>
            <thead><tr><th>登记意图</th><th>服务端状态</th><th>创建于</th><th>只读核查</th></tr></thead>
            <tbody>{rows.map(row => <tr key={row.task_id}>
              <td>{row.intent_type}</td><td><StateBadge state={row.state} /></td><td>{dateText(row.created_at, true)}</td>
              <td><button type="button" className="text-button" onClick={() => openEvidence(row.task_id)}>导览此任务</button></td>
            </tr>)}</tbody>
          </table></div>
          {!rows.length && <p className="extension-empty">当前页没有符合筛选的已返回样本。可切换状态或翻页；不会自动创建测试任务。</p>}
          <div className="pagination"><span>第 {list.data.page} 页 · 每页最多 {list.data.page_size} 条</span><div>
            <button type="button" disabled={page <= 1} onClick={() => {setPage(value => value - 1); setSelected("");}}>上一页</button>
            <button type="button" disabled={page * list.data.page_size >= list.data.total} onClick={() => {setPage(value => value + 1); setSelected("");}}>下一页</button>
          </div></div>
        </>}
      </div>
    </section>
    {selected && <section className="panel"><div className="panel-heading"><h2 ref={detailHeading} tabIndex={-1} className="judge-evidence-detail-heading">所选任务核查</h2><button type="button" className="text-button" onClick={closeEvidence}>收起导览</button></div>
      <div className="admin-panel-body">
        {detail.loading && !detail.data && <Skeleton lines={5} />}
        {detail.error && <p className="login-error" role="alert">{detail.error}；没有取得可核查的新投影。</p>}
        {summary && <><JudgeEvidenceFacts summary={summary} />
          <button type="button" className="button secondary" disabled={!summary.identityMatches || !summary.projectionReadOnly || summary.versionConflict} onClick={() => onTaskOpen(selected)}>打开原任务只读详情</button>
        </>}
      </div>
    </section>}
    <ExpertAcceptanceCollector />
  </>;
}

/** Role is checked before mounting any hook; server ADMIN checks remain authoritative. */
export function JudgeEvidenceGuide({token, role, onTaskOpen}: {token: string; role: string; onTaskOpen: (id: string) => void}) {
  if (role !== "ADMIN" || !token) return <p className="login-error" role="alert">只读证据导览仅供现有管理员使用，不新增评委万能权限。</p>;
  return <AuthorizedJudgeEvidenceGuide key={token} token={token} onTaskOpen={onTaskOpen} />;
}
