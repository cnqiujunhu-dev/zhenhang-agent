// customer-copy-literals-v1
import { useCustomerLanguage } from "./CustomerLanguageProvider";
import { extensionPanelUiText, extensionScenarioPresentation, extensionNamedControl, extensionProposalLabel } from "./extensionPanelLanguage";
import { useState } from "react";
import { request } from "./api";
import {
  dateText,
  Icon,
  money,
  Skeleton,
  StateBadge,
  type IconName,
} from "./components";
import { useEndpoint } from "./useEndpoint";
import type { DemoSession } from "./types";
import { customerName, customerNarrative } from "./customerPresentation";
import { FamilyBoundContexts } from "./FamilyBoundContexts";

function useExtensionLanguage() {
  const { locale } = useCustomerLanguage();
  return { locale, t: (text: string) => extensionPanelUiText(text, locale) };
}

function CustomerText({ text }: { text: string }) {
  const { t } = useExtensionLanguage();
  return t(text);
}

export const EXTENSION_SCENARIOS: {
  id: string;
  label: string;
  icon: IconName;
  prompt: string;
  description: string;
  path: string;
}[] = [
  {
    id: "fraud",
    label: "反诈守护",
    icon: "shield",
    prompt: "检查给房东转账的风险",
    description: "核对风险线索，明确行动边界",
    path: "/api/v2/extensions/fraud",
  },
  {
    id: "family",
    label: "家庭适老",
    icon: "family",
    prompt: "查看家庭协助关系和有效委托",
    description: "协助与本人授权分开，办理过程可追溯",
    path: "/api/v2/extensions/family",
  },
  {
    id: "goals",
    label: "目标现金流",
    icon: "target",
    prompt: "查看我的储蓄目标和可用现金流",
    description: "在生活支出与目标预留之间安排资金",
    path: "/api/v2/extensions/goals",
  },
  {
    id: "carbon",
    label: "低碳权益",
    icon: "leaf",
    prompt: "查看个人低碳行为和可领取权益",
    description: "基于可核对的低碳行为记录积累个人权益",
    path: "/api/v2/extensions/carbon",
  },
];
type ActorSession = {
  token: string;
  user_id: string;
  display_name: string;
  role: string;
  expires_at: string;
};
type PanelProps = {
  extensionId: string;
  token: string;
  revision: number;
  onPrompt: (message: string) => void;
  onTaskOpen: (id: string) => void;
  onActorSession: (session: ActorSession) => void;
  onOwnerRestore: () => void;
  currentSession: DemoSession;
  ownerSession: DemoSession | null;
  availableHelperIds: string[];
  onHelperRestore: (userId: string) => void;
};
type FamilyData = {
  current_actor: { user_id: string; display_name: string; role: string };
  actors: { user_id: string; display_name: string; role: string }[];
  delegations: {
    delegation_id: string;
    owner_user_id: string;
    owner_name: string;
    grantee_user_id: string;
    grantee_name: string;
    scopes: string[];
    maximum_amount_minor: number;
    expires_at: string;
    status: string;
    version: number;
    valid: boolean;
    limit_period?: string;
    month_used_minor?: number | null;
  }[];
  permissions: {
    can_grant: boolean;
    can_propose_transfer: boolean;
    can_propose_card_lock: boolean;
    can_read_owner_balance: boolean;
  };
  proposals: {
    proposal_id: string;
    owner_name: string;
    grantee_name: string;
    proposal_kind: string;
    payee_name?: string;
    amount_minor?: number;
    card_last4?: string;
    status: string;
    version: number;
    owner_task_id?: string;
    owner_task_state?: string | null;
    redacted_request_id?: string;
  }[];
  authorized_balances?: { owner_name: string; balance_minor: number }[];
};
type FraudData = {
  risk: {
    risk_level: string;
    decision: string;
    reason_codes: string[];
    confirmation_can_override: boolean;
    rules_version?: string;
    sources: { kind: string; version: number }[];
    explanation?: string;
  };
  contacts: { payee_name: string; account_last4: string; status: string }[];
};
type GoalsData = {
  goals: {
    goal_id: string;
    goal_name: string;
    target_minor: number;
    due_date: string | null;
    reserved_minor: number;
    remaining_minor: number;
    progress: number;
    version: number;
    status: string;
    monthly_suggested_minor: number | null;
    reservation_balanced: boolean;
    feasibility?: string;
    monthly_gap_minor?: number | null;
    current_cash_gap_minor?: number;
    options?: {
      kind: string;
      read_only: boolean;
      message: string;
      amount_minor?: number;
    }[];
  }[];
  forecast: {
    data_months: number;
    monthly_income_minor: number;
    monthly_spending_minor: number;
    monthly_refund_minor: number;
    estimated_surplus_minor: number;
    source: string;
    projection_nonbinding: boolean;
    forecast_version?: string;
    horizon_days?: number;
    sample_status?: string;
    history_window?: {
      start: string;
      end: string;
      covered_days: number;
      transaction_count: number;
    };
    scenarios?: Record<
      string,
      {
        income_90d_minor: number;
        expense_90d_minor: number;
        net_90d_minor: number;
        monthly_net_minor: number;
        unreserved_cash_end_90d_minor: number;
        emergency_gap_90d_minor: number;
      }
    >;
    assumptions?: string[];
    refund_as_future_income_minor?: number;
  };
  account_balance_minor: number;
  goal_reserved_minor: number;
  available_cash_minor: number;
  emergency_reserved_minor: number;
  spendable_after_emergency_minor: number;
  reservation_ledger_net_minor: number;
  explanation: string;
  portfolio?: {
    required_monthly_minor: number;
    monthly_capacity_baseline_minor: number;
    monthly_gap_minor: number;
    current_cash_gap_minor: number;
  };
  budget_reward_feedback?: {
    redemption_id: string;
    reward_name: string;
    counted_as_income: boolean;
    cash_value_minor: number;
    realized_saving_minor: number;
    message: string;
  }[];
};
type CarbonData = {
  methodology: {
    methodology_version: string;
    grams_per_point: number;
    avoided_grams_per_km: Record<string, number>;
    factor_basis: string;
    certified_carbon_asset: boolean;
  };
  events: {
    behavior_id: string;
    behavior_name: string;
    mode: string;
    distance_m: number;
    source: string;
    status: string;
    occurred_at: string;
    estimated_avoided_grams: number;
    estimated_points: number;
    claimed: boolean;
    claimable: boolean;
  }[];
  wallet: { points: number; version: number | null };
  rewards: {
    reward_id: string;
    reward_name: string;
    cost_points: number;
    stock: number;
    can_redeem: boolean;
  }[];
  redemptions: {
    redemption_id: string;
    reward_name: string;
    cost_points: number;
    status: string;
  }[];
  point_ledger_net: number;
  explanation: string;
};

