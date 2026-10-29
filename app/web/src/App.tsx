// customer-copy-literals-v1
import { CustomerText, useCustomerLanguage, CustomerLanguageScope } from "./CustomerLanguageProvider";
import { CustomerTopbarActions, profileLabels } from "./CustomerTopbarActions";
import { subscriptionDueLabel } from "./customerContinuation";
import { billCoverageNotice, billCoverageUnusable } from "./billCoveragePresentation";
import { SubscriptionResourcePanel } from "./SubscriptionResourcePanel";
import { CustomerDataHome } from "./CustomerDataHome";
import { SubscriptionOverviewCard } from "./SubscriptionOverviewCard";
import { UnifiedHistory } from "./UnifiedHistory";
import { customerUiText, translateCustomerText } from "./customerLanguage";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, listOf, request } from "./api";
import { useLayoutEffect } from "react";
import { memo } from "react";
import {
  Brand,
  dateText,
  Icon,
  money,
  SpendingChart,
  stateLabels,
  StructuredData,
  useDialogFocus,
  Skeleton,
  CardTile,
} from "./components";
import {
  BillReports,
  RecurringPanel,
  SimulationDrawer,
  TransferCenter,
  WealthPanel,
} from "./BusinessPanels";
import { EXTENSION_SCENARIOS, ExtensionPanels } from "./ExtensionPanels";
import { useEndpoint } from "./useEndpoint";
import { TaskOutcomeCard } from "./TaskOutcomeCard";
import { ReplyText } from "./ReplyText";
import { CreditResources } from "./CreditResources";
import { CreditConfirmation } from "./CreditConfirmation";
import { TransferConfirmation } from "./TransferConfirmation";
import { SubscriptionCancelConfirmation } from "./SubscriptionCancelConfirmation";
import { LifeDomainConfirmation, lifeConfirmationActions, lifeConfirmationReady, lifeConfirmationTitle, lifeConfirmationSpeech } from "./LifeDomainConfirmation";
import { BankingWorkflowConfirmation, bankingWorkflowConfirmationActions, bankingWorkflowConfirmationReady, bankingWorkflowConfirmationTitle, bankingWorkflowConfirmationSpeech } from "./BankingWorkflowConfirmation";
import { OwnAccountTransferConfirmation } from "./OwnAccountTransferCards";
import { ClarificationDetails } from "./ClarificationDetails";
import { CustomerDisclosure } from "./CustomerDisclosure";
import { CustomerMotionBoundary } from "./CustomerMotionBoundary";
import { ResourceReadArrival, useResourceReadArrival } from "./ResourceReadArrival";
import { useCustomerMotion } from "./useCustomerMotion";
import { useConversationTail } from "./useConversationTail";
import { useHistoricalConversationReading } from "./useHistoricalConversationReading";
import { MissingConfirmationNotice } from "./MissingConfirmationNotice";
import { isCustomerPausedPlan } from "./customerPausedPlan";
import { mayInitiallyFocusComposer } from "./taskEditDraft";
import "./customerSurface.css";
import "./customerControlFeedback.css";
import "./resourceReadArrival.css";
import "./confirmationCompact.css";
import "./customerDensity.css";
import { readableCustomerRegion } from "./customerVisibility";
import { UniversalWelcome } from "./UniversalWelcome";
import { useWelcomeSendFocus } from "./useWelcomeSendFocus";
import { FxPanels } from "./FxPanels";
import { protectCardDraft } from "./cardIdentity";
import { customerName } from "./customerPresentation";
import { confirmationSpeech } from "./confirmationSpeech";
import { confirmationFacts, confirmationSummary, confirmationTitle, customerCarbonConfirmation, wealthConfirmationReady, subscriptionCancelConfirmation } from "./confirmationPresentation";
import { AAPayConfirmation, aaPayReviewReady } from "./AAResultCards";
import { inlineReplyTurn, isQueryOnlyTask, requestDetailsTurn } from "./taskReply";
import { unavailableCompletedRead, unavailableReadSelection, unavailableReadFailureVersion } from "./conversationReadAvailability.ts";
import { historicalReadForTurn } from "./conversationReadSnapshots";
import { conversationReplyLanguage } from "./conversationReplyLanguage";
import { customerSuggestionCatalogVersion } from "./conversationSuggestions";
import { conversationOwnsPreparation } from "./conversationPreparation";
import { useReleaseUpdate } from "./useReleaseUpdate";
import { AliasBindingNotice } from "./ContactAliasesPanel";
import { useConversations } from "./useConversations";
import { ConversationRecoveryPanel } from "./ConversationRecoveryPanel";
import { conversationRecoveryCopy } from "./conversationRecoveryCopy";
import {
  ConversationTimeline,
} from "./ConversationTimeline";
import {
  conversationTaskIds,
  conversationTurnInput,
  hasProcessingTurn,
} from "./conversationState";
import { AttachmentPanel } from "./AttachmentPanel";
import { useVisibleViewportGeometry } from "./useVisibleViewport";
import { focusedElementScrollDelta } from "./viewportGeometry";
import { boundedFocusScrollTop, confirmationFeedback } from "./conversationAccessibility";
import type { QueryRevealRequest } from "./queryReveal";
import { customerRecovery, type CustomerRecovery } from "./customerRecovery";
import { shouldSendOnShortcut } from "./keyboardIntent";
import { JourneyMetrics } from "./journeyMetrics";
import { journeyDiagnostic } from "./journeyDiagnostic";
import { BankInteractionGate, canApplyTaskTrace, verificationConfirmed } from "./bankInteraction";
import {currentTaskTraceReadReceipt,type TaskTraceReadReceipt} from "./taskTraceReadReceipt";
import { canRetainTaskSelectionTrace } from "./taskSelectionTrace";
import { taskFocusPresentation } from "./taskFocusPresentation";
import { captureReadingAnchor, resolveReadingAnchor, type ReadingAnchor } from "./conversationReadingAnchor";
import { collectReadingTurns } from "./conversationReadingGeometry";
import { useMaterialNavigation } from "./useMaterialNavigation";
import { composerPlaceholder } from "./composerPlaceholder";
import { composerTaskBinding, composerClarificationContext, type ComposerTaskChoice } from "./composerClarification";
import { composerExplicitTaskBinding } from "./composerExplicitTaskBinding";
import { composerLifecycleTaskBinding } from "./composerLifecycle";
import { useFreshReplyReading } from "./useFreshReplyReading";
import { useReadVisibility } from "./useReadVisibility";
import { nextReadPollDelay } from "./pollSchedule";
import { beginRecoveryDelay, recoveryDelayState, type RecoveryDelay } from "./recoveryDelay";
import { validChallengeResponse } from "./verificationResponse";
import { useChallengeFreshness } from "./useChallengeFreshness";
import { confirmationValidityText, confirmationValidityLabel, useConfirmationValidity } from "./useConfirmationValidity";
import { authRecoveryText, sameAuthRecoveryScope, useAuthRecovery, type AuthRecoveryScope } from "./useAuthRecovery";
import { assertFinancialResource } from "./financialResource";
import { financialResourceKinds, type ResourceReadTicket } from "./financialResourceReadTrust";
import { useFinancialResourceReadTrust } from "./useFinancialResourceReadTrust";
import { ResourceHoldingOverview, ResourceReadEmpty } from "./ResourceReadPresentation";
import { containsCredentialValue } from "./conversationPendingPrivacy";
import { safeSessionGet, safeSessionSet, safeSessionRemove } from "./safeStorage";
import type { DraftActorIdentity } from "./volatileDraftBridge";
import type { ReviewedEvidenceRef, ConversationSuggestion, ConversationTurn } from "./conversationTypes";
import { KnowledgeProductCards } from "./KnowledgeProductCards";
import { PersonalServiceResources, isPersonalResource } from "./PersonalServiceResources";
import {
  conversationTaskSelectionKey,
  makeConversationTaskSelection,
  resolveConversationTaskSelection,
} from "./conversationTaskSelection";
import {
  universalScene,
  taskAcceptancePayload,
  canUseReadonlySuggestion,
  agentCapabilities,
  resourceContextFor,
  canPrepareResourceDraft,
} from "./universalAgent";
import type { AccountIdentity } from "./auth/types";
import type {
  Account,
  BillAnalysis,
  Card,
  Challenge,
  DemoSession,
  Investment,
  Notification,
  Profile,
  Subscription,
  TaskListItem,
  TaskTrace,
  TraceNode,
} from "./types";

const terminalStates = ["SUCCEEDED", "FAILED", "CLOSED"];
const MemoConversationTimeline = memo(ConversationTimeline);
const workingStates = [
  "QUEUED",
  "UNDERSTANDING",
  "PLANNING",
  "PLANNED",
  "POLICY_CHECKED",
  "AUTHORIZED",
  "EXECUTING",
  "COMPENSATING",
];

