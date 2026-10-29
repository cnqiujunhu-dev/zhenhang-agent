// customer-copy-literals-v1
import { CustomerText, useCustomerLanguage } from "./CustomerLanguageProvider";
import { customerUiText } from "./customerLanguage";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { AccessibleReloadButton } from "./AccessibleReloadButton";
import { ApiError, isAbortError, request } from "./api";
import { customerRecovery } from "./customerRecovery";
import { useReadVisibility } from "./useReadVisibility";
import { ResourceReadArrival, useResourceReadArrival } from "./ResourceReadArrival";
import { customerName } from "./customerPresentation";
import { TransferRecipientPanel } from "./TransferRecipientPanel";
import { OwnTransferHistoryPanel } from "./OwnTransferHistoryPanel";
import { ContactAliasesPanel } from "./ContactAliasesPanel";
import {
  dateText,
  Icon,
  money,
  SpendingChart,
  StateBadge,
  useDialogFocus,
} from "./components";
import type { AARequest, BillReport, ScheduledTransfer, Card } from "./types";
import { billCoverageUnusable, billCoverageNotice } from "./billCoveragePresentation";
import { OwnedBillingCards } from "./OwnedBillingCards";

export function BillReports({
  token,
  onBack,
  onAnalyze,
}: {
  token: string;
  onBack: () => void;
  onAnalyze: (message: string) => void;
}) {
  const { locale, t: customerUiText } = useCustomerLanguage();
  const [period, setPeriod] = useState<"month" | "year">("month");
  const calendar = new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", timeZone: "Asia/Shanghai" }).formatToParts(new Date());
  const [year, setYear] = useState(Number(calendar.find(part => part.type === "year")!.value));
  const [month, setMonth] = useState(Number(calendar.find(part => part.type === "month")!.value));
  const [report, setReport] = useState<BillReport | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [refreshKey, setRefreshKey] = useState(0);
  useEffect(() => {
    let disposed = false;
    setLoading(true);
    setError("");
    const query = new URLSearchParams({
      period,
      year: String(year),
      ...(period === "month" ? { month: String(month) } : {}),
    });
    request<BillReport>(`/api/v2/bills/report?${query}`, token)
      .then((value) => {
        if (!disposed) {
          setReport(value);
          setPage(0);
          setSearch("");
        }
      })
      .catch((err) => {
        if (!disposed) {
          setError(err.message);
          setReport(null);
        }
      })
      .finally(() => {
        if (!disposed) setLoading(false);
      });
    return () => {
      disposed = true;
    };
  }, [token, period, year, month, refreshKey, locale]);
  const filtered = (report?.transactions || []).filter(
    (item) => item.merchant.includes(search) || item.category.includes(search),
  );
  const rows = filtered.slice(page * 20, page * 20 + 20);
  const monthly = report?.summary.monthly || [];
  const reportCoverage = report?.coverage || report?.summary.coverage;
  const reportUncovered = billCoverageUnusable(reportCoverage);
  const reportLiveOnly = reportCoverage?.basis === "LIVE_ACTIVITY_ONLY";
  const upgradedBill = Boolean(report?.summary && "billing_schema_version" in report.summary
    && report.summary.billing_schema_version === "owned-bill-analysis-v2");
  const metricMoney = (value: unknown) => typeof value === "number" && Number.isSafeInteger(value) && !reportUncovered && !(reportLiveOnly && value === 0) ? money(value) : "—";
  const max = Math.max(
    1,
    ...monthly.flatMap((item) => [item.income_minor, item.spending_minor]),
  );
  return (
    <div className="reports-page">
      <div className="report-toolbar">
        <button className="text-button" onClick={onBack}>
          <Icon name="arrow" size={15} className="back-arrow" /><CustomerText text="回到 Agent 办理" /></button>
        <div className="period-controls">
          <div className="segmented-control">
            <button
              className={period === "month" ? "selected" : ""}
              onClick={() => setPeriod("month")}
            ><CustomerText text="月度报告" /></button>
            <button
              className={period === "year" ? "selected" : ""}
              onClick={() => setPeriod("year")}
            ><CustomerText text="年度报告" /></button>
          </div>
          <label><CustomerText text="年份" /><select
              aria-label={customerUiText("账单报告年份")}
              value={year}
              onChange={(event) => setYear(Number(event.target.value))}
            >
              {Array.from({ length: 5 }, (_, i) => 2023 + i).map((value) => (
                <option value={value} key={value}>
                  {value}<CustomerText text="年" /></option>
              ))}
            </select>
          </label>
          {period === "month" && (
            <label><CustomerText text="月份" /><select
                aria-label={customerUiText("账单报告月份")}
                value={month}
                onChange={(event) => setMonth(Number(event.target.value))}
              >
                {Array.from({ length: 12 }, (_, i) => i + 1).map((value) => (
                  <option value={value} key={value}>
                    {value}<CustomerText text="月" /></option>
                ))}
              </select>
            </label>
          )}
          <button
            className="icon-button"
            aria-label={customerUiText("刷新账单报告")}
            onClick={() => setRefreshKey((key) => key + 1)}
          >
            <Icon name="refresh" size={18} />
          </button>
        </div>
      </div>
      {loading && (
        <div className="report-loading">
          <span className="spinner" /><CustomerText text="正在汇总账单流水…" /></div>
      )}
      {error && (
        <div className="banner error" role="alert">
          <Icon name="info" size={17} />
          {error}
        </div>
      )}
      {report && !loading && (
        <>
          <div className="report-title">
            <div>
              <span className="eyebrow"><CustomerText text="金融账单报告" /></span>
              <h2>{locale === "en" ? period === "month" ? `${report.period.year}-${String(report.period.month).padStart(2, "0")}` : String(report.period.year) : report.period.label} <CustomerText text="收支概览" /></h2>
              <p>
                {reportUncovered || reportLiveOnly ? billCoverageNotice(reportCoverage, locale) : report.period.partial
                  ? locale === "en" ? `Partial period · data through ${dateText(report.period.data_through || undefined)}` : `本期尚未结束 · 数据截至 ${dateText(report.period.data_through || undefined)}`
                  : report.period.status === "NOT_STARTED"
                    ? customerUiText("统计期尚未开始，暂无账单流水。")
                    : upgradedBill ? locale === "en" ? "Closed statement period" : "已结束统计期"
                    : customerUiText("已结束统计期 · 来源为当前账户流水")}
              </p>
              {reportCoverage && <details className="bill-coverage-dates"><summary>{locale === "en" ? "Statement coverage" : "查看资料范围"}</summary><p>{locale === "en" ? "Data through" : "数据截止"}: {dateText(reportCoverage.data_through || undefined, true)} · {locale === "en" ? "Calendar time" : "当前日历时间"}: {dateText(reportCoverage.calendar_time, true)}</p></details>}
            </div>
            <button
              className="button primary"
              onClick={() =>
                onAnalyze(
                  locale === "en" ? `Analyze my ${year}${period === "month" ? `-${String(month).padStart(2, "0")}` : ""} statement. Explain spending changes and unusual payments.` : `分析${year}年${period === "month" ? `${month}月` : ""}账单，看看支出变化和异常扣款`,
                )
              }
            >
              <Icon name="spark" size={17} /><CustomerText text="让 Agent 进一步分析" /></button>
          </div>
          {upgradedBill ? <OwnedBillingCards analysis={report.summary} /> : <>
          <div className="report-summary-grid">
            <div>
              <span><CustomerText text="收入" /></span>
              <strong>{metricMoney(report.summary.income_minor)}</strong>
            </div>
            <div>
              <span><CustomerText text="消费支出" /></span>
              <strong>{metricMoney(report.summary.spending_minor)}</strong>
            </div>
            <div>
              <span><CustomerText text="退款" /></span>
              <strong>{metricMoney(report.summary.refund_minor)}</strong>
            </div>
            <div>
              <span><CustomerText text="流水净额" /></span>
              <strong>
                {typeof report.summary.net_minor === "number" && Number.isSafeInteger(report.summary.net_minor) && !reportUncovered ? money(report.summary.net_minor, { signed: true }) : "—"}
              </strong>
            </div>
          </div>
          <div className="cash-movement-strip">
            <span><CustomerText text="资金搬移流入" /><strong>{metricMoney(report.summary.movement_in_minor)}</strong>
            </span>
            <span><CustomerText text="资金搬移流出" /><strong>{metricMoney(report.summary.movement_out_minor)}</strong>
            </span>
            <small><CustomerText text="转账、申赎与 AA 回款单列，避免混入消费支出。" /></small>
          </div>
          {!reportUncovered && !reportLiveOnly && <div className="report-chart-grid">
            <section className="panel report-chart-panel">
              <div className="small-panel-heading">
                <h2>{period === "year" ? customerUiText("月度收支趋势") : customerUiText("本月收入与支出")}</h2>
                <div className="bar-legend">
                  <span>
                    <i /><CustomerText text="收入" /></span>
                  <span>
                    <i /><CustomerText text="支出" /></span>
                </div>
              </div>
              <div className="bar-chart">
                {monthly.length ? (
                  monthly.map((item) => (
                    <div className="bar-column" key={item.month}>
                      <div className="bar-pair">
                        <div
                          className="bar income"
                          style={{
                            height: `${Math.max(item.income_minor > 0 ? 3 : 0, (item.income_minor / max) * 160)}px`,
                          }}
                          title={`${item.month} 收入 ${money(item.income_minor)}`}
                        >
                          <span>{money(item.income_minor)}</span>
                        </div>
                        <div
                          className="bar spending"
                          style={{
                            height: `${Math.max(item.spending_minor > 0 ? 3 : 0, (item.spending_minor / max) * 160)}px`,
                          }}
                          title={`${item.month} 支出 ${money(item.spending_minor)}`}
                        >
                          <span>{money(item.spending_minor)}</span>
                        </div>
                      </div>
                      <small>{item.month.slice(5)}<CustomerText text="月" /></small>
                    </div>
                  ))
                ) : (
                  <p className="empty-text"><CustomerText text="没有可绘制的流水数据。" /></p>
                )}
              </div>
              <p className="report-chart-note"><CustomerText text="按银行流水逐笔汇总 · 收入与退款分开统计" /></p>
            </section>
            <section className="panel report-category-panel">
              <div className="small-panel-heading">
                <h2><CustomerText text="消费分类" /></h2>
                <span className="period-label">
                  {report.summary.transaction_count}<CustomerText text="笔流水" /></span>
              </div>
              <SpendingChart analysis={report.summary} />
            </section>
          </div>}
          {!reportUncovered && <div
            className={`reconciliation-note ${report.reconciliation.balanced ? "" : "unbalanced"}`}
          >
            <Icon
              name={report.reconciliation.balanced ? "check" : "info"}
              size={17}
            />
            <strong>
              {report.reconciliation.balanced
                ? customerUiText("本期流水核对一致")
                : customerUiText("本期流水核对存在差异")}
            </strong>
            <span>{report.reconciliation.explanation}</span>
          </div>}
          <section className="panel report-transactions">
            <div className="panel-heading">
              <h2><CustomerText text="交易明细" /></h2>
              <input
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value);
                  setPage(0);
                }}
                placeholder={customerUiText("搜索商户或分类")}
                aria-label={customerUiText("搜索账单交易")}
              />
            </div>
            <div className="transaction-head">
              <span><CustomerText text="商户 / 说明" /></span>
              <span><CustomerText text="分类" /></span>
              <span><CustomerText text="时间" /></span>
              <span><CustomerText text="金额" /></span>
            </div>
            {rows.length ? (
              rows.map((item) => (
                <div className="transaction-row" key={item.transaction_id}>
                  <span>
                    <strong>{customerName(item.merchant)}</strong>
                    {item.flag === "ANOMALY" && <small><CustomerText text="待核验交易" /></small>}
                  </span>
                  <span>{item.category}</span>
                  <time>{dateText(item.occurred_at)}</time>
                  <strong className={item.amount_minor > 0 ? "positive" : ""}>
                    {money(item.amount_minor, { signed: true })}
                  </strong>
                </div>
              ))
            ) : (
              <div className="empty-text transaction-empty"><CustomerText text="没有符合条件的交易。" /></div>
            )}
            <div className="pagination">
              <span><CustomerText text="共" />{filtered.length}<CustomerText text="笔 · 每页 20 笔" /></span>
              <div>
                <button
                  disabled={page === 0}
                  onClick={() => setPage((value) => value - 1)}
                ><CustomerText text="上一页" /></button>
                <span>
                  {page + 1} / {Math.max(1, Math.ceil(filtered.length / 20))}
                </span>
                <button
                  disabled={(page + 1) * 20 >= filtered.length}
                  onClick={() => setPage((value) => value + 1)}
                ><CustomerText text="下一页" /></button>
              </div>
            </div>
          </section>
          </>}
        </>
      )}
    </div>
  );
}

