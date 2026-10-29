import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { request } from "./api";
import { useEndpoint } from "./useEndpoint";
import { useConversationTaskResults } from "./useConversationTaskResults";
import { customerRecovery } from "./customerRecovery";
import { conversationRecoveryCopy } from "./conversationRecoveryCopy";
import { understandingRetryVersion, understandingRetryAcknowledged } from "./conversationSemanticRecovery";
import { matchesConversationAcknowledgement } from "./conversationPendingAcknowledgement";
import { HISTORY_TURN_LIMIT, HISTORY_WINDOW_LIMIT_NOTICE, pendingHistoryViews, readAuthorizedHistoryWindow, validateAuthorizedConversationPage } from "./conversationHistoryWindow";
import { useReadVisibility } from "./useReadVisibility";
import { nextReadPollDelay } from "./pollSchedule";
import { acknowledgementMatchesPending,containsCredentialValue,persistedConversationPending } from "./conversationPendingPrivacy";
import { inspectConversationPending } from "./conversationPendingRestore";
import { beginRecoveryDelay,recoveryDelayState,type RecoveryDelay } from "./recoveryDelay";
import { safeSessionGet,safeSessionSet,safeSessionRemove } from "./safeStorage.ts";
import {
  hasProcessingTurn,
  mergeConversationTurns,
  mergeConversationPages,
} from "./conversationState";
import type {
  Conversation,
  ConversationSummary,
  ConversationTurn,
  ConversationTurnInput,
  PendingConversationRequest,
  ConversationAcknowledgement,
} from "./conversationTypes";

