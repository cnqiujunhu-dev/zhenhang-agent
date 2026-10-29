type CatalogProduct = { product_id: string; merchant_name?: string; product_name?: string; category?: string;
  unit_price_minor?: number; currency?: string; stock?: number; status?: string };
type MerchantOrderProjection = { order_id: string; amount_minor?: number; currency?: string; status: string; merchant_name?: string; product_name?: string;
  payment?: { instrument_type?: string; cash_debit_minor?: number; credit_debit_minor?: number } };
const format = (amount?: number, currency?: string) => typeof amount === "number" && Number.isSafeInteger(amount)
  ? (currency === "USD" ? "US$" : currency === "CNY" ? "¥" : (currency || "币种暂未提供") + " ") + (amount / 100).toFixed(2) : "暂未提供";
const labels: Record<string, string> = { PAID: "已付款，尚未履约", FULFILLED: "商户已记录履约",
  REFUND_PENDING: "退款处理中，尚未到账", REFUNDED: "已原路退款",
  FULFILMENT_UNVERIFIED: "履约情况暂未确认", SOURCE_UNVERIFIED: "订单信息暂未确认" };
const categoryLabels: Record<string, string> = { CAKE: "蛋糕", FLOWER: "鲜花", RESTAURANT: "餐厅" };
type CatalogScope = { version?: string; merchant_categories?: unknown };
function selectedCategories(scope?: CatalogScope, products?: CatalogProduct[]): string[] | null | undefined {
  if (!scope || scope.version !== "merchant-catalog-category-set-v1" && scope.merchant_categories === undefined) return undefined;
  const values = scope.merchant_categories;
  if (scope.version !== "merchant-catalog-category-set-v1" || !Array.isArray(values) || values.length < 1 || values.length > 3
    || Object.keys(scope).some(key => !["version", "merchant_categories", "product_name", "merchant_product_id"].includes(key))
    || values.some(value => typeof value !== "string" || !Object.hasOwn(categoryLabels, value))
    || new Set(values).size !== values.length || JSON.stringify([...values].sort()) !== JSON.stringify(values)
    || products?.some(product => typeof product.category !== "string" || !values.includes(product.category))) return null;
  return values as string[];
}

export function MerchantCards({ result }: { result: { products?: CatalogProduct[]; orders?: MerchantOrderProjection[]; query_scope?: CatalogScope } }) {
  const categories = selectedCategories(result.query_scope, result.products);
  return <section aria-label="商户目录与订单" className="merchant-cards">
    {categories === null && <p>筛选范围暂未核对，请重新查询。</p>}
    {categories && <p className="merchant-catalog-scope">{categories.map(value => categoryLabels[value]).join("、")}</p>}
    {categories !== null && result.products?.map(product => <article key={product.product_id}><h3>{product.merchant_name || "商户"}</h3>
      <p>{product.product_name || "商品"} · {format(product.unit_price_minor, product.currency)}</p>
      <p>库存：{typeof product.stock === "number" ? product.stock : "暂未提供"} · {product.stock === 0 ? "暂时售罄" : product.status === "AVAILABLE" && typeof product.stock === "number" && product.stock > 0 ? "可选" : "暂不可选"}</p></article>)}
    {categories !== null && result.products && !result.products.length && <p>{categories ? "所选类别目前没有商品，可以稍后再看看。" : "暂时没有上架商品，可以稍后再看看。"}</p>}
    {result.orders?.map(order => <article key={order.order_id}><h3>{labels[order.status] || "订单状态暂未确定"}</h3>
      <p>{order.merchant_name || "商户"} · {order.product_name || "商品"}</p>
      <p>{format(order.amount_minor, order.currency)}</p>
      {order.payment?.instrument_type === "DEBIT" && <p>借记卡现金付款：{format(order.payment.cash_debit_minor, order.currency)}</p>}
      {order.payment?.instrument_type === "CREDIT" && <p>付款方式：信用卡</p>}
    </article>)}
    {result.orders && !result.orders.length && <p>目前没有本人的商户订单。</p>}
  </section>;
}