export type AAInvitation = {
  request_id: string; participant_id: string; amount_minor: number; currency: string;
  status: string; request_status: string; version: number; expired: boolean; expires_at: string;
  paymentable: boolean; payment_task_id: string | null; payment_task_state: string | null;
  receipt_verified?: boolean | null;
  recipient: { username: string; display_last4: string; instrument_type: string; currency: string };
};
type AACollection = Omit<AARequest, "received_minor"> & { received_minor: number | null; version?: number;
  request_status?: string; expires_at?: string; payment_mode?: string };

export function AAInvitationCard({ invitation, cards, source, busy, canOpen, onSource, onPrepare, onOpen, onDecline }: {
  invitation: AAInvitation; cards: Card[]; source: string; busy: boolean; canOpen: boolean;
  onSource: (value: string) => void; onPrepare: () => void; onOpen: () => void;
  onDecline?: () => void;
}) {
  const { t: customerUiText } = useCustomerLanguage();
  const ready = invitation.paymentable === true && invitation.request_status === "ACTIVE" && invitation.expired === false
    && (invitation.status === "PENDING" || invitation.status === "PAYMENT_PENDING" && ["FAILED", "CLOSED"].includes(invitation.payment_task_state || ""));
  const state = invitation.expired ? "EXPIRED" : invitation.status === "PAID" && invitation.receipt_verified !== true ? "UNVERIFIED" : invitation.status;
  const labels: Record<string, string> = { PENDING: "待本人处理", PAYMENT_PENDING: "已准备付款任务", PAID: "已付款",
    DECLINED: "已拒绝", CANCELLED: "已取消", EXPIRED: "已过期", UNVERIFIED: "付款回执待核验" };
  return <div className="aa-item">
    <div className="aa-heading"><strong>{customerName(invitation.recipient.username)}</strong><StateBadge state={state} label={labels[state] || "状态待核实"} /></div>
    <p>{money(invitation.amount_minor, { currency: invitation.currency })} · <CustomerText text="收款卡尾号" /> {invitation.recipient.display_last4}</p>
    <small><CustomerText text="有效期至" /> {dateText(invitation.expires_at, true)}</small>
    {ready && <label><CustomerText text="本人付款借记卡" />
      <select value={source} disabled={busy} onChange={event => onSource(event.target.value)}>
        <option value="">{customerUiText("登记默认付款卡")}</option>
        {cards.filter(card => !("instrument_type" in card) || card.instrument_type === "DEBIT").map(card =>
          <option key={card.card_id} value={card.card_number || card.card_id}>{customerUiText("卡尾号")} {card.display_last4 || card.last4} · CNY</option>)}
      </select>
      <button disabled={busy || !canOpen} onClick={onPrepare}><CustomerText text="准备本人付款" /></button>
    </label>}
    {invitation.payment_task_id && canOpen && <button disabled={busy} onClick={onOpen}><CustomerText text="查看付款任务" /></button>}
    {onDecline && invitation.request_status === "ACTIVE" && invitation.expired === false
      && ["PENDING", "PAYMENT_PENDING"].includes(invitation.status)
      && <button disabled={busy} onClick={onDecline}><CustomerText text="拒绝本人未付份额" /></button>}
    <small><CustomerText text="准备任务不等于付款；只有本人完成二次验证并确认后才会扣款。" /></small>
  </div>;
}