function FamilyPanel({
  data,
  props,
  onRefresh,
}: {
  data: FamilyData;
  props: PanelProps;
  onRefresh: () => void;
}) {
  const { locale, t: customerUiText } = useExtensionLanguage();
  const [helperName, setHelperName] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [grantForms, setGrantForms] = useState<
    Record<
      string,
      { scopes: string[]; period: string; amount: string; days: string }
    >
  >({});
  const scopeChoices = [
    { value: "READ_BALANCE", label: "查看余额" },
    { value: "PROPOSE_TRANSFER", label: "提出转账提案" },
    { value: "PROPOSE_CARD_LOCK", label: "提出锁卡提案" },
  ];
  async function createHelper() {
    if (!helperName.trim()) return;
    setBusy("helper");
    setError("");
    try {
      const helper = await request<{
        helper_user_id: string;
        display_name: string;
        role: string;
        token: string;
        expires_at: string;
      }>("/api/v2/extensions/family/helpers", props.token, "POST", {
        display_name: helperName.trim(),
      });
      props.onActorSession({
        token: helper.token,
        user_id: helper.helper_user_id,
        display_name: helper.display_name,
        role: helper.role,
        expires_at: helper.expires_at,
      });
      setHelperName("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "协助身份未能创建");
    } finally {
      setBusy("");
    }
  }
  async function ownerReview(
    proposal: FamilyData["proposals"][number],
    decision: "APPROVE" | "REJECT",
  ) {
    setBusy(proposal.proposal_id);
    setError("");
    try {
      const result = await request<{
        owner_task_id?: string;
        task_id?: string;
      }>(
        `/api/v2/extensions/family/proposals/${encodeURIComponent(proposal.proposal_id)}/owner-review`,
        props.token,
        "POST",
        { decision, expected_proposal_version: proposal.version },
      );
      onRefresh();
      if (decision === "APPROVE" && (result.owner_task_id || result.task_id))
        props.onTaskOpen((result.owner_task_id || result.task_id)!);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "提案审阅暂未完成");
    } finally {
      setBusy("");
    }
  }
  const scopeLabels: Record<string, string> = {
    READ_BALANCE: "查看余额",
    BALANCE_READ: "查看余额",
    TRANSFER_PROPOSE: "提出转账方案",
    PROPOSE_TRANSFER: "提出转账方案",
    CARD_LOCK_PROPOSE: "提出锁卡方案",
    PROPOSE_CARD_LOCK: "提出锁卡方案",
  };
  const isHelper = data.current_actor.role === "FAMILY_HELPER";
  const transferOwner = data.delegations.find(
    (row) =>
      row.valid &&
      row.grantee_user_id === data.current_actor.user_id &&
      row.scopes.includes("PROPOSE_TRANSFER"),
  );
  const cardOwner = data.delegations.find(
    (row) =>
      row.valid &&
      row.grantee_user_id === data.current_actor.user_id &&
      row.scopes.includes("PROPOSE_CARD_LOCK"),
  );
  return (
    <div className="extension-data family-data">
      {!isHelper && ["CUSTOMER", "DEV_TEST"].includes(props.currentSession.actor_role || "") && (
        <FamilyBoundContexts token={props.token} revision={props.revision}
          currentSession={props.currentSession} onActorSession={props.onActorSession} />
      )}
      <div className="actor-context">
        <span className="actor-avatar">
          <Icon name="family" size={21} />
        </span>
        <div>
          <strong>{data.current_actor.display_name}</strong>
          <small>{isHelper ? customerUiText("当前是家庭协助身份") : customerUiText("当前是本人身份")}</small>
        </div>
        {isHelper && props.ownerSession && (
          <button className="text-button" onClick={props.onOwnerRestore}><CustomerText text="返回本人" /></button>
        )}
      </div>
      <p className="extension-explanation"><CustomerText text="协助者只在有效委托范围内查看或提出方案。转账与卡片动作仍由本人独立确认。" /></p>
      {data.authorized_balances?.length ? (
        <div className="authorized-balances">
          {data.authorized_balances.map((row) => (
            <div key={row.owner_name}>
              <span>{row.owner_name}<CustomerText text="的已授权余额" /></span>
              <strong>{money(row.balance_minor)}</strong>
            </div>
          ))}
        </div>
      ) : null}
      {error && (
        <p className="error-inline" role="alert">
          {error}
        </p>
      )}
      {data.permissions.can_grant && (
        <details className="extension-form">
          <summary><CustomerText text="创建家庭协助身份" /></summary>
          <p><CustomerText text="创建后切换到该独立身份；未获得委托时没有协助权限。" /></p>
          <div>
            <input
              aria-label={customerUiText("家庭协助者姓名")}
              value={helperName}
              onChange={(event) => setHelperName(event.target.value)}
              placeholder={customerUiText("输入家庭协助者姓名")}
              maxLength={40}
            />
            <button
              className="button secondary compact"
              disabled={Boolean(busy) || !helperName.trim()}
              onClick={createHelper}
            ><CustomerText text="创建并切换" /></button>
          </div>
        </details>
      )}
      <h3><CustomerText text="协助身份" /></h3>
      <div className="family-actors">
        {data.actors.map((actor) => (
          <div key={actor.user_id}>
            <span>{actor.display_name}</span>
            <small>{actor.role === "FAMILY_HELPER" ? customerUiText("协助者") : customerUiText("本人")}</small>
            {data.permissions.can_grant && actor.role === "FAMILY_HELPER" && (
              <div className="family-grant-actions">
                {props.availableHelperIds.includes(actor.user_id) && (
                  <button
                    className="text-button"
                    onClick={() => props.onHelperRestore(actor.user_id)}
                  ><CustomerText text="切换协助身份" /></button>
                )}
                <button
                  className="text-button"
                  onClick={() =>
                    props.onPrompt(
                      `授权${actor.display_name}协助查看余额，有效期30天`,
                    )
                  }
                ><CustomerText text="准备查看余额委托" /></button>
                <button
                  className="text-button"
                  onClick={() =>
                    props.onPrompt(
                      `授权${actor.display_name}协助提出转账提案，单笔上限500元，有效期30天`,
                    )
                  }
                ><CustomerText text="准备转账提案委托" /></button>
                <details className="family-grant-form">
                  <summary><CustomerText text="自定义授权范围与额度" /></summary>
                  {(() => {
                    const form = grantForms[actor.user_id] || {
                      scopes: [],
                      period: "TRANSACTION",
                      amount: "",
                      days: "30",
                    };
                    const update = (partial: Partial<typeof form>) =>
                      setGrantForms((current) => ({
                        ...current,
                        [actor.user_id]: { ...form, ...partial },
                      }));
                    const hasTransfer =
                      form.scopes.includes("PROPOSE_TRANSFER");
                    const amountValid =
                      /^(?:0|[1-9]\d*)(?:\.\d{1,2})?$/.test(form.amount) &&
                      Number(form.amount) > 0;
                    const daysValid =
                      /^\d+$/.test(form.days) &&
                      Number(form.days) >= 1 &&
                      Number(form.days) <= 180;
                    return (
                      <>
                        <div className="grant-scope-options">
                          {scopeChoices.map((choice) => (
                            <label key={choice.value}>
                              <input
                                type="checkbox"
                                checked={form.scopes.includes(choice.value)}
                                onChange={(event) =>
                                  update({
                                    scopes: event.target.checked
                                      ? [...form.scopes, choice.value]
                                      : form.scopes.filter(
                                          (scope) => scope !== choice.value,
                                        ),
                                  })
                                }
                              />
                              <span>{customerUiText(choice.label)}</span>
                            </label>
                          ))}
                        </div>
                        {hasTransfer && (
                          <div className="grant-limit-inputs">
                            <label><CustomerText text="金额边界" /><select
                                aria-label={extensionNamedControl(actor.display_name, "委托额度周期", locale)}
                                value={form.period}
                                onChange={(event) =>
                                  update({ period: event.target.value })
                                }
                              >
                                <option value="TRANSACTION"><CustomerText text="单笔上限" /></option>
                                <option value="MONTH"><CustomerText text="每月累计上限" /></option>
                              </select>
                            </label>
                            <label><CustomerText text="额度（元）" /><input
                                aria-label={extensionNamedControl(actor.display_name, "委托额度", locale)}
                                inputMode="decimal"
                                value={form.amount}
                                onChange={(event) =>
                                  update({ amount: event.target.value })
                                }
                                placeholder={customerUiText("请输入明确额度")}
                              />
                            </label>
                          </div>
                        )}
                        <label className="grant-days"><CustomerText text="有效期（1 至 180 天）" /><input
                            aria-label={extensionNamedControl(actor.display_name, "委托有效天数", locale)}
                            inputMode="numeric"
                            value={form.days}
                            onChange={(event) =>
                              update({ days: event.target.value })
                            }
                          />
                        </label>
                        <button
                          className="button secondary compact"
                          disabled={
                            !form.scopes.length ||
                            !daysValid ||
                            (hasTransfer && !amountValid)
                          }
                          onClick={() =>
                            props.onPrompt(
                              `授权${actor.display_name}协助${scopeChoices
                                .filter((choice) =>
                                  form.scopes.includes(choice.value),
                                )
                                .map((choice) => choice.label)
                                .join(
                                  "、",
                                )}有效期${form.days}天${hasTransfer ? (form.period === "MONTH" ? `每月累计不超过${form.amount}元` : `单笔不超过${form.amount}元`) : ""}`,
                            )
                          }
                        ><CustomerText text="生成本人授权计划" /></button>
                      </>
                    );
                  })()}
                </details>
              </div>
            )}
          </div>
        ))}
      </div>
      <h3><CustomerText text="有效范围与委托" /></h3>
      {data.delegations.length ? (
        data.delegations.map((delegation) => (
          <div className="delegation-card" key={delegation.delegation_id}>
            <div>
              <strong>
                {delegation.owner_name} → {delegation.grantee_name}
              </strong>
              <StateBadge
                state={delegation.valid ? delegation.status : "EXPIRED"}
              />
            </div>
            <div className="scope-chips">
              {delegation.scopes.map((scope) => (
                <span key={scope}>{scopeLabels[scope] ? customerUiText(scopeLabels[scope]) : scope}</span>
              ))}
            </div>
            <p>
              {delegation.limit_period === "MONTH" ? customerUiText("月度额度") : customerUiText("单笔额度")}{" "}
              {money(delegation.maximum_amount_minor)}
              {typeof delegation.month_used_minor === "number"
                ? ` · ${customerUiText("本月已占用")} ${money(delegation.month_used_minor)}`
                : ""}{" "}<CustomerText text="· 有效至" />{dateText(delegation.expires_at, true)}
            </p>
            {data.permissions.can_grant && delegation.valid && (
              <button
                className="text-button"
                onClick={() =>
                  props.onPrompt(`撤销${delegation.grantee_name}的家庭委托`)
                }
              ><CustomerText text="准备撤销委托" /></button>
            )}
          </div>
        ))
      ) : (
        <p className="extension-empty"><CustomerText text="当前没有家庭委托。" /></p>
      )}
      <div className="family-capabilities">
        {data.permissions.can_propose_transfer && transferOwner && (
          <button
            className="button secondary compact"
            onClick={() =>
              props.onPrompt(
                `为${transferOwner.owner_name}提交转账协助提案，给房东转100元`,
              )
            }
          ><CustomerText text="准备转账协助提案" /></button>
        )}
        {data.permissions.can_propose_card_lock && cardOwner && (
          <button
            className="button secondary compact"
            onClick={() =>
              props.onPrompt(
                `为${cardOwner.owner_name}提交锁卡协助提案，尾号4321`,
              )
            }
          ><CustomerText text="准备卡片保护提案" /></button>
        )}
      </div>
      <h3><CustomerText text="本人待审提案" /></h3>
      {data.proposals.length ? (
        data.proposals.map((proposal) => (
          <div className="family-proposal" key={proposal.proposal_id}>
            <div>
              <strong>{proposal.grantee_name}<CustomerText text="的协助提案" /></strong>
              <StateBadge
                state={proposal.owner_task_state || proposal.status}
              />
            </div>
            <p>
              {extensionProposalLabel(proposal.proposal_kind, locale)}
              {proposal.payee_name ? ` · ${proposal.payee_name}` : ""}
              {typeof proposal.amount_minor === "number"
                ? ` · ${money(proposal.amount_minor)}`
                : ""}
              {proposal.card_last4 ? ` · ${customerUiText("卡片尾号")} ${proposal.card_last4}` : ""}
            </p>
            {proposal.owner_task_id ? (
              <button
                className="text-button"
                onClick={() => props.onTaskOpen(proposal.owner_task_id!)}
              ><CustomerText text="打开本人办理任务" /><Icon name="arrow" size={13} />
              </button>
            ) : !isHelper &&
              [
                "PENDING",
                "PENDING_OWNER_REVIEW",
                "PENDING_OWNER_APPROVAL",
                "PROPOSED",
              ].includes(proposal.status) ? (
              <div>
                <button
                  className="button secondary compact"
                  disabled={Boolean(busy)}
                  onClick={() => ownerReview(proposal, "APPROVE")}
                ><CustomerText text="生成本人确认任务" /></button>
                <button
                  className="text-button"
                  disabled={Boolean(busy)}
                  onClick={() => ownerReview(proposal, "REJECT")}
                ><CustomerText text="拒绝提案" /></button>
              </div>
            ) : (
              <small>
                {isHelper
                  ? customerUiText("本人审阅后仍需独立确认；此身份不能打开本人业务参数。")
                  : customerUiText("提案已处理。")}
              </small>
            )}
          </div>
        ))
      ) : (
        <p className="extension-empty"><CustomerText text="当前没有待审协助提案。" /></p>
      )}
    </div>
  );
}

