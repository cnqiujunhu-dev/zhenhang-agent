import { useCustomerLanguage } from "./CustomerLanguageProvider";
import { Icon } from "./components";
import { subscriptionOverview, type SubscriptionLoadState } from "./subscriptionOverview";

export function SubscriptionOverviewCard({ records, loadState, onOpen }: {
  records: unknown; loadState: SubscriptionLoadState; onOpen: () => void;
}) {
  const { locale } = useCustomerLanguage();
  const summary = subscriptionOverview(records, loadState), english = locale === "en";
  const note = loadState === "LOADING" ? (english ? "Reading subscription records…" : "正在读取订阅资料…")
    : summary.active === null ? (english ? "Subscription records are unavailable or incomplete." : "订阅资料未取得或尚未完整返回。")
    : null;
  return <div className="asset-card" aria-label={english ? "Subscription overview" : "订阅概览"}>
    <div className="asset-label"><span>{english ? "Active subscriptions" : "启用的订阅"}</span><Icon name="subscription" size={19} /></div>
    <strong className="asset-value number">{summary.active ?? "—"}{summary.active !== null && <small>{english ? " services" : "项"}</small>}</strong>
    <div className="asset-foot">{note && <span>{note}</span>}<button type="button" className="text-button" style={{ minHeight: 44, minWidth: 44 }} onClick={onOpen}>
      {english ? "View list" : "查看清单"}<Icon name="chevron" size={12} />
    </button></div>
  </div>;
}