export function TransferCenter({
  token,
  revision,
  onPrompt,
  onUpdated,
  onTaskOpen,
}: {
  token: string;
  revision: number;
  onPrompt: (message: string) => void;
  onUpdated: () => void;
  onTaskOpen?: (taskId: string) => void;
}) {
  const { t: customerUiText } = useCustomerLanguage();
  const [schedules, setSchedules] = useState<ScheduledTransfer[]>([]);
  const [collections, setCollections] = useState<AACollection[]>([]);
  const [invitations, setInvitations] = useState<AAInvitation[]>([]);
  const [paymentCards, setPaymentCards] = useState<Card[]>([]);
  const [sourceChoices, setSourceChoices] = useState<Record<string, string>>({});
  const [loadedActor, setLoadedActor] = useState("");
  const actorRef = useRef(token); actorRef.current = token;
  const paymentRequests = useRef<Record<string, { source: string; key: string }>>({});
  const preparingPayment = useRef(false);
  const [paymentNotice, setPaymentNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [tab, setTab] = useState<
    "peer" | "contacts" | "history" | "schedule" | "aa"
  >("peer");
  const refresh = useCallback(async () => {
    if (!token) return;
    const results = await Promise.allSettled([
      request<ScheduledTransfer[]>("/api/v2/transfers/schedules", token),
      request<AACollection[]>("/api/v2/transfers/aa", token),
      request<{ invitations: AAInvitation[] }>("/api/v2/transfers/aa/invitations", token),
      request<Card[]>("/api/v1/cards", token),
    ]);
    if (actorRef.current !== token) return;
    setSchedules(results[0].status === "fulfilled" ? results[0].value : []);
    setCollections(results[1].status === "fulfilled" ? results[1].value : []);
    setInvitations(results[2].status === "fulfilled" ? results[2].value.invitations : []);
    setPaymentCards(results[3].status === "fulfilled" ? results[3].value.filter(card => card.status === "ACTIVE") : []);
    setLoadedActor(token);
    setError(
      results.some((item) => item.status === "rejected")
        ? "部分转账信息暂未加载，请稍后刷新。"
        : "",
    );
  }, [token]);
  useEffect(() => {
    refresh();
  }, [refresh, revision]);
  useEffect(() => { setSourceChoices({}); paymentRequests.current = {}; preparingPayment.current = false; setPaymentNotice(""); }, [token]);
  const ownInvitations = loadedActor === token ? invitations : [];
  const ownCollections = loadedActor === token ? collections : [];
  const ownSchedules = loadedActor === token ? schedules : [];
  const ownCards = loadedActor === token ? paymentCards : [];
  async function prepareAAPayment(invitation: AAInvitation) {
    if (!onTaskOpen || actorRef.current !== token || preparingPayment.current) return;
    preparingPayment.current = true;
    const participantId = invitation.participant_id;
    const selected = sourceChoices[participantId] || "";
    const previous = paymentRequests.current[participantId];
    const requestKey = previous?.source === selected ? previous.key : `aa-pay-${crypto.randomUUID()}`;
    paymentRequests.current[participantId] = { source: selected, key: requestKey };
    setBusy(participantId);
    setError("");
    try {
      const prepared = await request<{ task_id: string }>(
        `/api/v2/transfers/aa/${encodeURIComponent(invitation.request_id)}/participants/${encodeURIComponent(participantId)}/payment-task`,
        token, "POST", { source_card_reference: selected || null }, requestKey);
      if (actorRef.current !== token) return;
      setPaymentNotice("已创建本人付款任务，请以任务当前状态及银行回执为准。");
      await refresh();
      onUpdated();
      onTaskOpen(prepared.task_id);
    } catch (err) {
      if (actorRef.current === token) setError(err instanceof Error ? err.message : "付款任务未准备完成；请先查看已有任务。");
    } finally {
      if (actorRef.current === token) { setBusy(""); preparingPayment.current = false; }
    }
  }
  async function updateAA(path: string, version: number, key: string) {
    if (preparingPayment.current || actorRef.current !== token) return;
    preparingPayment.current = true; setBusy(key); setError("");
    try {
      await request(path, token, "POST", { expected_version: version });
      if (actorRef.current !== token) return;
      await refresh(); onUpdated();
    } catch (err) {
      if (actorRef.current === token) setError(err instanceof Error ? err.message : "AA状态未更新，请先刷新核对。");
    } finally {
      if (actorRef.current === token) { preparingPayment.current = false; setBusy(""); }
    }
  }
  return (
    <section className="panel transfer-center">
      <div className="small-panel-heading">
        <h2><CustomerText text="转账与收款" /></h2>
        <button
          className="icon-button"
          aria-label={customerUiText("刷新转账计划")}
          onClick={refresh}
        >
          <Icon name="refresh" size={16} />
        </button>
      </div>
      <div className="mini-tabs">
        <button
          className={tab === "peer" ? "selected" : ""}
          onClick={() => setTab("peer")}
        ><CustomerText text="平台互转" /></button>
        <button
          className={tab === "contacts" ? "selected" : ""}
          onClick={() => setTab("contacts")}
        ><CustomerText text="身份联系人" /></button>
        <button
          className={tab === "history" ? "selected" : ""}
          onClick={() => setTab("history")}
        ><CustomerText text="收付款记录" /></button>
        <button
          className={tab === "schedule" ? "selected" : ""}
          onClick={() => setTab("schedule")}
        ><CustomerText text="定时转账" /><span>{ownSchedules.length}</span>
        </button>
        <button
          className={tab === "aa" ? "selected" : ""}
          onClick={() => setTab("aa")}
        ><CustomerText text="AA 收款" /><span>{ownCollections.length + ownInvitations.length}</span>
        </button>
      </div>
      {error && <p className="error-inline transfer-error">{error}</p>}
      {tab === "peer" ? (
        <TransferRecipientPanel
          token={token}
          revision={revision}
          onPrompt={onPrompt}
        />
      ) : tab === "contacts" ? (
        <ContactAliasesPanel
          token={token}
          revision={revision}
          onPrompt={onPrompt}
        />
      ) : tab === "history" ? (
        <OwnTransferHistoryPanel token={token} revision={revision} />
      ) : tab === "schedule" ? (
        <div className="scheduled-list">
          {ownSchedules.length ? (
            ownSchedules.map((item) => (
              <div className="scheduled-item" key={item.schedule_id}>
                <div>
                  <span>
                    <Icon name="clock" size={15} />
                    {customerName(item.payee_name)}
                  </span>
                  <StateBadge state={item.status} />
                </div>
                <strong>{money(item.amount_minor)}</strong>
                <small><CustomerText text="银行业务时间" />{dateText(item.due_at, true)}</small>
                {item.error && <p className="error-inline">{item.error}</p>}
              </div>
            ))
          ) : (
            <div className="resource-empty">
              <Icon name="clock" size={25} />
              <p><CustomerText text="暂无定时转账安排" /></p>
            </div>
          )}
          <button
            className="full-width-link"
            onClick={() => onPrompt("明天给房东转100元，备注预约房租")}
          ><CustomerText text="让 Agent 安排一次定时转账" /><Icon name="arrow" size={14} />
          </button>
        </div>
      ) : (
        <div className="aa-list">
          <h3><CustomerText text="我的应付份额" /></h3>
          {paymentNotice && <p role="status"><CustomerText text={paymentNotice} /></p>}
          {ownInvitations.map(invitation => <AAInvitationCard key={invitation.participant_id} invitation={invitation}
            cards={ownCards} source={sourceChoices[invitation.participant_id] || ""} busy={Boolean(busy)} canOpen={Boolean(onTaskOpen)}
            onSource={value => setSourceChoices(current => ({ ...current, [invitation.participant_id]: value }))}
            onPrepare={() => prepareAAPayment(invitation)} onOpen={() => invitation.payment_task_id && onTaskOpen?.(invitation.payment_task_id)}
            onDecline={() => updateAA(`/api/v2/transfers/aa/${encodeURIComponent(invitation.request_id)}/participants/${encodeURIComponent(invitation.participant_id)}/decline`, invitation.version, invitation.participant_id)} />)}
          {!ownInvitations.length && <p><CustomerText text="当前没有属于本人的AA付款邀请。" /></p>}
          <h3><CustomerText text="我发起的收款" /></h3>
          {ownCollections.length ? (
            ownCollections.map((item) => (
              <div className="aa-item" key={item.request_id}>
                <div className="aa-heading">
                  <strong><CustomerText text="AA 收款" /></strong>
                  <StateBadge state={item.status} />
                </div>
                <p>
                  <span>{typeof item.received_minor === "number" ? money(item.received_minor) : customerUiText("到账金额待核验")}</span> /{" "}
                  {money(item.total_minor)}
                </p>
                <div className="progress-track">
                  <span
                    style={{
                      width: `${item.total_minor && typeof item.received_minor === "number" ? (item.received_minor / item.total_minor) * 100 : 0}%`,
                    }}
                  />
                </div>
                <div className="aa-participants">
                  {item.participants.map((person) => (
                    <div key={person.participant_id}>
                      <span>{customerName(person.name)}</span>
                      <strong>{money(person.amount_minor)}</strong>
                      <StateBadge state={person.status} label={item.payment_mode === "LEGACY_SIMULATION" && person.status === "PAID" ? "历史模拟记账" : undefined} />
                    </div>
                  ))}
                </div>
                <small><CustomerText text={item.payment_mode === "LEGACY_SIMULATION" ? "历史模拟记录不证明参与者本人付款。" : "参与者须各自登录确认付款；发起人不能代替他人登记到账。"} /></small>
                {typeof item.version === "number" && item.request_status === "ACTIVE" && ["COLLECTING", "PARTIAL"].includes(item.status)
                  && <button disabled={Boolean(busy)} onClick={() => updateAA(`/api/v2/transfers/aa/${encodeURIComponent(item.request_id)}/cancel`, item.version!, item.request_id)}><CustomerText text="取消剩余未付份额" /></button>}
              </div>
            ))
          ) : (
            <div className="resource-empty">
              <Icon name="transfer" size={25} />
              <p><CustomerText text="暂无 AA 收款请求" /></p>
            </div>
          )}
          <button
            className="full-width-link"
            onClick={() => onPrompt("请帮我发起AA收款，我会补充待收总额和参与者账号。")}
          ><CustomerText text="让 Agent 发起 AA 收款" /><Icon name="arrow" size={14} />
          </button>
        </div>
      )}
    </section>
  );
}

type RecurringItem = {
  merchant: string;
  amount_minor: number;
  cycle: string;
  occurrence_count: number;
  can_cancel_bank_mandate: boolean;
  candidate_only: boolean;
  authoritative_subscription?: { source_type: string } | null;
};
export function RecurringPanel({
  token,
  revision,
  onPrompt,
}: {
  token: string;
  revision: number;
  onPrompt: (message: string) => void;
}) {
  const { t: customerUiText } = useCustomerLanguage();
  const [items, setItems] = useState<RecurringItem[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let disposed = false;
    request<{ candidates: RecurringItem[] }>(
      "/api/v2/subscriptions/analysis",
      token,
    )
      .then((value) => {
        if (!disposed) {
          setItems(value.candidates || []);
          setError("");
        }
      })
      .catch((err) => {
        if (!disposed) setError(err.message);
      });
    return () => {
      disposed = true;
    };
  }, [token, revision]);
  const cycles: Record<string, string> = {
    WEEKLY: "每周",
    MONTHLY: "每月",
    QUARTERLY: "每季度",
    YEARLY: "每年",
  };
  return (
    <section className="panel recurring-panel">
      <div className="small-panel-heading">
        <h2><CustomerText text="周期扣费线索" /></h2>
        <span className="period-label"><CustomerText text="流水推断" /></span>
      </div>
      <p className="recurring-description"><CustomerText text="先从流水发现规律，再核对是否存在可取消的银行代扣协议。" /></p>
      {error ? (
        <p className="error-inline transfer-error">{error}</p>
      ) : items.length ? (
        items.map((item) => (
          <div className="recurring-item" key={item.merchant}>
            <div>
              <strong>{customerName(item.merchant)}</strong>
              <span>
                {money(item.amount_minor)} / {cycles[item.cycle] || item.cycle}
              </span>
            </div>
            <small>
              {item.occurrence_count}<CustomerText text="次规律扣款 ·" />{" "}
              {item.can_cancel_bank_mandate
                ? customerUiText("已核验银行代扣协议")
                : item.authoritative_subscription
                  ? customerUiText("商户侧订阅")
                  : customerUiText("待核验协议归属")}
            </small>
            {item.can_cancel_bank_mandate ? (
              <button
                className="text-button"
                onClick={() => onPrompt(`取消${item.merchant}自动续费`)}
              ><CustomerText text="让 Agent 处理取消" /><Icon name="arrow" size={12} />
              </button>
            ) : (
              <span className="recurring-authority"><CustomerText text="当前未提供银行侧取消能力" /></span>
            )}
          </div>
        ))
      ) : (
        <div className="resource-empty">
          <p><CustomerText text="暂无满足周期规则的扣费线索。" /></p>
        </div>
      )}
    </section>
  );
}

