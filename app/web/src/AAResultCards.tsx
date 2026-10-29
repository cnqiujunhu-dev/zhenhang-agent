import { useCustomerLanguage } from "./CustomerLanguageProvider";
import { CustomerResultDisclosure } from "./CustomerResultDisclosure";
import type { TraceNode, TaskTrace } from "./types";
import { formatMoneyMinor } from "./typedMoney";
import "./personalServiceCards.css";

import { aaReadPresentation, aaPayReview, aaPayReceipt, aaRequestPresentation, type Collection, type Status } from "./aaPresentation";
export { aaPayReviewReady } from "./aaPresentation";

const money = (value: number, _en: boolean) => `${formatMoneyMinor(value,{currency:"CNY"})} CNY`;
const date = (value: string, en: boolean) => new Intl.DateTimeFormat(en ? "en-US" : "zh-CN", {timeZone:"Asia/Shanghai",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hourCycle:"h23"}).format(new Date(value)) + (en ? " (Shanghai time)" : "（北京时间）");
const statusText = (value: Status, en: boolean) => ({pending:en?"Unpaid":"待付",processing:en?"Payment pending":"付款处理中",paid:en?"Paid":"已付",expired:en?"Expired":"已到期",cancelled:en?"Cancelled":"已取消",declined:en?"Declined":"已拒绝",review:en?"Receipt needs review":"到账待核对"})[value];

export function AAReadCards({node,heading}: {node: TraceNode; heading?: string}) {
  const en = useCustomerLanguage().locale === "en", view = aaReadPresentation(node);
  if (!view) return <section>{heading && <h3>{heading}</h3>}<p role="status">{en ? "AA records need a fresh query before their amounts or payment status can be shown." : "AA记录还需要重新查询，暂不显示未核对的金额或到账状态。"}</p></section>;
  return <section className="personal-service-results" aria-label={en ? "My AA records" : "我的AA收付款"}>
    {heading && <h3>{heading}</h3>}
    {view.scope !== "COLLECTIBLE" && <><h3>{en ? "My AA shares" : "本人应付AA"}</h3>
      {!view.obligations.length && <p className="personal-empty">{en ? "No matching AA shares." : "本次范围没有本人应付的AA记录。"}</p>}
      <div className="personal-result-grid">{view.obligations.map((row,index)=><article className="personal-result-card" key={index}>
        <header><h4>{en ? "Requested by " : "发起人 "}{row.person}</h4><span className="personal-kind">{statusText(row.status,en)}</span></header>
        <dl><div><dt>{en ? "Memo" : "备注"}</dt><dd>{row.memo || (en ? "No memo" : "未填写")}</dd></div>
          <div><dt>{en ? "My share" : "本人份额"}</dt><dd className="personal-primary-value">{money(row.amount,en)}</dd></div>
          <div><dt>{en ? "Deadline" : "截止时间"}</dt><dd><time dateTime={row.expires}>{date(row.expires,en)}</time></dd></div></dl>
      </article>)}</div></>}
    {!["PAYABLE","PENDING"].includes(view.scope) && <><h3>{en ? "My collections" : "本人发起收款"}</h3>
      {!view.collections.length && <p className="personal-empty">{en ? "No matching collections." : "本次范围没有本人发起的收款。"}</p>}
      <div className="personal-result-grid">{view.collections.map((row,index)=><AACollectionCard row={row} key={index} scopeKey={`aa-collection:${index}:${JSON.stringify(row)}`}/>)}</div></>}
  </section>;
}

