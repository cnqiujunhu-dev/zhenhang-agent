import assert from "node:assert/strict";
import {test} from "node:test";
import {customerDto} from "./banking-confirmation-fixtures.mjs";
import {renderBankingAppDto,subscriptionCancelConfirmation} from "./banking-confirmation-app-ssr.mjs";
function fixture(source="MERCHANT_SIM"){
  const scope={subscription_id:"PRIVATE_SUB",merchant:"视频会员",amount_minor:2500,source_type:source,expected_version:1};
  const summary=`准备取消 视频会员 的${source==="BANK_MANDATE"?"银行代扣协议":"商户订阅"}（25.00 元/期），确认后执行。`;
  const result={message:summary,source_type:source,simulated:true};
  const dto=customerDto({action:"SUBSCRIPTION_CANCEL"});dto.pending_confirmation.scope=scope;dto.pending_confirmation.summary=summary;dto.nodes[0].result=result;
  return {scope,summary,result,dto};
}
for(const source of ["MERCHANT_SIM","BANK_MANDATE"])test(`${source} exact registered cancellation reviews the existing period fee, never a current payment`,()=>{
  const {scope,result,summary}=fixture(source),before=JSON.stringify({scope,result,summary});
  assert.deepEqual(subscriptionCancelConfirmation("SUBSCRIPTION_CANCEL",scope,result,summary),{merchant:"视频会员",originalFeeMinor:2500,currency:"CNY"});
  assert.equal(JSON.stringify({scope,result,summary}),before);
});
for(const [name,change] of [
  ["different action",r=>r.action="SUBSCRIPTION_RENEW"],["unknown source",r=>r.scope.source_type="EXTERNAL_UNCONNECTED"],
  ["source mismatch",r=>r.result.source_type="BANK_MANDATE"],["missing owned subscription",r=>delete r.scope.subscription_id],
  ["missing version",r=>delete r.scope.expected_version],["wrong version type",r=>r.scope.expected_version="1"],
  ["wrong currency",r=>r.scope.currency="USD"],["wrong fee",r=>r.scope.amount_minor=2600],
  ["bool fee",r=>r.scope.amount_minor=true],["summary different",r=>r.summary+="可能收取违约费。"],
  ["message different",r=>r.result.message="准备取消视频会员，金额25元"],["preview extra warning",r=>r.result.warning="需要先处理待付费用"],
  ["scope extra terms",r=>r.scope.terms="取消可能产生费用"],["executed payment",r=>r.result.payment_id="posted"],
  ["wrong tool",r=>r.scope.tool_name="subscription.pay.v1"],
])test(`${name} retains ordinary review without making new cancellation-policy claims`,()=>{
  const row={...fixture(),action:"SUBSCRIPTION_CANCEL"};change(row);
  assert.equal(subscriptionCancelConfirmation(row.action,row.scope,row.result,row.summary),null);
});
for(const locale of ["zh-CN","en"])test(`actual App keeps original fee, original confirmation controls and paid-access policy without duplicate generic meta ${locale}`,()=>{
  const {dto}=fixture(),before=JSON.stringify(dto),html=renderBankingAppDto(dto,{locale});
  assert.match(html,/视频会员/);assert.match(html,/25\.00/);
  assert.match(html,locale==="en"?/Previous fee per billing cycle/:/原每期费用/);
  assert.match(html,locale==="en"?/No charge is made by this cancellation/:/本次不扣款/);
  assert.match(html,locale==="en"?/Existing paid access remains until its original expiry/:/已付权益保留至原到期/);
  assert.match(html,locale==="en"?/turning off renewal is not a refund/:/关闭续费不等于退款/);
  assert.doesNotMatch(html,/PRIVATE_SUB|准备取消 视频会员|<dt>金额<\/dt>|confirmation-meta|仅执行本次确认覆盖的步骤|作用于当前账户|确认有效至|完成验证后确认/);
  assert.match(html,locale==="en"?/Get verification code/:/获取验证码/);assert.match(html,/<button[^>]*class="button primary"[^>]*disabled/);
  assert.doesNotMatch(html,/已退款|已取消|refund completed|cancellation completed/);assert.equal(JSON.stringify(dto),before);
});
test("an extra actual cancellation condition cannot be silently replaced with the concise standard policy",()=>{
  const {dto}=fixture();dto.pending_confirmation.summary+="本次有一笔待付费用需要核实。";
  const html=renderBankingAppDto(dto);assert.match(html,/本次有一笔待付费用需要核实/);
  assert.match(html,/confirmation-meta/);assert.doesNotMatch(html,/原每期费用|本次不扣款，已付权益保留/);
});
