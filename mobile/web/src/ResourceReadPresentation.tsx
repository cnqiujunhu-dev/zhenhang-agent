import { useCustomerLanguage, CustomerText } from "./CustomerLanguageProvider";
import { dateText, Icon, money } from "./components";
import { resourceReadNotice, type FinancialResourceKind, type ResourceDatasetRead } from "./financialResourceReadTrust";
import type { CustomerLocale } from "./customerLanguage";
import type { Investment, Notification } from "./types";
import { AccessibleReloadButton } from "./AccessibleReloadButton";

export function ResourceReadEmpty({ kind, read }: { kind: FinancialResourceKind; read: ResourceDatasetRead }) {
  const { locale, t } = useCustomerLanguage();
  const empty = kind === "cards" ? t("当前身份没有可管理卡片")
    : locale === "en" ? "No records were found in this read." : "本次读取未发现记录。";
  return <p role={read.status === "ERROR" ? "alert" : read.status === "READY" ? undefined : "status"}>
    {read.status === "READY" ? empty : resourceReadNotice(kind, read, locale)}
  </p>;
}

export function ResourceHoldingOverview({ records, read }: { records: Investment[]; read: ResourceDatasetRead }) {
  const { locale } = useCustomerLanguage();
  const ready = read.status === "READY";
  const amount = ready ? records.reduce((sum, product) => sum + product.holding_units * product.price_minor, 0) : null;
  return <div className="asset-card">
    <div className="asset-label"><span><CustomerText text="理财持仓参考金额" /></span><Icon name="wealth" size={18} /></div>
    <strong className="asset-value">{ready && records.length && Number.isSafeInteger(amount) ? money(amount!) : "—"}</strong>
    <div className="asset-foot"><span>{ready
      ? <>{records.filter(item => item.holding_units > 0).length}<CustomerText text="项持仓 · 以当前参考价格计算" /></>
      : resourceReadNotice("investments", read, locale)}</span></div>
  </div>;
}

export function notificationCountLabel(read: ResourceDatasetRead, locale: CustomerLocale) {
  if (read.status === "READY" && Array.isArray(read.data)) return locale === "en" ? `Notifications, ${read.data.length}` : `查看通知，${read.data.length}条`;
  if (Array.isArray(read.previous) && read.previous.length > 0) return locale === "en"
    ? `Notifications, ${read.previous.length} previously read records; latest count unverified`
    : `查看通知，上次读取${read.previous.length}条，最新数量尚未核验`;
  return locale === "en" ? "Notifications, latest count unverified" : "查看通知，最新数量尚未核验";
}

export function ResourceNotifications({ read, labels, onRefresh, onOpen }: {
  read: ResourceDatasetRead; labels: Record<string, string>; onRefresh: () => void; onOpen: (item: Notification) => void;
}) {
  const { locale, t } = useCustomerLanguage();
  const ready = read.status === "READY", current = ready && Array.isArray(read.data) ? read.data as Notification[] : null;
  const previous = !ready && Array.isArray(read.previous) && read.previous.length > 0 ? read.previous as Notification[] : null;
  const records = current ?? previous ?? [];
  return <>
    <div className="popover-heading"><strong><CustomerText text="办理通知" /></strong>
      <span>{current ? <>{current.length}<CustomerText text="条" /></> : previous
        ? locale === "en" ? `${previous.length} previously read` : `上次${previous.length}条` : "—"}</span>
    </div>
    <AccessibleReloadButton className="text-button" scopeKey={String(read.generation)} onReload={onRefresh}
      blocked={read.status === "LOADING" || read.status === "UNREAD"}
      label={locale === "en" ? "Reload notifications" : "重新读取办理通知"}>
      <Icon name="refresh" size={14} />{locale === "en" ? "Reload" : "重新读取"}
    </AccessibleReloadButton>
    {!ready && <p role={read.status === "ERROR" ? "alert" : "status"}>{resourceReadNotice("notifications", read, locale)}</p>}
    {previous && <p role="status">{locale === "en" ? "Previously read records are shown below; the latest notifications are unverified." : "以下为上次读取记录，最新通知尚未核验。"}</p>}
    {records.slice(0, 8).map(item => <button className="notification-item" key={item.notification_id}
      disabled={!ready || !(item.root_task_id || item.task_id)} onClick={() => onOpen(item)}>
      <span className="notification-icon"><Icon name="check" size={16} /></span>
      <span><strong>{labels[item.event_type] ? t(labels[item.event_type]) : t("金融任务状态更新")}</strong><small>{dateText(item.created_at)}</small></span>
      <Icon name="chevron" size={14} />
    </button>)}
    {current?.length === 0 && <div className="popover-empty"><Icon name="bell" size={28} />
      <p><CustomerText text="暂无办理通知" /></p><small><CustomerText text="执行结果投递后会显示在这里。" /></small>
    </div>}
  </>;
}