type SimulationView = {
  profile_label: string;
  seed_version: string;
  seed: number;
  business_time: string;
  fault_mode: string;
  counts: Record<string, number>;
  history: { months: number; anomaly_count: number; refund_count: number };
  reconciliation: {
    balanced: boolean;
    expected_balance_minor: number;
    actual_balance_minor: number;
    difference_minor: number;
    statement_ledger_difference_minor?: number;
  };
  advance?: {
    days: number;
    posted_recurring: unknown[];
    declined_recurring: unknown[];
  };
};
const faults = [
  { value: "NONE", label: "正常执行", description: "按正常业务流程执行。" },
  {
    value: "PRE_COMMIT_FAILURE",
    label: "提交前失败",
    description: "注入业务尚未提交的故障。",
  },
  {
    value: "AFTER_COMMIT_RESPONSE_LOST",
    label: "成功后响应丢失",
    description: "业务已改变，响应中断，观察 Agent 查单恢复。",
  },
  { value: "DELAY", label: "执行延迟", description: "注入业务响应延迟。" },
  {
    value: "PRICE_CHANGED",
    label: "产品价格变化",
    description: "验证计划确认后市场价格变化的处理。",
  },
];
export function SimulationDrawer({
  token,
  onClose,
  onUpdated,
}: {
  token: string;
  onClose: () => void;
  onUpdated: () => void;
}) {
  const { t: customerUiText } = useCustomerLanguage();
  const dialogRef = useRef<HTMLElement>(null);
  useDialogFocus(true, { dialogRef, onDismiss: onClose });
  const [state, setState] = useState<SimulationView | null>(null);
  const [days, setDays] = useState(1);
  const [mode, setMode] = useState("NONE");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try {
      const value = await request<SimulationView>("/api/v2/simulation", token);
      setState(value);
      setMode(value.fault_mode);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "未能读取账户运行状态");
    }
  }, [token]);
  useEffect(() => {
    load();
  }, [load]);
  async function advance() {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const value = await request<SimulationView>(
        "/api/v2/simulation/advance",
        token,
        "POST",
        { days },
      );
      setState(value);
      onUpdated();
      setNotice(
        `业务时间已推进 ${days} 天。实际执行结果请核对任务与账套回执。`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "业务时间未能推进");
    } finally {
      setBusy(false);
    }
  }
  async function updateFault() {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const value = await request<SimulationView>(
        "/api/v2/simulation/fault",
        token,
        "POST",
        { mode },
      );
      setState(value);
      setNotice("当前账套的故障注入配置已更新。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "故障配置未更新");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside
        ref={dialogRef}
        className="evidence-drawer simulation-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="simulation-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <span className="eyebrow"><CustomerText text="当前独立账套" /></span>
            <h2 id="simulation-title"><CustomerText text="账户运行控制台" /></h2>
          </div>
          <button
            className="icon-button"
            onClick={onClose}
            aria-label={customerUiText("关闭账户运行控制台")}
          >
            <Icon name="close" />
          </button>
        </header>
        <div className="drawer-content">
          {error && (
            <div className="banner error" role="alert">
              <Icon name="info" size={17} />
              {error}
            </div>
          )}
          {notice && (
            <div className="banner success" role="status">
              <Icon name="check" size={17} />
              {notice}
            </div>
          )}
          {state ? (
            <>
              <div className="simulation-overview">
                <span className="simulation-label">
                  {customerName(state.profile_label)}
                </span>
                <strong>{dateText(state.business_time, true)}</strong>
                <p><CustomerText text="银行业务时间 · Asia / Shanghai" /></p>
                <div>
                  <span><CustomerText text="种子" />{state.seed}</span>
                  <span>{state.seed_version}</span>
                </div>
              </div>
              <section className="simulation-section">
                <h3><CustomerText text="账套核验" /></h3>
                <div className="simulation-reconciliation">
                  <span
                    className={`state-badge ${state.reconciliation.balanced ? "success" : "danger"}`}
                  >
                    <i />
                    {state.reconciliation.balanced
                      ? customerUiText("余额与分录核对一致")
                      : customerUiText("余额或分录存在差异")}
                  </span>
                  <dl>
                    <div>
                      <dt><CustomerText text="账套期望余额" /></dt>
                      <dd>
                        {money(state.reconciliation.expected_balance_minor)}
                      </dd>
                    </div>
                    <div>
                      <dt><CustomerText text="当前账户余额" /></dt>
                      <dd>
                        {money(state.reconciliation.actual_balance_minor)}
                      </dd>
                    </div>
                    <div>
                      <dt><CustomerText text="余额差额" /></dt>
                      <dd>{money(state.reconciliation.difference_minor)}</dd>
                    </div>
                    <div>
                      <dt><CustomerText text="流水与总账差额" /></dt>
                      <dd>
                        {money(
                          state.reconciliation
                            .statement_ledger_difference_minor || 0,
                        )}
                      </dd>
                    </div>
                  </dl>
                </div>
                <div className="simulation-counts">
                  <span>
                    <strong>{state.history.months}</strong><CustomerText text="个月历史" /></span>
                  <span>
                    <strong>{state.counts.transactions}</strong><CustomerText text="笔流水" /></span>
                  <span>
                    <strong>{state.history.anomaly_count}</strong><CustomerText text="笔待核验" /></span>
                </div>
              </section>
              <section className="simulation-section">
                <h3><CustomerText text="推进银行业务时间" /></h3>
                <p><CustomerText text="推进后，已登记的定时转账将由 Worker 在到期时重新核验；有效订阅可产生新的账套扣款，仅作用于当前账户记录，不连接外部扣款服务。认证凭证仍按实际时间过期。" /></p>
                <div className="simulation-control-row">
                  <label>
                    <input
                      type="number"
                      min="1"
                      max="365"
                      value={days}
                      onChange={(event) => setDays(Number(event.target.value))}
                      aria-label={customerUiText("推进银行业务时间天数")}
                    /><CustomerText text="天" /></label>
                  <button
                    className="button secondary"
                    onClick={advance}
                    disabled={
                      busy || !Number.isInteger(days) || days < 1 || days > 365
                    }
                  >
                    <Icon name="clock" size={16} />
                    {busy ? customerUiText("处理中…") : customerUiText("推进业务时间")}
                  </button>
                </div>
                {state.advance && (
                  <div className="simulation-advance-result"><CustomerText text="本次周期扣款：" />{state.advance.posted_recurring.length}<CustomerText text="笔成功 ·" />{state.advance.declined_recurring.length}<CustomerText text="笔拒绝" /></div>
                )}
              </section>
              <section className="simulation-section">
                <h3><CustomerText text="业务故障注入" /></h3>
                <p><CustomerText text="配置作用于当前账套中的后续操作，用于观察 Agent 的异常处理与回执恢复。" /></p>
                <div className="fault-options">
                  {faults.map((item) => (
                    <label
                      key={item.value}
                      className={mode === item.value ? "selected" : ""}
                    >
                      <input
                        type="radio"
                        name="fault-mode"
                        value={item.value}
                        checked={mode === item.value}
                        onChange={() => setMode(item.value)}
                      />
                      <span>
                        <strong>{item.label}</strong>
                        <small>{item.description}</small>
                      </span>
                    </label>
                  ))}
                </div>
                <button
                  className="button secondary"
                  disabled={busy || mode === state.fault_mode}
                  onClick={updateFault}
                ><CustomerText text="应用故障注入配置" /></button>
              </section>
            </>
          ) : (
            !error && (
              <div className="loading-task">
                <span className="spinner" /><CustomerText text="正在读取运行状态…" /></div>
            )
          )}
        </div>
        <footer>
          <span className="muted small"><CustomerText text="所有操作限定在当前账户范围内，不接入外部银行或支付服务" /></span>
          <button className="button secondary" onClick={load}>
            <Icon name="refresh" size={16} /><CustomerText text="刷新核验" /></button>
        </footer>
      </aside>
    </div>
  );
}