const selectedKey = (scope: string) => `zx_v3_conversation:${scope}`;
const pendingKey = (scope: string) => `zx_v3_turn_pending:${scope}`;
function restorePending(scope:string){
  const raw=safeSessionGet(pendingKey(scope)),restored=inspectConversationPending(raw,scope);
  if(restored.incomplete){
    if(!safeSessionSet(pendingKey(scope),JSON.stringify({scope,restore_incomplete:true})))safeSessionRemove(pendingKey(scope));
  }else if(restored.clear)safeSessionRemove(pendingKey(scope));
  if(restored.record&&JSON.stringify(restored.record)!==raw)safeSessionSet(pendingKey(scope),JSON.stringify(restored.record));
  return restored;
}
const storedPending=(scope:string)=>restorePending(scope).record;
export function useConversations(
  token: string,
  scope: string,
  revalidateSensitiveHistory = false,
) {
  const readVisibility = useReadVisibility();
  const readVisibilityRef=useRef(readVisibility);
  readVisibilityRef.current=readVisibility;
  const [selection, setSelection] = useState({
    scope,
    id: safeSessionGet(selectedKey(scope)) || "",
  });
  const initialRestore=useRef<ReturnType<typeof restorePending>|null>(null);
  const initialRestoreUsed=useRef(false);
  if(initialRestore.current===null)initialRestore.current=restorePending(scope);
  const [pendingRecord, setPendingRecord] =
    useState<PendingConversationRequest | null>(() => initialRestore.current!.record);
  const [pendingRecoveryIncomplete,setPendingRecoveryIncomplete]=useState(initialRestore.current.incomplete);
  const [storageNotice,setStorageNotice]=useState("");
  const [pendingDelay,setPendingDelay]=useState<RecoveryDelay|null>(null);
  const [pendingClock,setPendingClock]=useState(()=>performance.now());
  const [acceptedRecord, setAcceptedRecord] = useState<{
    token: string;
    turn: ConversationTurn;
  } | null>(null);
  const [writeBusy, setWriteBusy] = useState(false);
  const [writeError, setWriteError] = useState("");
  const [earlier, setEarlier] = useState<{
    token: string;
    conversationId: string;
    turns: ConversationTurn[];
    hasEarlier: boolean;
    cursor: number | null;
    verifiedBefore: number;
    checkedAt: number;
  } | null>(null);
  const [earlierLoading, setEarlierLoading] = useState(false),
    [earlierError, setEarlierError] = useState("");
  const [earlierRecoveryNeeded, setEarlierRecoveryNeeded] = useState(false);
  const historyAutoRetryBlocked = useRef(false);
  const earlierAbort = useRef<AbortController | null>(null),
    earlierLock = useRef(false);
  const historyNeedsRecheck = useRef(false);
  const waitingHistoryLatest = useRef<Conversation | null>(null);
  const [historyDenied, setHistoryDenied] = useState<{ token: string; conversationId: string } | null>(null);
  const tokenRef = useRef(token);
  tokenRef.current = token;
  const scopeRef = useRef(scope);
  scopeRef.current = scope;
  const writeLock = useRef<string | null>(null);
  const actorGeneration = useRef(0);
  useEffect(() => {
    actorGeneration.current += 1;
    return () => { actorGeneration.current += 1; earlierAbort.current?.abort(); };
  }, [token, scope]);
  const currentId =
    selection.scope === scope
      ? selection.id
      : safeSessionGet(selectedKey(scope)) || "";
  const index = useEndpoint<{ items: ConversationSummary[] }>(
    token && scope ? "/api/v3/customer/conversations" : null,
    token,
  );
  const detail = useEndpoint<Conversation>(
    currentId && token
      ? `/api/v3/customer/conversations/${encodeURIComponent(currentId)}`
      : null,
    token,
  );
  const pageIntegrityError = useMemo(() => {
    if (!detail.data) return "";
    try { validateAuthorizedConversationPage(detail.data,currentId); return ""; }
    catch { return "会话内容未通过连续性核对，请重新读取；没有重新提交任何办理。"; }
  }, [detail.data,currentId]);
  const historyPermissionDenied = [401, 403, 404].includes(detail.errorStatus || 0) ||
    historyDenied?.token === token && historyDenied.conversationId === currentId || Boolean(pageIntegrityError);
  const pending = pendingRecord?.scope === scope ? pendingRecord : null;
  const delayBinding={actorEpoch:actorGeneration.current,taskId:pending?.conversation_id||pending?.conversation_request_id||"",operationId:pending?.payload.client_request_id||""};
  const pendingDelayState=recoveryDelayState(pendingDelay,delayBinding,pendingClock);
  useEffect(()=>{
    if(!readVisibility.visible)return;
    setPendingClock(performance.now());
    if(!pendingDelayState.current||pendingDelayState.canRead)return;
    const timer=window.setInterval(()=>setPendingClock(performance.now()),250);
    return()=>clearInterval(timer);
  },[pendingDelay,pendingDelayState.current,pendingDelayState.canRead,readVisibility.visible]);
  const accepted =
    !historyPermissionDenied &&
    acceptedRecord?.token === token &&
    acceptedRecord.turn.conversation_id === currentId
      ? acceptedRecord.turn
      : null;
  const selectedIdRef = useRef(currentId);
  selectedIdRef.current = currentId;
  // Selection and outbound/recovery gestures advance synchronously, before
  // React can commit the next render. Retained callbacks cannot use an old
  // thread, task binding or pre-UNKNOWN pending state, even if an ID matches.
  const selectionGeneration = useRef(0);
  const boundSelectionGeneration = selectionGeneration.current;
  const earlierForActor =
    !historyPermissionDenied && earlier?.token === token && earlier.conversationId === currentId
      ? earlier
      : null;
  const previousLatestMetadata = useRef<{ token: string; conversationId: string; turns: ConversationTurn[] } | null>(null);
  const latestFirst = detail.data?.turns[0]?.sequence;
  const bridgeRows = useMemo(()=>!historyPermissionDenied && latestFirst && previousLatestMetadata.current?.token === token &&
    previousLatestMetadata.current.conversationId === currentId
    ? previousLatestMetadata.current.turns.filter(turn => turn.sequence < latestFirst)
    : [],[historyPermissionDenied,latestFirst,token,currentId,detail.data]);
  const hasEarlier =
    earlierForActor?.hasEarlier ?? detail.data?.has_earlier ?? false;
  const beforeCursor =
    earlierForActor?.cursor ?? detail.data?.before_cursor ?? null;
  const conversation = useMemo(()=>detail.data && !historyPermissionDenied
    ? {
        ...detail.data,
        turns: mergeConversationPages(
          detail.data.turns || [],
          mergeConversationPages(earlierForActor?.turns || [], bridgeRows).slice(-HISTORY_TURN_LIMIT),
        ),
        has_earlier: hasEarlier,
        before_cursor: beforeCursor,
      }
    : null,[detail.data,historyPermissionDenied,earlierForActor,bridgeRows,hasEarlier,beforeCursor]);
  const turns = useMemo(()=>mergeConversationTurns(conversation?.turns || [], accepted),[conversation?.turns,accepted]);
  const turnsRef = useRef(turns); turnsRef.current = turns;
  const taskResults = useConversationTaskResults(
    token, scope, currentId, turns,
    historyPermissionDenied,
    async () => {
      const actorToken = token, actorScope = scope, conversationId = currentId, generation = actorGeneration.current,
        visibilityEpoch=readVisibilityRef.current.epoch;
      const page = await request<Conversation>(
        `/api/v3/customer/conversations/${encodeURIComponent(conversationId)}`,
        actorToken, "GET",
      );
      if (tokenRef.current !== actorToken || scopeRef.current !== actorScope || selectedIdRef.current !== conversationId || actorGeneration.current !== generation ||
        !readVisibilityRef.current.visible || readVisibilityRef.current.epoch!==visibilityEpoch)
        throw new DOMException("Reading context changed", "AbortError");
      validateAuthorizedConversationPage(page,conversationId);
      setHistoryDenied(null);
      index.refresh();
      detail.refresh();
      return page.turns;
    },
  );
  useEffect(() => {
    if (!historyPermissionDenied) return;
    earlierAbort.current?.abort();
    earlierLock.current = false;
    setEarlier(null);
    setAcceptedRecord(null);
    setEarlierLoading(false);
  }, [historyPermissionDenied]);
  useEffect(() => {
    writeLock.current = null;
    selectionGeneration.current += 1;
    const restored=!initialRestoreUsed.current?initialRestore.current!:restorePending(scope);initialRestoreUsed.current=true;
    setPendingRecord(restored.record);setPendingRecoveryIncomplete(restored.incomplete);
    setPendingDelay(null);setStorageNotice("");
    setAcceptedRecord(null);
    setWriteError("");
    setWriteBusy(false);
    const restoredId=safeSessionGet(selectedKey(scope)) || "";
    selectedIdRef.current=restoredId;
    setSelection({ scope, id: restoredId });
  }, [token, scope]);
  useEffect(() => {
    earlierAbort.current?.abort();
    earlierLock.current = false;
    setEarlier(null);
    setEarlierLoading(false);
    setEarlierError("");
    setEarlierRecoveryNeeded(false);
    historyAutoRetryBlocked.current = false;
    setHistoryDenied(null);
    historyNeedsRecheck.current = false;
    waitingHistoryLatest.current = null;
    previousLatestMetadata.current = null;
  }, [token, scope, currentId]);
  const detailRef = useRef(detail.data);
  detailRef.current = detail.data;
  const earlierRef = useRef(earlierForActor);
  earlierRef.current = earlierForActor;
  function hideHistoricalAnswers() {
    const current = earlierRef.current;
    if (!current?.turns.length) return;
    historyNeedsRecheck.current = true;
    historyAutoRetryBlocked.current = false;
    setEarlierRecoveryNeeded(false);
    waitingHistoryLatest.current = detailRef.current;
    setEarlierLoading(true);
    setEarlier(previous => previous?.token === token && previous.conversationId === currentId
      ? { ...previous, turns: pendingHistoryViews(previous.turns) } : previous);
  }
  useEffect(() => {
    const latest = detail.data;
    if (!latest || historyPermissionDenied || latest.conversation_id !== currentId) return;
    const previous = previousLatestMetadata.current;
    const first = latest.turns[0]?.sequence;
    const exiting = first && previous?.token === token && previous.conversationId === currentId
      ? previous.turns.filter(turn => turn.sequence < first) : [];
    if (exiting.length) setEarlier(current => {
      const matching = current?.token === token && current.conversationId === currentId ? current : null;
      const rows = mergeConversationPages(matching?.turns || [], exiting).slice(-HISTORY_TURN_LIMIT);
      return { token, conversationId: currentId, turns: rows,
        hasEarlier: matching?.hasEarlier ?? rows[0].sequence > 1,
        cursor: matching?.cursor ?? rows[0].sequence,
        verifiedBefore: matching?.verifiedBefore ?? previous!.turns[0].sequence,
        checkedAt: matching?.checkedAt ?? 0 };
    });
    // Keep geometry/provenance IDs only, not the answers leaving a live page.
    previousLatestMetadata.current = { token, conversationId: currentId, turns: pendingHistoryViews(latest.turns).slice(-200) };
  }, [detail.data, token, currentId, historyPermissionDenied]);
  useEffect(() => {
    const current = earlierForActor, latest = detail.data;
    const first = latest?.turns?.[0]?.sequence;
    if (!current?.turns.length || !first || detail.loading || historyPermissionDenied || earlierLock.current ||
      !readVisibility.visible || historyAutoRetryBlocked.current) return;
    if (waitingHistoryLatest.current === latest) {
      if (detail.error) {
        waitingHistoryLatest.current = null;
        historyNeedsRecheck.current = false;
        setEarlierLoading(false);
        historyAutoRetryBlocked.current = true;
        setEarlierRecoveryNeeded(true);
        setEarlierError("历史内容暂未重新核验，可以恢复连接后重新读取；此前的解释保持隐藏。");
      }
      return;
    }
    waitingHistoryLatest.current = null;
    if (!historyNeedsRecheck.current && current.verifiedBefore === first) return;
    void revalidateEarlier(first, current.turns[0].sequence);
  }, [detail.data, detail.loading, detail.error, earlierForActor, historyPermissionDenied,readVisibility.visible]);
  useEffect(() => {
    if (!currentId || !hasProcessingTurn(turns)) return;
    const delay=nextReadPollDelay({visible:readVisibility.visible,inFlight:detail.loading,cancelled:detail.cancelled});
    if(delay===null)return;
    const timer = window.setTimeout(()=>detail.refresh({automatic:true}), delay);
    return () => clearTimeout(timer);
  }, [currentId, detail.data, accepted, detail.refresh,detail.loading,detail.cancelled,readVisibility.visible]);
  const observedVersion = useRef("");
  useEffect(() => {
    if (!token || !currentId) return;
    let lastFocusRefresh = -Infinity;
    const refreshVisible = () => {
      if (document.visibilityState === "visible") {
        if (Date.now() - lastFocusRefresh < 1000) return;
        lastFocusRefresh = Date.now();
        if (!historyAutoRetryBlocked.current) hideHistoricalAnswers();
        index.refresh({automatic:true});
        detail.refresh({automatic:true});
      }
    };
    window.addEventListener("focus", refreshVisible);
    document.addEventListener("visibilitychange", refreshVisible);
    const interval = window.setInterval(refreshVisible, revalidateSensitiveHistory ? 10000 : 60000);
    return () => {
      window.removeEventListener("focus", refreshVisible);
      document.removeEventListener("visibilitychange", refreshVisible);
      if (interval) clearInterval(interval);
    };
  }, [
    token,
    currentId,
    revalidateSensitiveHistory,
    index.refresh,
    detail.refresh,
  ]);
  useEffect(()=>{
    if(readVisibility.visible)return;
    earlierAbort.current?.abort();
    earlierLock.current=false;
    setEarlierLoading(false);
    const current=earlierRef.current;
    if(current?.turns.length){
      setEarlier(previous=>previous?{...previous,turns:pendingHistoryViews(previous.turns)}:previous);
      historyNeedsRecheck.current=true;
    }
  },[readVisibility.visible]);
  useEffect(() => {
    if (!conversation) return;
    const key = `${conversation.conversation_id}:${conversation.version}:${conversation.updated_at}`;
    if (observedVersion.current !== key) {
      observedVersion.current = key;
      index.refresh({automatic:true});
    }
  }, [conversation, index.refresh]);
  const select = useCallback(
    (id: string) => {
      if(tokenRef.current!==token||scopeRef.current!==scope||selectionGeneration.current!==boundSelectionGeneration)return false;
      if (writeLock.current) return false;
      if (pending && pending.conversation_id !== id) {
        setWriteError(
          conversationRecoveryCopy().selectBlocked,
        );
        return false;
      }
      selectionGeneration.current += 1;
      selectedIdRef.current=id;
      setSelection({ scope, id });
      safeSessionSet(selectedKey(scope), id);
      setWriteError("");
      return true;
    },
    [token, scope, pending, boundSelectionGeneration],
  );
  const fresh = useCallback(() => {
    if(tokenRef.current!==token||scopeRef.current!==scope||selectionGeneration.current!==boundSelectionGeneration)return false;
    if (writeLock.current || pending || pendingRecoveryIncomplete) {
      setWriteError(
        conversationRecoveryCopy().newBlocked,
      );
      return false;
    }
    selectionGeneration.current += 1;
    selectedIdRef.current="";
    setSelection({ scope, id: "" });
    safeSessionRemove(selectedKey(scope));
    setAcceptedRecord(null);
    setWriteError("");
    return true;
  }, [token, scope, pending, pendingRecoveryIncomplete, boundSelectionGeneration]);
  async function send(input: ConversationTurnInput) {
    if (!token || !scope || writeLock.current) return null;
    if(tokenRef.current!==token||scopeRef.current!==scope)return null;
    if(selectionGeneration.current!==boundSelectionGeneration||selectedIdRef.current!==currentId){
      setWriteError("会话已切换，输入内容会保留。请在当前会话中发送，不会向旧会话提交消息。");
      return null;
    }
    if(pendingRecoveryIncomplete){setWriteError(conversationRecoveryCopy().incomplete);return null;}
    if(pending?.lookup_only)return recoverPending();
    if (pending && input.message !== pending.payload.message) {
      setWriteError(
        conversationRecoveryCopy().changed,
      );
      return null;
    }
    selectionGeneration.current += 1;
    const operationId = crypto.randomUUID();
    writeLock.current = operationId;
    setWriteBusy(true);
    setWriteError("");
    const actorToken = token,
      actorScope = scope,
      generation = actorGeneration.current,
      selectedGeneration = selectionGeneration.current;
    const currentActor = () => tokenRef.current === actorToken && scopeRef.current === actorScope &&
      actorGeneration.current === generation && selectionGeneration.current === selectedGeneration;
    let draft: PendingConversationRequest = pending || {
      scope,
      conversation_id: currentId || null,
      conversation_request_id: crypto.randomUUID(),
      title: containsCredentialValue(input.message) ? "安全内容受理记录" : input.message.slice(0, 60),
      payload: input,
      created_at: new Date().toISOString(),
    };
    const persist = () => {
      if (!currentActor())
        return;
      const safe=persistedConversationPending(draft);
      if(!safeSessionSet(pendingKey(actorScope), JSON.stringify(safe)))setStorageNotice(conversationRecoveryCopy().storageUnavailable);
      setPendingRecord(safe);
    };
    persist();
    try {
      if (!draft.conversation_id) {
        const created = await request<ConversationSummary>(
          "/api/v3/customer/conversations",
          actorToken,
          "POST",
          {
            title: draft.title,
            client_request_id: draft.conversation_request_id,
          },
        );
        if (!currentActor())
          return null;
        if (!created?.conversation_id)
          throw new Error("会话受理响应缺少对话标识。");
        draft = { ...draft, conversation_id: created.conversation_id };
        persist();
      }
      const requestedConversationId = draft.conversation_id;
      if (!requestedConversationId)
        throw new Error("没有对应的会话标识，未发送消息。");
      if (!currentActor())
        return null;
      selectedIdRef.current=requestedConversationId;
      setSelection({ scope: actorScope, id: requestedConversationId });
      safeSessionSet(selectedKey(actorScope), requestedConversationId);
      index.refresh();
      const turn = await request<ConversationTurn>(
        `/api/v3/customer/conversations/${encodeURIComponent(requestedConversationId)}/turns`,
        actorToken,
        "POST",
        draft.payload,
      );
      if (!turn?.turn_id || turn.conversation_id !== draft.conversation_id)
        throw new Error("消息受理响应缺少对应会话标识。");
      if (!currentActor())
        return null;
      const savedPending = storedPending(actorScope);
      if (matchesConversationAcknowledgement(savedPending, draft))
        if(!safeSessionRemove(pendingKey(actorScope)))setStorageNotice(conversationRecoveryCopy().acknowledgedStorage);
      if (savedPending && !matchesConversationAcknowledgement(savedPending, draft))
        setPendingRecord(savedPending);
      else setPendingRecord(previous => matchesConversationAcknowledgement(previous, draft) ? null : previous);
      setAcceptedRecord({ token: actorToken, turn });
      selectedIdRef.current=turn.conversation_id;
      setSelection({ scope: actorScope, id: turn.conversation_id });
      safeSessionSet(selectedKey(actorScope), turn.conversation_id);
      index.refresh();
      detail.refresh();
      return turn;
    } catch (error) {
      if (currentActor())
        setWriteError(
          customerRecovery(error, { operation: "submit" }).message,
        );
      return null;
    } finally {
      if (writeLock.current === operationId) writeLock.current = null;
      if (currentActor())
        setWriteBusy(false);
    }
  }
  async function recoverPending(): Promise<ConversationTurn | null> {
    if(tokenRef.current!==token||scopeRef.current!==scope||selectionGeneration.current!==boundSelectionGeneration||selectedIdRef.current!==currentId)return null;
    if(pendingRecoveryIncomplete){index.refresh();detail.refresh();setWriteError(conversationRecoveryCopy().incompleteChecked);return null;}
    if(!pending||!token||!scope||writeLock.current||!readVisibility.visible)return null;
    const wait=recoveryDelayState(pendingDelay,{actorEpoch:actorGeneration.current,taskId:pending.conversation_id||pending.conversation_request_id,operationId:pending.payload.client_request_id},performance.now());
    if(wait.current&&!wait.canRead){setPendingClock(performance.now());return null;}
    selectionGeneration.current += 1;
    const original=pending,actorToken=token,actorScope=scope,generation=actorGeneration.current,visibilityEpoch=readVisibilityRef.current.epoch,
      selectedGeneration=selectionGeneration.current;
    const operationId=crypto.randomUUID();writeLock.current=operationId;setWriteBusy(true);setWriteError("");
    const matches=()=>tokenRef.current===actorToken&&scopeRef.current===actorScope&&actorGeneration.current===generation&&selectionGeneration.current===selectedGeneration&&
      readVisibilityRef.current.visible&&readVisibilityRef.current.epoch===visibilityEpoch;
    const parameters=new URLSearchParams({client_request_id:original.payload.client_request_id});
    if(original.conversation_id)parameters.set("conversation_id",original.conversation_id);
    else parameters.set("conversation_request_id",original.conversation_request_id);
    try{
      const found=await request<ConversationAcknowledgement>(`/api/v3/customer/conversations/acknowledgement?${parameters}`,actorToken,"GET");
      if(!matches())return null;
      if(!acknowledgementMatchesPending(found,original))
        throw new Error("查回内容尚未完整核验");
      if(found.status!=="ACCEPTED"||!found.turn||!found.conversation){
        setWriteError(conversationRecoveryCopy().notFound);return null;
      }
      const saved=storedPending(actorScope);
      if(matchesConversationAcknowledgement(saved,original)&&!safeSessionRemove(pendingKey(actorScope)))setStorageNotice(conversationRecoveryCopy().acknowledgedStorage);
      setPendingRecord(previous=>matchesConversationAcknowledgement(previous,original)?null:previous);
      setAcceptedRecord({token:actorToken,turn:found.turn});
      selectedIdRef.current=found.conversation.conversation_id;
      setSelection({scope:actorScope,id:found.conversation.conversation_id});
      setPendingDelay(null);safeSessionSet(selectedKey(actorScope),found.conversation.conversation_id);index.refresh();detail.refresh();
      return found.turn;
    }catch(error){if(matches()){
      const info=customerRecovery(error,{operation:"read"});
      if(info.retryAfterMs!==undefined){setPendingDelay(beginRecoveryDelay({actorEpoch:generation,taskId:original.conversation_id||original.conversation_request_id,operationId:original.payload.client_request_id},info.retryAfterMs,performance.now()));setPendingClock(performance.now());}
      setWriteError(`${info.kind==="RATE_LIMITED"?`${info.message} `:""}${conversationRecoveryCopy().readFailed}`);
    }return null;}
    finally{
      if(writeLock.current===operationId)writeLock.current=null;
      // A background pause rejects the old read result, but must release this
      // same actor's UI lock; a replaced actor/generation remains untouched.
      if(tokenRef.current===actorToken&&scopeRef.current===actorScope&&actorGeneration.current===generation)setWriteBusy(false);
    }
  }
  async function retryUnderstanding(turn: ConversationTurn): Promise<ConversationTurn | null> {
    if (!token || !scope || writeLock.current || pending || pendingRecoveryIncomplete || historyPermissionDenied
      || tokenRef.current !== token || scopeRef.current !== scope || selectedIdRef.current !== currentId
      || selectionGeneration.current !== boundSelectionGeneration || !readVisibilityRef.current.visible) return null;
    const latest = turnsRef.current.at(-1);
    if (!latest || latest.turn_id !== turn.turn_id) return null;
    const version = understandingRetryVersion(latest, { conversationId: currentId, latestTurnId: latest.turn_id,
      blocked: detail.loading || Boolean(detail.error) || detail.paused });
    if (version === null || understandingRetryVersion(turn, { conversationId: currentId, latestTurnId: latest.turn_id, blocked: false }) !== version) return null;
    const actorToken = token, actorScope = scope, generation = actorGeneration.current,
      visibilityEpoch = readVisibilityRef.current.epoch, selectedGeneration = ++selectionGeneration.current;
    const operationId = crypto.randomUUID(); writeLock.current = operationId; setWriteBusy(true); setWriteError("");
    const matches = () => tokenRef.current === actorToken && scopeRef.current === actorScope
      && actorGeneration.current === generation && selectedIdRef.current === currentId
      && selectionGeneration.current === selectedGeneration && readVisibilityRef.current.visible
      && readVisibilityRef.current.epoch === visibilityEpoch;
    try {
      const result = await request<ConversationTurn>(`/api/v3/customer/conversations/${encodeURIComponent(currentId)}/turns/${encodeURIComponent(turn.turn_id)}/retry-understanding`,
        actorToken, "POST", { expected_retry_version: version });
      if (!matches()) return null;
      if (!understandingRetryAcknowledged(result, latest)) throw new Error("Retry acknowledgement does not match the original message");
      setAcceptedRecord({ token: actorToken, turn: result }); index.refresh(); detail.refresh();
      return result;
    } catch {
      if (matches()) { setWriteError(conversationRecoveryCopy().readFailed); index.refresh(); detail.refresh(); }
      return null;
    } finally {
      if (writeLock.current === operationId) writeLock.current = null;
      if (tokenRef.current === actorToken && scopeRef.current === actorScope && actorGeneration.current === generation) setWriteBusy(false);
    }
  }
  async function loadEarlier() {
    if (
      !readVisibility.visible ||
      !token ||
      !currentId ||
      !hasEarlier ||
      !beforeCursor ||
      earlierLock.current
    )
      return;
    earlierLock.current = true;
    historyAutoRetryBlocked.current=false;
    setEarlierRecoveryNeeded(false);
    setEarlierLoading(true);
    setEarlierError("");
    const actorToken = token,
      conversationId = currentId,
      windowBefore = detailRef.current?.turns[0]?.sequence ?? beforeCursor,
      controller = new AbortController();
    earlierAbort.current = controller;
    try {
      const page = await readAuthorizedHistoryWindow(
        cursor => request<Conversation>(
          `/api/v3/customer/conversations/${encodeURIComponent(conversationId)}?before_sequence=${cursor}`,
          actorToken, "GET", undefined, undefined, { signal: controller.signal },
        ), conversationId, beforeCursor, 1, 1,
      );
      if (
        controller.signal.aborted ||
        tokenRef.current !== actorToken ||
        selectedIdRef.current !== conversationId
      )
        return;
      setEarlier((previous) => ({
        token: actorToken,
        conversationId,
        turns: mergeConversationPages(
          page.turns || [],
          previous?.token === actorToken &&
            previous.conversationId === conversationId
            ? previous.turns
            : [],
        ).slice(-HISTORY_TURN_LIMIT),
        hasEarlier: page.hasEarlier && (page.turns.length + (previous?.turns.length || 0)) < HISTORY_TURN_LIMIT,
        cursor: page.cursor,
        verifiedBefore: windowBefore,
        checkedAt: Date.now(),
      }));
      if (page.turns.length + (earlierRef.current?.turns.length || 0) >= HISTORY_TURN_LIMIT)
        setEarlierError(HISTORY_WINDOW_LIMIT_NOTICE);
    } catch (error) {
      if (
        !controller.signal.aborted &&
        tokenRef.current === actorToken &&
        selectedIdRef.current === conversationId
      ) {
        historyAutoRetryBlocked.current=true;
        setEarlierRecoveryNeeded(true);
        if (error && typeof error === "object" && "status" in error && [401,403,404].includes(Number(error.status))) {
          setHistoryDenied({ token: actorToken, conversationId });
          setEarlier(null);
          setAcceptedRecord(null);
        }
        setEarlierError(
          customerRecovery(error, { operation: "read" }).message,
        );
      }
    } finally {
      if (earlierAbort.current === controller) {
        earlierLock.current = false;
        setEarlierLoading(false);
      }
    }
  }
  async function revalidateEarlier(before: number, desiredOldest: number) {
    if (earlierLock.current || !token || !currentId || historyPermissionDenied || !readVisibility.visible) return;
    earlierLock.current = true;
    setEarlierLoading(true);
    setEarlierError("");
    setEarlierRecoveryNeeded(false);
    const actorToken = token, actorScope = scope, conversationId = currentId,
      generation = actorGeneration.current, controller = new AbortController();
    earlierAbort.current = controller;
    const matches = () => !controller.signal.aborted && actorToken === tokenRef.current &&
      actorScope === scopeRef.current && conversationId === selectedIdRef.current && generation === actorGeneration.current;
    // Remove old financial/doc explanations before reading fresh authorization.
    setEarlier(previous => previous?.token === actorToken && previous.conversationId === conversationId
      ? { ...previous, turns: pendingHistoryViews(previous.turns) } : previous);
    try {
      const fresh = await readAuthorizedHistoryWindow(
        cursor => request<Conversation>(`/api/v3/customer/conversations/${encodeURIComponent(conversationId)}?before_sequence=${cursor}`,
          actorToken, "GET", undefined, undefined, { signal: controller.signal }),
        conversationId, before, desiredOldest,
      );
      if (!matches()) return;
      historyAutoRetryBlocked.current=false;
      setEarlierRecoveryNeeded(false);
      historyNeedsRecheck.current = false;
      setEarlier({ token: actorToken, conversationId, turns: fresh.turns,
        hasEarlier: fresh.hasEarlier && !fresh.limitReached && fresh.turns.length < HISTORY_TURN_LIMIT,
        cursor: fresh.cursor, verifiedBefore: fresh.verifiedBefore, checkedAt: Date.now() });
      if (fresh.limitReached || fresh.turns.length >= HISTORY_TURN_LIMIT) setEarlierError(HISTORY_WINDOW_LIMIT_NOTICE);
    } catch (error) {
      if (!matches()) return;
      historyAutoRetryBlocked.current=true;
      setEarlierRecoveryNeeded(true);
      historyNeedsRecheck.current = false;
      if (error && typeof error === "object" && "status" in error && [401,403,404].includes(Number(error.status))) {
        setHistoryDenied({ token: actorToken, conversationId });
        setEarlier(null);
      }
      setEarlierError(customerRecovery(error, { operation: "read" }).message);
    } finally {
      if (earlierAbort.current === controller) { earlierLock.current = false; setEarlierLoading(false); }
    }
  }
  function abandonRetry() {
    if(tokenRef.current!==token||scopeRef.current!==scope||selectionGeneration.current!==boundSelectionGeneration||selectedIdRef.current!==currentId)return;
    if (writeLock.current || !pending&&!pendingRecoveryIncomplete) return;
    selectionGeneration.current += 1;
    safeSessionRemove(pendingKey(scope));setPendingRecoveryIncomplete(false);setPendingDelay(null);
    setPendingRecord(null);
    setWriteError(
      conversationRecoveryCopy().left,
    );
    index.refresh();
    detail.refresh();
  }
  return {
    items: index.data?.items || [],
    indexError: index.error,
    indexLoading: index.loading,
    conversation,
    turns,
    taskResults,
    currentId,
    loading: detail.loading,
    error: detail.error || pageIntegrityError,
    pending,
    writeBusy,
    writeError,
    storageNotice,
    pendingRecoveryIncomplete,
    pendingRecoveryDelay:{waiting:pendingDelayState.current&&!pendingDelayState.canRead,remainingSeconds:pendingDelayState.remainingSeconds,
      canRecover:Boolean(pending||pendingRecoveryIncomplete)&&!writeBusy&&readVisibility.visible&&(!pendingDelayState.current||pendingDelayState.canRead)},
    select,
    fresh,
    send,
    recoverPending,
    retryUnderstanding,
    abandonRetry,
    hasEarlier,
    earlierLoading,
    earlierError,
    earlierRecoveryNeeded,
    pausedRead: !readVisibility.visible,
    retryEarlier: () => {
      if (!readVisibility.visible || earlierLock.current) return;
      historyAutoRetryBlocked.current=false;
      setEarlierRecoveryNeeded(false);
      const current=earlierRef.current,first=detailRef.current?.turns[0]?.sequence;
      if(current?.turns.some(turn=>turn.error_code==="HISTORY_ACCESS_RECHECK")&&first){
        historyNeedsRecheck.current=false;
        waitingHistoryLatest.current=null;
        void revalidateEarlier(first,current.turns[0].sequence);
      }else if(current?.turns.some(turn=>turn.error_code==="HISTORY_ACCESS_RECHECK")){
        hideHistoricalAnswers();index.refresh();detail.refresh();
      }else void loadEarlier();
    },
    loadEarlier,
    refresh: () => {
      hideHistoricalAnswers();
      index.refresh();
      detail.refresh();
    },
    cancelRead: detail.cancel,
  };
}