function FraudPanel({
  data,
  appliedPayee,
  appliedMessage,
  onAnalyze,
  onPrompt,
}: {
  data: FraudData;
  appliedPayee: string;
  appliedMessage: string;
  onAnalyze: (payeeName: string, message: string) => void;
  onPrompt: (message: string) => void;
}) {
  const { locale, t: customerUiText } = useExtensionLanguage();
  const [payee, setPayee] = useState(appliedPayee);
  const [message, setMessage] = useState(appliedMessage);
  const reasons: Record<string, string> = {
    SAFETY_ACCOUNT_INDUCEMENT: "出现转入所谓安全账户的诱导",
    ADVANCE_FEE_INDUCEMENT: "出现先付款换取返利或奖励的诱导",
    AUTHORITY_IMPERSONATION: "出现冒充公检法要求汇款的诱导",
    SECRECY_INDUCEMENT: "出现隐瞒家人或银行的转账要求",
    REMOTE_ACCESS_INDUCEMENT: "远程控制或屏幕共享涉及账户、密码或支付",
    REFUND_ADVANCE_FEE_INDUCEMENT: "要求先付费或缴保证金才能退款",
    CREDENTIAL_COLLECTION_INDUCEMENT: "有人索要验证码或支付密码",
    RECIPIENT_CONTEXT_NOT_RESOLVED: "收款人信息尚未唯一核验",
    KNOWN_SYNTHETIC_CONTACT_NO_RISK_FLAG: "当前已知联系人未触发风险标记",
  };
  const decision = data.risk.decision;
  const contactNames = [
    ...new Set(data.contacts.map((contact) => contact.payee_name)),
  ];
  return (
    <div className="extension-data fraud-data">
      <div
        className={`fraud-risk-card ${decision === "BLOCK" ? "blocked" : decision === "ALLOW" ? "allowed" : "unresolved"}`}
      >
        <Icon
          name={
            decision === "BLOCK"
              ? "shield"
              : decision === "ALLOW"
                ? "check"
                : "clock"
          }
          size={27}
        />
        <div>
          <small>
            {appliedPayee ? `${customerUiText("当前核验")}${locale === "en" ? ": " : "："}${appliedPayee}` : customerUiText("尚未指定唯一收款人")}
          </small>
          <strong>
            {decision === "BLOCK"
              ? customerUiText("高风险 · 已拦截")
              : decision === "ALLOW"
                ? customerUiText("当前证据未触发拦截规则")
                : customerUiText("风险未核清 · 等待核验")}
          </strong>
        </div>
      </div>
      {decision === "BLOCK" && <p className="extension-explanation">{locale === "en" ? "Stop contact and transfers. Do not share your screen or verification codes. Check independently through the bank or platform's official channels. If money has been lost, keep the evidence and contact your bank and the police." : "先停止联系与转账，不要屏幕共享或提供验证码。自行通过银行或平台官方渠道核实；若资金已受损，保留证据并联系银行与警方。"}</p>}
      <p className="extension-explanation">
        {data.risk.explanation ||
          customerUiText("风险结论基于当前返回的证据；没有核实的收款信息会单列等待核验。")}
      </p>
      <form
        className="fraud-check-form"
        onSubmit={(event) => {
          event.preventDefault();
          onAnalyze(payee, message);
        }}
      >
        <label><CustomerText text="收款联系人" /><select
            aria-label={customerUiText("反诈核验收款人")}
            value={payee}
            onChange={(event) => setPayee(event.target.value)}
          >
            <option value=""><CustomerText text="请选择当前账套中的联系人" /></option>
            {contactNames.map((name) => {
              const contacts = data.contacts.filter(
                (contact) => contact.payee_name === name,
              );
              return (
                <option key={name} value={name}>
                  {name}
                  {contacts.length === 1
                    ? ` · ${customerUiText("尾号")}${locale === "en" ? " " : ""}${contacts[0].account_last4}`
                    : customerUiText(" · 存在同名待核验")}
                </option>
              );
            })}
          </select>
        </label>
        <label><CustomerText text="对方要求或转账背景" /><textarea
            aria-label={customerUiText("反诈核验转账背景")}
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            placeholder={customerUiText("如有异常要求，可描述收到的实际话术")}
            rows={2}
            maxLength={1000}
          />
        </label>
        <button className="button secondary compact" type="submit">
          <Icon name="shield" size={15} /><CustomerText text="重新核验风险" /></button>
      </form>
      <h3><CustomerText text="本次规则线索" /></h3>
      <ul className="fraud-reasons">
        {data.risk.reason_codes.map((reason) => (
          <li key={reason}>
            <Icon name={decision === "ALLOW" ? "check" : "info"} size={14} />
            <span>{reasons[reason] ? customerUiText(reasons[reason]) : reason}</span>
          </li>
        ))}
      </ul>
      <div className="fraud-boundary">
        <Icon name="lock" size={16} />
        <p>
          {data.risk.confirmation_can_override === false
            ? customerUiText("风险拦截不能被普通执行确认覆盖。")
            : customerUiText("后续动作仍须经过业务策略核验。")}<CustomerText text="核验报告不自动执行转账或锁卡。" /></p>
      </div>
      <div className="extension-provenance">
        <span>{data.risk.rules_version || customerUiText("未返回规则版本")}</span>
        <span>
          {data.risk.sources.length
            ? `${data.risk.sources.length} ${customerUiText("项风险源记录")}`
            : customerUiText("尚无可展示的收款人风险源记录")}
        </span>
      </div>
      {appliedPayee && (
        <button
          className="full-width-link"
          onClick={() =>
            onPrompt(
              `检查给${appliedPayee}转账的风险${appliedMessage ? `，转账背景：${appliedMessage}` : ""}`,
            )
          }
        ><CustomerText text="让 Agent 对本次背景进行风险分析" /><Icon name="arrow" size={14} />
        </button>
      )}
    </div>
  );
}

