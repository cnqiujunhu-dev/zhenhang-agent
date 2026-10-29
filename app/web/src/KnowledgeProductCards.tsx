import { useId, useState } from "react";
import { useCustomerLanguage } from "./CustomerLanguageProvider";
import { AccessibleReloadButton } from "./AccessibleReloadButton";
import { Skeleton } from "./components";
import { useEndpoint } from "./useEndpoint";
import type { CustomerLocale } from "./customerLanguage";
import "./knowledgeProductCards.css";

type ProductLiterature = {
  product_key: string; catalog_version: string; name: string; currency: "CNY";
  illustrative_risk_level: number; risk_level_is_current_bank_fact: false;
  audience: string; risk_note: string; principal_guaranteed: false; return_guaranteed: false;
  guidance_only: true; fees: string; minimum: string; exit_conditions: string;
  availability: string; source: string;
};
type PublishedTerms = { source_id: string; title: string; source_uri: string; version: string; content_hash: string; content: string; effective_from: string; expires_at: string | null; guidance_only: true; execution_authority: false };
const validProduct = (value: unknown): value is ProductLiterature => {
  if (!value || typeof value !== "object") return false;
  const row = value as Record<string, unknown>;
  return ["product_key", "catalog_version", "name", "audience", "risk_note", "fees", "minimum", "exit_conditions", "availability", "source"].every(key => typeof row[key] === "string" && (row[key] as string).length > 0)
    && row.currency === "CNY" && row.guidance_only === true && row.principal_guaranteed === false && row.return_guaranteed === false
    && row.risk_level_is_current_bank_fact === false && Number.isInteger(row.illustrative_risk_level) && Number(row.illustrative_risk_level) >= 1 && Number(row.illustrative_risk_level) <= 5;
};
const validTerms = (value: unknown): value is PublishedTerms => {
  if (!value || typeof value !== "object") return false;
  const row = value as Record<string, unknown>;
  return ["source_id", "title", "source_uri", "version", "content", "effective_from"].every(key => typeof row[key] === "string" && (row[key] as string).length > 0)
    && typeof row.content_hash === "string" && /^[a-f0-9]{64}$/.test(row.content_hash)
    && row.guidance_only === true && row.execution_authority === false;
};

