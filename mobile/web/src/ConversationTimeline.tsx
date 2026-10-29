// customer-copy-literals-v1
import { CustomerText, useCustomerLanguage, CustomerLanguageScope } from "./CustomerLanguageProvider";
import { customerUiText, getCustomerLocale, translateCustomerText, type CustomerLocale } from "./customerLanguage";
import { dateText, Icon, stateLabels } from "./components";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { conversationModeLabels } from "./conversationState";
import type {
  ConversationTurn,
  PendingConversationRequest,
} from "./conversationTypes";
import type { JsonValue } from "./types";
import type { TaskTrace, BillAnalysis } from "./types";
import { billAnalysisForDisplay } from "./billCoveragePresentation";
import { TaskOutcomeCard } from "./TaskOutcomeCard";
import { inlineReplyTurn, isQueryOnlyTask, requestDetailsTurn } from "./taskReply";
import { readSourceUnavailable, unavailableCompletedRead } from "./conversationReadAvailability.ts";
import { customerName, customerNarrative } from "./customerPresentation";
import type { ConversationTaskResult, ConversationTaskResults } from "./conversationTaskResults";
import { queryReadHasOwnReply, showConversationMode } from "./customerConversationPresentation";
import { CustomerMotionBoundary } from "./CustomerMotionBoundary";
import { useCustomerMotion } from "./useCustomerMotion";
import type { CustomerMotionSnapshot } from "./customerMotion";
import { useQueryReveal } from "./useQueryReveal";
import type { QueryRevealRequest } from "./queryReveal";
import { conversationLinkedTaskProgress, conversationLiveStatus, conversationQueueJumpText, conversationWaitingText } from "./conversationUiStatus";
import { requestAcknowledgementCovered, requestNavigationCovered, type ExternalRequestDetails } from "./activeRequestPresentation";
import { conversationOwnsPreparation, preparationReply } from "./conversationPreparation";
import { conversationQueueBlockers, conversationQueueRecordId, queueJumpMustWait, type ConversationQueueBlocker } from "./conversationQueueWait";
import { ConversationSuggestionCards, type ConversationSuggestionHandler } from "./ConversationSuggestionCards";
import { isCustomerPausedPlan } from "./customerPausedPlan";
import { historicalReadForTurn, historicalReadPresentation } from "./conversationReadSnapshots";
import { understandingRetryVersion } from "./conversationSemanticRecovery";
import { useResultDisclosureMemory } from "./useResultDisclosureMemory";
import { useConversationRefreshLayout } from "./useConversationRefreshLayout";
import { ReplyRevealScope, ReplyText } from "./ReplyText";
import { ReplyTextRevealLedger } from "./replyTextReveal";

// Text policy is independently checked from timeline layout and live controls.
import { clarificationReplyText, customerTurnAnswer } from "./conversationTurnAnswer";
export { clarificationReplyText, customerTurnAnswer } from "./conversationTurnAnswer";

/** A task acknowledgement is not yet the corresponding customer result. */
export function customerLinkedTaskProgress(turn?: ConversationTurn, dataReady = false, readError = false, locale: CustomerLocale = getCustomerLocale()): string | null {
  return conversationLinkedTaskProgress(turn, dataReady, readError, locale);
}

/** Saved-result access stays explicit, without a per-message reread/draft action. */
function ConversationResultRead({ taskId, statusId, result, ready, knownQuery, taskResults }: {
  taskId: string;
  statusId: string;
  result?: ConversationTaskResult;
  ready: boolean;
  knownQuery: boolean;
  taskResults: ConversationTaskResults;
}) {
  const { locale, t } = useCustomerLanguage();
  const retainedQuery = useRef(false);
  if (ready || knownQuery || ["LOADING", "ERROR", "HIDDEN"].includes(result?.status || "")) retainedQuery.current = true;
  const loading = result?.status === "LOADING";
  if (!retainedQuery.current) return null;
  let status = "";
  if (taskResults.permissionBlocked) status = t("当前查询资料的读取权限需要重新核验，结果内容已隐藏。");
  else if (taskResults.paused) status = locale === "en"
    ? "This result is hidden while the page is not visible. Access will be rechecked when you return."
    : "页面暂不可见，本条结果已隐藏；返回后会重新核验访问并读取。";
  // Restoring an authorized historical card is not a new user request.
  // Keep this transient background read silent; access failures stay explicit.
  else if (loading) return null;
  else if (result?.status === "ERROR") status = locale === "en"
    ? "This query result could not be loaded. You can ask in the message box to check it again."
    : "这条查询结果暂未读取完成，可以在消息框里说“再查一次”。";
  else if (result?.status === "HIDDEN") status = t("这条查询记录目前不可查看。");
  // A bounded history window is a page implementation detail, not a failed
  // query. History's existing load-more control exposes retained records;
  // actual read errors / access failures above remain explicit.
  if (!status) return null;
  return <div className={ready ? "turn-result-actions" : "turn-result-recovery"}
    data-result-read-task-id={taskId}>
    <p id={statusId} role="status" aria-live="polite" aria-atomic="true">{status}</p>
  </div>;
}

import { ConversationRecipientChoices, type RecipientChoiceSubmission } from "./ConversationRecipientChoices";