export default function App({
  bootstrapSession,
  account,
  onLogout,
  onPassword,
  onTestTools,
  onAuthExpired,
  resourceRevision = 0,
  onVolatileDraftChange,
  draftHandoffNonce,
  takeVolatileDraft,
}: {
  bootstrapSession?: DemoSession;
  account?: AccountIdentity;
  onLogout?: () => void;
  onPassword?: () => void;
  onTestTools?: () => void;
  onAuthExpired?: () => void;
  resourceRevision?: number;
  onVolatileDraftChange?: (message: string, actor: DraftActorIdentity) => void;
  draftHandoffNonce?: string | null;
  takeVolatileDraft?: (nonce: string, actor: DraftActorIdentity) => string | null;
}) {
  const { locale, t, explicitPreference } = useCustomerLanguage();
  const customerUiText = t;
  const formalAccount = Boolean(account?.username && account?.account_id);
  const initialSession =
    bootstrapSession ||
    (() => {
      try {
        return JSON.parse(
          safeSessionGet("zx_web_session") || "null",
        ) as DemoSession | null;
      } catch {
        return null;
      }
    })();
  type PendingTaskRequest = {
    message: string;
    idempotency_key: string;
    sandbox_id: string;
  };
  const initialPendingRequest = (() => {
    try {
      const value = JSON.parse(
        safeSessionGet("zx_web_pending_request") || "null",
      ) as PendingTaskRequest | null;
      return value &&
        initialSession &&
        value.sandbox_id === initialSession.sandbox_id &&
        typeof value.message === "string" &&
        typeof value.idempotency_key === "string"
        ? value
        : null;
    } catch {
      return null;
    }
  })();
  const initialOwnerSession = (() => {
    try {
      const value = JSON.parse(
        safeSessionGet("zx_web_owner_session") || "null",
      ) as DemoSession | null;
      return (value?.sandbox_id === initialSession?.sandbox_id ||
        Boolean(value?.account_id && value.account_id === initialSession?.main_account_id)) &&
        typeof value?.token === "string"
        ? value
        : initialSession?.actor_role === "FAMILY_HELPER"
          ? null
          : initialSession;
    } catch {
      return initialSession?.actor_role === "FAMILY_HELPER"
        ? null
        : initialSession;
    }
  })();
  const [session, setSession] = useState<DemoSession | null>(initialSession);
  const [ownerSession, setOwnerSession] = useState<DemoSession | null>(
    initialOwnerSession,
  );
  const [helperSessions, setHelperSessions] = useState<DemoSession[]>(() => {
    try {
      const values = JSON.parse(
        safeSessionGet("zx_web_helper_sessions") || "[]",
      );
      return Array.isArray(values)
        ? values.filter(
            (value) =>
              value.sandbox_id === initialSession?.sandbox_id &&
              value.actor_role === "FAMILY_HELPER" &&
              typeof value.token === "string",
          )
        : [];
    } catch {
      return [];
    }
  });
  const token = session?.token || "";
  const identity = useEndpoint<{
    user_id: string;
    sandbox_id: string;
    role: string;
    has_personal_bank_account: boolean;
  }>("/api/v2/identity", token);
  const actorIsHelper =
    identity.data?.role === "FAMILY_HELPER" ||
    session?.actor_role === "FAMILY_HELPER";
  const hasBankAccount = identity.data?.has_personal_bank_account === true;
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [selectedProfile, setSelectedProfile] = useState(
    initialSession?.profile || "young_professional",
  );
  const [sessionBusy, setSessionBusy] = useState(false);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [cards, setCards] = useState<Card[]>([]);
  const [investments, setInvestments] = useState<Investment[]>([]);
  const [subscriptions, setSubscriptions] = useState<Subscription[]>([]);
  const [subscriptionRead, setSubscriptionRead] = useState<{ token: string; ok: boolean } | null>(null);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [tasks, setTasks] = useState<TaskListItem[]>([]);
  const [bill, setBill] = useState<BillAnalysis | null>(null);
  const [activeId, setActiveId] = useState(
    safeSessionGet("zx_web_task") || "",
  );
  const [trace, setTrace] = useState<TaskTrace | null>(null);
  const activeTraceReadSequence=useRef(0);
  const [activeTraceRead,setActiveTraceRead]=useState<TaskTraceReadReceipt|null>(null);
  const traceRef = useRef(trace);
  traceRef.current = trace;
  const bankGateRef = useRef(new BankInteractionGate());
  bankGateRef.current.forActor(token);
  const [resourceContext, setResourceContext] = useState(
    initialSession?.actor_role === "FAMILY_HELPER" ? "family" : "overview",
  );
  const [resourcesOpen, setResourcesOpen] = useState(false);
  const [resourceLoading, setResourceLoading] = useState(false);
  const [largeText, setLargeText] = useState(
    safeSessionGet("zx_web_large_text") === "true",
  );
  const visibleViewport = useVisibleViewportGeometry();
  const readVisibility = useReadVisibility();
  const resourceReadScope = { token, actorEpoch: bankGateRef.current.epochId,
    actorId: `${session?.sandbox_id || ""}:${session?.user_id || ""}:${session?.actor_role || account?.role || ""}:${account?.account_id || ""}`,
    visibilityEpoch: readVisibility.epoch,
    revision: resourceRevision, visible: readVisibility.visible,
    enabled: !actorIsHelper && hasBankAccount && readVisibility.visible
      && !identity.loading && !identity.error && !identity.cancelled && !identity.paused
      && identity.readReceipt > 0 && identity.readReceipt === identity.readGeneration
      && Boolean(session && typeof identity.data?.user_id === "string" && identity.data.user_id.trim()
        && typeof identity.data.sandbox_id === "string" && identity.data.sandbox_id.trim()
        && identity.data.user_id === session.user_id && identity.data.sandbox_id === session.sandbox_id) };
  const resourceTrust = useFinancialResourceReadTrust(resourceReadScope);
  const accountRead = resourceTrust.view("accounts");
  const cardRead = resourceTrust.view("cards");
  const investmentRead = resourceTrust.view("investments");
  const notificationRead = resourceTrust.view("notifications");
  const visibleHeight = visibleViewport.height;
  const [message, setMessage] = useState(initialPendingRequest?.message || "");
  const [pendingRequest, setPendingRequest] =
    useState<PendingTaskRequest | null>(initialPendingRequest);
  const [page, setPage] = useState<"workspace" | "history">("workspace");
  const [reportOpen, setReportOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const conversationScope = session
    ? `${session.sandbox_id}:${session.user_id}`
    : "";
  const draftActor = session && account?.account_id ? { user_id: session.user_id, sandbox_id: session.sandbox_id,
    account_id: account.account_id, role: session.actor_role || account.role } : null;
  const chat = useConversations(token, conversationScope, actorIsHelper);
  const taskAccessDenied = chat.taskResults.permissionBlocked;
  const [queryRevealRequest, setQueryRevealRequest] = useState<QueryRevealRequest | null>(null);
  const queryRevealActor = `${bankGateRef.current.epochId}:${session?.actor_role || "UNKNOWN"}:${account?.role || "UNKNOWN"}`;
  const queryRevealScope = `${queryRevealActor}:${chat.currentId}:${readVisibility.epoch}`;
  const composerRoutingScope = JSON.stringify([queryRevealActor, conversationScope, chat.currentId, readVisibility.epoch]);
  const composerAuthorityCurrent = Boolean(session && identity.data && typeof session.user_id === "string" && session.user_id.trim()
    && typeof session.sandbox_id === "string" && session.sandbox_id.trim() && identity.data.user_id === session.user_id
    && identity.data.sandbox_id === session.sandbox_id && !identity.loading && !identity.error
    && !identity.cancelled && !identity.paused && identity.readReceipt > 0 && identity.readReceipt === identity.readGeneration);
  const dialogueCallbackScope = JSON.stringify([composerRoutingScope, chat.turns.at(-1),
    identity.data?.role, composerAuthorityCurrent, busy, pendingRequest, chat.pending, chat.loading, chat.pausedRead]);
  // A retained callback from a previous actor/reply cannot send with its old
  // closure even when a DOM element or event is retained by external code.
  const dialogueCallbackState = useRef({ mounted: true, scope: dialogueCallbackScope });
  dialogueCallbackState.current.scope = dialogueCallbackScope;
  useEffect(() => {
    dialogueCallbackState.current.mounted = true;
    return () => { dialogueCallbackState.current.mounted = false; };
  }, []);
  const composerExplicitTaskRef = useRef<ComposerTaskChoice | null>(null);
  const latestProductReply = chat.turns.at(-1);
  const productReadingScope = JSON.stringify([bankGateRef.current.epochId, conversationScope, chat.currentId,
    latestProductReply?.turn_id, identity.data?.role, explicitPreference ? locale : latestProductReply?.reply_language || locale]);
  const productReadingScopeRef = useRef(productReadingScope);
  productReadingScopeRef.current = productReadingScope;
  const [productReading, setProductReading] = useState({ scope: "", key: "" });
  if (composerExplicitTaskRef.current?.scopeKey !== composerRoutingScope) composerExplicitTaskRef.current = null;
  const queryRevealBinding = useRef({ actor: queryRevealActor, conversationId: chat.currentId,
    visibilityEpoch: readVisibility.epoch, visible: readVisibility.visible });
  queryRevealBinding.current = { actor: queryRevealActor, conversationId: chat.currentId,
    visibilityEpoch: readVisibility.epoch, visible: readVisibility.visible };
  useEffect(() => {
    setQueryRevealRequest(previous => previous?.scopeKey === queryRevealScope ? previous : null);
  }, [queryRevealScope]);
  const journeyRef = useRef<JourneyMetrics | null>(null);
  if (!journeyRef.current) journeyRef.current = new JourneyMetrics();
  journeyRef.current.forActor(`${conversationScope}:${token}`);
  const working = busy || chat.writeBusy;
  const [contextPolicy, setContextPolicy] = useState<"AUTO" | "INDEPENDENT">(
    "AUTO",
  );
  const [attachmentOpen, setAttachmentOpen] = useState(false);
  const [attachmentBusy, setAttachmentBusy] = useState(false);
  const [imageSelection, setImageSelection] = useState<{
    id: string; token: string; actorScope: string; files: readonly File[];
  } | null>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const imagePickerOwner = JSON.stringify([token, conversationScope, chat.currentId, page, hasBankAccount, actorIsHelper]);
  const imagePickerOwnerRef = useRef(imagePickerOwner);
  imagePickerOwnerRef.current = imagePickerOwner;
  const imagePickerIntent = useRef<{ owner: string; trigger: Element | null } | null>(null);
  const messageRevisionRef = useRef(0);
  const [reviewedEvidenceRefs, setReviewedEvidenceRefs] = useState<
    ReviewedEvidenceRef[]
  >([]);
  const [error, setError] = useState("");
  const [recovery, setRecovery] = useState<{ token: string; taskId: string; info: CustomerRecovery; operationId: string; delay: RecoveryDelay | null } | null>(null);
  const [recoveryClock, setRecoveryClock] = useState(() => performance.now());
  const [notice, setNotice] = useState("");
  const resourceError = resourceTrust.warning(locale);
  const [pollError, setPollError] = useState("");
  const [unavailableReadRecord, setUnavailableReadRecord] = useState<{ scope: string; conversationId: string; taskId: string; version: number } | null>(null);
  const [simulationOpen, setSimulationOpen] = useState(false);
  const [pollEpoch, setPollEpoch] = useState(0);
  const popoverOwner = `${bankGateRef.current.epochId}:${conversationScope}:${session?.actor_role || account?.role || ""}:${chat.currentId}:${page}`;
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [revisionOpen, setRevisionOpen] = useState(false);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [cancelFeedback, setCancelFeedback] = useState<{ actor: string; taskId: string; kind: "error" | "status"; message: string } | null>(null);
  const [cancelReadBusy, setCancelReadBusy] = useState(false);
  const cancelReadLockRef = useRef(false);
  const [challenge, setChallenge] = useState<Challenge | null>(null);
  const challengeIdRef = useRef(challenge?.challenge_id || null);
  challengeIdRef.current = challenge?.challenge_id || null;
  const [authCode, setAuthCode] = useState("");
  const [authVerified, setAuthVerified] = useState(false);
  const challengeCurrent=useChallengeFreshness(challenge);
  const effectiveAuthVerified=authVerified&&challengeCurrent;
  const [authError, setAuthError] = useState("");
  const [authCodeInvalid, setAuthCodeInvalid] = useState(false);
  const reportDialogRef = useRef<HTMLDivElement>(null);
  const cancelDialogRef = useRef<HTMLDivElement>(null);
  useDialogFocus(reportOpen || cancelOpen, { dialogRef: reportOpen ? reportDialogRef : cancelDialogRef, onDismiss: () => {
    if (reportOpen) setReportOpen(false);
    else if (cancelOpen && !busy) setCancelOpen(false);
  } });
  const activeRef = useRef(activeId);
  activeRef.current = activeId;
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const composingRef = useRef(false);
  const conversationContentRef = useRef<HTMLDivElement>(null);
  const latestMessageRef = useRef<HTMLDivElement>(null);
  const followMessagesRef = useRef(true);
  const readingAnchorRef = useRef<{ anchor: ReadingAnchor | null; scrollTop: number } | null>(null);
  const restoringReadingRef = useRef(false);
  const readingRestoreFrameRef = useRef(0);
  const readingGestureUntilRef = useRef(0);
  const [readingEarlier, setReadingEarlier] = useState(false);
  const selectedTaskFocusRef = useRef<{ taskId: string; trigger: Element | null } | null>(null);
  const [selectedTaskFocusEpoch, setSelectedTaskFocusEpoch] = useState(0);
  const taskOpenHandlerRef = useRef(selectTask);
  taskOpenHandlerRef.current = selectTask;
  const resultPromptHandlerRef = useRef(prepareDraft);
  resultPromptHandlerRef.current = prepareDraft;
  const stableTaskOpen = useCallback((id: string) => taskOpenHandlerRef.current(id), []);
  const stableResultPrompt = useCallback((text: string) => resultPromptHandlerRef.current(text), []);
  const initialInteractionRef = useRef(false);
  useEffect(() => {
    const interacted = () => {initialInteractionRef.current=true;};
    document.addEventListener("pointerdown",interacted,true);
    document.addEventListener("keydown",interacted,true);
    document.addEventListener("input",interacted,true);
    return () => {
      document.removeEventListener("pointerdown",interacted,true);
      document.removeEventListener("keydown",interacted,true);
      document.removeEventListener("input",interacted,true);
    };
  }, []);
  useEffect(() => {
    const focused=document.activeElement;
    if (token && identity.data && mayInitiallyFocusComposer({
      desktop:window.matchMedia("(min-width: 761px)").matches,
      visible:document.visibilityState==="visible",documentFocused:document.hasFocus(),
      focusVacant:focused===document.body||focused===document.documentElement,
      hasDraft:Boolean(message||textareaRef.current?.value),hasTask:Boolean(activeId),
      pending:Boolean(initialPendingRequest||pendingRequest||chat.pending),
      dialogOpen:Boolean(document.querySelector('[role="dialog"], [inert]')),
      userInteracted:initialInteractionRef.current,
    })) {
      textareaRef.current?.focus({ preventScroll: true });
    }
  }, [token, identity.data?.user_id]);
  const previousState = useRef("");
  const tokenRef = useRef(token);
  tokenRef.current = token;
  const previousDisplayLanguage = useRef({ locale, explicitPreference });
  useEffect(() => {
    const previous = previousDisplayLanguage.current;
    previousDisplayLanguage.current = { locale, explicitPreference };
    if (previous.locale === locale && previous.explicitPreference === explicitPreference || !token) return;
    // A display-language change only rereads. It never revises a plan, changes
    // a pending send, rotates a nonce, repeats payment, or verifies a code.
    chat.taskResults.refresh();
    if (activeRef.current) void readCurrentTrace().catch(() => {});
    void refreshResources();
  }, [locale, explicitPreference]);
  useEffect(() => {
    setBusy(false); setRecovery(null); setAuthError(""); setAuthCodeInvalid(false);
    setCancelFeedback(null); setCancelReadBusy(false); cancelReadLockRef.current = false;
    readingAnchorRef.current = null; followMessagesRef.current = true;
    setReadingEarlier(false);
  }, [token]);
  useEffect(() => {
    if (historyNavigation.isHistorical(chat.currentId)) return;
    readingAnchorRef.current = null; followMessagesRef.current = true;
    setReadingEarlier(false);
  }, [chat.currentId]);
  useEffect(() => {
    cancelReadLockRef.current = false; setCancelReadBusy(false); setCancelFeedback(null);
  }, [activeId]);
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      const active = document.activeElement;
      if (!(active instanceof HTMLElement) || !conversationContentRef.current?.parentElement?.contains(active)
        || !active.matches("textarea,input,select") || active.closest("[inert], [hidden]")) return;
      let delta = focusedElementScrollDelta(active.getBoundingClientRect(), visibleViewport, { focused: true });
      const ancestors: HTMLElement[] = [];
      for (let element = active.parentElement; element; element = element.parentElement) {
        if (/(auto|scroll|overlay)/.test(getComputedStyle(element).overflowY)) ancestors.push(element);
      }
      const pageScroller = document.scrollingElement;
      if (pageScroller instanceof HTMLElement && !ancestors.includes(pageScroller)) ancestors.push(pageScroller);
      for (const container of ancestors) {
        if (Math.abs(delta) <= 1 || document.activeElement !== active) break;
        const top = boundedFocusScrollTop(container, delta);
        if (top === null || Math.abs(top - container.scrollTop) <= 1) continue;
        container.scrollTo({ top, behavior: "instant" });
        delta = focusedElementScrollDelta(active.getBoundingClientRect(), visibleViewport, { focused: true });
      }
    });
    return () => cancelAnimationFrame(frame);
  }, [visibleViewport]);
  const resourceAbortRef = useRef<AbortController | null>(null);
  const resourceTicketsRef = useRef<ResourceReadTicket[]>([]);
  const notificationAbortRef = useRef<AbortController | null>(null);
  const pending = trace?.pending_confirmation;
  const taskLocale = explicitPreference ? locale : trace?.assistant_response?.reply_language || trace?.reply_language || locale;
  const taskText = (value: string) => translateCustomerText(value, taskLocale);
  const authScope: AuthRecoveryScope | null = pending && trace ? {
    actorEpoch: bankGateRef.current.epochId, taskId: trace.task_id, childTaskId: pending.task_id,
    confirmationId: pending.confirmation_id, planHash: pending.plan_hash,
    expectedVersion: pending.expected_task_version,
  } : null;
  const authScopeRef = useRef(authScope);
  authScopeRef.current = authScope;
  const authRecovery = useAuthRecovery(authScope, challenge?.challenge_id || null);
  const confirmationValidity = useConfirmationValidity(authScope, pending?.expires_at);
  const linkedTurnVersion = Math.max(
    0,
    ...chat.turns
      .filter((turn) => turn.task_link?.task_id === trace?.task_id)
      .map((turn) => turn.task_link?.version || 0),
  );
  const taskReadUnavailable = unavailableCompletedRead(trace, chat.turns);
  const readAvailabilityRef = useRef({ conversationId: chat.currentId, scope: conversationScope, turns: chat.turns });
  readAvailabilityRef.current = { conversationId: chat.currentId, scope: conversationScope, turns: chat.turns };
  const unavailableReadTaskIds = new Set(Object.entries(chat.taskResults.items)
    .filter(([id, item]) => item.status === "UNAVAILABLE" && unavailableReadSelection(id, item.version, chat.turns))
    .map(([id]) => id));
  if (unavailableReadRecord?.scope === conversationScope && unavailableReadRecord.conversationId === chat.currentId
    && unavailableReadSelection(unavailableReadRecord.taskId, unavailableReadRecord.version, chat.turns))
    unavailableReadTaskIds.add(unavailableReadRecord.taskId);
  if (trace && taskReadUnavailable && unavailableReadSelection(trace.task_id, trace.version, chat.turns))
    unavailableReadTaskIds.add(trace.task_id);
  const unavailableReadSelectionKey = JSON.stringify([...unavailableReadTaskIds].sort());
  const taskViewStale = Boolean(trace && (linkedTurnVersion > trace.version || taskReadUnavailable));
  let selectedTaskEvidencePending = false;
  if (chat.conversation) {
    try {
      const saved = JSON.parse(
        safeSessionGet(
          conversationTaskSelectionKey(
            conversationScope,
            chat.conversation.conversation_id,
          ),
        ) || "null",
      );
      selectedTaskEvidencePending =
        resolveConversationTaskSelection(
          chat.conversation,
          saved,
          conversationScope,
          unavailableReadTaskIds,
        ).reason === "WAITING_EARLIER_SELECTION";
    } catch {}
  }
  const bankActionBlocked =
    busy ||
    chat.writeBusy ||
    Boolean(chat.pending) ||
    hasProcessingTurn(chat.turns) ||
    taskViewStale || taskAccessDenied || !readVisibility.visible || Boolean(recovery?.token === token && recovery.taskId === activeId &&
      (recovery.info.resultUnknown || ["REVIEW_LATEST", "WAIT", "VERIFY_ACCESS"].includes(recovery.info.action)));
  const confirmationRef = useRef(pending?.confirmation_id);
  const confirmKeysRef = useRef(new Map<string, string>());
  confirmationRef.current = pending?.confirmation_id;
  useEffect(() => {
    if (!draftHandoffNonce || !draftActor || !takeVolatileDraft || message.trim() || pendingRequest || chat.pending) return;
    const restored = takeVolatileDraft(draftHandoffNonce, draftActor);
    if (restored !== null) { setMessage(restored); onVolatileDraftChange?.(restored, draftActor); }
  }, [draftHandoffNonce, token]);
  useEffect(() => {
    if (!taskAccessDenied) return;
    setTrace(null); setChallenge(null); setAuthCode(""); setAuthVerified(false);
    setRecovery(null);
  }, [taskAccessDenied]);
  const pendingNode = trace?.nodes.find(
    (node) => node.child_task_id === pending?.task_id,
  );
  const pendingResult = pendingNode?.result;
  const pendingOrderPreview = pendingResult?.order_preview && typeof pendingResult.order_preview === "object" && !Array.isArray(pendingResult.order_preview) ? pendingResult.order_preview : null;
  const pendingScopeObject = pending?.scope && typeof pending.scope === "object" && !Array.isArray(pending.scope) ? pending.scope : null;
  const pendingLifeAction = pendingNode?.action || "";
  const pendingLifeConfirmation = pendingScopeObject?.domain_confirmation;
  const pendingLifeRequired = lifeConfirmationActions.has(pendingLifeAction);
  const pendingLifeReady = !pendingLifeRequired || lifeConfirmationReady(pendingLifeAction,pendingLifeConfirmation);
  const pendingWorkflowAction = pendingNode?.action || "";
  const pendingWorkflowConfirmation = pendingScopeObject?.domain_confirmation;
  const pendingWorkflowRequired = bankingWorkflowConfirmationActions.has(pendingWorkflowAction);
  const pendingWorkflowReady = !pendingWorkflowRequired || bankingWorkflowConfirmationReady(pendingWorkflowAction,pendingWorkflowConfirmation,pending?.expires_at);
  const pendingReviewReady = pendingLifeReady && pendingWorkflowReady
    && (pendingNode?.action !== "AA_PAY" || aaPayReviewReady(pendingScopeObject));
  const pendingCashSourceTail = [pendingResult?.source_card_last4,pendingOrderPreview?.source_card_last4,pendingScopeObject?.source_card_last4]
    .find(value => typeof value === "string" && /^[0-9a-f]{4}$/i.test(value));
  const pendingCashSourceShown = Boolean(pendingCashSourceTail && ["WEALTH_BUY","WEALTH_REDEEM","SUBSCRIPTION_START","SUBSCRIPTION_RENEW"].includes(pendingNode?.action || ""));
  // Display-only deduplication of the same source suffix. The signed scope and
  // conflicting identifiers remain unchanged and are never used from this view.
  const pendingScopeDisplay = pendingCashSourceShown && pendingScopeObject && pendingScopeObject.source_card_last4 === pendingCashSourceTail
    ? Object.fromEntries(Object.entries(pendingScopeObject).filter(([key]) => key !== "source_card_last4")) : pending?.scope;
  const pendingOrderDisplay = pendingCashSourceShown && pendingOrderPreview && pendingOrderPreview.source_card_last4 === pendingCashSourceTail
    ? Object.fromEntries(Object.entries(pendingOrderPreview).filter(([key]) => key !== "source_card_last4")) : pendingResult?.order_preview;
  const previewScope = pendingResult?.preview;
  const pendingTransferScope =
    pending?.scope ||
    (previewScope &&
    typeof previewScope === "object" &&
    !Array.isArray(previewScope)
      ? previewScope
      : pendingResult);
  const currentCapability = agentCapabilities.find(
    (item) => item.id === resourceContext,
  );
  const quickSuggestionEnabled = canUseReadonlySuggestion({
    authenticated: Boolean(token && identity.data),
    hasBankAccount,
    helper: actorIsHelper,
    busy: working,
    activeTask: Boolean(activeId),
    pendingAcceptance: Boolean(pendingRequest),
    hasTrace:
      Boolean(trace) ||
      chat.turns.length > 0 ||
      Boolean(chat.pending) ||
      Boolean(chat.currentId && chat.loading),
    hasDraft: Boolean(message.trim()),
  });
  const welcomeSendFocus = useWelcomeSendFocus({
    scopeKey: JSON.stringify([queryRevealActor, conversationScope, chat.currentId, page, readVisibility.epoch]),
    enabled: quickSuggestionEnabled && !chat.pendingRecoveryIncomplete && !taskAccessDenied
      && !(selectedTaskEvidencePending && contextPolicy === "AUTO") && page === "workspace" && !resourcesOpen && !attachmentOpen,
    visible: readVisibility.visible, composing: () => composingRef.current,
    readingRoot: conversationContentRef,
  });
  const isExtension = EXTENSION_SCENARIOS.some(
    (item) => item.id === resourceContext,
  );
  const totalBalance = accounts.reduce(
    (sum, account) => sum + account.balance_minor,
    0,
  );
  const availableBalance = accounts.reduce(
    (sum, account) =>
      sum + (account.available_balance_minor ?? account.balance_minor),
    0,
  );
  const featuredCard =
    cards.find(
      (card) => card.currency_mode === "CNY_USD" || card.fx_card === true,
    ) || cards[0];
  const additionalCards = cards.filter(
    (card) => card.card_id !== featuredCard?.card_id,
  );
  const cardArrival = useResourceReadArrival({
    scopeKey: JSON.stringify([resourceReadScope.actorEpoch, resourceReadScope.actorId, resourceReadScope.visibilityEpoch, resourceRevision]),
    generation: cardRead.generation, state: cardRead.status, count: cards.length,
    visible: resourceReadScope.enabled && resourcesOpen && (resourceContext === "overview" || resourceContext === "card"),
  });
  useEffect(() => {
    setAttachmentOpen(false);
    setAttachmentBusy(false);
    setImageSelection(null);
    imagePickerIntent.current = null;
    if (imageInputRef.current) imageInputRef.current.value = "";
    setReviewedEvidenceRefs([]);
    setContextPolicy("AUTO");
  }, [token, conversationScope]);
  useEffect(() => {
    setImageSelection(null);
    setAttachmentOpen(false);
    setAttachmentBusy(false);
    imagePickerIntent.current = null;
    if (imageInputRef.current) imageInputRef.current.value = "";
  }, [chat.currentId]);
  useEffect(() => {
    const conversation = chat.conversation;
    if (!conversation || conversation.conversation_id !== chat.currentId)
      return;
    let saved: unknown = null;
    try {
      saved = JSON.parse(
        safeSessionGet(
          conversationTaskSelectionKey(
            conversationScope,
            conversation.conversation_id,
          ),
        ) || "null",
      );
    } catch {}
    const resolved = resolveConversationTaskSelection(
      conversation,
      saved,
      conversationScope,
      unavailableReadTaskIds,
    );
    const candidate = resolved.taskId;
    if (activeRef.current && unavailableReadTaskIds.has(activeRef.current)) {
      if (trace && unavailableCompletedRead(trace, conversation.turns))
        setUnavailableReadRecord({ scope: conversationScope, conversationId: conversation.conversation_id, taskId: trace.task_id, version: trace.version });
      selectedTaskFocusRef.current = null;
      setActiveId(""); setTrace(null); setPollError("");
      safeSessionRemove("zx_web_task");
      return;
    }
    if (candidate && resolved.reason === "NEWER_EXPLICIT_TURN")
      safeSessionSet(
        conversationTaskSelectionKey(
          conversationScope,
          conversation.conversation_id,
        ),
        JSON.stringify(
          makeConversationTaskSelection(
            conversationScope,
            conversation.conversation_id,
            candidate,
            conversation.turns,
          ),
        ),
      );
    if (
      candidate &&
      conversationTaskIds(conversation).has(candidate) &&
      activeRef.current !== candidate
    )
      selectTask(candidate, false);
  }, [
    chat.conversation?.conversation_id,
    chat.conversation?.current_task_id,
    chat.conversation?.version,
    chat.turns.length,
    unavailableReadSelectionKey,
  ]);

  const refreshTasks = useCallback(async () => {
    if (!token) return;
    const items = await request<TaskListItem[] | { items: TaskListItem[] }>(
      "/api/v3/customer/tasks",
      token,
    );
    if (tokenRef.current === token) setTasks(listOf(items));
  }, [token]);

  const refreshResources = useCallback(async () => {
    if (!token || tokenRef.current !== token) return;
    if (actorIsHelper || !hasBankAccount) {
      setResourceLoading(false);
      return;
    }
    const tickets = financialResourceKinds.map(kind => resourceTrust.begin(kind, resourceReadScope));
    if (tickets.some(ticket => ticket === null)) return;
    const reads = tickets as ResourceReadTicket[];
    resourceAbortRef.current?.abort();
    notificationAbortRef.current?.abort();
    const abort = new AbortController();
    resourceAbortRef.current = abort;
    resourceTicketsRef.current = reads;
    setResourceLoading(true);
    setBill(null);
    const options = { signal: abort.signal };
    try {
      const results = await Promise.allSettled([
        request<Account[]>(
          "/api/v1/accounts",
          token,
          "GET",
          undefined,
          undefined,
          options,
        ).then(value => assertFinancialResource("accounts", value)),
        request<Card[]>(
          "/api/v1/cards",
          token,
          "GET",
          undefined,
          undefined,
          options,
        ).then(value => assertFinancialResource("cards", value)),
        request<Investment[]>(
          "/api/v1/investments",
          token,
          "GET",
          undefined,
          undefined,
          options,
        ).then(value => assertFinancialResource("investments", value)),
        request<Subscription[]>(
          "/api/v1/subscriptions",
          token,
          "GET",
          undefined,
          undefined,
          options,
        ).then(value => assertFinancialResource("subscriptions", value)),
        request<Notification[]>(
          "/api/v1/notifications",
          token,
          "GET",
          undefined,
          undefined,
          options,
        ).then(value => assertFinancialResource("notifications", value)),
        request<BillAnalysis>(
          "/api/v1/bills/summary",
          token,
          "GET",
          undefined,
          undefined,
          options,
        ).then(value => assertFinancialResource("bill", value)),
      ]);
      if (abort.signal.aborted || tokenRef.current !== token) return;
      if (
        results[0].status === "rejected" &&
        results[0].reason instanceof ApiError &&
        results[0].reason.status === 401
      ) {
        safeSessionRemove("zx_web_session");
        safeSessionRemove("zx_web_task");
        safeSessionRemove("zx_web_pending_request");
        safeSessionRemove("zx_web_owner_session");
        safeSessionRemove("zx_web_helper_sessions");
        setHelperSessions([]);
        setPendingRequest(null);
        setOwnerSession(null);
        setSession(null);
        setActiveId("");
        setTrace(null);
        setError("会话已过期，请重新登录。");
        if (formalAccount) onAuthExpired?.();
        return;
      }
      const current = reads.map(ticket => resourceTrust.current(ticket));
      reads.forEach((ticket, index) => resourceTrust.settle(ticket, results[index]));
      if (current[0]) {
        if (results[0].status === "fulfilled") setAccounts(results[0].value); else setAccounts([]);
      }
      if (current[1]) {
        if (results[1].status === "fulfilled") setCards(results[1].value); else setCards([]);
      }
      if (current[2]) {
        if (results[2].status === "fulfilled") setInvestments(results[2].value); else setInvestments([]);
      }
      if (current[3]) {
        if (results[3].status === "fulfilled") setSubscriptions(results[3].value); else setSubscriptions([]);
        setSubscriptionRead({ token, ok: results[3].status === "fulfilled" });
      }
      if (current[4] && results[4].status === "fulfilled") setNotifications(results[4].value);
      if (current[5]) {
        if (results[5].status === "fulfilled") setBill(results[5].value); else setBill(null);
      }
    } finally {
      if (resourceAbortRef.current === abort) { resourceTicketsRef.current = []; setResourceLoading(false); }
    }
  }, [token, actorIsHelper, hasBankAccount, resourceReadScope.enabled, resourceReadScope.actorEpoch, resourceReadScope.actorId, resourceReadScope.visibilityEpoch, resourceRevision, readVisibility.visible, resourceTrust]);

  function cancelResourceRead() {
    if (!resourceTicketsRef.current.length) return;
    resourceTicketsRef.current.forEach(ticket => resourceTrust.cancel(ticket));
    resourceTrust.cancelLatest("notifications");
    resourceTicketsRef.current = [];
    resourceAbortRef.current?.abort();
    notificationAbortRef.current?.abort();
    setResourceLoading(false);
    setSubscriptionRead({ token, ok: false });
  }

  const refreshNotifications = useCallback(async () => {
    if (!token || tokenRef.current !== token) return;
    const ticket = resourceTrust.begin("notifications", resourceReadScope);
    if (!ticket) return;
    notificationAbortRef.current?.abort();
    const abort = new AbortController(); notificationAbortRef.current = abort;
    try {
      const items = assertFinancialResource("notifications", await request<Notification[]>("/api/v1/notifications", token,
        "GET", undefined, undefined, { signal: abort.signal }));
      if (abort.signal.aborted) return;
      if (resourceTrust.settle(ticket, { status: "fulfilled", value: items })) setNotifications(items);
    } catch (reason) {
      if (!abort.signal.aborted) resourceTrust.settle(ticket, { status: "rejected", reason });
    } finally {
      if (notificationAbortRef.current === abort) notificationAbortRef.current = null;
    }
  }, [token, resourceReadScope.actorEpoch, resourceReadScope.actorId, resourceReadScope.visibilityEpoch, resourceReadScope.enabled, resourceRevision, readVisibility.visible, resourceTrust]);

  useEffect(() => {
    const abort = new AbortController();
    request<Profile[] | { profiles: Profile[] }>(
      "/api/v2/demo/profiles",
      "",
      "GET",
      undefined,
      undefined,
      { signal: abort.signal },
    )
      .then((value) => setProfiles(listOf(value)))
      .catch(() => {});
    return () => abort.abort();
  }, []);
  useEffect(() => {
    if (identity.errorStatus === 401) {
      resourceAbortRef.current?.abort();
      for (const key of [
        "zx_web_session",
        "zx_web_task",
        "zx_web_pending_request",
        "zx_web_owner_session",
        "zx_web_helper_sessions",
      ])
        safeSessionRemove(key);
      setSession(null);
      setOwnerSession(null);
      setHelperSessions([]);
      setTrace(null);
      setActiveId("");
      setTasks([]);
      setAccounts([]);
      setCards([]);
      setBill(null);
      setInvestments([]);
      setSubscriptions([]);
      setPendingRequest(null);
      setError("当前身份会话已失效，请重新开启账套。");
      if (formalAccount) onAuthExpired?.();
    }
    if (identity.data?.role === "FAMILY_HELPER") {
      setResourceContext("family");
      if (ownerSession?.user_id === session?.user_id) setOwnerSession(null);
    }
    if (identity.data && session && session.actor_role !== identity.data.role) {
      const verifiedSession = { ...session, actor_role: identity.data.role };
      setSession(verifiedSession);
      safeSessionSet("zx_web_session", JSON.stringify(verifiedSession));
    }
  }, [identity.errorStatus, identity.data?.role]);

  useEffect(() => {
    if (!token || !readVisibility.visible) return;
    refreshResources();
    refreshTasks().catch(() => {});
    const interval = window.setInterval(() => {
      if (document.visibilityState !== "visible") return;
      refreshNotifications();
    }, 10000);
    return () => {
      clearInterval(interval);
      resourceAbortRef.current?.abort();
      notificationAbortRef.current?.abort();
    };
  }, [token, refreshResources, refreshNotifications, refreshTasks, resourceRevision, readVisibility.visible, readVisibility.epoch]);

  useEffect(() => {
    if (!token || !activeId || !readVisibility.visible) return;
    let disposed = false;
    let failures = 0;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      let delay = 2500;
      try {
        const data = await request<TaskTrace>(
          `/api/v3/customer/tasks/${encodeURIComponent(activeId)}`,
          token,
          "GET",
          undefined,
          undefined,
          { signal: abort.signal },
        );
        if (disposed) return;
        if (!canApplyTaskTrace(data, activeId, traceRef.current)) {
          timer = setTimeout(poll, 1000); return;
        }
        data.nodes ||= [];
        data.allowed_actions ||= [];
        setTrace(data);
        setActiveTraceRead({actor:token,visibilityEpoch:readVisibility.epoch,taskId:data.task_id,
          version:data.version,sequence:++activeTraceReadSequence.current});
        failures = 0;
        setPollError("");
        const changed =
          previousState.current !==
          `${data.task_id}:${data.state}:${data.version}`;
        if (changed) {
          previousState.current = `${data.task_id}:${data.state}:${data.version}`;
          refreshTasks().catch(() => {});
          if (terminalStates.includes(data.state) || data.state === "PARTIAL")
            refreshResources();
        }
        if (terminalStates.includes(data.state) && !["QUEUED", "PROCESSING"].includes(data.customer_reply_status || "")) return;
        delay = ["QUEUED", "PROCESSING"].includes(data.customer_reply_status || "") || workingStates.includes(data.state) ? 1000 : 3000;
      } catch (err) {
        if (disposed) return;
        const info = customerRecovery(err, { operation: "read" });
        if (info.kind === "HISTORICAL_READ_UNAVAILABLE" && (!traceRef.current
          || traceRef.current.state === "SUCCEEDED" && isQueryOnlyTask(traceRef.current))) {
          // The ordinary history reader owns this local READ recovery. It is
          // not a changed/expired money confirmation and never retries a write.
          const context = readAvailabilityRef.current;
          const version = unavailableReadFailureVersion(err, activeId, context.turns, traceRef.current);
          if (version !== null && context.scope === conversationScope && context.conversationId === chat.currentId) {
            setUnavailableReadRecord({ scope: context.scope, conversationId: context.conversationId, taskId: activeId, version });
            selectedTaskFocusRef.current = null;
            setActiveId(""); safeSessionRemove("zx_web_task");
          }
          setTrace(null); setPollError("");
          return;
        }
        if (err instanceof ApiError && [401, 403, 404].includes(err.status)) setTrace(null);
        setPollError(info.message);
        failures += 1;
        delay = 5000;
      }
      const next = nextReadPollDelay({ visible: document.visibilityState === "visible", failures }, delay);
      if (!disposed && next !== null) timer = setTimeout(poll, next);
    };
    poll();
    return () => {
      disposed = true;
      abort.abort();
      clearTimeout(timer);
    };
  }, [token, activeId, refreshTasks, refreshResources, pollEpoch, readVisibility.visible, readVisibility.epoch]);
  useEffect(() => {
    setTrace(null); setChallenge(null); setAuthCode(""); setAuthVerified(false);
    setAccounts([]); setCards([]); setBill(null); setInvestments([]); setSubscriptions([]);
  }, [readVisibility.epoch]);

  useEffect(() => {
    setChallenge(null);
    setAuthCode("");
    setAuthVerified(false);
    setAuthError("");
    setAuthCodeInvalid(false);
  }, [token, activeId, pending?.confirmation_id, pending?.task_id, pending?.plan_hash, pending?.expected_task_version]);
  useEffect(()=>{
    if(!challenge||challengeCurrent||!confirmationValidity.current)return;
    setAuthVerified(false);setAuthCode("");setAuthCodeInvalid(false);
    setAuthError("本次验证已过期，请重新获取验证码；操作对象和金额未改变。");
  },[challenge?.challenge_id,challengeCurrent,confirmationValidity.current]);
  useEffect(() => {
    if (!pending || confirmationValidity.current || !sameAuthRecoveryScope(authScope, authScopeRef.current)) return;
    // Keep the challenge/recovery identity for read-only UNKNOWN reconciliation.
    // Expiry withdraws local verification, not evidence of a request in flight.
    setAuthVerified(false); setAuthCode(""); setAuthCodeInvalid(false);
  }, [pending?.confirmation_id, confirmationValidity.current]);
  useEffect(
    () => () => {
      window.speechSynthesis?.cancel();
    },
    [token, pending?.confirmation_id],
  );

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !event.defaultPrevented) {
        if (document.querySelector('[role="dialog"]')) return;
        setSidebarOpen(false);
      }
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, []);

  async function startSession() {
    if (formalAccount) return;
    setSessionBusy(true);
    setError("");
    try {
      const result = await request<DemoSession>(
        "/api/v1/demo/sessions",
        "",
        "POST",
        { profile: selectedProfile },
      );
      result.profile ||= selectedProfile;
      safeSessionSet("zx_web_session", JSON.stringify(result));
      safeSessionRemove("zx_web_task");
      safeSessionRemove("zx_web_pending_request");
      setPendingRequest(null);
      changeMessage("");
      setSession(result);
      setOwnerSession(result);
      safeSessionRemove("zx_web_owner_session");
      safeSessionRemove("zx_web_helper_sessions");
      setHelperSessions([]);
      setActiveId("");
      setTrace(null);
      setTasks([]);
      setAccounts([]);
      setCards([]);
      setBill(null);
      setInvestments([]);
      setSubscriptions([]);
      setNotifications([]);
      setPage("workspace");
      setNotice("你的独立账套已准备好，可以开始办理金融任务。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "未能开启账户会话");
    } finally {
      setSessionBusy(false);
    }
  }

  function selectTask(id: string, rememberChoice = true) {
    if (unavailableReadTaskIds.has(id)) return;
    if (rememberChoice) composerExplicitTaskRef.current = !message.trim() && !composingRef.current
      && chat.conversation && conversationTaskIds(chat.conversation).has(id)
      ? { scopeKey: composerRoutingScope, taskId: id, afterSequence: Math.max(0, ...chat.turns.map(turn => turn.sequence)) } : null;
    if (rememberChoice) historyNavigation.cancel();
    const sameTask = activeRef.current === id;
    const retainTrace = canRetainTaskSelectionTrace({ selectedTaskId: id, activeTaskId: activeRef.current,
      trace: traceRef.current, stale: taskViewStale, accessDenied: taskAccessDenied,
      visible: readVisibility.visible, readError: Boolean(pollError) });
    // Explicit record selection can navigate. Automatic context selection must
    // not drag somebody away from the earlier message they are reading.
    selectedTaskFocusRef.current = rememberChoice ? { taskId: id, trigger: document.activeElement } : null;
    if (rememberChoice) setSelectedTaskFocusEpoch(value => value + 1);
    if (
      rememberChoice &&
      chat.conversation &&
      conversationTaskIds(chat.conversation).has(id)
    )
      safeSessionSet(
        conversationTaskSelectionKey(
          conversationScope,
          chat.conversation.conversation_id,
        ),
        JSON.stringify(
          makeConversationTaskSelection(
            conversationScope,
            chat.conversation.conversation_id,
            id,
            chat.turns,
          ),
        ),
      );
    safeSessionRemove("zx_web_pending_request");
    setPendingRequest(null);
    setActiveId(id);
    if (!retainTrace) {
      setTrace(null);
      // The same ID cannot restart its terminal poll through setActiveId alone.
      if (sameTask) setPollEpoch(value => value + 1);
    }
    safeSessionSet("zx_web_task", id);
    setPage("workspace");
    setReportOpen(false);
    setResourcesOpen(false);
    setSidebarOpen(false);
    if (!sameTask) {
      setError("");
      setNotice("");
      setPollError("");
      setRecovery(null);
    }
    setRevisionOpen(false);
    setCancelOpen(false);
  }

  function changeMessage(value: string) {
    messageRevisionRef.current += 1;
    setMessage(value);
    if (draftActor) onVolatileDraftChange?.(value, draftActor);
    // Editing the question does not remove its explicitly attached materials.
    // Identity/history changes, successful send and "remove reference" own
    // that lifecycle; the server revalidates every version/hash at submission.
    if (pendingRequest && value.trim() !== pendingRequest.message) {
      safeSessionRemove("zx_web_pending_request");
      setPendingRequest(null);
      setError("");
      setNotice(
        "任务内容已修改，下一次发送将新建任务。上次请求可在任务中心核对。",
      );
    }
  }

  function selectResourceContext(id: string) {
    setResourceContext(resourceContextFor(id, actorIsHelper));
    setPage("workspace");
    setReportOpen(false);
    setSidebarOpen(false);
    setResourcesOpen(true);
  }

  function prepareDraft(text: string) {
    if (
      !canPrepareResourceDraft({
        busy: working,
        pendingAcceptance: Boolean(pendingRequest || chat.pending),
        currentMessage: message,
        proposedMessage: text,
      })
    ) {
      setNotice(
        "输入框已有草稿或请求正在核对，请先处理当前内容；查看资料不会改变它。",
      );
      return;
    }
    changeMessage(text);
    setResourcesOpen(false);
    setTimeout(() => textareaRef.current?.focus(), 0);
  }

  function focusTaskStep(taskId: string) {
    const currentTrace = traceRef.current;
    if (currentTrace?.task_id !== taskId || activeRef.current !== taskId || taskAccessDenied
      || taskViewStale || !readVisibility.visible || composingRef.current) return false;
    const details = conversationContentRef.current?.querySelector<HTMLElement>(
      `.task-conversation[data-task-id="${CSS.escape(taskId)}"], .task-result-inline[data-task-id="${CSS.escape(taskId)}"]`,
    );
    const visibleTarget = (node?: HTMLElement | null) => Boolean(node && !node.closest("[hidden],[inert],details:not([open])"));
    if (!visibleTarget(details)) return false;
    const presentation = taskFocusPresentation(currentTrace, taskLocale);
    const target = presentation.selectors.flatMap(selector => Array.from(details?.querySelectorAll<HTMLElement>(selector) || []))
      .find(node => visibleTarget(node) && (!node.matches(".confirmation-card")
        || node.dataset.confirmationId === presentation.confirmationId)) || details;
    if (!target) return false;
    historyNavigation.cancel();
    conversationTail.stopFollowingAnimation();
    followMessagesRef.current = false;
    target.tabIndex = -1;
    if (!target.hasAttribute("aria-label") && !target.hasAttribute("aria-labelledby")) {
      if (!target.hasAttribute("role")) target.setAttribute("role", "region");
      target.setAttribute("aria-label", presentation.label);
    }
    target.focus({ preventScroll: true });
    target.scrollIntoView({ block: target.matches(".confirmation-card,.clarification-card") ? "start" : "nearest", behavior: "auto" });
    // Explicit navigation becomes the new reading intention. A later scoped
    // read/poll must preserve this position, not restore the previous anchor.
    saveReadingAnchor();
    setReadingEarlier(true);
    return true;
  }

  function revealCurrentTaskStep() {
    const taskId = trace?.task_id, trigger = document.activeElement;
    setResourcesOpen(false);
    setAttachmentOpen(false);
    window.setTimeout(() => {
      if (taskId && !composingRef.current && (document.activeElement === trigger || document.activeElement === document.body))
        focusTaskStep(taskId);
    }, 0);
  }

  function applyActorSession(next: DemoSession) {
    setAttachmentOpen(false);
    setReviewedEvidenceRefs([]);
    setContextPolicy("AUTO");
    setReportOpen(false);
    setSimulationOpen(false);
    setRevisionOpen(false);
    setCancelOpen(false);
    setChallenge(null);
    setAuthVerified(false);
    resourceAbortRef.current?.abort();
    safeSessionSet("zx_web_session", JSON.stringify(next));
    safeSessionRemove("zx_web_pending_request");
    safeSessionRemove("zx_web_task");
    setPendingRequest(null);
    setSession(next);
    setActiveId("");
    setTrace(null);
    setTasks([]);
    setAccounts([]);
    setCards([]);
    setInvestments([]);
    setSubscriptions([]);
    setNotifications([]);
    setBill(null);
    setResourceContext("family");
    setPage("workspace");
    setResourcesOpen(false);
    changeMessage("");
    setError("");
    setPollError("");
  }
  function switchActor(actor: {
    token: string;
    user_id: string;
    display_name: string;
    role: string;
    expires_at: string;
    sandbox_id?: string;
  }) {
    if (!session) return;
    if (session.actor_role !== "FAMILY_HELPER") {
      setOwnerSession(session);
      safeSessionSet("zx_web_owner_session", JSON.stringify(session));
    }
    const nextSession = {
      ...session,
      token: actor.token,
      user_id: actor.user_id,
      sandbox_id: actor.sandbox_id || session.sandbox_id,
      display_name: actor.display_name,
      actor_role: actor.role,
      expires_at: actor.expires_at,
      username: undefined,
      account_id: null,
      profile: null,
      registered: false,
      main_account_id: session.account_id || ownerSession?.account_id || undefined,
    };
    const savedHelpers = [
      ...helperSessions.filter((value) => value.user_id !== actor.user_id),
      nextSession,
    ];
    setHelperSessions(savedHelpers);
    safeSessionSet(
      "zx_web_helper_sessions",
      JSON.stringify(savedHelpers),
    );
    applyActorSession(nextSession);
    setNotice(
      "已切换到独立家庭协助身份。委托范围由本人确认，银行操作仍需本人办理。",
    );
  }
  function restoreOwner() {
    if (!ownerSession) return;
    applyActorSession(ownerSession);
    setNotice("已返回本人身份，可审阅协助提案并独立确认办理。");
  }
  function restoreHelper(userId: string) {
    const helper = helperSessions.find(
      (value) =>
        value.user_id === userId && value.sandbox_id === session?.sandbox_id,
    );
    if (!helper) return;
    if (session?.actor_role !== "FAMILY_HELPER" && session) {
      setOwnerSession(session);
      safeSessionSet("zx_web_owner_session", JSON.stringify(session));
    }
    applyActorSession(helper);
    setNotice("已切换到已创建的协助身份，仅有效委托范围可用。");
  }

  async function sendLegacyTask(suppliedMessage?: string) {
    const text = (suppliedMessage ?? message).trim();
    if (!text || !token || busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    const draft =
      pendingRequest &&
      pendingRequest.sandbox_id === session!.sandbox_id &&
      pendingRequest.message === text
        ? pendingRequest
        : {
            message: text,
            idempotency_key: crypto.randomUUID(),
            sandbox_id: session!.sandbox_id,
          };
    safeSessionSet("zx_web_pending_request", JSON.stringify(draft));
    setPendingRequest(draft);
    try {
      const result = await request<{
        task_id: string;
        state: string;
        version: number;
      }>(
        "/api/v2/tasks",
        token,
        "POST",
        taskAcceptancePayload(text),
        draft.idempotency_key,
      );
      if (!result?.task_id) throw new Error("受理回执缺少任务ID");
      if (tokenRef.current !== token) return;
      selectTask(result.task_id);
      changeMessage("");
      setTrace({
        schema_version: "2.0",
        task_id: result.task_id,
        version: result.version,
        state: result.state,
        message: text,
        summary: "",
        model_label: "",
        plan_version: 0,
        nodes: [],
        pending_confirmation: null,
        allowed_actions: [],
      });
      refreshTasks().catch(() => {});
    } catch (err) {
      setError(
        `${err instanceof Error ? err.message : "暂未收到受理响应"}。上次请求结果未确定，重试会查回同一任务。`,
      );
    } finally {
      setBusy(false);
    }
  }
  async function sendTask(suppliedMessage?: string, independent = false, retryOriginal = false,
    recipientChoice?: import("./ConversationRecipientChoices").RecipientChoiceSubmission) {
    // Ordinary Send / keyboard gestures verify uncertain acceptance first.
    // Only the explicit secondary recovery action may replay the retained body.
    if (chat.pending && !retryOriginal) { await recoverSensitiveMessage(); return; }
    if (retryOriginal && (!chat.pending || chat.pending.lookup_only || chat.pendingRecoveryIncomplete)) return;
    const sendingDraft = suppliedMessage ?? message;
    const text = (suppliedMessage ?? message).trim();
    if (!text || !token || working || composingRef.current || !readVisibility.visible || document.visibilityState !== "visible") return;
    if (chat.pending?.lookup_only) { await recoverSensitiveMessage(); return; }
    if (!retryOriginal && !independent && selectedTaskEvidencePending && contextPolicy === "AUTO") {
      setError(
        "手选历史任务尚未在已加载消息中核验；请先加载更早消息，或明确改为独立需求，不会自动切换到另一个任务。",
      );
      return;
    }
    historyNavigation.cancel();
    followMessagesRef.current = true;
    readingAnchorRef.current = null;
    setReadingEarlier(false);
    // A pre-v3 unresolved acceptance keeps its original v2 idempotency workflow.
    if (pendingRequest) {
      setQueryRevealRequest(null);
      await sendLegacyTask(text);
      return;
    }
    setError("");
    setNotice("");
    setResourcesOpen(false);
    setAttachmentOpen(false);
    setQueryRevealRequest(null);
    try {
      const composerBindingInput = { conversation: chat.conversation, currentId: chat.currentId,
        trace, activeId, policy: independent ? "INDEPENDENT" : contextPolicy, scopeKey: composerRoutingScope, explicitChoice: composerExplicitTaskRef.current,
        blocked: !composerAuthorityCurrent || taskAccessDenied || taskViewStale || Boolean(pollError || chat.error || chat.pausedRead || chat.loading) } as const;
      const composerBinding = composerExplicitTaskBinding(composerBindingInput) || composerTaskBinding(composerBindingInput);
      const statusBinding = !composerBinding ? composerLifecycleTaskBinding({ message:text, conversation:chat.conversation,
        currentId:chat.currentId, trace, activeId, policy:independent?'INDEPENDENT':contextPolicy, scopeKey:composerRoutingScope,
        blocked:!composerAuthorityCurrent || taskAccessDenied || taskViewStale || Boolean(pollError || chat.error || chat.pausedRead || chat.loading)
          || reviewedEvidenceRefs.length>0 }) : null;
      const linkedTask = composerBinding?.taskId || statusBinding?.taskId;
      const input = retryOriginal && chat.pending ? chat.pending.payload : conversationTurnInput({
        message: text,
        requestId: crypto.randomUUID(),
        policy: independent ? "INDEPENDENT" : contextPolicy,
        conversation: chat.conversation,
        taskId: linkedTask,
        taskVersion: composerBinding?.version || statusBinding?.version,
        explicitTaskSelection: composerBinding?.deliberate === true,
        reviewedRefs: reviewedEvidenceRefs,
        replyLanguage: conversationReplyLanguage(text, explicitPreference ? locale : undefined),
      });
      const ticket = journeyRef.current!.begin();
      if (recipientChoice && !retryOriginal) {
        if (!composerAuthorityCurrent || chat.currentId !== chat.conversation?.conversation_id
          || !chat.turns.some(turn => turn.turn_id === recipientChoice.origin_turn_id && turn.task_link?.task_id === recipientChoice.task_id)) return;
        input.recipient_choice = { origin_turn_id: recipientChoice.origin_turn_id,
          choice_set_id: recipientChoice.choice_set_id, choice_token: recipientChoice.choice_token };
        input.current_task_id = recipientChoice.task_id;
        input.expected_task_version = recipientChoice.task_version;
        input.explicit_task_selection = true;
        input.context_policy = "AUTO";
        input.reviewed_evidence_refs = [];
      }
      const revealBinding = { ...queryRevealBinding.current };
      const freshReveal = !chat.pending && !chat.pendingRecoveryIncomplete && readVisibility.visible;
      const revealStartedAt = performance.now();
      const acceptedTurn = await chat.send(input);
      if (acceptedTurn) {
        const currentRevealBinding = queryRevealBinding.current;
        if (freshReveal && currentRevealBinding.actor === revealBinding.actor
          && currentRevealBinding.visibilityEpoch === revealBinding.visibilityEpoch && currentRevealBinding.visible
          && (!currentRevealBinding.conversationId || currentRevealBinding.conversationId === revealBinding.conversationId
            || currentRevealBinding.conversationId === acceptedTurn.conversation_id)) {
          setQueryRevealRequest({ requestId: input.client_request_id, turnId: acceptedTurn.turn_id,
            scopeKey: `${revealBinding.actor}:${acceptedTurn.conversation_id}:${revealBinding.visibilityEpoch}`,
            startedAtMs: revealStartedAt });
        }
        journeyRef.current!.accepted(ticket, acceptedTurn.turn_id, acceptedTurn.task_link?.task_id,
          acceptedTurn.mode === "CONSULT" ? "CONSULT" : "OTHER");
        const ownsComposerDraft = tokenRef.current === token && textareaRef.current?.value === sendingDraft && !composingRef.current;
        if (ownsComposerDraft) changeMessage("");
        if (!retryOriginal || ownsComposerDraft) {
          setRevisionOpen(false);
          if (input.context_policy === "INDEPENDENT") setContextPolicy("AUTO");
          setReviewedEvidenceRefs([]);
        }
      }
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "消息暂未准备好。");
    }
  }
  function handleConversationSuggestion(suggestion: ConversationSuggestion, turn: ConversationTurn, event: React.MouseEvent<HTMLButtonElement>) {
    const latest = chat.turns.at(-1);
    if (!dialogueCallbackState.current.mounted || dialogueCallbackState.current.scope !== dialogueCallbackScope
      || event.nativeEvent.isTrusted !== true || event.eventPhase === 0
      || !composerAuthorityCurrent || page !== "workspace" || !readVisibility.visible || document.visibilityState !== "visible"
      || working || composingRef.current || message.trim() || pendingRequest || chat.pending || chat.pendingRecoveryIncomplete
      || taskAccessDenied || taskViewStale || chat.pausedRead || chat.loading || latest?.turn_id !== turn.turn_id
      || turn.status !== "ANSWERED" || turn.task_link || turn.conversation_id !== chat.currentId
      || !turn.suggestions?.some(item => JSON.stringify(item) === JSON.stringify(suggestion))
      || turn.response_meta?.suggestion_role !== identity.data?.role) return;
    if (suggestion.behavior === "SEND_READ_ONLY" && suggestion.read_only) {
      void sendTask(suggestion.prompt, true);
    } else if (suggestion.behavior === "FILL_COMPOSER") {
      setContextPolicy("INDEPENDENT");
      prepareDraft(suggestion.prompt);
    }
  }
  function openHistoryTask(id: string) {
    if (imagePickerOwnerRef.current !== imagePickerOwner || !composerAuthorityCurrent || !readVisibility.visible
      || document.visibilityState !== "visible" || composingRef.current) return;
    if (pendingRequest || chat.pending || chat.pendingRecoveryIncomplete || working || attachmentOpen || attachmentBusy || message.trim()) {
      setNotice(locale === "en" ? "Finish the current draft or request before opening another record."
        : "请先处理当前草稿、图片或未确定的请求，再打开其他记录。");
      return;
    }
    if (!chat.fresh()) return;
    selectTask(id);
    setReviewedEvidenceRefs([]);
    setImageSelection(null);
    setContextPolicy("AUTO");
  }
  function openConversation(id: string) {
    if (composingRef.current) return;
    if (pendingRequest || chat.pending || chat.pendingRecoveryIncomplete || working || attachmentBusy || attachmentOpen || message.trim()) {
      setNotice(
        "请先核对当前草稿或未确定的请求；切换对话不会取消银行任务，也不会替你发送内容。",
      );
      return;
    }
    if (!chat.select(id)) return;
    historyNavigation.open(id, document.activeElement);
    setQueryRevealRequest(null);
    setActiveId("");
    setTrace(null);
    safeSessionRemove("zx_web_task");
    setPage("workspace");
    setResourcesOpen(false);
    setAttachmentOpen(false);
    setSidebarOpen(false);
    setContextPolicy("AUTO");
  }
  function newConversation() {
    if (composingRef.current) return;
    if (pendingRequest || chat.pending || chat.pendingRecoveryIncomplete || working || attachmentBusy || attachmentOpen) {
      setNotice(
        "请先核对未确定的发送或图片材料；新会话不撤销已有请求或任务。",
      );
      return;
    }
    if (!chat.fresh()) return;
    historyNavigation.cancel();
    setNotice("");
    setQueryRevealRequest(null);
    setActiveId("");
    setTrace(null);
    safeSessionRemove("zx_web_task");
    setPage("workspace");
    setResourcesOpen(false);
    setAttachmentOpen(false);
    setSidebarOpen(false);
    setContextPolicy("AUTO");
    setReviewedEvidenceRefs([]);
  }
  function sendReadonlySuggestion(text: string, trigger: HTMLButtonElement, gesture?: MouseEvent) {
    if (!quickSuggestionEnabled) return;
    welcomeSendFocus(trigger, () => {
      changeMessage(text);
      void sendTask(text);
    }, gesture);
  }

  async function readCurrentTrace() {
    if (!activeRef.current || tokenRef.current !== token) return;
    const requestedTask = activeRef.current;
    const data = await request<TaskTrace>(
      `/api/v3/customer/tasks/${encodeURIComponent(requestedTask)}`,
      token,
    );
    if (activeRef.current !== requestedTask || tokenRef.current !== token)
      return;
    if (!canApplyTaskTrace(data, requestedTask, traceRef.current))
      throw new ApiError("办理信息尚未完整读取，请重新核对。", 0, "RESPONSE_UNREADABLE");
    const currentTrace = {
      ...data,
      nodes: data.nodes || [],
      allowed_actions: data.allowed_actions || [],
    };
    setTrace(currentTrace);
    setActiveTraceRead({actor:token,visibilityEpoch:readVisibility.epoch,taskId:currentTrace.task_id,
      version:currentTrace.version,sequence:++activeTraceReadSequence.current});
    setPollEpoch((value) => value + 1);
    refreshTasks().catch(() => {});
    refreshResources();
    return currentTrace;
  }

  async function recoverCurrentResult() {
    const actor = token, taskId = activeRef.current;
    try {
      await readCurrentTrace();
      if (tokenRef.current !== actor || activeRef.current !== taskId) return;
      setPollError(""); setError(""); setRecovery(null);
    } catch (failure) {
      if (tokenRef.current === actor && activeRef.current === taskId)
        setPollError(customerRecovery(failure, { operation: "read" }).message);
    }
  }
  async function recoverSensitiveMessage() {
    const actor = token, originalDraft = textareaRef.current?.value || "", originalRequest = chat.pending;
    const recovered = await chat.recoverPending();
    // A verified acknowledgement consumes the original editor text too. Keep
    // a different unsent draft, but never leave the just-accepted payment text
    // ready to be sent again with a fresh request identity.
    const ownsOriginalDraft = originalRequest && (originalRequest.lookup_only ? containsCredentialValue(originalDraft)
      : originalDraft.trim() === originalRequest.payload.message.trim());
    if (recovered && tokenRef.current === actor && textareaRef.current?.value === originalDraft && ownsOriginalDraft) {
      changeMessage("");
      setReviewedEvidenceRefs([]);
    }
  }

  function showOperationRecovery(info: CustomerRecovery, taskId: string) {
    const operationId = crypto.randomUUID();
    const delay = beginRecoveryDelay({ actorEpoch: bankGateRef.current.epochId, taskId, operationId }, info.retryAfterMs, performance.now());
    setRecoveryClock(performance.now());
    setRecovery({ token, taskId, info, operationId, delay }); setError(info.message);
  }

  async function confirmAction() {
    if (
      !trace ||
      trace.state !== "AWAITING_CONFIRMATION" ||
      !pending ||
      !pendingReviewReady ||
      !authScope || !confirmationValidity.isCurrent(authScope) ||
      bankActionBlocked ||
      authRecovery.blocked || !authRecovery.canPost() ||
      (pending.requires_step_up && (!effectiveAuthVerified||!validChallengeResponse(challenge)))
    )
      return;
    const scope = {...authScope};
    const ticket = bankGateRef.current.start(trace.task_id);
    if (!ticket) return;
    if (page === "workspace" && readVisibility.visible && !resourcesOpen && !attachmentOpen
      && chat.turns.at(-1)?.task_link?.task_id === trace.task_id) {
      // Explicitly confirming the latest conversation task resumes its live
      // result reading. Polling/window focus cannot create this intention;
      // any later deliberate upward scroll can cancel it normally.
      historyNavigation.cancel();
      followMessagesRef.current = true;
      readingAnchorRef.current = null;
      readingGestureUntilRef.current = 0;
      setReadingEarlier(false);
      conversationTail.resumeFollowingAfterAction();
    }
    setBusy(true);
    setError("");
    setNotice("");
    setRecovery(null);
    const storedKey = `zx_confirm_${pending.confirmation_id}`;
    const idempotencyKey =
      confirmKeysRef.current.get(pending.confirmation_id) || safeSessionGet(storedKey) || crypto.randomUUID();
    confirmKeysRef.current.set(pending.confirmation_id, idempotencyKey);
    if (confirmKeysRef.current.size > 40) confirmKeysRef.current.delete(confirmKeysRef.current.keys().next().value!);
    safeSessionSet(storedKey, idempotencyKey);
    try {
      await request(
        `/api/v2/tasks/${encodeURIComponent(trace.task_id)}/confirm`,
        token,
        "POST",
        {
          confirmation_id: pending.confirmation_id,
          plan_hash: pending.plan_hash,
          expected_task_version: pending.expected_task_version,
          ...(effectiveAuthVerified && challenge
            ? { auth_challenge_id: challenge.challenge_id }
            : {}),
        },
        idempotencyKey,
        { operation: "confirm" },
      );
      if (!bankGateRef.current.current(ticket, activeRef.current) || !sameAuthRecoveryScope(scope, authScopeRef.current))
        return;
      await readCurrentTrace();
      if (!bankGateRef.current.current(ticket, activeRef.current) || !sameAuthRecoveryScope(scope, authScopeRef.current)) return;
      setNotice("确认已提交；执行结果请查看任务状态。");
    } catch (err) {
      if (!bankGateRef.current.current(ticket, activeRef.current) || !sameAuthRecoveryScope(scope, authScopeRef.current)) return;
      const info = customerRecovery(err, { operation: "confirm", locale: taskLocale });
      showOperationRecovery(info, trace.task_id);
      await readCurrentTrace().catch(() =>
        setPollError("暂时无法查到最新状态，请稍后刷新核对。"),
      );
    } finally {
      if (bankGateRef.current.finish(ticket)) setBusy(false);
    }
  }

  async function beginChallenge() {
    if (!trace || trace.state !== "AWAITING_CONFIRMATION" || !pending || !pendingReviewReady || !authScope || !confirmationValidity.isCurrent(authScope) || bankActionBlocked || !authRecovery.canPost()) return;
    const scope = { ...authScope }, oldChallengeId = challenge?.challenge_id || null;
    const ticket = bankGateRef.current.start(activeId);
    if (!ticket) return;
    setBusy(true);
    setAuthError("");
    setAuthCodeInvalid(false);
    try {
      const result = await request<Challenge>(
        "/api/v2/auth/challenges",
        token,
        "POST",
        {
          task_id: pending.task_id,
          confirmation_id: pending.confirmation_id,
        },
        undefined,
        { operation: "auth", authAction: "challenge" },
      );
      if (
        !bankGateRef.current.current(ticket, activeRef.current) ||
        !sameAuthRecoveryScope(scope, authScopeRef.current) || challengeIdRef.current !== oldChallengeId || !confirmationValidity.isCurrent(scope)
      )
        return;
      if (!validChallengeResponse(result)) throw new ApiError("验证信息尚未完整核验，请重新查看当前办理。", 0, "RESPONSE_UNREADABLE");
      setChallenge(result);
      setAuthCode("");
      setAuthVerified(false);
      authRecovery.clear();
    } catch (err) {
      if (bankGateRef.current.current(ticket, activeRef.current) && sameAuthRecoveryScope(scope, authScopeRef.current)
        && challengeIdRef.current === oldChallengeId) {
        const info = customerRecovery(err, { operation: "auth", authAction: "challenge", locale: taskLocale });
        if (authRecovery.fail(scope, oldChallengeId, "challenge", info)) {
          setAuthVerified(false); setAuthError(info.message);
        }
      }
    } finally {
      if (bankGateRef.current.finish(ticket)) setBusy(false);
    }
  }

  async function verifyChallenge() {
    if (!trace || trace.state !== "AWAITING_CONFIRMATION" || !challenge || !pendingReviewReady || !authScope || !confirmationValidity.isCurrent(authScope) || !challengeCurrent || !validChallengeResponse(challenge) || !authCode.trim() || bankActionBlocked || !authRecovery.canPost()) return;
    const scope = { ...authScope };
    const ticket = bankGateRef.current.start(activeId);
    if (!ticket) return;
    setBusy(true);
    setAuthError("");
    setAuthCodeInvalid(false);
    try {
      const result = await request<{ verified?: boolean; error?: string; challenge_id?: string }>(
        `/api/v2/auth/challenges/${encodeURIComponent(challenge.challenge_id)}/verify`,
        token,
        "POST",
        { code: authCode.trim() },
        undefined,
        { operation: "auth", authAction: "verify" },
      );
      if (
        !bankGateRef.current.current(ticket, activeRef.current) ||
        !sameAuthRecoveryScope(scope, authScopeRef.current) || challengeIdRef.current !== challenge.challenge_id || !confirmationValidity.isCurrent(scope)
      )
        return;
      if (!validChallengeResponse(challenge)) {
        setAuthVerified(false);setAuthCode("");
        const info = customerRecovery({ code: "AUTH_CHALLENGE_INVALID" }, { operation: "auth", authAction: "verify", locale: taskLocale });
        authRecovery.fail(scope, challenge.challenge_id, "verify", info); setAuthError(info.message);
        return;
      }
      if (!verificationConfirmed(result, challenge.challenge_id)) {
        setAuthVerified(false);
        const code = result?.verified === false && typeof result.error === "string"
          ? result.error : "RESPONSE_UNREADABLE";
        const info = customerRecovery({ code }, { operation: "auth", authAction: "verify", locale: taskLocale });
        authRecovery.fail(scope, challenge.challenge_id, "verify", info);
        setAuthError(info.message); setAuthCodeInvalid(info.kind === "CREDENTIAL_INVALID");
        return;
      }
      setAuthVerified(true);
      authRecovery.clear();
    } catch (err) {
      if (bankGateRef.current.current(ticket, activeRef.current) && sameAuthRecoveryScope(scope, authScopeRef.current)
        && challengeIdRef.current === challenge.challenge_id) {
        const info = customerRecovery(err, { operation: "auth", authAction: "verify", locale: taskLocale });
        if (authRecovery.fail(scope, challenge.challenge_id, "verify", info)) {
          setAuthVerified(false); setAuthError(info.message); setAuthCodeInvalid(info.kind === "CREDENTIAL_INVALID");
        }
      }
    } finally {
      if (bankGateRef.current.finish(ticket)) setBusy(false);
    }
  }

  async function readLatestAuthConfirmation() {
    const scope = authScope && { ...authScope };
    if (!scope || bankActionBlocked || !authRecovery.canRead) return;
    const restored = await authRecovery.readLatest(async () => {
      const data = await readCurrentTrace();
      if (!data || !sameAuthRecoveryScope(scope, authScopeRef.current)) return false;
      const next = data.pending_confirmation;
      const sameConfirmation = Boolean(next && next.task_id === scope.childTaskId
        && next.confirmation_id === scope.confirmationId && next.plan_hash === scope.planHash
        && next.expected_task_version === scope.expectedVersion);
      if (!sameConfirmation) return false;
      // A task trace is not a verification grant. Withdraw the uncertain local
      // challenge and let the user decide whether to request a fresh code.
      setChallenge(null); setAuthCode(""); setAuthVerified(false); setAuthCodeInvalid(false);
      return true;
    }, error => customerRecovery(error, { operation: "read", locale: taskLocale }));
    if (restored && sameAuthRecoveryScope(scope, authScopeRef.current))
      setAuthError(confirmationValidity.isCurrent(scope) ? authRecoveryText(taskLocale, "ready")
        : confirmationValidityText(taskLocale, confirmationValidity.state === "UNAVAILABLE" ? "UNAVAILABLE" : "EXPIRED"));
  }

  function readConfirmationAloud() {
    if (!pending || !trace) return;
    const synth = window.speechSynthesis;
    if (!synth || typeof SpeechSynthesisUtterance === "undefined") {
      setNotice("当前环境不支持朗读，请继续核对确认卡中的文字。");
      return;
    }
    const voice = synth
      .getVoices()
      .find(
        (item) => item.localService && item.lang.toLowerCase().startsWith(taskLocale === "en" ? "en" : "zh"),
      );
    if (!voice) {
      setNotice(taskLocale === "en" ? "No local English voice is available. Review the confirmation text." : "当前浏览器没有可用的本地中文语音，请继续阅读文字摘要。");
      return;
    }
    const node = trace.nodes.find(
      (item) => item.child_task_id === pending.task_id,
    );
    const utterance = new SpeechSynthesisUtterance(
      pendingWorkflowRequired ? bankingWorkflowConfirmationSpeech(pendingWorkflowAction,pendingWorkflowConfirmation,pending.expires_at,taskLocale)
        : pendingLifeRequired ? lifeConfirmationSpeech(pendingLifeAction,pendingLifeConfirmation,taskLocale) : confirmationSpeech(node, pending, taskLocale),
    );
    utterance.voice = voice;
    utterance.lang = voice.lang;
    utterance.rate = 0.86;
    utterance.onerror = () => {
      if (
        tokenRef.current === token &&
        confirmationRef.current === pending.confirmation_id
      )
        setNotice("朗读未完成，请以确认卡文字为准继续核对。");
    };
    synth.cancel();
    synth.speak(utterance);
    setNotice("已请求本地朗读操作、目标与金额；确认仍以页面文字为准。");
  }

  async function cancelTask() {
    if (!trace || bankActionBlocked) return;
    const ticket = bankGateRef.current.start(trace.task_id);
    if (!ticket) return;
    setBusy(true);
    setError("");
    setCancelFeedback(null);
    try {
      await request(
        `/api/v2/tasks/${encodeURIComponent(trace.task_id)}/cancel`,
        token,
        "POST",
        { expected_task_version: trace.version },
      );
      if (!bankGateRef.current.current(ticket, activeRef.current)) return;
      setCancelOpen(false);
      await readCurrentTrace();
      if (!bankGateRef.current.current(ticket, activeRef.current)) return;
      setNotice("已提交撤销请求。已完成步骤的回执仍保留。");
    } catch (err) {
      if (!bankGateRef.current.current(ticket, activeRef.current)) return;
      const info = customerRecovery(err, { operation: "submit" });
      setCancelFeedback({ actor: token, taskId: trace.task_id, kind: "error", message: info.message });
      showOperationRecovery(info, trace.task_id);
      await readCurrentTrace().catch(() => {});
    } finally {
      if (bankGateRef.current.finish(ticket)) setBusy(false);
    }
  }

  async function readCancelResult() {
    if (cancelReadLockRef.current || busy || !activeRef.current) return;
    const actor = token, taskId = activeRef.current;
    cancelReadLockRef.current = true; setCancelReadBusy(true);
    try {
      await readCurrentTrace();
      if (tokenRef.current !== actor || activeRef.current !== taskId) return;
      setRecovery(null); setError(""); setPollError("");
      setCancelFeedback({ actor, taskId, kind: "status", message: "最新办理信息已读取，请重新核对当前状态。未再次提交撤销。" });
    } catch (failure) {
      if (tokenRef.current !== actor || activeRef.current !== taskId) return;
      setCancelFeedback({ actor, taskId, kind: "error", message: customerRecovery(failure, { operation: "read" }).message });
    } finally {
      if (tokenRef.current === actor && activeRef.current === taskId) {
        cancelReadLockRef.current = false; setCancelReadBusy(false);
      }
    }
  }

  async function requestCompensation(node: TraceNode) {
    if (
      !trace ||
      bankActionBlocked ||
      !node.can_compensate ||
      !node.operation_id ||
      !trace.allowed_actions.includes("compensate")
    )
      return;
    const ticket = bankGateRef.current.start(trace.task_id);
    if (!ticket) return;
    setBusy(true);
    setError("");
    try {
      const result = await request<{
        task_id: string;
        state: string;
        version: number;
      }>(
        `/api/v2/tasks/${encodeURIComponent(trace.task_id)}/compensate`,
        token,
        "POST",
        {
          expected_task_version: trace.version,
          operation_id: node.operation_id,
        },
      );
      if (!bankGateRef.current.current(ticket, activeRef.current)) return;
      selectTask(result.task_id);
      setNotice(
        "已生成独立的卡片状态恢复提案，请在新任务中重新核对和确认；解锁仍需二次验证。",
      );
      refreshTasks().catch(() => {});
    } catch (err) {
      if (!bankGateRef.current.current(ticket, activeRef.current)) return;
      const info = customerRecovery(err, { operation: "submit" });
      showOperationRecovery(info, trace.task_id);
      await readCurrentTrace().catch(() => {});
    } finally {
      if (bankGateRef.current.finish(ticket)) setBusy(false);
    }
  }

  const currentProfile = profiles.find(
    (item) => (item.profile || item.key || item.id) === selectedProfile,
  );
  const profileName = customerName(
    currentProfile?.label ||
    currentProfile?.name ||
    profileLabels[selectedProfile] ||
    "个人账户");
  const canCancel = trace?.allowed_actions.includes("cancel");
  const canRevise = trace?.allowed_actions.includes("revise");
  const latestTurn = chat.turns.at(-1);
  const availableRelease = useReleaseUpdate();
  const inlineResultTurn = inlineReplyTurn(trace, chat.turns);
  const currentDetailsOwner = requestDetailsTurn(trace,chat.turns);
  const historicalReadOperationIds = !chat.taskResults.permissionBlocked && !chat.taskResults.paused
    ? chat.turns.filter(turn=>turn.turn_id!==currentDetailsOwner).flatMap(turn=>{
      const saved=historicalReadForTurn(turn,chat.taskResults.historyReads?.items[turn.turn_id]);
      return saved && trace && saved.task_id===trace.task_id?saved.nodes.map(node=>node.operation_id).filter((id):id is string=>typeof id==="string"):[];
    }) : [];
  const inlineClarificationTurn = trace?.state === "NEED_CLARIFICATION" && !taskAccessDenied && !taskViewStale && !pollError
    ? [...chat.turns].reverse().find(turn => turn.status === "ANSWERED" && turn.task_link?.task_id === trace.task_id
      && ["NEW_TASK", "REVISE", "CLARIFY"].includes(turn.mode || "") && turn.task_link.version <= trace.version)?.turn_id : undefined;
  const currentTaskTurn = latestTurn?.task_link?.task_id === trace?.task_id ? latestTurn : null;
  const externalTaskMotion = useCustomerMotion({
    snapshot: trace && currentTaskTurn ? {
      scopeKey: queryRevealScope, turnId: currentTaskTurn.turn_id, taskId: trace.task_id, version: trace.version,
      state: trace.state, status: currentTaskTurn.status,
      visible: !inlineResultTurn && !resourcesOpen && readVisibility.visible && !taskAccessDenied,
      // Public structure only. Motion receipts never contain summaries, amounts, nonces or confirmation IDs.
      contentKey: JSON.stringify([trace.state, trace.nodes.map(node => node.status), trace.outcome?.kind,
        trace.outcome?.verified === true, Boolean(trace.pending_confirmation), Boolean(trace.clarification_questions?.length)]),
      urgent: trace.nodes.some(node => ["FAILED", "UNKNOWN"].includes(node.status)) || Boolean(trace.outcome && !trace.outcome.verified
        && terminalStates.includes(trace.state)),
    } : null,
    liveRequest: currentTaskTurn ? queryRevealRequest : null,
    allowMotion: !readingEarlier && !chat.earlierLoading && !resourcesOpen && readVisibility.visible,
    blocked: taskAccessDenied || taskViewStale || Boolean(chat.pending?.lookup_only || chat.pendingRecoveryIncomplete),
    rereading: readingEarlier || chat.earlierLoading,
    sensitiveEditing: composingRef.current || Boolean(authCode.trim()),
  });
  const localRecovery = recovery?.token === token && recovery.taskId === trace?.task_id ? recovery.info : null;
  const confirmationResultUnknown = Boolean(localRecovery?.resultUnknown || authRecovery.record?.info.resultUnknown);
  const customerPlanPaused = !taskAccessDenied && !taskViewStale && !pollError && !localRecovery
    && !confirmationResultUnknown && isCustomerPausedPlan(trace);
  const localCancelFeedback = cancelFeedback?.actor === token && cancelFeedback.taskId === trace?.task_id ? cancelFeedback : null;
  const waitingRecovery = recoveryDelayState(recovery?.delay ?? null, { actorEpoch: bankGateRef.current.epochId, taskId: activeId, operationId: recovery?.operationId || "" }, recoveryClock);
  useEffect(() => {
    if (!recovery?.delay) return;
    let timer: ReturnType<typeof setTimeout>;
    const deadline = recovery.delay.deadlineMs;
    const tick = () => {
      const now = performance.now(); setRecoveryClock(now);
      if (now < deadline) timer = setTimeout(tick, Math.min(1000, deadline - now));
    };
    tick(); return () => clearTimeout(timer);
  }, [recovery?.delay?.deadlineMs]);
  function saveReadingAnchor() {
    const root = conversationContentRef.current;
    if (!root) return;
    const viewport = { top: root.getBoundingClientRect().top, height: root.clientHeight, scrollTop: root.scrollTop, scrollHeight: root.scrollHeight };
    const turns = collectReadingTurns(root);
    readingAnchorRef.current = { anchor: captureReadingAnchor({ actor: token, conversationId: chat.currentId }, viewport, turns), scrollTop: root.scrollTop };
  }
  function preserveReadingAnchor() {
    const root = conversationContentRef.current, saved = readingAnchorRef.current;
    if (!root || !saved || resourcesOpen || root.clientHeight <= 0 || followMessagesRef.current || taskAccessDenied
      || performance.now() < readingGestureUntilRef.current) return;
    const viewport = { top: root.getBoundingClientRect().top, height: root.clientHeight, scrollTop: root.scrollTop, scrollHeight: root.scrollHeight };
    const turns = collectReadingTurns(root);
    const top = resolveReadingAnchor(saved.anchor, { actor: token, conversationId: chat.currentId }, viewport, turns, saved.scrollTop);
    if (top !== null && Math.abs(top - root.scrollTop) > 1) {
      cancelAnimationFrame(readingRestoreFrameRef.current);
      restoringReadingRef.current = true; root.scrollTop = top;
      readingRestoreFrameRef.current = requestAnimationFrame(() => { readingRestoreFrameRef.current = 0; restoringReadingRef.current = false; });
    }
  }
  const historyNavigation = useHistoricalConversationReading({
    root: conversationContentRef, actor: conversationScope, conversationId: chat.currentId,
    turns: chat.turns, loading: chat.loading,
    enabled: page === "workspace" && readVisibility.visible && !resourcesOpen && !attachmentOpen
      && !taskAccessDenied && !chat.taskResults.permissionBlocked && !chat.earlierLoading,
    editing: () => composingRef.current || Boolean(message.trim() || authCode.trim()),
    followIntent: followMessagesRef, restoring: restoringReadingRef,
    stopFollowing: () => conversationTail.stopFollowingAnimation(), saveReadingAnchor,
    onReadingEarlier: setReadingEarlier,
  });
  const conversationTail = useConversationTail({
    root: conversationContentRef, scopeKey: `${conversationScope}:${chat.currentId}`,
    followIntent: followMessagesRef, restoring: restoringReadingRef,
    guards: { enabled: page === "workspace" && !resourcesOpen && readVisibility.visible && !taskAccessDenied,
      readingEarlier, loadingEarlier: chat.earlierLoading },
    composing: () => composingRef.current,
    editing: () => {
      const active = document.activeElement;
      if (active instanceof HTMLElement && conversationContentRef.current?.contains(active)
        && active.matches('button,summary,[tabindex="-1"]')) return true;
      if (!(active instanceof HTMLElement) || !active.matches('input,textarea,select,[contenteditable="true"]')) return false;
      // Empty idle composer focus is not typing. Verification/select controls and drafts are protected.
      return active instanceof HTMLTextAreaElement ? Boolean(active.value.trim()) : true;
    },
    onReadingEarlier: value => { setReadingEarlier(value); if (!value) readingAnchorRef.current = null; },
    onSaveAnchor: saveReadingAnchor,
    onPreserveAnchor: preserveReadingAnchor,
    onUserGesture: () => {
      readingGestureUntilRef.current = performance.now() + 800;
      cancelAnimationFrame(readingRestoreFrameRef.current); readingRestoreFrameRef.current = 0;
      restoringReadingRef.current = false;
    },
  });
  const materialNavigation = useMaterialNavigation({open:attachmentOpen,setOpen:setAttachmentOpen,
    context:{actor:conversationScope,conversationId:chat.currentId},root:conversationContentRef,
    visible:readVisibility.visible && !resourcesOpen && !taskAccessDenied,composing:()=>composingRef.current,
    followIntent:followMessagesRef,stopFollowing:conversationTail.stopFollowingAnimation,
    setReadingEarlier,clearReadingAnchor:()=>{readingAnchorRef.current=null;},readingEarlier});
  useFreshReplyReading({root:conversationContentRef,scopeKey:queryRevealScope,request:queryRevealRequest,
    turn:chat.turns.at(-1),enabled:page==="workspace"&&readVisibility.visible&&!resourcesOpen&&!attachmentOpen
      &&!taskAccessDenied&&!chat.taskResults.permissionBlocked&&!chat.earlierLoading,
    editing:()=>composingRef.current||Boolean(message.trim()||authCode.trim()),
    followIntent:followMessagesRef,restoring:restoringReadingRef,
    stopFollowing:conversationTail.stopFollowingAnimation,saveReadingAnchor});
  useLayoutEffect(() => {
    preserveReadingAnchor();
  }, [chat.turns, chat.taskResults.items, visibleHeight, taskAccessDenied, resourcesOpen]);
  useEffect(() => () => { cancelAnimationFrame(readingRestoreFrameRef.current); restoringReadingRef.current = false; }, []);
  useEffect(() => {
    const root = conversationContentRef.current;
    if (!root || resourcesOpen || !journeyRef.current) return;
    const observers=new Map<number,IntersectionObserver>();
    const registered=new Map<number,HTMLElement>();
    let frame=0,disposed=false;
    const registerVisibleTargets=()=>{
    if(disposed)return;
    for (const target of journeyRef.current!.targets()) {
      const turn = chat.turns.find(item => item.turn_id === target.turnId);
      if (!turn || turn.status !== "ANSWERED") continue;
      // A 202 acceptance is usually QUEUED without a task link. Resolve it from
      // the later authorized answered turn rather than freezing the empty ID.
      const currentTaskId = turn.task_link?.task_id || target.taskId;
      const result = currentTaskId ? (currentTaskId === trace?.task_id ? trace : chat.taskResults.items[currentTaskId]?.trace) : null;
      const turnContainer=root.querySelector<HTMLElement>(`[data-turn-id="${CSS.escape(target.turnId)}"]`);
      const latestAction=currentTaskId?[...chat.turns].reverse().find(item=>item.task_link?.task_id===currentTaskId
        &&["NEW_TASK","REVISE","CLARIFY"].includes(item.mode||"")):null;
      const element = turn.mode==="CONSULT"
        ? turnContainer?.querySelector<HTMLElement>(".turn-answer")
        : currentTaskId
        ? turnContainer?.querySelector<HTMLElement>(`[data-task-id="${CSS.escape(currentTaskId)}"] [data-outcome-kind]:not([data-outcome-kind="PROGRESS"]) .query-reply-intro, [data-task-id="${CSS.escape(currentTaskId)}"] [data-outcome-kind]:not([data-outcome-kind="PROGRESS"]) .receipt-description`)
          || (trace?.task_id===currentTaskId&&latestAction?.turn_id===target.turnId&&!isQueryOnlyTask(trace)
            ? root.querySelector<HTMLElement>(`.task-conversation[data-task-id="${CSS.escape(currentTaskId)}"] [data-outcome-kind]:not([data-outcome-kind="PROGRESS"]) .receipt-description`):null)
        : ["NEW_TASK", "REVISE", "CLARIFY"].includes(turn.mode || "") ? null : turnContainer?.querySelector<HTMLElement>(".turn-answer");
      // Reserved full-size prose is accessible immediately, but is not yet a
      // fully visible visual reply. Do not count its hidden layout source as
      // finished UI latency; the final overlay removal triggers registration.
      if (element?.querySelector('[data-reply-revealing="true"]')) {
        observers.get(target.ticket.id)?.disconnect(); observers.delete(target.ticket.id);
        registered.delete(target.ticket.id); continue;
      }
      if (!element||registered.get(target.ticket.id)===element) continue;
      observers.get(target.ticket.id)?.disconnect();
      registered.set(target.ticket.id,element);
      const complete = turn.mode === "CONSULT" || Boolean(result && terminalStates.includes(result.state));
      const observer = new IntersectionObserver(entries => {
        if(disposed||registered.get(target.ticket.id)!==element)return;
        if(element.querySelector('[data-reply-revealing="true"]'))return;
        if (!entries.some(entry => entry.isIntersecting && readableCustomerRegion({
          intersectionWidth: entry.intersectionRect.width, intersectionHeight: entry.intersectionRect.height,
          elementWidth: entry.boundingClientRect.width, elementHeight: entry.boundingClientRect.height,
          textLength: entry.target.textContent?.trim().length || 0,
          hidden: document.visibilityState === "hidden" || Boolean(entry.target.closest("[hidden]")),
          inert: Boolean(entry.target.closest("[inert]")), accessBlocked: taskAccessDenied,
          opacity: Number.parseFloat(getComputedStyle(entry.target).opacity),
        }))) return;
        const sample = journeyRef.current?.displayed(target.ticket, complete, turn.mode === "CONSULT" ? "CONSULT" : result && isQueryOnlyTask(result) ? "QUERY" : "OTHER");
        if (sample) {
          window.dispatchEvent(new CustomEvent("zx-customer-journey", { detail: sample }));
          const diagnostic=journeyDiagnostic(sample,journeyRef.current?.coverage());
          if(diagnostic)console.debug("zx-customer-visible-journey-v1",JSON.stringify(diagnostic));
          observer.disconnect();observers.delete(target.ticket.id);
        }
      }, { root, threshold: [0, .1, .5, 1] });
      observer.observe(element); observers.set(target.ticket.id,observer);
    }
    };
    registerVisibleTargets();
    // A child's scoped query-reveal timer can mount the result after the
    // parent's effect. Register that actual region, not the earlier wait text.
    const mutations=new MutationObserver(()=>{
      if(frame||disposed)return;
      frame=requestAnimationFrame(()=>{frame=0;registerVisibleTargets();});
    });
    mutations.observe(root,{childList:true,subtree:true});
    return () => {disposed=true;cancelAnimationFrame(frame);mutations.disconnect();observers.forEach(observer => observer.disconnect());};
  }, [chat.turns, chat.taskResults.items, trace, resourcesOpen, taskAccessDenied]);
  useEffect(() => {
    const intent = selectedTaskFocusRef.current;
    if (!trace || intent?.taskId !== trace.task_id || resourcesOpen || page !== "workspace"
      || taskAccessDenied || taskViewStale || !readVisibility.visible) return;
    const frame = window.requestAnimationFrame(() => {
      if (selectedTaskFocusRef.current !== intent) return;
      if (composingRef.current || document.activeElement !== intent.trigger && document.activeElement !== document.body) {
        selectedTaskFocusRef.current = null; return;
      }
      if (focusTaskStep(intent.taskId)) selectedTaskFocusRef.current = null;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [trace?.task_id, trace?.version, resourcesOpen, chat.taskResults.items, selectedTaskFocusEpoch,
    page, taskAccessDenied, taskViewStale, readVisibility.visible]);
  const accountOverview = (
    <section className="asset-grid" aria-label={customerUiText("账户资产概览")}>
      <div className="asset-card balance-card">
        <div className="asset-label">
          <span><CustomerText text="人民币可用余额" /></span>
          <Icon name="wealth" size={19} />
        </div>
        <strong className="asset-value">
          {accountRead.status === "READY" && accounts.length ? money(availableBalance) : "—"}
        </strong>
        <div className="asset-foot">
          <span>
            {accountRead.status === "READY" && accounts.length
              ? `${locale === "en" ? "Total cash" : "现金总额"} ${money(totalBalance)}`
              : accountRead.status === "LOADING" || identity.loading
                ? customerUiText("正在读取账户")
                : customerUiText("未加载账户")}
          </span>
          <button onClick={() => refreshResources()} aria-label={customerUiText("刷新账户数据")}>
            <Icon name="refresh" size={14} />
          </button>
        </div>
      </div>
      <ResourceHoldingOverview records={investments} read={investmentRead} />
      <SubscriptionOverviewCard records={subscriptions}
        loadState={resourceTrust.view("subscriptions").status === "LOADING" ? "LOADING" : subscriptionRead?.token === token && subscriptionRead.ok && resourceTrust.ready("subscriptions") ? "READY" : "UNAVAILABLE"}
        onOpen={() => selectResourceContext("subscription")} />
      <div className="asset-card">
        <div className="asset-label">
          <span><CustomerText text="金融任务" /></span>
          <Icon name="workflow" size={19} />
        </div>
        <strong className="asset-value number">
          {tasks.length}
          <small><CustomerText text="项" /></small>
        </strong>
        <div className="asset-foot">
          <span>
            {
              tasks.filter((item) => !terminalStates.includes(item.state))
                .length
            }{" "}<CustomerText text="项进行中" /></span>
          <button disabled={attachmentOpen || attachmentBusy} onClick={() => setPage("history")}><CustomerText text="全部" /><Icon name="chevron" size={12} />
          </button>
        </div>
      </div>
    </section>
  );
  const composerClarification = composerClarificationContext({ conversation: chat.conversation, currentId: chat.currentId,
    trace, activeId, policy: contextPolicy, scopeKey: composerRoutingScope, explicitChoice: composerExplicitTaskRef.current,
    blocked: !composerAuthorityCurrent || working || taskAccessDenied || taskViewStale || Boolean(chat.pending || pendingRequest || chat.pendingRecoveryIncomplete
      || pollError || chat.error || chat.pausedRead || chat.loading) || page !== "workspace" || !readVisibility.visible });
  const composerRevision = revisionOpen && trace?.allowed_actions.includes("revise") && composerTaskBinding({
    conversation: chat.conversation, currentId: chat.currentId, trace, activeId, policy: contextPolicy,
    scopeKey: composerRoutingScope, explicitChoice: composerExplicitTaskRef.current,
    blocked: !composerAuthorityCurrent || working || taskAccessDenied || taskViewStale || Boolean(chat.pending || pendingRequest
      || chat.pendingRecoveryIncomplete || pollError || chat.error || chat.pausedRead || chat.loading)
      || page !== "workspace" || !readVisibility.visible,
  });
  const materialDraftRevision = messageRevisionRef.current;
  const materialComposerCurrent = useRef({ message, working, pending: Boolean(pendingRequest || chat.pending),
    authorized: composerAuthorityCurrent, visible: readVisibility.visible });
  materialComposerCurrent.current = { message, working, pending: Boolean(pendingRequest || chat.pending),
    authorized: composerAuthorityCurrent, visible: readVisibility.visible };
  function mayFillMaterialDraft(draft: string) {
    const current = materialComposerCurrent.current;
    return imagePickerOwnerRef.current === imagePickerOwner && messageRevisionRef.current === materialDraftRevision
      && current.authorized && current.visible && document.visibilityState === "visible" && !composingRef.current
      && canPrepareResourceDraft({ busy: current.working, pendingAcceptance: current.pending,
        currentMessage: textareaRef.current?.value ?? current.message, proposedMessage: draft });
  }
  // UI-only delivery classification. A fresh POST stores its recovery key
  // before receiving an acknowledgement; do not present that as a failure.
  // Once genuinely uncertain, the same request keeps its caution during GET
  // recovery. This does not change retry payloads, nonce or write authority.
  const chatPendingPresentation = useRef({ key: "", uncertain: false });
  const chatPendingPresentationKey = chat.pending ? JSON.stringify([chat.pending.scope,
    chat.pending.conversation_request_id, chat.pending.payload.client_request_id]) : "";
  if (chatPendingPresentation.current.key !== chatPendingPresentationKey)
    chatPendingPresentation.current = { key: chatPendingPresentationKey,
      uncertain: Boolean(chat.pending && (!chat.writeBusy || chat.writeError)) };
  else if (chat.pending && (!chat.writeBusy || chat.writeError)) chatPendingPresentation.current.uncertain = true;
  const chatPendingUncertain = Boolean(chat.pending && chatPendingPresentation.current.uncertain);
  const composer = (
    <div className="composer">
      {composerRevision && !composerClarification && <div className="composer-continuation" role="note">
        <span>{locale === "en" ? "Describe the change in the input below. A new plan still needs your review and confirmation."
          : "在下方输入框说明需要调整的内容。新计划仍需重新核对和确认。"}</span>
        <button type="button" className="text-button" onClick={() => setRevisionOpen(false)}><CustomerText text="取消修改" /></button>
      </div>}
      <div className="conversation-composer-context">
        <button type="button" className="button subtle compact composer-new-conversation"
          onClick={newConversation} disabled={working || attachmentBusy || Boolean(pendingRequest || chat.pending || chat.pendingRecoveryIncomplete)}>
          <Icon name="spark" size={15} />{locale === "en" ? "New conversation" : "发起新会话"}
        </button>
        {hasBankAccount && !actorIsHelper && (
          <>
          <input ref={imageInputRef} type="file" multiple hidden
            accept="image/jpeg,image/png,image/webp" aria-label={locale === "en" ? "Select up to 5 images" : "选择图片，最多5张"}
            onChange={event => {
              const files = Array.from(event.currentTarget.files || []);
              event.currentTarget.value = "";
              const intent = imagePickerIntent.current;
              imagePickerIntent.current = null;
              if (!intent || intent.owner !== imagePickerOwnerRef.current || !composerAuthorityCurrent
                || !readVisibility.visible || document.visibilityState !== "visible" || working || attachmentBusy) return;
              if (!files.length) return;
              if (files.length > 5) {
                setNotice(locale === "en" ? "Select up to 5 images at a time. Nothing has been uploaded." : "一次最多选择5张图片，本次尚未上传。");
                return;
              }
              setImageSelection({ id: crypto.randomUUID(), token, actorScope: conversationScope, files });
              setResourcesOpen(false);
              if (!attachmentOpen) materialNavigation.toggle(intent.trigger);
            }} />
          <button
            type="button"
            className="button subtle compact"
            aria-label={locale === "en" ? "Upload images, up to 5" : "上传图片，最多5张"}
            disabled={working || attachmentBusy || !composerAuthorityCurrent}
            onClick={() => {
              imagePickerIntent.current = { owner: imagePickerOwner, trigger: document.activeElement };
              imageInputRef.current?.click();
            }}
          >
            <Icon name="bill" size={15} /><CustomerText text="图片材料" /></button>
          </>
        )}
      </div>
      {reviewedEvidenceRefs.length > 0 && (
        <div className="reviewed-document-chip">
          <Icon name="shield" size={13} />
          <span>
            {reviewedEvidenceRefs.length}{" "}<CustomerText text="份已核对材料，仅供咨询来源；不授权银行操作" /></span>
          <button
            className="text-button"
            onClick={() => setReviewedEvidenceRefs([])}
          ><CustomerText text="移除引用" /></button>
        </div>
      )}
      {chat.storageNotice && <div className="pending-request-note" role="status"><Icon name="info" size={15}/><span>{chat.storageNotice}</span></div>}
      {(chatPendingUncertain || chat.writeError || chat.pendingRecoveryIncomplete) && (
        <ConversationRecoveryPanel message={chat.writeError} incomplete={chat.pendingRecoveryIncomplete}
          busy={working} waiting={chat.pendingRecoveryDelay.waiting} remainingSeconds={chat.pendingRecoveryDelay.remainingSeconds}
          canCheck={chat.pendingRecoveryDelay.canRecover} canRetry={Boolean(chat.pending && !chat.pending.lookup_only && !chat.pendingRecoveryIncomplete)}
          canLeave={Boolean(chat.pending && chat.writeError || chat.pendingRecoveryIncomplete)}
          onCheck={() => void recoverSensitiveMessage()}
          onRetry={() => { if (chat.pending && !chat.pending.lookup_only) void sendTask(chat.pending.payload.message, false, true); }}
          onLeave={() => { if (window.confirm(conversationRecoveryCopy(locale).leaveConfirm)) chat.abandonRetry(); }} />
      )}
      {pendingRequest && !busy && (
        <div className="pending-request-note" role="status">
          <Icon name="info" size={15} />
          <span><CustomerText text="上次请求结果未确定，重试会查回同一任务。修改内容将新建任务。" /></span>
        </div>
      )}
      <div className="composer-input">
        <textarea
          ref={textareaRef}
          value={message}
          onChange={(event) => changeMessage(event.target.value)}
          disabled={working}
          rows={2}
          maxLength={2000}
          placeholder={composerPlaceholder(locale,{ready:Boolean(session),helper:actorIsHelper})}
          aria-label={composerClarification ? locale === "en" ? "Reply to the agent's question" : "回复 Agent 的问题" : customerUiText("输入新的金融任务")}
          onCompositionStart={() => { composingRef.current = true; }}
          onCompositionEnd={() => { composingRef.current = false; }}
          onKeyDown={(event) => {
            if (shouldSendOnShortcut(event, composingRef.current)) {
              event.preventDefault();
              void sendTask();
            }
          }}
        />
        <div className="composer-bottom">
          <span>
            <Icon name="shield" size={13} />
            {actorIsHelper
              ? customerUiText("协助提案由你提交，银行操作由本人确认")
              : customerUiText("资金与授权操作由你确认")}
          </span>
          <button
            className="send-button"
            onClick={() => sendTask()}
            disabled={!session || !identity.data || working || Boolean(chat.pending) || !message.trim()}
            aria-label={
              pendingRequest && !working
                ? customerUiText("重试未确定的消息")
                : customerUiText("发送会话消息")
            }
          >
            {working ? (
              <span className="spinner" />
            ) : (
              <Icon name="send" size={18} />
            )}
            <span>
              {pendingRequest && !working
                ? customerUiText("重试本次")
                : customerUiText("发送")}
            </span>
          </button>
        </div>
      </div>
      <div className="composer-hint">
        <span><CustomerText text="Ctrl / ⌘ + Enter 发送" /></span>
        <span>{message.length} / 2000</span>
      </div>
    </div>
  );

  return (
    <div
      className={`app-shell ${largeText ? "large-text" : ""} ${actorIsHelper ? "actor-helper" : ""} ${visibleHeight < 600 ? "compact-viewport" : ""}`}
      style={
        {
          "--agent-visible-height": `${visibleHeight}px`,
        } as React.CSSProperties
      }
    >
      {sidebarOpen && (
        <button
          className="sidebar-backdrop"
          aria-label={customerUiText("关闭导航")}
          onClick={() => setSidebarOpen(false)}
        />
      )}
      <aside className={`sidebar ${sidebarOpen ? "open" : ""}`}>
        <Brand />
        <div className="sidebar-navigation">
          <nav
            aria-label={customerUiText("统一Agent入口")}
            className="universal-agent-navigation"
          >
            <button
              className={`nav-item ${page === "workspace" ? "active" : ""}`}
              onClick={() => {
                setPage("workspace");
                setSidebarOpen(false);
                setResourcesOpen(false);
              }}
            >
              <Icon name="spark" size={19} />
              <span><CustomerText text="智能助手" /></span>
              {page === "workspace" && <span className="nav-dot" />}
            </button>
          </nav>
          <div className="sidebar-divider" />
          <button
            className={`nav-item ${page === "history" ? "active" : ""}`}
            disabled={attachmentOpen || attachmentBusy}
            onClick={() => {
              setPage("history");
              setSidebarOpen(false);
            }}
          >
            <Icon name="history" size={19} />
            <span>{locale === "en" ? "History" : "历史记录"}</span>
          </button>
        </div>
        <div className="sidebar-footer">
          <span className="demo-pill">
            <i /><CustomerText text="安全办理 · 全程可查" /></span>
          <p><CustomerText text="个人金融服务 · 授权后执行" /></p>
          <div className="sidebar-city">
            <span />
            <span />
            <span />
            <span />
            <span />
            <span />
            <span />
          </div>
        </div>
      </aside>

      <div className="main-shell">
        <header className="topbar">
          <div className="topbar-title">
            <button
              className="icon-button mobile-menu"
              aria-label={customerUiText("打开导航")}
              onClick={() => setSidebarOpen(true)}
            >
              <Icon name="menu" />
            </button>
            <span><CustomerText text="个人金融工作台" /></span>
            <span className="topbar-slash">/</span>
            <strong>
              {page === "history"
                ? locale === "en" ? "History" : "历史记录"
                : chat.conversation?.title || customerUiText(universalScene.label)}
            </strong>
          </div>
          <CustomerTopbarActions session={session} account={account} formalAccount={formalAccount} actorIsHelper={actorIsHelper}
            hasBankAccount={hasBankAccount} profileName={profileName} selectedProfile={selectedProfile} profiles={profiles}
            sessionBusy={sessionBusy} largeText={largeText} scope={popoverOwner} visible={readVisibility.visible} notificationRead={notificationRead}
            onLargeText={next=>{setLargeText(next);safeSessionSet("zx_web_large_text",String(next));}}
            onSimulation={()=>setSimulationOpen(true)} onPassword={onPassword} onTestTools={onTestTools} onLogout={onLogout}
            onProfileChange={setSelectedProfile} onStartSession={startSession} onRefreshNotifications={()=>refreshNotifications()}
            onOpenNotification={item=>{if(!resourceTrust.ready("notifications",notificationRead.generation))return false;
              const taskId=item.root_task_id||item.task_id;if(!taskId)return false;selectTask(taskId);return true;}}/>
        </header>

        <main className="main-content">
          {availableRelease && <div className="interface-update-note" role="status">
            <span><CustomerText text="页面有新体验可用。" />{working || message.trim() || pendingRequest || chat.pending || pending ? customerUiText("请先处理当前草稿或办理，再刷新页面。") : customerUiText("可以刷新获取最新版，已有办理记录会保留。")}</span>
            <button className="button secondary compact" disabled={working || Boolean(message.trim() || pendingRequest || chat.pending || pending)} onClick={() => window.location.reload()}><CustomerText text="刷新页面" /></button>
          </div>}

          {((error && !localRecovery) || notice) && (
            <div
              className={`banner ${error ? "error" : "success"}`}
              role={error ? "alert" : "status"}
            >
              <Icon name={error ? "info" : "check"} size={18} />
              <span>{customerUiText(error || notice)}</span>
              <button
                className="icon-button"
                aria-label={customerUiText("关闭提示")}
                onClick={() => {
                  setError("");
                  setNotice("");
                }}
              >
                <Icon name="close" size={15} />
              </button>
            </div>
          )}

          {!session && formalAccount ? (
            <section className="onboarding">
              <Icon name="lock" size={25} />
              <div>
                <h2><CustomerText text="当前会话已失效" /></h2>
                <p><CustomerText text="请重新登录账户后继续查看持续保存的任务与账套。" /></p>
              </div>
              <button className="button primary" onClick={onAuthExpired}><CustomerText text="返回账号登录" /></button>
            </section>
          ) : !session ? (
            <section className="onboarding">
              <div className="onboarding-icon">
                <Icon name="spark" size={30} />
              </div>
              <div>
                <h2><CustomerText text="开启你的金融服务工作台" /></h2>
                <p><CustomerText text="自然表达金融需求，查看计划、核对信息并确认办理。所有操作进度与结果均可追踪。" /></p>
              </div>
              <button
                className="button primary"
                onClick={startSession}
                disabled={sessionBusy}
              >
                {sessionBusy ? customerUiText("正在准备账套…") : customerUiText("开启独立账套")}
                <Icon name="arrow" size={17} />
              </button>
            </section>
          ) : actorIsHelper ? (
            <section className="actor-access-note">
              <Icon name="family" size={22} />
              <div>
                <strong><CustomerText text="家庭协助身份 ·" />{session.display_name || customerUiText("当前协助者")}
                </strong>
                <p><CustomerText text="仅展示有效委托范围内的资料。本人银行卡、完整账户与运行控制不向此身份开放。" /></p>
              </div>
              {ownerSession && (
                <button
                  className="button secondary compact"
                  onClick={restoreOwner}
                ><CustomerText text="返回本人工作台" /><Icon name="arrow" size={14} />
                </button>
              )}
            </section>
          ) : null}
          {resourceError && (
            <div className="resource-warning" role="alert">
              <Icon name="info" size={14} />
              <span>{resourceError}</span>
              <button onClick={() => refreshResources()}><CustomerText text="重新加载" /></button>
            </div>
          )}
          {(identity.error || identity.cancelled) && session && (
            <div className="resource-warning" role="alert">
              <Icon name="info" size={14} />
              {identity.error || customerUiText("已取消当前身份读取")}
              <button onClick={identity.refresh}><CustomerText text="重新核验身份" /></button>
            </div>
          )}
          {resourceLoading && (
            <div className="resource-loading-status" role="status">
              <span className="spinner" /><CustomerText text="正在读取当前身份的账户资料" /><button className="text-button" onClick={cancelResourceRead}><CustomerText text="取消加载" /></button>
            </div>
          )}

          {page === "history" ? (
            <UnifiedHistory token={token} actorScope={conversationScope}
              actorRole={identity.data?.role || ""}
              navigationRevision={messageRevisionRef.current}
              enabled={Boolean(token && composerAuthorityCurrent && readVisibility.visible)}
              openDisabled={working || attachmentOpen || attachmentBusy || Boolean(message.trim() || pendingRequest
                || chat.pending || chat.pendingRecoveryIncomplete)}
              onConversationOpen={id => {
                if (imagePickerOwnerRef.current !== imagePickerOwner || !composerAuthorityCurrent || !readVisibility.visible
                  || document.visibilityState !== "visible") return;
                openConversation(id);
              }}
              onTaskOpen={openHistoryTask} />
          ) : (
            <div className="workspace-grid agent-first-workspace">
              <section className="panel conversation-panel">
                <div className="panel-heading">
                  <div className="agent-heading">
                    <span className="agent-icon">
                      <Icon name="spark" size={21} />
                    </span>
                    <div>
                      <h2><CustomerText text="圳行Agent" /></h2>
                      <p><CustomerText text="告诉我你的需求，我来帮你处理。" /></p>
                    </div>
                  </div>
                </div>

                <div className="agent-context-toolbar">
                  <button
                    className="agent-resources-toggle"
                    aria-expanded={resourcesOpen}
                    aria-controls="resource-panels"
                    onClick={() => setResourcesOpen(!resourcesOpen)}
                  >
                    <Icon name="home" size={16} />
                    {resourcesOpen ? customerUiText("返回对话") : locale === "en" ? "User profile" : "用户资料"}
                    <Icon name="chevron" size={14} />
                  </button>
                </div>
                <div
                  className="conversation-content"
                  role="region"
                  aria-label={customerUiText("对话消息")}
                  tabIndex={-1}
                  hidden={resourcesOpen}
                  ref={conversationContentRef}
                  onScroll={conversationTail.onScroll}
                >
                  {chat.error && (
                    <div className="poll-warning" role="alert"><CustomerText text="会话读取暂未完成：" />{chat.error}
                      <button onClick={chat.refresh}><CustomerText text="重新读取" /></button>
                    </div>
                  )}
                  {attachmentOpen && hasBankAccount && !actorIsHelper && (
                    <div data-customer-opened-surface="materials">
                    <AttachmentPanel
                      key={session?.user_id}
                      token={token}
                      actorScope={conversationScope}
                      initialSelection={imageSelection || undefined}
                      onBusyChange={value => {
                        if (imagePickerOwnerRef.current === imagePickerOwner) setAttachmentBusy(value);
                      }}
                      onClose={() => { materialNavigation.close(); setImageSelection(null); }}
                      onReviewedTransferDraft={draft => {
                        if(!mayFillMaterialDraft(draft)) {setNotice('请先处理当前输入，再使用图片中的信息。');return;}
                        changeMessage(draft);
                        setReviewedEvidenceRefs([]);
                        materialNavigation.close(false);
                        setImageSelection(null);
                        textareaRef.current?.focus();
                      }}
                      onReviewedDraft={(draft, ref) => {
                        if (!mayFillMaterialDraft(draft)) {
                          setNotice(
                            "当前草稿或请求尚未核对，材料不会覆盖原消息。请先处理当前内容。",
                          );
                          return;
                        }
                        changeMessage(draft);
                        setReviewedEvidenceRefs([ref]);
                        materialNavigation.close(false);
                        setImageSelection(null);
                        textareaRef.current?.focus();
                      }}
                      onReviewedBatchDraft={(draft, refs) => {
                        if (!refs.length || refs.length > 5 || !mayFillMaterialDraft(draft)) {
                          setNotice(locale === "en" ? "Finish the current message before adding these images." : "请先处理当前输入，再将图片加入对话。");
                          return;
                        }
                        changeMessage(draft);
                        setReviewedEvidenceRefs([...refs]);
                        materialNavigation.close(false);
                        setImageSelection(null);
                        textareaRef.current?.focus();
                      }}
                    />
                    </div>
                  )}
                  {chat.hasEarlier && (
                    <button
                      className="load-earlier-turns"
                      disabled={chat.earlierLoading}
                      onClick={() => { followMessagesRef.current = false; saveReadingAnchor(); void chat.loadEarlier(); }}
                    >
                      {chat.earlierLoading
                        ? customerUiText("正在读取更早消息…")
                        : customerUiText("加载更早消息（不切换当前任务）")}
                    </button>
                  )}
                  {chat.earlierError && (
                    <div className="error-inline" role="alert">
                      {chat.earlierError}
                      {chat.earlierRecoveryNeeded && <button className="button subtle compact" disabled={chat.earlierLoading} onClick={() => void chat.retryEarlier()}><CustomerText text="重新读取历史内容" /></button>}
                    </div>
                  )}
                  {selectedTaskEvidencePending && (
                    <p className="task-view-sync-note"><CustomerText text="正在加载之前的办理记录，暂时无法修改。请稍后继续，或选择作为新需求发送。" /></p>
                  )}
                  <MemoConversationTimeline
                    bankToken={composerAuthorityCurrent && !actorIsHelper ? token : undefined}
                    turns={chat.turns}
                    pending={chat.pending}
                    sending={chat.writeBusy}
                    activeTaskId={activeId}
                    onTaskOpen={stableTaskOpen}
                    onSuggestion={handleConversationSuggestion}
                    onRetryUnderstanding={(turn) => void chat.retryUnderstanding(turn)}
                    onRecipientChoice={(selection) => sendTask(selection.label, false, false, selection)}
                    suggestionRole={composerAuthorityCurrent ? identity.data?.role : undefined}
                    activeTrace={taskAccessDenied ? null : trace}
                    activeTaskReadReceipt={currentTaskTraceReadReceipt(activeTraceRead,token,readVisibility.epoch,
                      readVisibility.visible,trace)}
                    taskResults={chat.taskResults}
                    onResultPrompt={stableResultPrompt}
                    allowReplyMotion={!readingEarlier && !chat.earlierLoading && !resourcesOpen && !composingRef.current}
                    queryRevealScope={resourcesOpen || !readVisibility.visible ? "" : queryRevealScope}
                    disclosureScope={session ? `${bankGateRef.current.epochId}:${conversationScope}:${chat.currentId}:${session.actor_role || ""}` : ""}
                    queryRevealRequest={queryRevealRequest}
                    queryRevealBlocked={Boolean(pendingRequest || chat.pendingRecoveryIncomplete || chat.pending?.lookup_only
                      || trace?.pending_confirmation || busy || taskAccessDenied)}
                    preparationBlocked={Boolean(localRecovery || pollError || taskViewStale || taskAccessDenied || chat.taskResults.permissionBlocked)}
                    queueConversationId={chat.currentId}
                    queueEvidenceBlocked={Boolean(chat.error || chat.pausedRead || chat.loading || chat.earlierLoading || chat.earlierRecoveryNeeded)}
                    invalidConfirmationSummary={pending && !confirmationValidity.current && !confirmationResultUnknown && trace?.state === "AWAITING_CONFIRMATION"
                      ? confirmationValidityText(taskLocale,confirmationValidity.state) : undefined}
                    externalRequestDetails={trace && !taskAccessDenied && !inlineResultTurn && !inlineClarificationTurn && !localRecovery
                      && (trace.state === "NEED_CLARIFICATION" || Boolean(trace.pending_confirmation))
                      ? {taskId: trace.task_id, version: trace.version, state: trace.state} : undefined}
                    visibleRequestDetails={trace && !customerPlanPaused && !taskAccessDenied && !inlineResultTurn && !inlineClarificationTurn && !(conversationOwnsPreparation(trace,chat.turns) && !localRecovery && !pollError && !taskViewStale)
                      ? {taskId: trace.task_id, version: trace.version, state: trace.state} : undefined}
                    activeTaskDetails={trace && !customerPlanPaused && !taskAccessDenied && !inlineResultTurn && !inlineClarificationTurn && !(conversationOwnsPreparation(trace,chat.turns) && !localRecovery && !pollError && !taskViewStale) ? (<CustomerLanguageScope locale={taskLocale}>
                    <div
                      className="task-conversation"
                      data-task-id={trace.task_id}
                      data-task-state={trace.state}
                      tabIndex={-1}
                      role="region"
                      aria-label={taskText("当前事项的办理详情")}
                    >
                      {trace.compensation_source && (
                        <div className="compensation-origin">
                          <Icon name="history" size={16} />
                          <span><CustomerText text="本任务是原操作的独立恢复提案，确认后才执行。" /></span>
                          <button
                            className="text-button"
                            onClick={() =>
                              selectTask(
                                trace.compensation_source!.source_task_id,
                              )
                            }
                          ><CustomerText text="查看原任务" /></button>
                        </div>
                      )}
                      {localRecovery && (
                        <div className="task-recovery-note" role="alert" id="current-task-recovery">
                          <Icon name="info" size={17}/>
      <p>{taskText(localRecovery.message)}</p>
                          <button className="button secondary compact" disabled={working || Boolean(recovery?.delay && !waitingRecovery.canRead)} onClick={() => {
                            if (localRecovery.action === "LOGIN") onAuthExpired?.();
                            else void recoverCurrentResult();
                          }}>{waitingRecovery.current && waitingRecovery.remainingSeconds > 0 ? `${waitingRecovery.remainingSeconds}秒后可查看` : localRecovery.action === "LOGIN" ? taskText("重新登录") : taskText("查看最新办理信息")}</button>
                        </div>
                      )}
                      {!chat.turns.some(
                        (turn) => turn.task_link?.task_id === trace.task_id,
                      ) && (
                        <div className="user-message">
                          <span className="user-avatar"><CustomerText text="你" /></span>
                          <div>
                            <span className="message-author"><CustomerText text="你的需求" /></span>
                            <p>{trace.message}</p>
                          </div>
                        </div>
                      )}
                      <div className="assistant-message">
                        <span className="assistant-avatar">
                          <Icon name="spark" size={20} />
                        </span>
                        <div className="assistant-result">
                          <div className="result-heading">
                          </div>
                          <MissingConfirmationNotice trace={trace} blocked={bankActionBlocked} onEdit={()=>setRevisionOpen(true)} />

                          {pending && trace.state === "AWAITING_CONFIRMATION" && (
                            <CustomerMotionBoundary event={externalTaskMotion} layer={0} interactive>
                            <div
                              className="confirmation-card confirmation-compact"
                              data-confirmation-id={pending.confirmation_id}
                              tabIndex={-1}
                              role="region"
                              aria-labelledby="current-confirmation-title"
                              aria-describedby="current-confirmation-status"
                            >
                              {(taskViewStale ||
                                hasProcessingTurn(chat.turns) ||
                                chat.pending) && (
                                <p
                                  className="task-view-sync-note"
                                  role="status"
                                ><CustomerText text="正在更新办理信息，请稍候再核对确认。" /></p>
                              )}
                              <div className="confirmation-heading">
                                <span>
                                  <Icon name="shield" size={18} />
                                </span>
                                <div>
                                  <h3 id="current-confirmation-title">{pendingWorkflowRequired ? bankingWorkflowConfirmationTitle(pendingWorkflowAction,taskLocale) : pendingLifeRequired ? lifeConfirmationTitle(pendingLifeAction,taskLocale) : pendingNode?.action === "TRANSFER" ? taskLocale === "en" ? "Review transfer" : "核对转账" : confirmationTitle(pending.label,pending.display_label,taskLocale,pendingNode?.action,pendingNode?.result)}</h3>
                                </div>
                                <span className="confirmation-tag">{confirmationValidity.current ? taskText("待确认")
                                  : confirmationValidity.state === "EXPIRED" ? taskText("已到期")
                                    : taskLocale === "en" ? "Deadline unverified" : "有效期待核验"}</span>
                              </div>
                              {pendingNode?.action !== "AA_PAY" && pendingCashSourceShown && <p><CustomerText text={pendingNode?.action === "WEALTH_REDEEM" ? "收款卡尾号" : "付款卡尾号"}/> {String(pendingCashSourceTail)}</p>}
                              {pendingWorkflowRequired
                                ? <BankingWorkflowConfirmation action={pendingWorkflowAction} scope={pendingWorkflowConfirmation} expiresAt={pending.expires_at}/>
                                : pendingLifeRequired
                                ? <LifeDomainConfirmation action={pendingLifeAction} scope={pendingLifeConfirmation}/>
                                : pendingNode?.action==="OWN_ACCOUNT_TRANSFER"
                                ? <OwnAccountTransferConfirmation preview={pendingResult?.preview}/>
                                : (pendingNode?.action.startsWith("CREDIT_") || pendingResult?.preview && typeof pendingResult.preview === "object" && !Array.isArray(pendingResult.preview) && pendingResult.preview.instrument_type === "CREDIT")
                                ? <CreditConfirmation action={pendingNode?.action || ""} preview={pendingResult?.preview}/>
                                : pendingNode?.action === "AA_PAY"
                                ? <AAPayConfirmation scope={pendingScopeObject}/>
                                : pendingNode?.action === "TRANSFER"
                                ? <TransferConfirmation scope={pending.scope} summary={pending.summary} result={pendingResult}/>
                                : pendingNode?.action === "SUBSCRIPTION_CANCEL" && subscriptionCancelConfirmation(pendingNode.action,pending.scope,pendingResult,pending.summary)
                                ? <SubscriptionCancelConfirmation scope={pending.scope} result={pendingResult} summary={pending.summary}/>
                                : <><StructuredData data={confirmationFacts(pendingScopeDisplay,pendingOrderDisplay,{action:pendingNode?.action || "",locale:taskLocale})} confirmation />
                                  <StructuredData data={confirmationSummary(customerCarbonConfirmation(taskLocale === "en" ? pending.display_summary ?? pending.summary : pending.summary,pendingNode?.action,pendingResult?.preview,taskLocale),pendingOrderDisplay)} confirmation /></>}
                              <p id="current-confirmation-status" role="status" aria-live="polite" aria-atomic="true">
                                {!confirmationValidity.current && confirmationValidityText(taskLocale, confirmationValidity.state)}
                                {!confirmationValidity.current && confirmationResultUnknown && " "}
                                {(confirmationValidity.current || confirmationResultUnknown) && (!["TRANSFER", "TRANSFER_SCHEDULE", "AA_REQUEST"].includes(pendingNode?.action || "")
                                  && !(["WEALTH_BUY","WEALTH_REDEEM"].includes(pendingNode?.action || "") && wealthConfirmationReady(pendingNode?.action || "",pendingOrderPreview))
                                  && !(pendingNode?.action === "SUBSCRIPTION_CANCEL" && subscriptionCancelConfirmation(pendingNode.action,pending.scope,pendingResult,pending.summary))
                                  || busy || taskViewStale || hasProcessingTurn(chat.turns) || Boolean(chat.pending) || confirmationResultUnknown
                                  || bankActionBlocked || authRecovery.blocked || Boolean(authError)) && taskText(confirmationFeedback({ busy, updating: taskViewStale || hasProcessingTurn(chat.turns) || Boolean(chat.pending),
                                  blocked: bankActionBlocked || authRecovery.blocked, resultUnknown: confirmationResultUnknown,
                                  requiresVerification: Boolean(pending.requires_step_up), verified: effectiveAuthVerified,
                                  challengeReady: Boolean(challenge&&challengeCurrent), verificationError: Boolean(authError) }))}
                              </p>
                              {pendingNode?.action !== "AA_PAY" && <AliasBindingNotice data={pending?.scope} />}
                              {pendingNode?.action !== "AA_PAY" && !pending.scope?.alias_binding && <AliasBindingNotice data={pendingResult} />}
                              {pendingNode?.action !== "TRANSFER" && !pendingWorkflowRequired && !pendingLifeRequired
                                && !pendingNode?.action.startsWith("CREDIT_")
                                && !(["WEALTH_BUY","WEALTH_REDEEM"].includes(pendingNode?.action || "") && wealthConfirmationReady(pendingNode?.action || "",pendingOrderPreview))
                                && !(pendingNode?.action === "SUBSCRIPTION_CANCEL" && subscriptionCancelConfirmation(pendingNode.action,pending.scope,pendingResult,pending.summary)) && <div className="confirmation-meta">
                                <span><CustomerText text="仅执行本次确认覆盖的步骤" /></span>
                                <span>
                                  {pendingWorkflowRequired
                                    ? taskLocale === "en" ? "Only the displayed bounded transfer or invitation scope" : "仅作用于上方列明的有界转账或收款邀请范围"
                                    : pendingTransferScope?.transfer_channel ===
                                  "INTERNAL_BANK"
                                    ? taskText("作用于付款账号与所选平台收款账号")
                                    : pendingTransferScope?.transfer_channel ===
                                        "EXTERNAL_SIM"
                                      ? taskText("作用于付款账户与外部清算账户；外部到账状态未核验")
                                      : pendingLifeRequired ? taskLocale === "en" ? "Only the displayed recipient, items and bounded scope" : "仅作用于上方列明的对象、商品和范围" : taskText("作用于当前账户")}
                                </span>
                                {pending.expires_at && (
                                  <span><CustomerText text="确认有效至" />{dateText(pending.expires_at)}
                                  </span>
                                )}
                              </div>}
                              {pending.requires_step_up && (
                                <div className="auth-section">
                                  <div className="auth-section-heading">
                                    <Icon name="lock" size={16} />
                                    <strong><CustomerText text="操作二次验证" /></strong>
                                    {confirmationValidity.current && pendingReviewReady && effectiveAuthVerified && (
                                      <span className="verified-label">
                                        <Icon name="check" size={13} /><CustomerText text="已验证" /></span>
                                    )}
                                  </div>
                                  <p id="current-verification-instructions"><CustomerText text="验证码仅用于本次操作，过期或使用后失效。同屏代码用于确认流程，不代表独立身份认证。" /></p>
                                  {confirmationValidity.current && pendingReviewReady && (!challenge || !challengeCurrent ? (
                                    <button
                                      className="button secondary compact"
                                      onClick={beginChallenge}
                                      disabled={bankActionBlocked || authRecovery.blocked}
                                    ><CustomerText text={challenge ? "重新获取验证码" : "获取验证码"} /></button>
                                  ) : !effectiveAuthVerified ? (
                                    <>
                                      <div className="demo-code">
                                        <span><CustomerText text="本次验证码" /></span>
                                        <strong>
                                          {challenge.demo_code ||
                                            taskText("请使用本次验证码")}
                                        </strong>
                                        <small><CustomerText text="有效至" />{" "}
                                          {dateText(challenge.expires_at)}
                                        </small>
                                      </div>
                                      <div className="auth-input">
                                        <input
                                          aria-label={taskText("输入验证码")}
                                          aria-invalid={authCodeInvalid}
                                          aria-describedby={`current-verification-instructions current-confirmation-status${authError ? " current-auth-error" : ""}`}
                                          inputMode="numeric"
                                          autoComplete="off"
                                          value={authCode}
                                          onChange={(event) => {
                                            setAuthCode(event.target.value);
                                            if (authCodeInvalid) { setAuthCodeInvalid(false); setAuthError(""); }
                                          }}
                                          placeholder={taskText("输入本次验证码")}
                                          maxLength={12}
                                        />
                                        <button
                                          className="button secondary compact"
                                          onClick={verifyChallenge}
                                          disabled={
                                            bankActionBlocked ||
                                            authRecovery.blocked ||
                                            !authCode.trim()
                                          }
                                        ><CustomerText text="验证" /></button>
                                        <button
                                          className="text-button"
                                          onClick={beginChallenge}
                                          disabled={bankActionBlocked || authRecovery.blocked}
                                        ><CustomerText text="重新获取" /></button>
                                      </div>
                                    </>
                                  ) : (
                                    <p className="auth-success"><CustomerText text="本次验证已完成，可确认操作。" /></p>
                                  ))}
                                  {(authRecovery.record || authError) && (
                                    <p className="error-inline" role="alert" id="current-auth-error">
                                      {authRecovery.record?.info.message || authError}
                                    </p>
                                  )}
                                  {authRecovery.record && authRecovery.record.info.action !== "NONE" && (
                                    <div className="auth-recovery" id="current-auth-recovery">
                                      {authRecovery.remainingSeconds > 0 && <p role="status">
                                        {authRecoveryText(taskLocale, "wait", authRecovery.remainingSeconds)}
                                      </p>}
                                      <button className="button secondary compact" onClick={readLatestAuthConfirmation}
                                        disabled={bankActionBlocked || !authRecovery.canRead}>
                                        {authRecovery.reading ? authRecoveryText(taskLocale, "reading")
                                          : authRecovery.record.info.action === "WAIT"
                                            ? taskLocale === "en" ? "Read latest confirmation" : "读取最新确认"
                                            : authRecovery.record.info.actionLabel}
                                      </button>
                                    </div>
                                  )}
                                </div>
                              )}
                              <div className="confirmation-actions" aria-busy={busy}>
                                {confirmationValidity.current && pendingNode?.action !== "TRANSFER" && <button
                                  className="button subtle speak-confirmation"
                                  onClick={readConfirmationAloud}
                                >
                                  <Icon name="info" size={15} /><CustomerText text="朗读确认要点" /></button>}
                                {confirmationValidity.current && <button
                                  className="button primary"
                                  onClick={confirmAction}
                                  aria-describedby={`current-confirmation-status${pending.requires_step_up ? " current-verification-instructions" : ""}${localRecovery ? " current-task-recovery" : ""}`}
                                  disabled={
                                    !pendingReviewReady ||
                                    bankActionBlocked ||
                                    authRecovery.blocked ||
                                    (pending.requires_step_up && !effectiveAuthVerified)
                                  }
                                >
                                  <Icon name="check" size={17} />
                                  {busy ? taskText("正在提交…") : taskText("确认执行")}
                                </button>}
                                {canRevise && (
                                  <button
                                    className="button subtle"
                                    disabled={bankActionBlocked || Boolean(authRecovery.record?.info.resultUnknown)}
                                    onClick={() => {
                                      setRevisionOpen(confirmationValidity.current ? !revisionOpen : true);
                                    }}
                                  >{confirmationValidity.current ? taskText("修改需求")
                                    : taskLocale === "en" ? "Edit and prepare a new confirmation" : "修改需求并重新准备确认"}</button>
                                )}
                                {pendingNode?.action === "TRANSFER" && canCancel && <button className="button subtle"
                                  disabled={bankActionBlocked || cancelReadBusy}
                                  onClick={() => { setCancelFeedback(null); setCancelOpen(true); }}>
                                  {taskLocale === "en" ? "Cancel" : <CustomerText text="取消" />}
                                </button>}
                                {!confirmationValidity.current && !canRevise && <button className="button subtle"
                                  disabled={bankActionBlocked || Boolean(authRecovery.record?.info.resultUnknown)} onClick={() => prepareDraft(trace.message)}>
                                  {taskLocale === "en" ? "Prepare a new request" : "准备新需求"}
                                </button>}
                              </div>
                            </div>
                            </CustomerMotionBoundary>
                          )}

                          {(trace.state === "NEED_CLARIFICATION" || revisionOpen) && (
                            <CustomerMotionBoundary event={externalTaskMotion} layer={0} interactive>
                            <div className="assistant-message customer-clarification-message">
                              <span className="assistant-avatar"><Icon name="spark" size={20} /></span>
                              <div className="conversation-answer">
                              {revisionOpen ? <p className="turn-answer"><ReplyText slot={`task:${trace.task_id}:revision`} text={taskLocale === "en" ? "What would you like to change? Tell me here." : "你想调整哪一项？直接在这里告诉我就好。"}/></p>
                                : ["QUEUED", "PROCESSING"].includes(trace.customer_reply_status || "") ? <p className="turn-processing"><span className="spinner" />{taskLocale === "en" ? "Checking what else I need…" : "正在确认还需要的信息…"}</p>
                                : trace.customer_reply_status === "READY" && trace.assistant_response ? <p className="turn-answer"><ReplyText slot={`task:${trace.task_id}:clarification`} text={trace.assistant_response.intro + (trace.assistant_response.detail ? "\n" + trace.assistant_response.detail : "")}/></p>
                                : <ClarificationDetails questions={trace.clarification_questions}
                                    message={trace.message} locale={taskLocale} bankScopeAvailable={hasBankAccount && !actorIsHelper}
                                    fallback={taskText("在这里补充所需信息…")}/>}
                              </div>
                            </div>
                            </CustomerMotionBoundary>
                          )}

                          <TaskOutcomeCard trace={trace} bankToken={composerAuthorityCurrent && !actorIsHelper ? token : undefined} onPrompt={prepareDraft} motionEvent={externalTaskMotion} clarificationInParent={trace.state === "NEED_CLARIFICATION"} confirmationInParent={Boolean(trace.pending_confirmation)} historicalReadOperationIds={historicalReadOperationIds}
                            currentConfirmationInParent={pending && trace.state==="AWAITING_CONFIRMATION" && confirmationValidity.current && pendingReviewReady && !bankActionBlocked
                              && !authRecovery.blocked && !confirmationResultUnknown && !taskViewStale && !taskAccessDenied && !pollError && !localRecovery
                              ? {taskId:trace.task_id,taskVersion:trace.version,parentNodeId:trace.nodes.find(node=>node.child_task_id===pending.task_id)?.node_id || "",
                                childTaskId:pending.task_id,confirmationId:pending.confirmation_id,planHash:pending.plan_hash,nodeKey:pending.node_key,
                                expectedVersion:pending.expected_task_version,expiresAt:pending.expires_at || "",state:"CURRENT",cardVisible:true,
                                ...((pendingLifeRequired || pendingWorkflowRequired) ? {nativeReview:{schema_version:"customer-native-confirmation-display-v1" as const,
                                  action:pendingNode?.action || "",scopeJson:JSON.stringify(pendingLifeConfirmation)}} : {})} : null}
                            expiredConfirmationInParent={pending && confirmationValidity.state === "EXPIRED" && canRevise && !bankActionBlocked
                              && !authRecovery.blocked && !confirmationResultUnknown && !taskViewStale && !taskAccessDenied && !pollError && !localRecovery
                              ? {taskId:trace.task_id,taskVersion:trace.version,parentNodeId:trace.nodes.find(node=>node.child_task_id===pending.task_id)?.node_id || "",
                                childTaskId:pending.task_id,confirmationId:pending.confirmation_id,planHash:pending.plan_hash,nodeKey:pending.node_key,
                                expectedVersion:pending.expected_task_version,expiresAt:pending.expires_at || "",state:"EXPIRED",recoveryAvailable:true} : null}/>
                          {trace.state === "UNKNOWN" && (
                            <div className="unknown-note" role="status" aria-live="polite" aria-atomic="true" tabIndex={-1} aria-label={taskText("本次操作结果正在核查")}>
                              <Icon name="clock" size={18} />
                              <p><CustomerText text="正在核查本次执行结果。请等待查单回执，避免重复提交同一操作。" /></p>
                            </div>
                          )}
                          {trace.nodes.some(
                            (node) =>
                              node.compensation_task_id ||
                              (node.can_compensate &&
                                node.operation_id &&
                                trace.allowed_actions.includes("compensate")),
                          ) && (
                            <div className="compensation-card">
                              <strong>
                                <Icon name="history" size={17} /><CustomerText text="已完成操作的状态恢复" /></strong>
                              <p><CustomerText text="恢复将创建新的业务任务，仍需独立确认。原操作回执会保留。" /></p>
                              {trace.nodes
                                .filter(
                                  (node) =>
                                    node.compensation_task_id ||
                                    (node.can_compensate &&
                                      node.operation_id &&
                                      trace.allowed_actions.includes(
                                        "compensate",
                                      )),
                                )
                                .map((node) => (
                                  <div key={node.node_id}>
                                    <span>{node.label}</span>
                                    {node.compensation_task_id ? (
                                      <button
                                        className="button secondary compact"
                                        onClick={() =>
                                          selectTask(node.compensation_task_id!)
                                        }
                                      ><CustomerText text="打开恢复任务" /><Icon name="arrow" size={13} />
                                      </button>
                                    ) : (
                                      <button
                                        className="button secondary compact"
                                        onClick={() =>
                                          requestCompensation(node)
                                        }
                                        disabled={bankActionBlocked}
                                      ><CustomerText text="申请恢复卡片状态" /><Icon name="arrow" size={13} />
                                      </button>
                                    )}
                                  </div>
                                ))}
                            </div>
                          )}
                          <div className="task-toolbar">
                            {canRevise &&
                              !pending &&
                              !revisionOpen &&
                              trace.state !== "NEED_CLARIFICATION" && (
                                <button
                                  className="text-button"
                                  onClick={() => setRevisionOpen(true)}
                                ><CustomerText text="修改需求" /></button>
                              )}
                            {canCancel && !(pending && pendingNode?.action === "TRANSFER" && trace.state === "AWAITING_CONFIRMATION") && (
                              <button
                                className="text-button muted"
                                onClick={() => { setCancelFeedback(null); setCancelOpen(true); }}
                              ><CustomerText text="撤销未完成步骤" /></button>
                            )}
                          </div>
                        </div>
                      </div>
                    </div>
                  </CustomerLanguageScope>)
                      : null}
                  />
                  {token && composerAuthorityCurrent && !actorIsHelper && chat.turns.at(-1)?.status === "ANSWERED"
                    && chat.turns.at(-1)?.mode === "CONSULT" && !chat.turns.at(-1)?.task_link
                    && chat.turns.at(-1)?.response_meta?.product_panel === true
                    && chat.turns.at(-1)?.response_meta?.suggestion_source === "CUSTOMER_CAPABILITY_CATALOG"
                    && chat.turns.at(-1)?.response_meta?.suggestion_catalog_version === customerSuggestionCatalogVersion
                    && chat.turns.at(-1)?.response_meta?.suggestion_role === identity.data?.role
                    && !chat.pausedRead && !chat.loading && !chat.error && !chat.pending && !working
                    && readVisibility.visible && !taskAccessDenied && !taskViewStale && !resourcesOpen
                    && <KnowledgeProductCards token={token} language={latestProductReply?.reply_language}
                      selectedProductKey={productReading.scope === productReadingScope ? productReading.key : ""}
                      onSelectedProductKeyChange={key => {
                        if (!dialogueCallbackState.current.mounted || productReadingScopeRef.current !== productReadingScope
                          || !composerAuthorityCurrent || !readVisibility.visible || document.visibilityState !== "visible") return;
                        setProductReading({ scope: productReadingScope, key });
                      }} />}
                  <div
                    className="latest-message-anchor"
                    ref={latestMessageRef}
                  />
                  {!trace &&
                  !activeId &&
                  !chat.turns.length &&
                  !chat.pending &&
                  !(chat.loading && chat.currentId) &&
                  !attachmentOpen ? (
                    <div
                      className={`conversation-welcome ${!actorIsHelper ? "universal-conversation-welcome" : ""}`}
                    >
                      <span className="assistant-avatar">
                        <Icon name="spark" size={22} />
                      </span>
                      <div className="assistant-intro">
                        {!actorIsHelper ? (
                          <UniversalWelcome
                            canSend={quickSuggestionEnabled}
                            onSend={sendReadonlySuggestion}
                          />
                        ) : (
                          <>
                            <h3>
                              {actorIsHelper
                                ? customerUiText("你好，我可以协助你核对家庭授权与提案。")
                                : customerUiText("你好，今天有什么需要我帮你办理？")}
                            </h3>
                            <p>
                              {actorIsHelper
                                ? customerUiText("你可以查看有效委托范围内的授权投影、了解协助提案进度，或在已有授权内准备提案。资金与卡片操作仍需由账户本人独立审阅和确认。")
                                : customerUiText("你可以让我分析账单、办理转账，或把几个需求一次告诉我。执行涉及资金或授权的操作前，我会请你核对确认。")}
                            </p>
                            <div className="suggestion-grid">
                              <>
                                <button
                                  onClick={() =>
                                    prepareDraft("查看家庭协助关系与已授权余额")
                                  }
                                >
                                  <Icon name="family" size={17} />
                                  <span><CustomerText text="查看授权关系" /></span>
                                  <Icon name="arrow" size={14} />
                                </button>
                                <button
                                  onClick={() =>
                                    prepareDraft("查看家庭协助提案与进度")
                                  }
                                >
                                  <Icon name="workflow" size={17} />
                                  <span><CustomerText text="查看协助提案" /></span>
                                  <Icon name="arrow" size={14} />
                                </button>
                              </>
                            </div>
                          </>
                        )}
                      </div>
                    </div>
                  ) : activeId && !trace && !chat.turns.length && !chat.pending ? (
                    <div className="loading-task">
                      <Skeleton lines={5} />
                      <p><CustomerText text="正在读取已保存的任务状态…" /></p>
                    </div>
                  ) : null}
                  {pollError && (
                    <div className="poll-warning">
                      <Icon name="info" size={15} />
                      <span><CustomerText text="状态更新暂时中断：" />{pollError}</span>
                      <button
                        onClick={() => void recoverCurrentResult()}
                      ><CustomerText text="重试" /></button>
                    </div>
                  )}
                </div>

                {readingEarlier && !resourcesOpen && !attachmentOpen && chat.turns.length > 0 && (
                  <button className="latest-messages-button" onClick={() => {
                    followMessagesRef.current = true; readingAnchorRef.current = null; setReadingEarlier(false);
                    conversationContentRef.current?.scrollTo({ top: conversationContentRef.current.scrollHeight,
                      behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
                  }}><CustomerText text="回到最新消息" /><Icon name="arrow" size={14}/></button>
                )}
                {resourcesOpen && token && (
                  <aside id="resource-panels" className="customer-data-surface" aria-label={locale === "en" ? "User profile" : "用户资料"}>
                    <CustomerDataHome key={conversationScope} token={token} session={session}
                      role={identity.data?.role || session?.actor_role || ""} onBack={() => setResourcesOpen(false)}/>
                  </aside>
                )}
                {!resourcesOpen && composer}
              </section>
            </div>
          )}
          <footer className="page-footer">
            <span><CustomerText text="圳行Agent · 对话式金融服务" /></span>
          </footer>
        </main>
      </div>
      {reportOpen && session && (
        <div
          ref={reportDialogRef}
          className="report-overlay"
          role="dialog"
          aria-modal="true"
          aria-label={customerUiText("账单月度与年度报告")}
        >
          <BillReports
            token={token}
            onBack={() => setReportOpen(false)}
            onAnalyze={(prompt) => {
              setReportOpen(false);
              prepareDraft(prompt);
            }}
          />
        </div>
      )}
      {simulationOpen && session && hasBankAccount && account?.role === "DEV_TEST" && (
        <SimulationDrawer
          token={token}
          onClose={() => setSimulationOpen(false)}
          onUpdated={() => {
            refreshResources();
            readCurrentTrace().catch(() => {});
          }}
        />
      )}
      {cancelOpen && trace && (
        <div className="modal-backdrop" onClick={() => { if (!busy) setCancelOpen(false); }}>
          <div
            ref={cancelDialogRef}
            className="cancel-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="cancel-title"
            aria-describedby={`cancel-description${localCancelFeedback ? " cancel-feedback" : ""}`}
            onClick={(event) => event.stopPropagation()}
          >
            <span className="modal-icon">
              <Icon name="info" size={24} />
            </span>
            <h2 id="cancel-title"><CustomerText text="撤销尚未完成的步骤？" /></h2>
            <p id="cancel-description"><CustomerText text="已执行的业务变化与回执会保留。结果未知的操作需要先核查执行结果。" /></p>
            {localCancelFeedback && (
              <section>
                <p id="cancel-feedback" role={localCancelFeedback.kind === "error" ? "alert" : "status"} aria-atomic="true">
                  {localCancelFeedback.message}
                </p>
                <button className="button secondary" onClick={() => void readCancelResult()}
                  disabled={busy || cancelReadBusy || Boolean(recovery?.delay && !waitingRecovery.canRead)}>
                  {cancelReadBusy ? customerUiText("正在读取…") : waitingRecovery.current && waitingRecovery.remainingSeconds > 0
                    ? `${waitingRecovery.remainingSeconds}秒后可查看` : customerUiText("查看最新办理信息")}
                </button>
              </section>
            )}
            <div>
              <button
                className="button secondary"
                onClick={() => setCancelOpen(false)}
                disabled={busy}
              ><CustomerText text="继续办理" /></button>
              <button
                className="button danger"
                onClick={cancelTask}
                disabled={bankActionBlocked || cancelReadBusy || !canCancel}
                aria-describedby={localCancelFeedback?.kind === "error" ? "cancel-feedback" : undefined}
              >
                {busy ? customerUiText("正在提交…") : customerUiText("确认撤销")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