type WealthProduct = {
  product_id: string;
  name: string;
  risk_level: number;
  currency: "CNY";
  status: string;
  version: number;
  unit_price_minor: number;
  holding_units: number;
  suitable: boolean;
  suitability_explanation: string;
  terms: {
    fee_minor: number;
    redemption_timing: string;
    term: string;
    capital_guaranteed: boolean;
  };
};
type RiskProfile = {
  risk_level: number | null;
  status: string;
  valid: boolean;
  expires_at: string | null;
  assessment_source: string;
  reason_explanations?: string[];
};
type WealthProductRead = {
  products: WealthProduct[];
  risk_profile: RiskProfile;
  business_time: string;
};

/** Accept both parts of the same read together; a malformed part is not an empty result. */
function checkedWealthProductRead(value: unknown): WealthProductRead {
  const record = (item: unknown): item is Record<string, unknown> => Boolean(item && typeof item === "object" && !Array.isArray(item));
  const text = (item: unknown): item is string => typeof item === "string" && item.trim().length > 0;
  const integer = (item: unknown): item is number => typeof item === "number" && Number.isSafeInteger(item) && item >= 0;
  const level = (item: unknown): item is number => integer(item) && item >= 1 && item <= 5;
  const date = (item: unknown): item is string => text(item) && /^\d{4}-\d{2}-\d{2}T/.test(item) && Number.isFinite(Date.parse(item));
  const invalid = () => { throw new ApiError("", 0, "WEALTH_READ_SCHEMA_INVALID"); };
  if (!record(value) || !Array.isArray(value.products) || !record(value.risk_profile) || !date(value.business_time)) return invalid();
  const profile = value.risk_profile;
  if (typeof profile.valid !== "boolean" || !["VALID", "EXPIRED", "MISSING"].includes(String(profile.status))
    || !text(profile.assessment_source) || (profile.risk_level !== null && !level(profile.risk_level))
    || (profile.expires_at !== null && !date(profile.expires_at))) return invalid();
  if (profile.status === "MISSING" ? profile.valid || profile.risk_level !== null || profile.expires_at !== null
    : !level(profile.risk_level) || !date(profile.expires_at)
      || profile.valid !== (profile.status === "VALID")
      || profile.valid !== (Date.parse(profile.expires_at) > Date.parse(value.business_time))) return invalid();
  const identities = new Set<string>();
  for (const product of value.products) {
    if (!record(product) || !text(product.product_id) || identities.has(product.product_id) || !text(product.name)
      || !level(product.risk_level) || product.currency !== "CNY" || !text(product.status)
      || !integer(product.version) || product.version < 1 || !integer(product.unit_price_minor)
      || !integer(product.holding_units) || typeof product.suitable !== "boolean" || !text(product.suitability_explanation)
      || !record(product.terms) || !integer(product.terms.fee_minor) || !text(product.terms.term)
      || !text(product.terms.redemption_timing) || product.terms.capital_guaranteed !== false) return invalid();
    if (product.suitable && (!profile.valid || !level(profile.risk_level) || product.risk_level > profile.risk_level
      || product.status !== "AVAILABLE" || product.unit_price_minor <= 0)) return invalid();
    identities.add(product.product_id);
  }
  return value as unknown as WealthProductRead;
}

