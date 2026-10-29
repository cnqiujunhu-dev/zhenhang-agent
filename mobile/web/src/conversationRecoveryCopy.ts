import { getCustomerLocale, type CustomerLocale } from "./customerLanguage.ts";

/** Controlled recovery language. No server exception, request identifier or
 * retained message is interpolated into recovery controls. */
export function conversationRecoveryCopy(locale: CustomerLocale = getCustomerLocale()) {
  return locale === "en" ? {
    incomplete: "I couldn't verify whether the last message was received. Check your conversations before sending it again.",
    uncertain: "I haven't confirmed whether this message was received. Check it first to avoid submitting the same request twice.",
    check: "Check this request", checking: "Checking this request…", more: "Other options",
    later: (seconds: number) => `Check again in ${seconds}s`,
    retry: "Continue this same request",
    retryNotice: "This reuses the original message. It does not start a separate request, and any payment still requires its own confirmation.",
    leave: "I've checked; continue with another request",
    leaveConfirm: "Have you checked your existing conversations? Continuing will not cancel any accepted request or banking action. If a payment is still uncertain, verify its result before asking to pay again. Continue?",
    left: "You can discuss something else now. Any previously accepted request remains unchanged; check its result before asking to make the same payment again.",
    incompleteChecked: "Your existing conversations are being checked. An absent result does not prove the message was never received; please review the records before continuing.",
    notFound: "I haven't found a verified result yet. Check again shortly; don't send the same payment or private details again.",
    readFailed: "I couldn't verify the result yet. You can check again; this check will not resend the message.",
    selectBlocked: "Check the last message first. Switching conversations will not cancel an accepted request.",
    newBlocked: "Check the last message first. Starting a new conversation will not cancel an accepted request.",
    changed: "Check the original message first. Changing the wording could create a separate request.",
    storageUnavailable: "Keep this page open and check your existing conversations if the connection is interrupted.",
    acknowledgedStorage: "The message was received. If this reminder appears after reopening the page, check the existing request instead of sending the same payment again.",
  } : {
    incomplete: "暂时无法核实上一条消息是否收到，请先查看已有会话，不要重复发送。",
    uncertain: "这条消息是否收到还未确定，先核实一下，避免重复办理。",
    check: "核实这次请求", checking: "正在核实这次请求…", more: "其他选项",
    later: (seconds: number) => `${seconds}秒后可再核实`,
    retry: "继续这次原请求",
    retryNotice: "将使用原来的消息继续，不另起一笔办理；涉及付款仍需单独核对确认。",
    leave: "已核对记录，继续其他需求",
    leaveConfirm: "你已核对已有会话了吗？继续其他需求不会取消已受理的请求或银行操作。如果付款结果仍不确定，请先核实，不要再次发起相同付款。是否继续？",
    left: "现在可以继续其他需求。此前已受理的请求不受影响；再次提出相同付款前，请先核实原办理结果。",
    incompleteChecked: "正在核对已有会话。暂未看到结果不代表消息没有收到，请查看已有记录后再继续。",
    notFound: "暂时还没查到已核实的结果，可以稍后再核实；请勿重复发送相同付款或隐私信息。",
    readFailed: "暂时还没核实到结果，可以稍后再查；查询不会重新发送消息。",
    selectBlocked: "请先核实上一条消息，切换会话不会取消已受理的请求。",
    newBlocked: "请先核实上一条消息，新会话不会取消已受理的请求。",
    changed: "请先核实原消息，修改说法可能会成为另一笔办理。",
    storageUnavailable: "请保留当前页面；若连接中断，请先核对已有会话。",
    acknowledgedStorage: "消息已收到。再次打开页面时若仍出现提醒，请核实已有请求，不要重复发起相同付款。",
  };
}