export function ConversationTimeline({
  turns,
  pending,
  sending,
  onTaskOpen,
  activeTaskId,
  activeTrace: cachedActiveTrace,
  activeTaskDetails,
  onResultPrompt,
  bankToken,
  taskResults,
  allowReplyMotion = false,
  queryRevealScope,
  disclosureScope,
  queryRevealRequest,
  queryRevealBlocked = false,
  preparationBlocked = false,
  invalidConfirmationSummary,
  externalRequestDetails,
  visibleRequestDetails,
  queueConversationId,
  queueEvidenceBlocked = false,
  suggestionRole,
  onSuggestion,
  onRetryUnderstanding,
  onRecipientChoice,
}: {
  turns: ConversationTurn[];
  pending: PendingConversationRequest | null;
  sending: boolean;
  onTaskOpen: (id: string) => void;
  activeTaskId: string;
  activeTrace?: TaskTrace | null;
  /** Already permission-gated controls from App, positioned at their own turn. */
  activeTaskDetails?: ReactNode;
  onResultPrompt?: (message: string) => void;
  bankToken?: string;
  taskResults?: ConversationTaskResults;
  allowReplyMotion?: boolean;
  queryRevealScope?: string;
  /** Non-authoritative open/closed preferences, scoped to actor and thread. */
  disclosureScope?: string;
  queryRevealRequest?: QueryRevealRequest | null;
  queryRevealBlocked?: boolean;
  preparationBlocked?: boolean;
  invalidConfirmationSummary?: string;
  externalRequestDetails?: ExternalRequestDetails;
  visibleRequestDetails?: ExternalRequestDetails;
  /** Current authorized conversation read; absent identity supplies no queue reason. */
  queueConversationId?: string;
  queueEvidenceBlocked?: boolean;
  /** Verified current actor role, not a role parsed from a model answer. */
  suggestionRole?: string;
  onSuggestion?: ConversationSuggestionHandler;
  onRetryUnderstanding?: (turn: ConversationTurn) => void;
  onRecipientChoice?: (selection: RecipientChoiceSubmission) => Promise<void>;
}) {
  const { locale, t: customerUiText } = useCustomerLanguage();
  // A persisted pending request exists during an ordinary outbound POST too.
  // It is not a fault until delivery actually becomes uncertain. Keep that
  // presentation distinction through a same-key acknowledgement lookup.
  const pendingPresentation = useRef({ key: "", uncertain: false });
  const pendingPresentationKey = pending ? JSON.stringify([pending.scope,
    pending.conversation_request_id, pending.payload.client_request_id]) : "";
  if (pendingPresentation.current.key !== pendingPresentationKey)
    pendingPresentation.current = { key: pendingPresentationKey, uncertain: Boolean(pending && !sending) };
  else if (pending && !sending) pendingPresentation.current.uncertain = true;
  const pendingUncertain = Boolean(pending && pendingPresentation.current.uncertain);
  const timelineRef = useRef<HTMLDivElement>(null);
  useConversationRefreshLayout(timelineRef, {
    scopeKey: disclosureScope ?? queryRevealScope ?? "",
    refreshing: Boolean(taskResults?.paused || taskResults?.loading || taskResults?.historyReads?.loading
      || turns.some(turn => turn.error_code === "HISTORY_ACCESS_RECHECK")),
    blocked: Boolean(taskResults?.permissionBlocked),
  });
  const replyTextLedger = useRef<ReplyTextRevealLedger | null>(null);
  if (!replyTextLedger.current) replyTextLedger.current = new ReplyTextRevealLedger();
  useResultDisclosureMemory(timelineRef, disclosureScope ?? queryRevealScope ?? "", Boolean(taskResults?.permissionBlocked));
  const queueComposing = useRef(false);
  const queueEvidence = { conversationId: queueConversationId, scopeKey: queryRevealScope,
    blocked: Boolean(queueEvidenceBlocked || preparationBlocked || queryRevealBlocked || pending || sending
      || taskResults?.permissionBlocked || taskResults?.paused) };
  const queueBlockers = queueConversationId && queryRevealScope && !queueEvidence.blocked
    ? conversationQueueBlockers(turns, queueEvidence) : new Map<string, ConversationQueueBlocker>();
  const queueJumpTargets = new Set([...queueBlockers.values()].map(item => item.turnId));
  const queueCurrent = useRef({ turns, evidence: queueEvidence });
  queueCurrent.current = { turns, evidence: queueEvidence };
  useEffect(() => {
    const document = timelineRef.current?.ownerDocument;
    if (!document) return;
    const started = () => { queueComposing.current = true; };
    const ended = () => { queueComposing.current = false; };
    document.addEventListener("compositionstart", started, true);
    document.addEventListener("compositionend", ended, true);
    return () => {
      document.removeEventListener("compositionstart", started, true);
      document.removeEventListener("compositionend", ended, true);
      queueComposing.current = false;
    };
  }, [queueConversationId, queryRevealScope]);
  const jumpToQueueBlocker = (turnId: string, blockerId: string, capturedScope: string | undefined) => {
    const root = timelineRef.current, current = queueCurrent.current;
    if (!root || current.evidence.scopeKey !== capturedScope || root.ownerDocument.visibilityState !== "visible"
      || root.closest('[hidden], [inert], [aria-hidden="true"]')
      || queueJumpMustWait(root.ownerDocument.activeElement, queueComposing.current)) return;
    // Recheck the current source at the gesture, including a replaced poll
    // window, completed predecessor, or changed actor/conversation scope.
    const blocker = conversationQueueBlockers(current.turns, current.evidence).get(turnId);
    if (!blocker || blocker.turnId !== blockerId) return;
    const candidates = [...root.querySelectorAll<HTMLElement>(".conversation-turn")].filter(element =>
      element.dataset.conversationId === blocker.conversationId && element.dataset.turnId === blocker.turnId);
    const target = candidates.length === 1 ? candidates[0] : null;
    if (!target || target.closest('[hidden], [inert], [aria-hidden="true"]') || !target.getClientRects().length) return;
    target.focus({ preventScroll: true });
    target.scrollIntoView({ block: "nearest", behavior: "auto" });
  };
  const seenReadReceipts = useRef(new Map<string, number>());
  const activeReadBarriers = useRef(new Map<string, number>());
  const priorReadStatuses = useRef(new Map<string, string>());
  const reads = taskResults?.items || {};
  for (const [id, entry] of Object.entries(reads)) if (entry.status === "READY"
    && Number.isSafeInteger(entry.readReceipt) && entry.readReceipt! > 0)
    seenReadReceipts.current.set(id, Math.max(entry.readReceipt!, seenReadReceipts.current.get(id) || 0));
  const inaccessible = Boolean(taskResults?.permissionBlocked || taskResults?.paused);
  if (inaccessible && cachedActiveTrace)
    activeReadBarriers.current.set(cachedActiveTrace.task_id, seenReadReceipts.current.get(cachedActiveTrace.task_id) || 0);
  for (const [id, status] of priorReadStatuses.current) if (status === "LOADING" && !reads[id])
    activeReadBarriers.current.set(id, seenReadReceipts.current.get(id) || 0);
  // Losing authorization or an in-progress read cannot resurrect a cached
  // active card. Only a newly validated GET receipt releases that task's fence.
  if (!inaccessible) for (const [id, receipt] of activeReadBarriers.current) if (reads[id]?.status === "READY"
    && (reads[id].readReceipt || 0) > receipt) activeReadBarriers.current.delete(id);
  priorReadStatuses.current = new Map(Object.entries(reads).map(([id, entry]) => [id, entry.status]));
  while (seenReadReceipts.current.size > 200) seenReadReceipts.current.delete(seenReadReceipts.current.keys().next().value!);
  while (activeReadBarriers.current.size > 200) activeReadBarriers.current.delete(activeReadBarriers.current.keys().next().value!);
  const activeTrace = !inaccessible && cachedActiveTrace && !activeReadBarriers.current.has(cachedActiveTrace.task_id)
    && !unavailableCompletedRead(cachedActiveTrace, turns) ? cachedActiveTrace : null;
  const inlineTraces = new Map<string, TaskTrace>();
  if (!taskResults?.permissionBlocked && !taskResults?.paused) {
    for (const [taskId, entry] of Object.entries(taskResults?.items || {})) {
      if (entry.status !== "READY" || !entry.trace || entry.trace.task_id !== taskId
        || entry.trace.version < entry.version || activeReadBarriers.current.has(taskId)) continue;
      const turnId = inlineReplyTurn(entry.trace, turns);
      if (turnId) inlineTraces.set(turnId, entry.trace);
    }
    // Preserve the just-finished active result before the history GET begins.
    // Failed/revalidating entries must not resurrect a stale active snapshot.
    const activeEntry = activeTrace ? taskResults?.items[activeTrace.task_id] : undefined;
    if (activeTrace && (activeEntry?.status === "READY" && activeTrace.version >= (activeEntry.trace?.version ?? activeEntry.version)
      || !activeEntry && !taskResults?.knownQueryIds?.includes(activeTrace.task_id))) {
      const turnId = inlineReplyTurn(activeTrace, turns);
      if (turnId) inlineTraces.set(turnId, activeTrace);
    }
  }
  const latestResultTurn = new Map<string, string>();
  const newestTurn = turns.at(-1);
  const newestQueueBlocker = newestTurn ? queueBlockers.get(newestTurn.turn_id) : undefined;
  const newestTrace = newestTurn ? inlineTraces.get(newestTurn.turn_id) : undefined;
  const newestCachedResult = newestTurn?.task_link ? taskResults?.items[newestTurn.task_link.task_id] : undefined;
  const newestResult: ConversationTaskResult | undefined = newestCachedResult?.status === "READY"
    && unavailableCompletedRead(newestCachedResult.trace, turns)
    ? { status: "UNAVAILABLE", version: newestCachedResult.version } : newestCachedResult;
  const newestAuthoritativeCandidate = !inaccessible && (newestResult?.status === "READY"
    ? activeTrace && activeTrace.task_id === newestTurn?.task_link?.task_id && activeTrace.version > (newestResult.trace?.version ?? -1)
      ? activeTrace : newestResult.trace
    : !newestResult || newestResult.status === "NOT_QUERY" ? activeTrace : null);
  const newestAuthoritativeTrace = newestAuthoritativeCandidate && newestTurn?.task_link && newestAuthoritativeCandidate.task_id === newestTurn.task_link.task_id
    && newestAuthoritativeCandidate.version >= newestTurn.task_link.version ? newestAuthoritativeCandidate : null;
  const newestProgressTurn = newestAuthoritativeTrace && newestTurn?.task_link ? { ...newestTurn, task_link: { ...newestTurn.task_link, state: newestAuthoritativeTrace.state } } : newestTurn;
  const revealingTrace = newestTrace || (activeTrace && activeTrace.task_id === newestTurn?.task_link?.task_id
    && !taskResults?.items[activeTrace.task_id] && !taskResults?.knownQueryIds?.includes(activeTrace.task_id) ? activeTrace : null);
  const reveal = useQueryReveal({request: queryRevealRequest, scopeKey: queryRevealScope, turn: newestTurn, trace: revealingTrace,
    blocked: Boolean(queryRevealBlocked || pending || sending || taskResults?.permissionBlocked), resultReadStatus: newestResult?.status});
  const ongoingQueryRead = isQueryOnlyTask(revealingTrace)
    && ["QUEUED", "UNDERSTANDING", "PLANNING", "PLANNED", "POLICY_CHECKED", "EXECUTING"].includes(revealingTrace?.state || "");
  const linkedTaskProgress = customerLinkedTaskProgress(newestProgressTurn,
    Boolean(newestTrace || newestResult?.status === "READY" || newestResult?.status === "NOT_QUERY" && activeTrace?.task_id === newestTurn?.task_link?.task_id),
    ["ERROR", "HIDDEN"].includes(newestResult?.status || ""), locale);
  const announcementKey = JSON.stringify([
    newestTurn?.turn_id, newestTurn?.status, newestTurn?.task_link?.state,
    newestTrace?.state, revealingTrace?.state, newestResult?.status, pending?.payload.client_request_id,
    sending, taskResults?.permissionBlocked, taskResults?.recheckingAccess,
    reveal.phase,
    newestAuthoritativeTrace?.state, newestTrace?.customer_reply_status, newestAuthoritativeTrace?.customer_reply_status, locale, invalidConfirmationSummary,
    newestQueueBlocker?.turnId, newestQueueBlocker?.status, queueConversationId, queryRevealScope,
  ]);
  const lastAnnouncementKey = useRef(reveal.phase === "none" ? announcementKey : "");
  const [announcement, setAnnouncement] = useState("");
  const freshRequestForLatest = queryRevealRequest?.turnId === newestTurn?.turn_id
    && queryRevealRequest?.scopeKey === queryRevealScope;
  const newestShownTrace = newestTrace || newestAuthoritativeTrace;
  const newestKnownQuery = Boolean(newestTurn?.task_link && taskResults?.knownQueryIds?.includes(newestTurn.task_link.task_id));
  const newestQueryOwnReply = Boolean(newestTurn && queryReadHasOwnReply(newestTurn, {
    originalResultTurn: true, knownQuery: newestKnownQuery, resultStatus: newestResult?.status,
  }));
  const contentVisible = Boolean(newestTrace ? !(reveal.turnId === newestTurn?.turn_id && reveal.holdResult)
    : newestTurn && ["ANSWERED", "FAILED"].includes(newestTurn.status) && !newestQueryOwnReply);
  const motionSnapshot: CustomerMotionSnapshot | null = newestTurn ? {
    scopeKey: queryRevealScope || "", turnId: newestTurn.turn_id, status: newestTurn.status,
    taskId: newestTurn.task_link?.task_id, version: newestShownTrace?.version || newestTurn.task_link?.version || 0,
    state: newestShownTrace?.state || newestTurn.task_link?.state,
    visible: contentVisible,
    contentKey: JSON.stringify([newestTurn.mode, newestTurn.status, newestShownTrace?.state,
      newestShownTrace?.outcome?.kind, newestShownTrace?.nodes.map(node => [node.node_id, node.status])]),
    urgent: Boolean(newestTurn.error_code === "HISTORY_ACCESS_RECHECK"
      || ["ERROR", "HIDDEN"].includes(newestResult?.status || "")
      || newestShownTrace?.nodes.some(node => node.result?.error || ["FAILED", "UNKNOWN"].includes(node.status))
      || newestShownTrace?.state === "SUCCEEDED" && newestShownTrace.outcome?.verified !== true),
  } : null;
  const visibleMotion = useCustomerMotion({ snapshot: motionSnapshot, liveRequest: queryRevealRequest,
    allowMotion: allowReplyMotion, blocked: Boolean(queryRevealBlocked || taskResults?.permissionBlocked
      || pending || sending), rereading: newestResult?.status === "LOADING" && !freshRequestForLatest });
  const currentInvalidConfirmation = !taskResults?.permissionBlocked && !pending && !sending
    && newestAuthoritativeTrace?.state === "AWAITING_CONFIRMATION" && newestAuthoritativeTrace.pending_confirmation
    && invalidConfirmationSummary;
  const latestSummary = currentInvalidConfirmation || conversationLiveStatus({
    permissionBlocked: taskResults?.permissionBlocked, recheckingAccess: taskResults?.recheckingAccess,
    pending: Boolean(pending), sending: sending && !pendingUncertain,
    revealText: reveal.phase !== "none" ? translateCustomerText(reveal.text, locale) : null,
    turn: newestTurn, traceState: newestTrace?.state,
    traceStateLabel: newestTrace ? customerUiText(stateLabels[newestTrace.state] || "状态更新中") : undefined,
    ongoingQueryRead, authoritativeState: newestAuthoritativeTrace?.state, linkedProgress: linkedTaskProgress,
    resultStatus: newestResult?.status,
    queryOnly: isQueryOnlyTask(newestTrace || newestAuthoritativeTrace),
    customerReplyStatus: (newestTrace || newestAuthoritativeTrace)?.customer_reply_status,
    controlHint: newestTurn && ["CONFIRM_HINT", "CANCEL_HINT", "STATUS"].includes(newestTurn.mode || "") ? customerTurnAnswer(newestTurn, locale) : null,
    waitingForEarlierReply: Boolean(newestQueueBlocker),
  }, locale);
  useEffect(() => {
    // Mounting or prepending history is not a new reply. Announce only a
    // transition of the latest message, independently of the interactive cards.
    if (lastAnnouncementKey.current === announcementKey) return;
    lastAnnouncementKey.current = announcementKey;
    setAnnouncement(latestSummary);
  }, [announcementKey, latestSummary]);
  for (const turn of turns) if (turn.task_link) latestResultTurn.set(turn.task_link.task_id, turn.turn_id);
  for (const turn of turns) if (turn.task_link && (["NEW_TASK", "REVISE", "CLARIFY"].includes(turn.mode || "") ||
    ["CREATED", "CONTINUED"].includes(turn.task_link.relationship))) latestResultTurn.set(turn.task_link.task_id, turn.turn_id);
  const scopedTaskDetails = activeTrace?.task_id === activeTaskId ? activeTaskDetails : null;
  const detailsTurnId = scopedTaskDetails && activeTrace ? requestDetailsTurn(activeTrace, turns) : null;
  const hasDetailsRequestReference = Boolean(activeTrace && turns.some(turn => turn.task_link?.task_id === activeTrace.task_id
    && (["NEW_TASK", "REVISE", "CLARIFY"].includes(turn.mode || "")
      || ["CREATED", "CONTINUED"].includes(turn.task_link?.relationship || ""))));
  return (
    <div ref={timelineRef} className="conversation-timeline" aria-label={customerUiText("对话消息")}>
      <p className="conversation-announcement sr-only" role="status" aria-live="polite" aria-atomic="true">{announcement}</p>
      {turns.map((turn) => {
        const inlineTrace = inlineTraces.get(turn.turn_id);
        // The server retained only this original turn's completed READ nodes.
        // A revised transfer's current confirmation is rendered separately at
        // its own newer turn, never reconstructed from this historical view.
        const historicalEntry = !inaccessible && !inlineTrace && detailsTurnId !== turn.turn_id
          ? taskResults?.historyReads?.items[turn.turn_id] : undefined;
        const historicalRead = historicalReadForTurn(turn,historicalEntry);
        const historicalPresentation = historicalReadPresentation(turn, historicalRead);
        const historicalReadReplacesReply = historicalPresentation.replaceReply;
        const historicalAccessCheck = turn.error_code === "HISTORY_ACCESS_RECHECK";
        const taskId = turn.task_link?.task_id;
        const readyEntry = taskId ? taskResults?.items[taskId] : undefined;
        const readyTrace = readyEntry?.status === "READY" && readyEntry.trace && readyEntry.trace.task_id === taskId && !activeReadBarriers.current.has(taskId!)
          && !unavailableCompletedRead(readyEntry.trace, turns)
          && readyEntry.trace.version >= readyEntry.version ? readyEntry.trace : null;
        const resultStatus = taskId ? taskResults?.items[taskId]?.status : undefined;
        const currentTrace = !inaccessible && (readyTrace
          ? activeTrace && activeTrace.task_id === taskId && activeTrace.version > readyTrace.version ? activeTrace : readyTrace
          : (!resultStatus || resultStatus === "NOT_QUERY") && activeTrace?.task_id === taskId && activeTrace);
        const clarificationTrace = !preparationBlocked && !inaccessible && currentTrace && !isCustomerPausedPlan(currentTrace) && currentTrace.state === "NEED_CLARIFICATION"
          && turn.status === "ANSWERED" && !turn.error_code && latestResultTurn.get(taskId || "") === turn.turn_id
          && turn.task_link && currentTrace.version >= turn.task_link.version ? currentTrace : null;
        const turnDescribesCurrent = latestResultTurn.get(taskId || "") === turn.turn_id || turn.mode === "STATUS";
        const effectiveState = turnDescribesCurrent && currentTrace && turn.task_link && currentTrace.version >= turn.task_link.version ? currentTrace.state : turn.task_link?.state;
        const effectiveTurn = turn.task_link && effectiveState !== turn.task_link.state ? { ...turn, task_link: { ...turn.task_link, state: effectiveState! } } : turn;
        const preparation = !preparationBlocked && !inaccessible && turn.turn_id === newestTurn?.turn_id
          && conversationOwnsPreparation(currentTrace || null,[effectiveTurn]);
        const result = taskId && latestResultTurn.get(taskId) === turn.turn_id ? taskResults?.items[taskId] : undefined;
        const knownQuery = Boolean(taskId && taskResults?.knownQueryIds?.includes(taskId));
        const originalResultTurn = Boolean(taskId && latestResultTurn.get(taskId) === turn.turn_id);
        // A later request for another task must not replace the older task's
        // real clarification with a generic acceptance placeholder. Saved
        // narration is historical only; live cards/confirmation remain scoped
        // to the current task and independently revalidate their permissions.
        const historicalTaskReply = Boolean(taskId && (latestResultTurn.get(taskId) !== turn.turn_id
          || turn.turn_id !== newestTurn?.turn_id));
        const queryReadReply = queryReadHasOwnReply(turn, { originalResultTurn, knownQuery, resultStatus: result?.status });
        const motionEvent = turn.turn_id === newestTurn?.turn_id ? visibleMotion : null;
        const observedAt = inlineTrace?.result_observed_at;
        const observationLocale = inlineTrace?.assistant_response?.reply_language || inlineTrace?.reply_language || turn.reply_language || locale;
        const hasObservationTime = typeof observedAt === "string" && Number.isFinite(Date.parse(observedAt));
        const billNode=inlineTrace?.nodes.length===1 && inlineTrace.nodes[0].action==="BILL_ANALYSIS" && inlineTrace.nodes[0].status==="SUCCEEDED" ? inlineTrace.nodes[0] : null;
        const billPrimary=inlineTrace?.analysis || billNode?.result?.analysis;
        const usingSupplementaryBill=Boolean(billNode && inlineTrace?.state==="SUCCEEDED" && inlineTrace.outcome?.state===inlineTrace.state
          && inlineTrace.outcome.kind==="QUERY_RESULT" && inlineTrace.outcome.verified===true && !inlineTrace.pending_confirmation
          && billPrimary && typeof billPrimary==="object" && !Array.isArray(billPrimary) && inlineTrace.supplementary_bill
          && billAnalysisForDisplay(billPrimary as BillAnalysis,inlineTrace.supplementary_bill)===inlineTrace.supplementary_bill);
        const coveredAcknowledgement = !inlineTrace && requestAcknowledgementCovered(turn,
          turn.task_link ? latestResultTurn.get(turn.task_link.task_id) : undefined, externalRequestDetails);
        const coveredNavigation = requestNavigationCovered(effectiveTurn,
          taskId ? latestResultTurn.get(taskId) : undefined, visibleRequestDetails);
        const coveredCurrentNavigation = taskId === activeTaskId && requestNavigationCovered(effectiveTurn,
          turn.turn_id, visibleRequestDetails);
        const latestActionTurn = taskId ? turns.find(item => item.turn_id === latestResultTurn.get(taskId)) : undefined;
        const savedReplyText = customerTurnAnswer(effectiveTurn, turn.reply_language || locale,
          historicalTaskReply || historicalPresentation.collapsed || knownQuery && !result?.status);
        // A revision can legitimately leave an earlier acceptance without a
        // retained answer. Do not repeat that placeholder above the verified
        // current details for the same request. This is display deduplication,
        // never permission recovery or restoration of old banking facts.
        const coveredUnavailableReply = Boolean(historicalTaskReply && !originalResultTurn && !inlineTrace
          && !clarificationTrace && !knownQuery && !inaccessible && !preparationBlocked && !pending && !sending
          && !turn.error_code && ["NOT_RECORDED", "NOT_RETAINED", "CAPACITY_REACHED"].includes(turn.reply_snapshot_status || "")
          && !turn.reply_snapshot && coveredCurrentNavigation && currentTrace && visibleRequestDetails
          && currentTrace.task_id === visibleRequestDetails.taskId && currentTrace.version === visibleRequestDetails.version
          && currentTrace.state === visibleRequestDetails.state
          && ["NEED_CLARIFICATION", "AWAITING_CONFIRMATION", "WAITING_CONFIRMATION"].includes(currentTrace.state)
          && !currentTrace.nodes.some(node => ["UNKNOWN", "FAILED", "PARTIAL"].includes(node.status) || node.result?.error)
          && !["UNKNOWN", "FAILED", "PARTIAL"].includes(turn.task_link?.state || "")
          && !["ERROR", "HIDDEN", "LOADING"].includes(resultStatus || "")
          && !/未知|失败|未完成|不要重复|勿重复|unknown|failed|do not repeat/i.test(turn.answer || "")
          && latestActionTurn && latestActionTurn.sequence > turn.sequence
          && ["CLARIFY", "REVISE"].includes(latestActionTurn.mode || "")
          && latestActionTurn.task_link?.task_id === taskId
          && Number.isSafeInteger(latestActionTurn.task_link.version)
          && latestActionTurn.task_link.version <= currentTrace.version
          && savedReplyText === ((turn.reply_language || locale) === "en"
            ? "This earlier reply is unavailable right now." : "这条较早回复暂不可查看。"));
        const coveredPausedMissingReply = Boolean(currentTrace && isCustomerPausedPlan(currentTrace)
          && historicalTaskReply && !inlineTrace && !clarificationTrace && !knownQuery && !inaccessible
          && !preparationBlocked && !pending && !sending && !turn.error_code && !turn.reply_snapshot
          && ["NEW_TASK", "REVISE", "CLARIFY"].includes(turn.mode || "")
          && ["NOT_RECORDED", "NOT_RETAINED", "CAPACITY_REACHED"].includes(turn.reply_snapshot_status || "")
          && newestTurn?.status === "ANSWERED" && newestTurn.mode === "FEEDBACK" && !newestTurn.error_code
          && newestTurn.task_link?.task_id === currentTrace.task_id && newestTurn.task_link.version === currentTrace.version
          && savedReplyText === ((turn.reply_language || locale) === "en"
            ? "This earlier reply is unavailable right now." : "这条较早回复暂不可查看。"));
        const queueBlocker = queueBlockers.get(turn.turn_id);
        const queueRecordId = queueBlocker || queueJumpTargets.has(turn.turn_id)
          ? conversationQueueRecordId(turn.conversation_id, turn.turn_id) : undefined;
        const queueStatusId = queueRecordId ? `${queueRecordId}-wait` : undefined;
        // App's complete, guarded card already supplies this request's reply,
        // status and safety information. Do not prepend a second acceptance or
        // "historical reply unavailable" bubble to the same visible card.
        // A newer completed receipt may follow a same-task status observation.
        // Keep what was correctly said before confirmation; only the live card
        // moves, never the saved answer or its original timestamp.
        const detailsOwnReply = detailsTurnId === turn.turn_id && Boolean(scopedTaskDetails) && turn.mode !== "STATUS";
        return (
        <ReplyRevealScope key={turn.turn_id} request={queryRevealRequest} scopeKey={queryRevealScope || ""}
          turnId={turn.turn_id} ledger={replyTextLedger.current!}
          enabled={allowReplyMotion && turn.turn_id === newestTurn?.turn_id && !queryRevealBlocked
            && !preparationBlocked && !inaccessible && !pending && !sending}>
        <article
          key={turn.turn_id}
          className="conversation-turn"
          data-turn-id={turn.turn_id}
          data-turn-status={turn.status}
          data-conversation-id={turn.conversation_id}
          id={queueRecordId}
          tabIndex={queueJumpTargets.has(turn.turn_id) ? -1 : undefined}
        >
          <div className="user-message">
            <span className="user-avatar"><CustomerText text="你" /></span>
            <div>
              <span className="message-author">
                {historicalAccessCheck ? customerUiText("历史消息") : customerUiText("你的消息")} · {dateText(turn.created_at, true)}
              </span>
              <p>{turn.message}</p>
            </div>
          </div>
          {!historicalReadReplacesReply && !detailsOwnReply && !coveredAcknowledgement && !coveredUnavailableReply && !coveredPausedMissingReply && <CustomerLanguageScope locale={turn.reply_language}><div className="assistant-message">
            <span className="assistant-avatar">
              <Icon name="spark" size={20} />
            </span>
            <div className="conversation-answer">
              <div className="result-heading">
                {!preparation && showConversationMode(turn, Boolean(inlineTrace) || reveal.turnId === turn.turn_id && reveal.phase !== "none") && <small
                  className={`conversation-mode mode-${turn.mode || "processing"}`}
                >
                  <CustomerText text={historicalAccessCheck ? "正在重新核验历史内容" : turn.mode
                    ? conversationModeLabels[turn.mode] || "回复"
                    : turn.status === "LEASED"
                      ? "正在处理消息"
                      : "消息已受理"} />
                </small>}
              </div>
              {clarificationTrace ? <div className="customer-clarification-message" data-task-id={clarificationTrace.task_id} data-task-state={clarificationTrace.state}>
                {["QUEUED", "PROCESSING"].includes(clarificationTrace.customer_reply_status || "")
                  ? <p className="turn-processing"><span className="spinner" />{locale === "en" ? "Checking what else I need…" : "正在确认还需要的信息…"}</p>
                  : <p className="turn-answer"><ReplyText slot={`task:${clarificationTrace.task_id}:clarification`} text={customerNarrative(clarificationTrace.assistant_response
                    ? clarificationReplyText(clarificationTrace.assistant_response.intro, clarificationTrace.assistant_response.detail)
                    : clarificationTrace.clarification_questions?.join("\n") || clarificationTrace.status_message || "")}/></p>}
              </div> : inlineTrace ? (
                <div className="task-result-inline" data-task-id={inlineTrace.task_id} data-task-state={inlineTrace.state}>
                  {reveal.turnId === turn.turn_id && reveal.holdResult ? <p className="turn-processing query-reveal-wait" data-query-reveal-phase={reveal.phase} data-query-reveal-exiting={reveal.exiting ? "true" : undefined}>
                    <span className="spinner" aria-hidden="true"/><span className="query-reveal-text" key={reveal.phase}>{translateCustomerText(reveal.text, locale)}</span>
                  </p> : <TaskOutcomeCard trace={inlineTrace} bankToken={bankToken} onPrompt={onResultPrompt} motionEvent={motionEvent} timestampInParent/>}
                  {!usingSupplementaryBill && <small className="turn-boundary">{hasObservationTime ? observationLocale === "en" ? `Retrieved at ${dateText(observedAt, true)}` : `查询于 ${dateText(observedAt, true)}` : observationLocale === "en" ? "Query time was not recorded" : "查询时间未记录"}{hasObservationTime && Date.now() - Date.parse(observedAt!) > 86_400_000 ? observationLocale === "en" ? "; historical result, not current account status." : " · 历史查询，不代表当前状态。" : ""}</small>}
                </div>
              ) : reveal.turnId === turn.turn_id && reveal.phase === "reading" ? <p className="turn-processing query-reveal-wait" data-query-reveal-phase="reading">
                <span className="spinner" aria-hidden="true"/><span className="query-reveal-text">{translateCustomerText(reveal.text, locale)}</span>
              </p> : turn.status === "QUEUED" || turn.status === "LEASED" ? (
                <div className="turn-queue-wait">
                <p id={queueStatusId} className="turn-processing">
                  <span className="spinner" />
                  {conversationWaitingText(turn.status, locale, Boolean(queueBlocker))}
                </p>
                {queueBlocker && <button type="button" className="text-button"
                  aria-controls={conversationQueueRecordId(queueBlocker.conversationId, queueBlocker.turnId)}
                  aria-describedby={queueStatusId}
                  onPointerDown={event => {
                    if (queueJumpMustWait(event.currentTarget.ownerDocument.activeElement, queueComposing.current)) event.preventDefault();
                  }}
                  onKeyDown={event => {
                    if (event.nativeEvent.isComposing || event.keyCode === 229 || queueComposing.current) event.preventDefault();
                  }}
                  onClick={() => jumpToQueueBlocker(turn.turn_id, queueBlocker.turnId, queryRevealScope)}>
                  {conversationQueueJumpText(locale)}
                </button>}
                </div>
              ) : preparation ? <p className="turn-processing"><span className="spinner" aria-hidden="true" />
                {preparationReply(isQueryOnlyTask(currentTrace || null),locale)}</p> : !queryReadReply ? (
                <CustomerMotionBoundary event={motionEvent} interactive={false}
                  urgent={turn.status === "FAILED" || ["UNKNOWN", "FAILED", "PARTIAL", "CLOSED"].includes(effectiveState || "")}>
                  <p
                  className={`turn-answer ${turn.status === "FAILED" ? "turn-failed" : ""}`}
                  data-reply-motion="none"
                >
                  <ReplyText slot="turn-answer" text={savedReplyText}
                    immediate={historicalAccessCheck || ["UNKNOWN", "PARTIAL"].includes(effectiveState || "")
                      || ["CONFIRM_HINT", "CANCEL_HINT", "STATUS"].includes(turn.mode || "")}/>
                </p></CustomerMotionBoundary>
              ) : null}
              {turn.mode === "CONFIRM_HINT" && (
                <p className="turn-boundary">
                  <Icon name="shield" size={13} /><CustomerText text="涉及资金或授权的操作，请在办理卡中确认。" /></p>
              )}
              {onRetryUnderstanding && understandingRetryVersion(turn, { conversationId: queueConversationId,
                latestTurnId: newestTurn?.turn_id, blocked: Boolean(queueEvidence.blocked || inaccessible) }) !== null &&
                <button type="button" className="button subtle compact conversation-understanding-retry"
                  onClick={() => onRetryUnderstanding(turn)}>{(turn.reply_language || locale) === "en" ? "Retry understanding this message" : "重新理解这条消息"}</button>}
              {turn.mode === "CANCEL_HINT" && (
                <p className="turn-boundary">
                  <Icon name="shield" size={13} /><CustomerText text="是否可取消，以当前办理状态为准。" /></p>
              )}
              {onSuggestion && turn.turn_id === newestTurn?.turn_id && <ConversationSuggestionCards turn={turn} onSuggestion={onSuggestion} motionEvent={motionEvent}
                context={{ currentTurnId: newestTurn?.turn_id, conversationId: queueConversationId,
                  actorRole: suggestionRole, scopeKey: queryRevealScope,
                  blocked: Boolean(queueEvidenceBlocked || preparationBlocked || queryRevealBlocked
                    || pending || sending || taskResults?.permissionBlocked || taskResults?.paused) }}/>} 
              {taskResults && taskId && originalResultTurn && !readSourceUnavailable(turn) && <ConversationResultRead
                key={JSON.stringify([queryRevealScope, turn.conversation_id, turn.turn_id, taskId])}
                taskId={taskId} statusId={`conversation-result-read-${encodeURIComponent(JSON.stringify([turn.conversation_id, turn.turn_id, taskId]))}`}
                result={result} ready={Boolean(inlineTrace)} knownQuery={knownQuery || activeReadBarriers.current.has(taskId)}
                taskResults={taskResults} />}
              {turn.mode !== "STATUS" && !historicalAccessCheck && turn.task_link
                && originalResultTurn && !knownQuery && !["READY", "LOADING", "ERROR", "HIDDEN"].includes(result?.status || "")
                && ["AWAITING_CONFIRMATION", "UNKNOWN", "FAILED", "PARTIAL"].includes(effectiveState || "")
                && !inlineTrace && !clarificationTrace && !coveredNavigation && !coveredCurrentNavigation && !preparation
                && !isCustomerPausedPlan(currentTrace || null)
                && !taskResults?.permissionBlocked && !taskResults?.paused && (
                <button
                  className="text-button conversation-task-recovery"
                  data-task-id={turn.task_link.task_id}
                  data-state={effectiveState}
                  onClick={() => onTaskOpen(turn.task_link!.task_id)}
                >
                  {locale === "en" ? effectiveState === "AWAITING_CONFIRMATION" ? "Review before confirming"
                    : effectiveState === "UNKNOWN" ? "Check the original request" : "View details and next steps"
                    : effectiveState === "AWAITING_CONFIRMATION" ? "核对并确认"
                    : effectiveState === "UNKNOWN" ? "核查原办理结果" : "查看说明与下一步"}
                </button>
              )}
            </div>
          </div></CustomerLanguageScope>}
          {historicalRead && <CustomerLanguageScope locale={historicalRead.reply_language || turn.reply_language}>
            <div className="assistant-message"><span className="assistant-avatar"><Icon name="spark" size={20}/></span>
              <div className="conversation-answer task-result-historical" data-task-id={historicalRead.task_id}
                data-source-turn-id={historicalRead.source_turn_id} data-read-only="true">
                {historicalPresentation.collapsed ? <details className="historical-read-details">
                  <summary>{locale === "en" ? historicalPresentation.beforeAction ? "Query before processing" : "Earlier query"
                    : historicalPresentation.beforeAction ? "办理前查询" : "此前查询"}</summary>
                  <TaskOutcomeCard trace={historicalRead}/>
                </details> : <TaskOutcomeCard trace={historicalRead}/>}
              </div>
            </div>
          </CustomerLanguageScope>}
          {["ERROR","HIDDEN"].includes(historicalEntry?.status || "") && <p className="turn-boundary" role="status">
            {locale === "en" ? "This earlier query result is currently unavailable. No action was repeated."
              : "这次较早的查询结果暂不可查看，未重复办理任何操作。"}
          </p>}
          {bankToken && onRecipientChoice && turn.turn_id === newestTurn?.turn_id && turn.status === "ANSWERED"
            && turn.task_link && ["NEED_CLARIFICATION", "AWAITING_CONFIRMATION"].includes(effectiveState || "")
            && !pending && !sending && !preparationBlocked && !taskResults?.permissionBlocked
            && <ConversationRecipientChoices token={bankToken} conversationId={turn.conversation_id} turnId={turn.turn_id}
              taskId={turn.task_link.task_id} taskVersion={currentTrace && currentTrace.version || turn.task_link.version}
              blocked={Boolean(taskResults?.paused || preparationBlocked || sending || pending)} onSelect={onRecipientChoice}/>}
          {detailsTurnId === turn.turn_id && scopedTaskDetails}
        </article>
        </ReplyRevealScope>
        );
      })}
      {!detailsTurnId && !hasDetailsRequestReference && activeTrace && scopedTaskDetails}
      {taskResults?.permissionBlocked && <div className="turn-result-recovery">
        <p className="turn-boundary"><CustomerText text="当前查询资料的读取权限需要重新核验，结果内容已隐藏。" /></p>
        {taskResults.recheckAccess && <button className="text-button" onClick={() => { reveal.cancel(); taskResults.recheckAccess?.(); }} disabled={taskResults.recheckingAccess}>
          {taskResults.recheckingAccess ? customerUiText("正在核验访问…") : customerUiText("重新核验访问")}
        </button>}
        {taskResults.recheckError && <p>{taskResults.recheckError}</p>}
      </div>}
      {Boolean(taskResults?.remaining) && <button className="button secondary compact" onClick={taskResults?.loadMore} disabled={taskResults?.loading}><CustomerText text="加载更早查询结果（还有" />{taskResults?.remaining}<CustomerText text="条）" /></button>}
      {pending && (
        <article className="turn-send-pending">
          <Icon name="clock" size={18} />
          <div>
            <strong>
              {sending
                ? pendingUncertain ? locale === "en" ? "Checking the original message…" : "正在核查原消息…"
                  : customerUiText("正在发送消息…")
                : customerUiText("暂未确认这条消息是否发送成功")}
            </strong>
            <p>{pending.payload.message}</p>
            {pendingUncertain && <small><CustomerText text="请先恢复发送，避免另发相同办理需求；重试会使用原来的消息。" /></small>}
          </div>
        </article>
      )}
    </div>
  );
}

export function ConversationEfficiency({
  diagnostics,
  technical = false,
}: {
  diagnostics?: Record<string, JsonValue> | null;
  technical?: boolean;
}) {
  const { t: customerUiText } = useCustomerLanguage();
  if (!technical || !diagnostics) return null;
  const provider =
    typeof diagnostics.actual_provider === "string"
      ? diagnostics.actual_provider
      : null;
  const model =
    typeof diagnostics.actual_model === "string"
      ? diagnostics.actual_model
      : null;
  const calls =
    typeof diagnostics.model_api_call_count === "number"
      ? diagnostics.model_api_call_count
      : null;
  if (!provider && !model && calls === null) return null;
  return (
    <div className="conversation-efficiency">
      <Icon name="workflow" size={13} />
      <span><CustomerText text="本次真实路径" />{provider || customerUiText("提供方未记录")}
        {model ? ` / ${model}` : ""}
      </span>
      {calls !== null && <strong>{calls}<CustomerText text="次已记录出站尝试" /></strong>}
      <small><CustomerText text="不是节省次数或语义正确率" /></small>
    </div>
  );
}