function useWealthProductRead(token: string, revision: number) {
  const visibility = useReadVisibility();
  const [attempt, setAttempt] = useState(0);
  const scope = { token, revision, epoch: visibility.epoch, attempt, visible: visibility.visible };
  type Scope = typeof scope;
  type Read = Scope & { status: "LOADING" | "READY" | "ERROR" | "CANCELLED"; data: WealthProductRead | null; reason?: unknown };
  type Ticket = { scope: Scope; abort: AbortController; data: WealthProductRead | null };
  const currentScope = useRef(scope);
  currentScope.current = scope;
  const ticketRef = useRef<Ticket | null>(null);
  const cancelledScope = useRef<{ token: string; revision: number } | null>(null);
  const [read, setRead] = useState<Read | null>(null);
  const sameScope = (bound: Scope, current: Scope) => bound.token === current.token && bound.revision === current.revision
    && bound.epoch === current.epoch && bound.attempt === current.attempt;
  const currentTicket = (ticket: Ticket) => ticketRef.current === ticket && !ticket.abort.signal.aborted
    && currentScope.current.visible && document.visibilityState === "visible" && sameScope(ticket.scope, currentScope.current);
  useEffect(() => {
    if (!token || !visibility.visible || cancelledScope.current?.token === token && cancelledScope.current.revision === revision) return;
    cancelledScope.current = null;
    const ticket: Ticket = { scope, abort: new AbortController(), data: null };
    ticketRef.current = ticket;
    setRead({ ...scope, status: "LOADING", data: null });
    request<unknown>("/api/v2/wealth/products", token, "GET", undefined, undefined, { signal: ticket.abort.signal })
      .then(checkedWealthProductRead)
      .then(data => {
        if (!currentTicket(ticket)) return;
        ticket.data = data;
        setRead({ ...ticket.scope, status: "READY", data });
      })
      .catch(reason => {
        if (!currentTicket(ticket)) return;
        ticket.data = null;
        setRead({ ...ticket.scope, status: isAbortError(reason) ? "CANCELLED" : "ERROR", data: null, reason });
      });
    return () => {
      ticket.data = null;
      ticket.abort.abort();
      if (ticketRef.current === ticket) ticketRef.current = null;
    };
  }, [token, revision, visibility.visible, visibility.epoch, attempt]);
  const isCurrent = (data: WealthProductRead | null) => Boolean(data && ticketRef.current
    && ticketRef.current.data === data && currentTicket(ticketRef.current));
  const live = read && sameScope(read, scope) ? read : null;
  const data = live?.status === "READY" && isCurrent(live.data) ? live.data : null;
  const cancelled = cancelledScope.current?.token === token && cancelledScope.current.revision === revision || live?.status === "CANCELLED";
  return { data, isCurrent, paused: !visibility.visible,
    arrivalScope: JSON.stringify([token, revision, visibility.epoch]), arrivalGeneration: scope.attempt,
    loading: Boolean(token && visibility.visible && !cancelled && (!live || live.status === "LOADING")),
    cancelled, reason: live?.status === "ERROR" ? live.reason : undefined,
    refresh() {
      if (!currentScope.current.token || !currentScope.current.visible || document.visibilityState !== "visible") return;
      cancelledScope.current = null;
      if (ticketRef.current) { ticketRef.current.data = null; ticketRef.current.abort.abort(); ticketRef.current = null; }
      setAttempt(value => value + 1);
    },
    cancel() {
      cancelledScope.current = { token: currentScope.current.token, revision: currentScope.current.revision };
      if (ticketRef.current) { ticketRef.current.data = null; ticketRef.current.abort.abort(); ticketRef.current = null; }
      setRead({ ...currentScope.current, status: "CANCELLED", data: null });
    },
  };
}
type Questionnaire = {
  questionnaire_version: string;
  explanation: string;
  questions: {
    question_id: string;
    title: string;
    options: { value: number; label: string }[];
  }[];
};
export function WealthPanel({
  token,
  revision,
  onPrompt,
  onUpdated,
  assessmentOnly = false,
}: {
  token: string;
  revision: number;
  onPrompt: (message: string) => void;
  onUpdated: () => void;
  assessmentOnly?: boolean;
}) {
  const { locale, t: customerUiText } = useCustomerLanguage();
  const copy = (zh: string, en: string) => locale === "en" ? en : zh;
  const riskNoticeId = useId();
  const productRead = useWealthProductRead(token, revision);
  const products = productRead.data?.products || [];
  const profile = productRead.data?.risk_profile;
  const productArrival = useResourceReadArrival({ scopeKey: productRead.arrivalScope,
    generation: productRead.arrivalGeneration, state: productRead.data ? "READY" : productRead.loading ? "LOADING"
      : productRead.cancelled ? "CANCELLED" : productRead.reason !== undefined ? "ERROR" : "UNREAD",
    visible: !productRead.paused, count: products.length });
  const [error, setError] = useState("");
  const [questionnaire, setQuestionnaire] = useState<Questionnaire | null>(
    null,
  );
  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [independent, setIndependent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [assessmentResult, setAssessmentResult] = useState<
    (RiskProfile & { score: number; message: string }) | null
  >(null);
  const [formError, setFormError] = useState("");
  const riskDialogRef = useRef<HTMLElement>(null);
  const closeQuestionnaire = useCallback(() => setQuestionnaire(null), []);
  useDialogFocus(Boolean(questionnaire), { dialogRef: riskDialogRef, onDismiss: closeQuestionnaire });
  useEffect(() => {
    setQuestionnaire(null); setAssessmentResult(null); setAnswers({}); setIndependent(false); setError(""); setBusy(false);
  }, [token, revision, productRead.paused]);
  useEffect(() => {
    if (!productRead.data) setBusy(false);
  }, [productRead.data]);
  async function openQuestionnaire() {
    const boundRead = productRead.data;
    if (!productRead.isCurrent(boundRead) || busy) return;
    setBusy(true);
    setFormError("");
    setError("");
    setAssessmentResult(null);
    try {
      const value = await request<Questionnaire>("/api/v2/wealth/risk-assessment", token);
      if (!productRead.isCurrent(boundRead)) return;
      setQuestionnaire(value);
      setAnswers({});
      setIndependent(false);
    } catch (err) {
      if (!productRead.isCurrent(boundRead)) return;
      setError(customerRecovery(err, { operation: "read", locale }).message);
    } finally {
      if (productRead.isCurrent(boundRead)) setBusy(false);
    }
  }
  async function submitAssessment() {
    const boundRead = productRead.data;
    if (
      !productRead.isCurrent(boundRead) || busy ||
      !questionnaire ||
      !independent ||
      questionnaire.questions.some(
        (question) => answers[question.question_id] === undefined,
      )
    )
      return;
    setBusy(true);
    setFormError("");
    try {
      const value = await request<
        RiskProfile & { score: number; message: string }
      >("/api/v2/wealth/risk-assessment", token, "POST", {
        questionnaire_version: questionnaire.questionnaire_version,
        answers,
        independent_confirmation: independent,
      });
      if (!productRead.isCurrent(boundRead)) return;
      setAssessmentResult(value);
      setBusy(false);
      productRead.refresh();
      onUpdated();
    } catch (err) {
      if (!productRead.isCurrent(boundRead)) return;
      setFormError(customerRecovery(err, { operation: "submit", locale }).message);
    } finally {
      if (productRead.isCurrent(boundRead)) setBusy(false);
    }
  }
  return (
    <>
      <section className="panel wealth-products-panel">
        <div className="small-panel-heading">
          <h2>{assessmentOnly ? copy("风险测评", "Risk assessment") : customerUiText("理财产品与风险档案")}</h2>
          <AccessibleReloadButton className="text-button" scopeKey={`${token}:${revision}`} onReload={productRead.refresh}
            blocked={busy || productRead.loading || productRead.paused || !token}
            label={copy("重新读取理财产品与风险档案", "Reload products and risk profile")}>
            <Icon name="refresh" size={14} />{copy("重新读取", "Reload")}
          </AccessibleReloadButton>
        </div>
        {!assessmentOnly && productRead.data && products.length > 0 && <p id={riskNoticeId} className="wealth-risk-notice" role="note">
          <Icon name="info" size={17} />
          <span>{copy("本金无保证，参考价格不承诺收益。准备草稿不会成交，交易前仍需核验并由你确认。",
            "Capital is not guaranteed. Reference prices are not a return promise. Preparing a draft does not place an order; transactions still require checks and your confirmation.")}</span>
        </p>}
        {productRead.loading && <div role="status"><span className="spinner" />{copy("正在读取理财产品与风险档案…", "Loading products and risk profile…")}
          <button className="text-button" onClick={productRead.cancel}>{copy("取消读取", "Cancel read")}</button>
        </div>}
        {productRead.paused && <p role="status">{copy("页面已暂停读取，返回后会重新核验资料。", "Reading is paused. Information will be rechecked when you return.")}</p>}
        {productRead.cancelled && !productRead.paused && <p role="status">{copy("本次读取已取消，产品与风险资料尚未核验。可点击重新读取。", "This read was cancelled. Products and risk information are unverified. Select Reload to try again.")}</p>}
        {productRead.reason !== undefined && !productRead.paused && <div className="error-inline transfer-error" role="alert">
          <p>{productRead.reason instanceof ApiError && productRead.reason.code === "WEALTH_READ_SCHEMA_INVALID"
            ? copy("理财产品或风险档案尚未通过完整核验，暂不展示适配结论。请重新读取。", "Products or the risk profile could not be fully verified. Suitability is unavailable. Please reload.")
            : customerRecovery(productRead.reason, { operation: "read", locale }).message}</p>
          <p>{copy("当前产品、持仓与适配结论尚未核验，重新读取成功后才能准备交易草稿。", "Products, holdings and suitability are unverified. Reload successfully before preparing a transaction draft.")}</p>
        </div>}
        {productRead.data && products.length === 0 && <p>{copy("本次读取未发现理财产品记录。", "No product records were found in this read.")}</p>}
        {profile && (
          <div className="risk-profile-card">
            <div>
              <span><CustomerText text="当前风险等级" /></span>
              <strong>
                {profile.valid && profile.risk_level ? `R${profile.risk_level}` : customerUiText("未测评")}
              </strong>
            </div>
            <div>
              <span
                className={`state-badge ${profile.valid ? "success" : "warning"}`}
              >
                <i />
                {profile.valid
                  ? customerUiText("测评有效")
                  : profile.status === "EXPIRED"
                    ? customerUiText("测评已过期")
                    : customerUiText("需要独立测评")}
              </span>
              <small>
                {profile.valid && profile.expires_at
                  ? `${copy("本次读取的有效期至", "Validity returned by this read:")} ${dateText(profile.expires_at, true)}`
                  : customerUiText("完成测评后才能核验适配")}
              </small>
              <small>{copy("档案有效性核验时点：", "Risk-status reference time:")} {dateText(productRead.data?.business_time, true)}</small>
            </div>
          </div>
        )}
        <button
          className="risk-assessment-link"
          onClick={openQuestionnaire}
          disabled={busy || !productRead.data}
        ><CustomerText text="本人独立风险测评" /><Icon name="arrow" size={14} />
        </button>
        {error && <p className="error-inline transfer-error" role="alert">{error}</p>}
        {!assessmentOnly && <ResourceReadArrival receipt={productArrival}>
        {products.map((product) => (
          <div className="wealth-product" key={product.product_id} data-resource-warning={!product.suitable ? "true" : undefined}>
            <div className="wealth-product-heading">
              <strong>{customerName(product.name)}</strong>
              <span>R{product.risk_level}</span>
            </div>
            <div className="wealth-product-price">
              <span><CustomerText text="份额单价" /><strong>{money(product.unit_price_minor)}</strong>
              </span>
              <span><CustomerText text="持有份额" /><strong>{product.holding_units}</strong>
              </span>
            </div>
            <div className="wealth-terms">
              <span><CustomerText text="平台费用" /> {money(product.terms.fee_minor)}</span>
              <span>{customerUiText(product.terms.term)}</span>
              <span>{customerUiText(product.terms.redemption_timing)}</span>
            </div>
            <p className="wealth-suitability">
              {product.suitable
                ? customerUiText("当前风险档案允许进入交易核验")
                : customerUiText(product.suitability_explanation)}
            </p>
            <div className="wealth-product-actions">
              <button
                className="button secondary compact"
                aria-describedby={riskNoticeId}
                disabled={!product.suitable}
                onClick={() => {
                  if (productRead.isCurrent(productRead.data) && product.suitable)
                    onPrompt(locale === "en" ? `Prepare a purchase of ${product.name} for CNY 200.` : `申购${product.name}200元`);
                }}
              ><CustomerText text="准备申购" /></button>
              <button
                className="text-button"
                aria-describedby={riskNoticeId}
                disabled={!product.holding_units}
                onClick={() => {
                  if (productRead.isCurrent(productRead.data) && product.holding_units > 0)
                    onPrompt(locale === "en" ? `Prepare a redemption of ${product.name} for CNY 100.` : `赎回${product.name}100元`);
                }}
              ><CustomerText text="准备赎回" /></button>
            </div>
          </div>
        ))}
        </ResourceReadArrival>}
        {!assessmentOnly && <p className="wealth-product-note"><CustomerText text="本金无保证。参考价格与条款来自当前平台产品记录，尚未接入外部产品服务，不构成外部成交承诺。交易前仍需核验金额、余额、应急金与产品版本。" /></p>}
      </section>
      {questionnaire && productRead.data && (
        <div className="drawer-backdrop" onClick={closeQuestionnaire}>
          <aside
            ref={riskDialogRef}
            className="evidence-drawer risk-drawer"
            role="dialog"
            aria-modal="true"
            aria-labelledby="risk-title"
            onClick={(event) => event.stopPropagation()}
          >
            <header>
              <div>
                <span className="eyebrow"><CustomerText text="本人独立提交" /></span>
                <h2 id="risk-title"><CustomerText text="个人风险测评" /></h2>
              </div>
              <button
                className="icon-button"
                onClick={closeQuestionnaire}
                aria-label={customerUiText("关闭风险测评")}
              >
                <Icon name="close" />
              </button>
            </header>
            <div className="drawer-content">
              <p className="risk-form-intro">{customerUiText(questionnaire.explanation)}</p>
              {assessmentResult ? (
                <div className="risk-assessment-result">
                  <span className="risk-result-icon">
                    <Icon name="check" size={27} />
                  </span>
                  <h3><CustomerText text="风险档案已更新" /></h3>
                  <strong>R{assessmentResult.risk_level}</strong>
                  <p>{customerUiText(assessmentResult.message)}</p>
                  {Array.isArray(assessmentResult.reason_explanations) && <ul>
                    {assessmentResult.reason_explanations.filter(reason => typeof reason === "string").map(reason =>
                      <li key={reason}>{customerUiText(reason)}</li>)}
                  </ul>}
                  <span><CustomerText text="本次得分" />{assessmentResult.score}<CustomerText text="· 有效至" />{" "}
                    {dateText(assessmentResult.expires_at || undefined)}
                  </span>
                </div>
              ) : (
                <form
                  onSubmit={(event) => {
                    event.preventDefault();
                    submitAssessment();
                  }}
                >
                  {questionnaire.questions.map((question, index) => (
                    <fieldset
                      className="risk-question"
                      key={question.question_id}
                    >
                      <legend>
                        <span>{index + 1}</span>
                        {customerUiText(question.title)}
                      </legend>
                      {question.options.map((option) => (
                        <label
                          key={option.value}
                          className={
                            answers[question.question_id] === option.value
                              ? "selected"
                              : ""
                          }
                        >
                          <input
                            type="radio"
                            name={question.question_id}
                            value={option.value}
                            checked={
                              answers[question.question_id] === option.value
                            }
                            onChange={() =>
                              setAnswers((current) => ({
                                ...current,
                                [question.question_id]: option.value,
                              }))
                            }
                          />
                          <span>{customerUiText(option.label)}</span>
                        </label>
                      ))}
                    </fieldset>
                  ))}
                  <label className="independent-confirmation">
                    <input
                      type="checkbox"
                      checked={independent}
                      onChange={(event) => setIndependent(event.target.checked)}
                    />
                    <span><CustomerText text="我确认上述答案由本人独立选择，Agent 没有替我填写或调整风险等级。" /></span>
                  </label>
                  {formError && (
                    <p className="error-inline" role="alert">
                      {formError}
                    </p>
                  )}
                  <button
                    className="button primary risk-submit"
                    type="submit"
                    disabled={
                      busy ||
                      !independent ||
                      questionnaire.questions.some(
                        (question) =>
                          answers[question.question_id] === undefined,
                      )
                    }
                  >
                    {busy ? customerUiText("正在保存…") : customerUiText("确认并提交独立测评")}
                    <Icon name="check" size={16} />
                  </button>
                </form>
              )}
            </div>
            <footer>
              <span className="muted small"><CustomerText text="固定评分规则 ·" />{questionnaire.questionnaire_version}
              </span>
              <button
                className="button secondary"
                onClick={closeQuestionnaire}
              >
                {assessmentResult ? customerUiText("完成测评") : customerUiText("暂不提交")}
              </button>
            </footer>
          </aside>
        </div>
      )}
    </>
  );
}