/** Product literature only. There is intentionally no order/confirmation callback. */
export function KnowledgeProductCards({ token, language, selectedProductKey, onSelectedProductKeyChange }: {
  token: string;
  language?: CustomerLocale;
  selectedProductKey?: string;
  onSelectedProductKeyChange?: (key: string) => void;
}) {
  const context = useCustomerLanguage();
  const locale = context.explicitPreference ? context.locale : language || context.locale;
  const en = locale === "en";
  const headingId = useId();
  const [selected, setSelected] = useState<{ token: string; locale: string; key: string } | null>(null);
  // A controlled parent binds selection to actor/reply/locale authority, so a
  // temporary unmount cannot erase it. Uncontrolled callers retain the original
  // actor+locale-bound behaviour. Neither form authorizes an unchecked key.
  const controlled = selectedProductKey !== undefined;
  const selectedKey = controlled ? selectedProductKey : selected?.token === token && selected.locale === locale ? selected.key : "";
  const list = useEndpoint<{ items: unknown[]; catalog_version: string; read_only: boolean; bank_confirmation_permitted: boolean }>(`/api/v3/customer/knowledge/products?language=${locale}`, token);
  const validList = list.data?.read_only === true && list.data.bank_confirmation_permitted === false && Array.isArray(list.data.items)
    && list.data.items.every(validProduct) && new Set(list.data.items.map(row => (row as ProductLiterature).product_key)).size === list.data.items.length
    && list.data.items.every(row => (row as ProductLiterature).catalog_version === list.data?.catalog_version);
  const products = validList ? list.data!.items as ProductLiterature[] : [];
  const knownKey = products.some(row => row.product_key === selectedKey) ? selectedKey : "";
  const detail = useEndpoint<{ product: unknown; public_terms: unknown; read_only: boolean; bank_confirmation_permitted: boolean }>(knownKey ? `/api/v3/customer/knowledge/products/${encodeURIComponent(knownKey)}?language=${locale}` : null, token);
  const validDetail = detail.data?.read_only === true && detail.data.bank_confirmation_permitted === false && validProduct(detail.data.product)
    && detail.data.product.product_key === knownKey && detail.data.product.catalog_version === list.data?.catalog_version;
  const terms = validDetail && validTerms(detail.data!.public_terms) ? detail.data!.public_terms as PublishedTerms : null;
  const unavailable = en ? "Product literature has not passed verification. This is not an empty product list." : "产品资料尚未通过核验，这不代表没有产品。";
  const choose = (key: string) => {
    if (controlled) onSelectedProductKeyChange?.(key);
    else setSelected(key ? { token, locale, key } : null);
  };

  return <section className="knowledge-products" aria-labelledby={headingId}>
    <header><h3 id={headingId}>{en ? "Explore product information" : "了解产品"}</h3></header>
    {list.loading && !list.data && <Skeleton lines={3} />}
    {(list.error || list.data && !validList) && <p className="login-error" role="alert">{list.error || unavailable}</p>}
    {list.paused && <p>{en ? "Reading is paused while this page is hidden." : "页面不可见时暂停读取。"}</p>}
    <div className="knowledge-products-grid">{products.map(product => <article className="knowledge-product-card" key={product.product_key}><div><h4>{product.name}</h4><span>{en ? `CNY · Illustrative risk R${product.illustrative_risk_level}` : `人民币 · 说明性风险 R${product.illustrative_risk_level}`}</span></div><p className="knowledge-product-risk">{en ? "Capital and returns are not guaranteed." : "本金与收益不保证。"}</p><button type="button" className="button secondary" aria-expanded={knownKey === product.product_key} disabled={list.loading || controlled && !onSelectedProductKeyChange} onClick={() => choose(knownKey === product.product_key ? "" : product.product_key)}>{knownKey === product.product_key ? (en ? "Close details" : "收起详情") : (en ? "Read details" : "查看详情")}</button></article>)}</div>
    {knownKey && <section className="knowledge-product-detail" aria-label={en ? "Selected product details" : "所选产品详情"}>
      {detail.loading && !detail.data && <Skeleton lines={4} />}
      {(detail.error || detail.data && !validDetail) && <p className="login-error" role="alert">{detail.error || unavailable}</p>}
      {validDetail && <><h4>{(detail.data!.product as ProductLiterature).name}</h4><p>{(detail.data!.product as ProductLiterature).audience}</p><p className="knowledge-product-risk">{(detail.data!.product as ProductLiterature).risk_note}</p><dl>{[
        [en ? "Minimum and limits" : "额度与门槛", (detail.data!.product as ProductLiterature).minimum],
        [en ? "Fees" : "费用说明", (detail.data!.product as ProductLiterature).fees],
        [en ? "Redemption" : "退出限制", (detail.data!.product as ProductLiterature).exit_conditions],
        [en ? "Availability" : "办理核验", (detail.data!.product as ProductLiterature).availability],
      ].map(([title, text]) => <div key={title}><dt>{title}</dt><dd>{text}</dd></div>)}</dl>
      {terms ? <><h5>{en ? "Published terms" : "已发布条款"}</h5><p className="knowledge-product-terms">{terms.content}</p><p className="knowledge-product-source">{en ? "Source" : "来源"}：{terms.title} · {terms.version} · {terms.source_uri}</p><details><summary>{en ? "Version verification" : "版本核验"}</summary><p>{en ? "Effective" : "生效时间"}：{terms.effective_from}</p><p>{en ? "Expires" : "到期时间"}：{terms.expires_at || (en ? "Not specified" : "未指定")}</p><code>{terms.content_hash}</code></details></> : <p>{en ? "No currently verified published terms are available. Do not infer fees, guaranteed returns or transaction eligibility from this catalogue." : "当前没有可核验的已发布条款，不据此推断费用、保本收益或办理资格。"}</p>}
      <AccessibleReloadButton className="text-button" scopeKey={`${token}:${locale}:${knownKey}`} blocked={detail.loading} onReload={detail.refresh} label={en ? "Refresh product literature" : "刷新产品资料"}>{en ? "Refresh product literature" : "刷新产品资料"}</AccessibleReloadButton></>}
    </section>}
    {(list.error || list.data && !validList) && <AccessibleReloadButton className="text-button" scopeKey={`${token}:${locale}`} blocked={list.loading} onReload={list.refresh} label={en ? "Reload product literature" : "重新读取产品资料"}>{en ? "Reload product literature" : "重新读取产品资料"}</AccessibleReloadButton>}
  </section>;
}
