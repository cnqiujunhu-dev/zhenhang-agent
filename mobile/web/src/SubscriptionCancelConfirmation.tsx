import {useCustomerLanguage} from "./CustomerLanguageProvider";
import {money} from "./components";
import {subscriptionCancelConfirmation} from "./confirmationPresentation";
import type {JsonValue} from "./types";

/** Read-only presentation; the original scope still owns confirmation. */
export function SubscriptionCancelConfirmation({scope,result,summary}:{scope:JsonValue|undefined;result:JsonValue|undefined;summary:JsonValue|undefined}) {
  const {locale}=useCustomerLanguage(),en=locale==="en";
  const review=subscriptionCancelConfirmation("SUBSCRIPTION_CANCEL",scope,result,summary);
  if(!review)return null;
  return <section aria-label={en?"Automatic renewal cancellation review":"关闭自动续费核对"}>
    <dl className="structured-data structured-confirmation">
      <div><dt>{en?"Service":"服务名称"}</dt><dd>{review.merchant}</dd></div>
      <div><dt>{en?"Previous fee per billing cycle":"原每期费用"}</dt><dd>{money(review.originalFeeMinor,{currency:review.currency})} {en?"CNY":"人民币"}</dd></div>
    </dl>
    <p>{en?"No charge is made by this cancellation. Existing paid access remains until its original expiry; turning off renewal is not a refund.":"本次不扣款，已付权益保留至原到期；关闭续费不等于退款。"}</p>
  </section>;
}