function AACollectionCard({row,scopeKey}: {row: Collection; scopeKey: string}) {
  const en = useCustomerLanguage().locale === "en", unknown = en ? "Needs review" : "待核对";
  const current = row.received === null ? en ? "Collection recorded; receipt needs review" : "已记录收款，到账待核验"
    : row.received === row.total && row.status === "COMPLETED" ? en ? "Fully received" : "已收齐"
    : row.status === "CANCELLED" ? en ? "Cancelled" : "已取消"
    : row.status === "EXPIRED" ? en ? "Expired" : "已到期"
    : row.received > 0 ? en ? "Partly received" : "部分已收" : en ? "Invitations sent" : "邀请已发出";
  return <article className="personal-result-card">
    <header><h4>{en ? "AA collection" : "AA收款"}</h4><span className="personal-kind">{current}</span></header>
    <p>{en ? "Memo: " : "备注："}{row.memo || (en ? "No memo" : "未填写")}</p>
    <dl>{[[en?"Total":"总额",money(row.total,en)],[en?"Received":"已收",row.received === null ? unknown : money(row.received,en)],[en?"Remaining":"剩余",row.remaining === null ? unknown : money(row.remaining,en)]].map(([name,value])=><div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl>
    <ul>{row.participants.map((person,ordinal)=><li key={ordinal}>{person.name} · {money(person.amount,en)} · {statusText(person.status,en)}</li>)}</ul>
    <CustomerResultDisclosure scopeKey={scopeKey} label={en?"Collection details":"收款详情"}>
      <p>{en?"Deadline: ":"截止："}<time dateTime={row.expires}>{date(row.expires,en)}</time></p>
      {row.received === null && row.known !== null && <p>{en?"Verified received: ":"已核实收到："}{money(row.known,en)}</p>}
    </CustomerResultDisclosure>
  </article>;
}

export function AARequestCards({trace,node}: {trace: TaskTrace; node: TraceNode}) {
  const en = useCustomerLanguage().locale === "en", view = aaRequestPresentation(trace,node);
  return view ? <section className="personal-service-results" aria-label={en?"AA collection progress":"AA收款进度"}>
    <AACollectionCard row={view} scopeKey={`aa-request:${trace.task_id}:${trace.version}:${node.node_id}`}/>
  </section> : <p role="status">{en?"The collection progress needs a fresh query.":"收款进度还需要重新查询核对。"}</p>;
}

export function AAPayConfirmation({scope}: {scope: unknown}) {
  const en = useCustomerLanguage().locale === "en", view = aaPayReview(scope);
  if (!view) return <p role="status">{en?"Payment details need a fresh review before confirmation.":"付款资料还需要重新核对，暂不能确认。"}</p>;
  return <section className="personal-service-results" aria-label={en?"AA payment review":"AA份额付款核对"}><dl className="structured-confirmation">
    {[[en?"Recipient":"收款人",view.recipient],[en?"My AA share":"本人AA份额",money(view.amount,en)],
      [en?"Payment card ending":"付款卡尾号",view.source],[en?"Receiving card ending":"收款卡尾号",view.target],
      ...(view.memo ? [[en?"Memo":"备注",en && view.memo === "AA份额付款" ? "AA share payment" : view.memo]] : [])].map(([name,value])=><div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}
  </dl></section>;
}

export function AAPayReceipt({trace,node,heading}: {trace: TaskTrace; node: TraceNode; heading?: string}) {
  const en = useCustomerLanguage().locale === "en", view = aaPayReceipt(trace,node);
  if (!view) return <p role="status">{en?"The payment receipt still needs verification. Check the original payment before trying again.":"付款回执还需要核验，请先查询原付款，不要重复支付。"}</p>;
  return <section className="personal-service-results" aria-label={en?"AA payment receipt":"AA份额付款回执"}><article className="personal-result-card">
    <header><h4>{heading || (en?"Paid AA share":"AA份额已支付")}</h4><span className="personal-kind">{en?"Paid":"已付"}</span></header>
    <dl><div><dt>{en?"My share":"本人份额"}</dt><dd className="personal-primary-value">{money(view.amount,en)}</dd></div>
      <div><dt>{en?"Recipient":"收款人"}</dt><dd>{view.recipient}</dd></div>
      <div><dt>{en?"Payment card ending":"付款卡尾号"}</dt><dd>{view.source}</dd></div>
      <div><dt>{en?"Receiving card ending":"收款卡尾号"}</dt><dd>{view.target}</dd></div></dl>
  </article></section>;
}