function GoalsPanel({
  data,
  onPrompt,
}: {
  data: GoalsData;
  onPrompt: (message: string) => void;
}) {
  const { locale, t: customerUiText } = useExtensionLanguage();
  const [name, setName] = useState("");
  const [target, setTarget] = useState("");
  const [deadline, setDeadline] = useState("");
  const [amounts, setAmounts] = useState<Record<string, string>>({});
  const feasibilityLabels: Record<string, string> = {
    FUNDED_NOT_SPENT: "已预留到位，尚未支出",
    OVERDUE_REVIEW_REQUIRED: "已到期，需要重新评估",
    FEASIBLE_FROM_CURRENT_CASH: "当前闲置现金可覆盖",
    SAMPLE_INSUFFICIENT: "样本不足，需核对收入假设",
    DUE_DATE_REQUIRED: "需要明确目标期限",
    FEASIBLE_BASELINE: "按基准假设可安排",
    MONTHLY_GAP: "存在月度筹资缺口",
  };
  const validAmount = (value: string) =>
    /^(?:0|[1-9]\d*)(?:\.\d{1,2})?$/.test(value) && Number(value) > 0;
  return (
    <div className="extension-data goals-data">
      <div className="goal-cash-card">
        <span><CustomerText text="扣除目标预留与应急金后的额度" /></span>
        <strong>{money(data.spendable_after_emergency_minor)}</strong>
        <div>
          <span><CustomerText text="账户现金" /><strong>{money(data.account_balance_minor)}</strong>
          </span>
          <span><CustomerText text="目标预留" /><strong>{money(data.goal_reserved_minor)}</strong>
          </span>
        </div>
        <div className="goal-cash-track">
          <span
            style={{
              width: `${data.account_balance_minor > 0 ? Math.min(100, (data.goal_reserved_minor / data.account_balance_minor) * 100) : 0}%`,
            }}
          />
        </div>
        <small><CustomerText text="目标预留占用支出额度，现金仍保留在账户。" /></small>
      </div>
      <p className="extension-explanation">{data.explanation}</p>
      <details className="extension-form">
        <summary><CustomerText text="准备建立新目标" /></summary>
        <div className="goal-create-form">
          <label><CustomerText text="目标名称" /><input
              aria-label={customerUiText("储蓄目标名称")}
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder={customerUiText("输入自己的目标名称")}
              maxLength={30}
            />
          </label>
          <label><CustomerText text="目标金额（元）" /><input
              aria-label={customerUiText("储蓄目标金额")}
              inputMode="decimal"
              value={target}
              onChange={(event) => setTarget(event.target.value)}
              placeholder={customerUiText("请输入金额")}
            />
          </label>
          <label><CustomerText text="计划截止日期（可选）" /><input
              aria-label={customerUiText("储蓄目标截止日期")}
              type="date"
              value={deadline}
              onChange={(event) => setDeadline(event.target.value)}
            />
          </label>
          <button
            className="button secondary compact"
            disabled={!name.trim() || !validAmount(target)}
            onClick={() =>
              onPrompt(
                `创建${name.trim()}目标${target}元${deadline ? `，截止${deadline}` : ""}`,
              )
            }
          ><CustomerText text="生成目标计划" /><Icon name="arrow" size={14} />
          </button>
        </div>
      </details>
      <h3><CustomerText text="当前目标" /></h3>
      {data.goals.length ? (
        data.goals.map((goal) => (
          <div className="cashflow-goal" key={goal.goal_id}>
            <div className="cashflow-goal-heading">
              <strong>{goal.goal_name}</strong>
              <StateBadge state={goal.status} />
            </div>
            <p>
              <strong>{money(goal.reserved_minor)}</strong>
              <span> / {money(goal.target_minor)}</span>
              <small>{(goal.progress * 100).toFixed(0)}%</small>
            </p>
            <div className="progress-track">
              <span
                style={{
                  width: `${Math.min(100, Math.max(0, goal.progress * 100))}%`,
                }}
              />
            </div>
            <div className="goal-details">
              <span><CustomerText text="仍需预留" /><strong>{money(goal.remaining_minor)}</strong>
              </span>
              {typeof goal.monthly_suggested_minor === "number" && (
                <span><CustomerText text="按期限月均目标" /><strong>{money(goal.monthly_suggested_minor)}</strong>
                </span>
              )}
            </div>
            <small className="goal-due">
              {goal.due_date
                ? `${customerUiText("计划截至")} ${dateText(goal.due_date, true)}`
                : customerUiText("未设置截止日期")}{" "}
              ·{" "}
              {goal.reservation_balanced
                ? customerUiText("预留账本核对一致")
                : customerUiText("预留账本存在差异")}
            </small>
            {goal.feasibility && (
              <p className="goal-feasibility">
                {feasibilityLabels[goal.feasibility] ? customerUiText(feasibilityLabels[goal.feasibility]) : goal.feasibility}
                {typeof goal.monthly_gap_minor === "number" &&
                goal.monthly_gap_minor > 0
                  ? ` · ${customerUiText("月度缺口")} ${money(goal.monthly_gap_minor)}`
                  : ""}
              </p>
            )}
            {goal.options && (
              <details className="goal-options">
                <summary><CustomerText text="可自愿调整的方案" /></summary>
                {goal.options.map((option) => (
                  <p key={option.kind}>{option.message}</p>
                ))}
              </details>
            )}
            {goal.status === "ACTIVE" && (
              <div className="goal-reserve-form">
                <input
                  aria-label={extensionNamedControl(goal.goal_name, "调整预留金额", locale)}
                  inputMode="decimal"
                  placeholder={customerUiText("调整金额（元）")}
                  value={amounts[goal.goal_id] || ""}
                  onChange={(event) =>
                    setAmounts((current) => ({
                      ...current,
                      [goal.goal_id]: event.target.value,
                    }))
                  }
                />
                <button
                  className="button secondary compact"
                  disabled={!validAmount(amounts[goal.goal_id] || "")}
                  onClick={() =>
                    onPrompt(
                      `为${goal.goal_name}目标预留${amounts[goal.goal_id]}元`,
                    )
                  }
                ><CustomerText text="准备预留" /></button>
                <button
                  className="text-button"
                  disabled={
                    !validAmount(amounts[goal.goal_id] || "") ||
                    goal.reserved_minor <= 0
                  }
                  onClick={() =>
                    onPrompt(
                      `释放${goal.goal_name}目标预留${amounts[goal.goal_id]}元`,
                    )
                  }
                ><CustomerText text="准备释放" /></button>
              </div>
            )}
          </div>
        ))
      ) : (
        <p className="extension-empty"><CustomerText text="当前没有已创建的目标，可先填写目标计划。" /></p>
      )}
      <h3><CustomerText text="现金流与情景参考" /></h3>
      <div className="goal-forecast">
        <span>
          {data.forecast.forecast_version
            ? customerUiText("90天基准情景月均参考")
            : locale === "en" ? `Average over ${data.forecast.data_months} months` : `近 ${data.forecast.data_months} 个月平均`}
        </span>
        <dl>
          <div>
            <dt><CustomerText text="月均收入" /></dt>
            <dd>{money(data.forecast.monthly_income_minor)}</dd>
          </div>
          <div>
            <dt><CustomerText text="月均消费支出" /></dt>
            <dd>{money(data.forecast.monthly_spending_minor)}</dd>
          </div>
          <div>
            <dt><CustomerText text="已观测退款参考" /></dt>
            <dd>{money(data.forecast.monthly_refund_minor)}</dd>
          </div>
          <div>
            <dt><CustomerText text="估算净结余" /></dt>
            <dd>
              {money(data.forecast.estimated_surplus_minor, { signed: true })}
            </dd>
          </div>
        </dl>
        <p><CustomerText text="历史与情景估算不保证未来收入。应急金" />{" "}
          {money(data.emergency_reserved_minor)}<CustomerText text="单独保护。" /></p>
      </div>
      {data.forecast.scenarios && (
        <details className="cashflow-scenarios">
          <summary><CustomerText text="展开" />{data.forecast.horizon_days || 90}<CustomerText text="天情景推演" /></summary>
          <p>
            {data.forecast.sample_status === "INSUFFICIENT_HISTORY"
              ? customerUiText("历史样本不足，收入假设需要本人核实。")
              : customerUiText("情景依据历史和明确压力假设，不是统计置信区间或收入保证。")}
          </p>
          <div className="scenario-comparison">
            {Object.entries(data.forecast.scenarios).map(([kind, values]) => (
              <div key={kind}>
                <strong>
                  {kind === "conservative"
                    ? customerUiText("保守")
                    : kind === "baseline"
                      ? customerUiText("基准")
                      : kind === "less_conservative"
                        ? customerUiText("较宽松")
                        : kind}
                </strong>
                <dl>
                  <div>
                    <dt><CustomerText text="预计收入" /></dt>
                    <dd>{money(values.income_90d_minor)}</dd>
                  </div>
                  <div>
                    <dt><CustomerText text="预计支出" /></dt>
                    <dd>{money(values.expense_90d_minor)}</dd>
                  </div>
                  <div>
                    <dt><CustomerText text="情景净额" /></dt>
                    <dd>{money(values.net_90d_minor, { signed: true })}</dd>
                  </div>
                  <div>
                    <dt><CustomerText text="应急金缺口" /></dt>
                    <dd>{money(values.emergency_gap_90d_minor)}</dd>
                  </div>
                </dl>
              </div>
            ))}
          </div>
          {data.forecast.refund_as_future_income_minor !== undefined && (
            <p><CustomerText text="未来情景计入退款：" />{money(data.forecast.refund_as_future_income_minor)}
            </p>
          )}
          <ul>
            {(data.forecast.assumptions || []).map((assumption) => (
              <li key={assumption}>{assumption}</li>
            ))}
          </ul>
        </details>
      )}
      {data.portfolio && (
        <div className="goal-portfolio">
          <strong><CustomerText text="多个目标共用同一笔现金与结余" /></strong>
          <p><CustomerText text="合计月需预留" />{money(data.portfolio.required_monthly_minor)}<CustomerText text="· 基准月缺口" />{money(data.portfolio.monthly_gap_minor)}<CustomerText text="· 当前现金缺口" />{" "}
            {money(data.portfolio.current_cash_gap_minor)}
          </p>
        </div>
      )}
      {data.budget_reward_feedback?.length ? (
        <div className="reward-budget-feedback">
          <strong><CustomerText text="低碳权益与预算" /></strong>
          {data.budget_reward_feedback.map((reward) => (
            <p key={reward.redemption_id}>
              {customerName(reward.reward_name)}：{customerNarrative(reward.message)}<CustomerText text="当前现金价值" />{" "}
              {money(reward.cash_value_minor)}<CustomerText text="，已实现节省" />{" "}
              {money(reward.realized_saving_minor)}。
            </p>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function CarbonPanel({
  data,
  onPrompt,
}: {
  data: CarbonData;
  onPrompt: (message: string) => void;
}) {
  const { locale, t: customerUiText } = useExtensionLanguage();
  const modeLabels: Record<string, string> = {
    WALK: "步行",
    BICYCLE: "骑行",
    METRO: "地铁",
    BUS: "公交",
  };
  return (
    <div className="extension-data carbon-data">
      <div className="carbon-wallet">
        <span className="carbon-leaf">
          <Icon name="leaf" size={28} />
        </span>
        <div>
          <small><CustomerText text="当前个人低碳积分" /></small>
          <strong>
            {data.wallet.points}
            <span><CustomerText text="积分" /></span>
          </strong>
        </div>
        <span className="carbon-demo-tag"><CustomerText text="低碳活动" /></span>
      </div>
      <p className="extension-explanation">{data.explanation}</p>
      <p className="extension-explanation"><CustomerText text="减排量为估算值，不是核证碳资产；权益兑换不代表现金到账。" /></p>
      <h3><CustomerText text="个人低碳行为记录" /></h3>
      {data.events.length ? (
        data.events.map((event) => (
          <div className="carbon-event" key={event.behavior_id}>
            <div className="carbon-event-heading">
              <strong>{customerName(event.behavior_name)}</strong>
              <span
                className={`state-badge ${event.claimed ? "success" : event.claimable ? "working" : "neutral"}`}
              >
                <i />
                {event.claimed
                  ? customerUiText("已领取")
                  : event.claimable
                    ? customerUiText("可领取")
                    : customerUiText("暂不可领取")}
              </span>
            </div>
            <p>
              {modeLabels[event.mode] ? customerUiText(modeLabels[event.mode]) : event.mode} ·{" "}
              {(event.distance_m / 1000).toFixed(1)} km ·{" "}
              {dateText(event.occurred_at, true)}
            </p>
            <div className="carbon-event-estimate">
              <span><CustomerText text="估算避免排放" /><strong>
                  {(event.estimated_avoided_grams / 1000).toFixed(3)} kg
                </strong>
              </span>
              <span><CustomerText text="对应积分" /><strong>{event.estimated_points}</strong>
              </span>
            </div>
            <small>
              {event.source === "SYNTHETIC_BANK_FEED"
                ? customerUiText("行为记录数据源")
                : event.source}{" "}
              ·{" "}
              {event.status === "VERIFIED_SYNTHETIC"
                ? customerUiText("行为凭据已核验")
                : event.status}
            </small>
            {event.claimable && (
              <button
                className="button secondary compact"
                onClick={() => onPrompt(`核算碳积分：${event.behavior_name}`)}
              ><CustomerText text="准备领取积分" /><Icon name="arrow" size={14} />
              </button>
            )}
          </div>
        ))
      ) : (
        <p className="extension-empty"><CustomerText text="当前没有可展示的行为凭据。" /></p>
      )}
      <h3><CustomerText text="可兑换权益" /></h3>
      {data.rewards.length ? (
        <div className="carbon-rewards">
          {data.rewards.map((reward) => (
            <div className="carbon-reward" key={reward.reward_id}>
              <span className="reward-icon">
                <Icon name="leaf" size={19} />
              </span>
              <div>
                <strong>{customerName(reward.reward_name)}</strong>
                <small>
                  {reward.cost_points}<CustomerText text="积分 · 剩余" />{reward.stock}
                </small>
              </div>
              <button
                className="button secondary compact"
                disabled={!reward.can_redeem}
                onClick={() => onPrompt(`兑换权益：${reward.reward_name}`)}
              ><CustomerText text="准备兑换" /></button>
            </div>
          ))}
        </div>
      ) : (
        <p className="extension-empty"><CustomerText text="当前没有已配置的权益。" /></p>
      )}
      {data.redemptions.length > 0 && (
        <>
          <h3><CustomerText text="兑换回执" /></h3>
          {data.redemptions.map((redemption) => (
            <div className="carbon-redemption" key={redemption.redemption_id}>
              <span>
                {customerName(redemption.reward_name)} · {redemption.cost_points}<CustomerText text="积分" /></span>
              <StateBadge state={redemption.status} />
            </div>
          ))}
        </>
      )}
      <details className="carbon-method">
        <summary><CustomerText text="查看估算方法与核对" /></summary>
        <p>{data.methodology.factor_basis}</p>
        <dl>
          {Object.entries(data.methodology.avoided_grams_per_km).map(
            ([mode, factor]) => (
              <div key={mode}>
                <dt>{modeLabels[mode] ? customerUiText(modeLabels[mode]) : mode}</dt>
                <dd>{factor} g / km</dd>
              </div>
            ),
          )}
          <div>
            <dt><CustomerText text="积分换算" /></dt>
            <dd>{data.methodology.grams_per_point}<CustomerText text="g / 积分" /></dd>
          </div>
          <div>
            <dt><CustomerText text="积分总账净额" /></dt>
            <dd>{data.point_ledger_net}</dd>
          </div>
        </dl>
        <small>
          {data.methodology.methodology_version} ·{" "}
          {data.methodology.certified_carbon_asset
            ? customerUiText("已返回核证资产标记")
            : customerUiText("不是核证碳资产")}
        </small>
      </details>
    </div>
  );
}

export function ExtensionPanels(props: PanelProps) {
  const { locale, t: customerUiText } = useExtensionLanguage();
  const [fraudContext, setFraudContext] = useState({
    payeeName: "",
    message: "",
  });
  const scenario = EXTENSION_SCENARIOS.find(
    (item) => item.id === props.extensionId,
  );
  const query = new URLSearchParams({
    ...(fraudContext.payeeName ? { payee_name: fraudContext.payeeName } : {}),
    ...(fraudContext.message ? { message: fraudContext.message } : {}),
  });
  const endpoint = useEndpoint<Record<string, unknown>>(
    scenario?.id === "fraud" && query.size
      ? `${scenario.path}?${query}`
      : scenario?.path || null,
    props.token,
    props.revision,
  );
  if (!scenario) return null;
  const presentation = extensionScenarioPresentation(scenario.label, locale);
  return (
    <section
      className={`panel extension-panel extension-${scenario.id}`}
      aria-label={presentation.sectionAria}
    >
      <div className="small-panel-heading">
        <h2>
          <Icon name={scenario.icon} size={17} />
          {presentation.title}
        </h2>
        <button
          className="icon-button"
          onClick={endpoint.refresh}
          aria-label={presentation.refreshAria}
        >
          <Icon name="refresh" size={16} />
        </button>
      </div>
      {endpoint.loading && !endpoint.data && (
        <div className="extension-loading">
          <Skeleton lines={5} />
          <button className="text-button" onClick={endpoint.cancel}><CustomerText text="取消数据加载" /></button>
        </div>
      )}
      {endpoint.loading && endpoint.data && (
        <p className="extension-updating" role="status"><CustomerText text="正在更新可信状态…" /><button className="text-button" onClick={endpoint.cancel}><CustomerText text="取消加载" /></button>
        </p>
      )}
      {endpoint.error && (
        <div className="extension-load-error" role="alert">
          <Icon name="info" size={17} />
          <p>{endpoint.error}</p>
          <button
            className="button secondary compact"
            onClick={endpoint.refresh}
          ><CustomerText text="重新读取" /></button>
        </div>
      )}
      {endpoint.cancelled && (
        <div className="extension-load-error">
          <p><CustomerText text="已取消本次只读加载，未改变业务任务。" /></p>
          <button
            className="button secondary compact"
            onClick={endpoint.refresh}
          ><CustomerText text="重新读取" /></button>
        </div>
      )}
      {endpoint.data && scenario.id === "family" && (
        <FamilyPanel
          data={endpoint.data as unknown as FamilyData}
          props={props}
          onRefresh={endpoint.refresh}
        />
      )}
      {endpoint.data && scenario.id === "fraud" && (
        <FraudPanel
          data={endpoint.data as unknown as FraudData}
          appliedPayee={fraudContext.payeeName}
          appliedMessage={fraudContext.message}
          onAnalyze={(payeeName, message) =>
            setFraudContext({ payeeName, message })
          }
          onPrompt={props.onPrompt}
        />
      )}
      {endpoint.data && scenario.id === "goals" && (
        <GoalsPanel
          data={endpoint.data as unknown as GoalsData}
          onPrompt={props.onPrompt}
        />
      )}
      {endpoint.data && scenario.id === "carbon" && (
        <CarbonPanel
          data={endpoint.data as unknown as CarbonData}
          onPrompt={props.onPrompt}
        />
      )}
      <button
        className="full-width-link"
        onClick={() => props.onPrompt(scenario.prompt)}
      >{presentation.footerDraft}
        <Icon name="arrow" size={14} />
      </button>
    </section>
  );
}
