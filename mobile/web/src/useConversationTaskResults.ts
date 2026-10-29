import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, request } from "./api";
import type { ConversationTurn } from "./conversationTypes";
import type { TaskTrace } from "./types";
import { customerRecovery } from "./customerRecovery";
import { useReadVisibility } from "./useReadVisibility";
import { useConversationReadSnapshots } from "./useConversationReadSnapshots";
import {
  ConversationTaskResultReader, historyResultCandidates,
  type ConversationReadAttempt, type ConversationTaskResult, type ConversationTaskResults, type ConversationTaskResultsSnapshot,
} from "./conversationTaskResults";

const EMPTY_RESULTS:ConversationTaskResultsSnapshot={items:{},remaining:0,loading:false,permissionBlocked:false};

export function useConversationTaskResults(
  token: string, scope: string, conversationId: string,
  turns: ConversationTurn[], permissionDenied = false,
  verifyConversation?: () => Promise<ConversationTurn[]>,
): ConversationTaskResults {
  const visibility=useReadVisibility();
  const visibilityRef=useRef(visibility);
  visibilityRef.current=visibility;
  const [record, setRecord] = useState<{ token: string; scope: string; conversationId: string; snapshot: ConversationTaskResultsSnapshot } | null>(null);
  const readerRef = useRef<ConversationTaskResultReader | null>(null);
  const readerContext=useRef<{token:string;scope:string;conversationId:string}|null>(null);
  const currentRef = useRef({ token, scope, conversationId });
  const verifyConversationRef = useRef(verifyConversation);
  verifyConversationRef.current = verifyConversation;
  const accessBusyRef = useRef(false);
  const accessAttempt = useRef(0);
  const turnsRef = useRef(turns);
  turnsRef.current = turns;
  const permissionDeniedRef = useRef(permissionDenied);
  permissionDeniedRef.current = permissionDenied;
  const retryCalls = useRef(new Map<string, Promise<ConversationReadAttempt>>());
  const [retryFeedback, setRetryFeedback] = useState<{ token: string; scope: string; conversationId: string; epoch: number;
    reader: ConversationTaskResultReader | null; generation: number; receipts: Record<string, number>;
    items: Record<string, ConversationTaskResult> } | null>(null);
  const [accessCheck, setAccessCheck] = useState<{ token: string; scope: string; conversationId: string; busy: boolean; error: string } | null>(null);
  currentRef.current = { token, scope, conversationId };
  const calculated = useMemo(()=>historyResultCandidates(turns),[turns]);
  const candidateKey = useMemo(()=>JSON.stringify(calculated),[calculated]);
  // The conversation merger returns new arrays on render; stable candidates
  // avoid a state/reader notification loop and needless history requests.
  const candidates = useMemo(() => calculated, [candidateKey]);
  const candidatesRef = useRef(candidates);
  candidatesRef.current = candidates;

  useEffect(() => {
    if (!token || !scope || !conversationId) { readerRef.current = null; setRecord(null); return; }
    const reader = new ConversationTaskResultReader(
      (id, signal) => request<TaskTrace>(`/api/v3/customer/tasks/${encodeURIComponent(id)}`, token, "GET", undefined, undefined, { signal }),
      snapshot => {
        const current = currentRef.current;
        if (current.token === token && current.scope === scope && current.conversationId === conversationId)
          {
            setRecord({ token, scope, conversationId, snapshot });
            setRetryFeedback(previous => {
              if (previous?.reader !== reader) return previous;
              const items = { ...previous.items };
              for (const id of Object.keys(items)) if (snapshot.items[id]?.status === "READY"
                && (snapshot.items[id].readReceipt ?? 0) > (previous.receipts[id] ?? 0)) delete items[id];
              return Object.keys(items).length === Object.keys(previous.items).length ? previous : { ...previous, items };
            });
          }
      },
      error => error instanceof ApiError ? error.status : undefined,
    );
    readerRef.current = reader;
    readerContext.current={token,scope,conversationId};
    retryCalls.current.clear();
    setRetryFeedback(null);
    if(!visibilityRef.current.visible)reader.pause();
    reader.update(candidatesRef.current);
    const revalidate = () => { if (document.visibilityState === "visible") reader.revalidate({ coalesce: true }); };
    const expire = () => { if (document.visibilityState === "visible") reader.expire(); };
    const invalid = (event: Event) => {
      if ((event as CustomEvent<{ token?: string }>).detail?.token === token) reader.blockPermissions();
    };
    window.addEventListener("focus", revalidate);
    window.addEventListener("zx-auth-invalid", invalid);
    const interval = window.setInterval(expire, 10_000);
    return () => {
      accessBusyRef.current = false;
      accessAttempt.current += 1;
      retryCalls.current.clear();
      reader.dispose();
      if (readerRef.current === reader) readerRef.current = null;
      if(readerContext.current?.token===token&&readerContext.current.conversationId===conversationId)readerContext.current=null;
      window.removeEventListener("focus", revalidate);
      window.removeEventListener("zx-auth-invalid", invalid);
      clearInterval(interval);
    };
  }, [token, scope, conversationId]);

  useEffect(() => { readerRef.current?.update(candidates); }, [candidates]);
  useEffect(()=>{if(visibility.visible)readerRef.current?.resume();else {readerRef.current?.pause();accessBusyRef.current=false;accessAttempt.current+=1;retryCalls.current.clear();setRetryFeedback(null);setAccessCheck(null);}},[visibility.visible,visibility.epoch]);
  useEffect(() => { if (permissionDenied) readerRef.current?.blockPermissions(); }, [permissionDenied, token, scope, conversationId]);
  const matching = record?.token === token && record.scope === scope && record.conversationId === conversationId;
  const matchingCheck = accessCheck?.token === token && accessCheck.scope === scope && accessCheck.conversationId === conversationId;
  const baseSnapshot = matching && !permissionDenied && visibility.visible ? record.snapshot : EMPTY_RESULTS;
  const retryItems = retryFeedback?.token === token && retryFeedback.scope === scope && retryFeedback.conversationId === conversationId
    && retryFeedback.epoch === visibility.epoch && visibility.visible && !permissionDenied && !baseSnapshot.permissionBlocked
    && retryFeedback.reader === readerRef.current && retryFeedback.generation === readerRef.current?.getReadGeneration()
    ? retryFeedback.items : EMPTY_RESULTS.items;
  const snapshot = useMemo(() => retryItems === EMPTY_RESULTS.items ? baseSnapshot : { ...baseSnapshot,
    items: { ...baseSnapshot.items, ...retryItems },
    loading: baseSnapshot.loading || Object.values(retryItems).some(item => item.status === "LOADING"),
  }, [baseSnapshot, retryItems]);
  const historyReads=useConversationReadSnapshots(token,scope,conversationId,turns,snapshot.items,
    permissionDenied||snapshot.permissionBlocked,visibility.visible,visibility.epoch);
  useEffect(()=>{if(historyReads.snapshot.permissionBlocked)readerRef.current?.blockPermissions();},[historyReads.snapshot.permissionBlocked]);
  // A bank query may be complete while its conversational explanation is
  // still queued. Poll only those authorized records, never rerun a tool.
  useEffect(() => {
    if (!visibility.visible || permissionDenied || snapshot.permissionBlocked || snapshot.paused) return;
    const ids = Object.entries(snapshot.items).filter(([, entry]) => entry.status === "READY"
      && ["QUEUED", "PROCESSING"].includes(entry.trace?.customer_reply_status || "")).map(([id]) => id);
    if (!ids.length) return;
    const bound = readerRef.current;
    const timer = window.setTimeout(() => {
      if (readerRef.current !== bound || !visibilityRef.current.visible || permissionDeniedRef.current) return;
      for (const id of ids) bound?.refreshNarration(id);
    }, 1800);
    return () => clearTimeout(timer);
  }, [snapshot, visibility.visible, visibility.epoch, permissionDenied]);
  const currentReader=useCallback(()=>{
    const context=readerContext.current,current=currentRef.current;
    return context?.token===current.token&&context.scope===current.scope&&context.conversationId===current.conversationId?readerRef.current:null;
  },[]);
  const retry=useCallback((id:string):Promise<ConversationReadAttempt>=>{
    const reader = currentReader(), context = { ...currentRef.current }, epoch = visibilityRef.current.epoch;
    const generation = reader?.getReadGeneration() ?? -1;
    const key = JSON.stringify([context.token, context.scope, context.conversationId, epoch, generation, id]);
    const existing = retryCalls.current.get(key);
    if (existing) return existing;
    const matches = () => currentRef.current.token === context.token && currentRef.current.scope === context.scope
      && currentRef.current.conversationId === context.conversationId && currentReader() === reader
      && visibilityRef.current.visible && visibilityRef.current.epoch === epoch && !permissionDeniedRef.current
      && !reader?.getSnapshot().permissionBlocked && (reader?.getReadGeneration() ?? -1) === generation;
    const expectedVersion = () => Math.max(-1, ...turnsRef.current.filter(turn => turn.conversation_id === context.conversationId
      && turn.task_link?.task_id === id && Number.isSafeInteger(turn.task_link.version) && turn.task_link.version >= 0)
      .map(turn => turn.task_link!.version));
    const initialVersion = expectedVersion();
    const feedback = (status?: "LOADING" | "ERROR") => {
      if (!matches()) return;
      setRetryFeedback(previous => {
        const items = previous?.token === context.token && previous.scope === context.scope
          && previous.conversationId === context.conversationId && previous.epoch === epoch && previous.reader === reader
          && previous.generation === generation ? { ...previous.items } : {};
        const receipts = previous?.reader === reader && previous.generation === generation ? { ...previous.receipts } : {};
        if (status) items[id] = { status, version: Math.max(initialVersion, expectedVersion(), 0) };
        else delete items[id];
        receipts[id] = reader?.getSnapshot().items[id]?.readReceipt ?? 0;
        return { ...context, epoch, reader, generation, receipts, items };
      });
    };
    // A candidate may lag a just-finished active result. Reauthorize its actual
    // conversation before adding an ID; a click never manufactures a candidate.
    const attempt = Promise.resolve().then(async ():Promise<ConversationReadAttempt> => {
      if (!matches()) return "INTERRUPTED";
      if (!reader || !context.token || !context.scope || !context.conversationId || initialVersion < 0) {
        feedback("ERROR"); return "UNAVAILABLE";
      }
      try {
        if (reader.retry(id)) { feedback(); return "STARTED"; }
        const verify = verifyConversationRef.current;
        if (!verify) { feedback("ERROR"); return "UNAVAILABLE"; }
        feedback("LOADING");
        const authorizedTurns = await verify();
        if (!matches()) return "INTERRUPTED";
        if (!Array.isArray(authorizedTurns) || authorizedTurns.some(turn => turn.conversation_id !== context.conversationId)) {
          feedback("ERROR"); return "UNAVAILABLE";
        }
        const authorizedCandidates = historyResultCandidates(authorizedTurns);
        const authorized = authorizedCandidates.find(candidate => candidate.taskId === id);
        if (!authorized || authorized.version < Math.max(initialVersion, expectedVersion())) {
          feedback("ERROR"); return "UNAVAILABLE";
        }
        if (!reader.retryAuthorizedCandidate(authorized)) { feedback("ERROR"); return "UNAVAILABLE"; }
        feedback();
        return "STARTED";
      } catch (error) {
        if (!matches()) return "INTERRUPTED";
        if (error instanceof ApiError && [401, 403, 404].includes(error.status)) reader.blockPermissions();
        feedback("ERROR");
        return "UNAVAILABLE";
      }
    }).finally(() => { if (retryCalls.current.get(key) === attempt) retryCalls.current.delete(key); });
    retryCalls.current.set(key, attempt);
    return attempt;
  },[currentReader]);
  const loadMore=useCallback(()=>currentReader()?.loadMore(),[currentReader]);
  const refresh=useCallback(()=>currentReader()?.revalidate(),[currentReader]);
  const recheckAccess=useCallback(async()=>{
      if (accessBusyRef.current || !verifyConversationRef.current || !token || !conversationId || !visibilityRef.current.visible) return;
      accessBusyRef.current = true;
      const ticket = ++accessAttempt.current;
      const reader = currentReader();
      const visibilityEpoch=visibilityRef.current.epoch;
      const readGeneration = reader?.getReadGeneration();
      const ownsCheck = () => accessAttempt.current === ticket && currentRef.current.token === token && currentRef.current.scope === scope &&
        currentRef.current.conversationId === conversationId && readerRef.current === reader &&
        visibilityRef.current.visible && visibilityRef.current.epoch===visibilityEpoch;
      const matches = () => ownsCheck() && reader?.getReadGeneration() === readGeneration;
      setAccessCheck({ token, scope, conversationId, busy: true, error: "" });
      try {
        const authorizedTurns = await verifyConversationRef.current();
        if (matches()) {reader?.restoreAccess(historyResultCandidates(authorizedTurns));historyReads.restoreAccess();}
      } catch (error) {
        if (matches()) setAccessCheck({ token, scope, conversationId, busy: false,
          error: customerRecovery(error, { operation: "read" }).message });
      } finally {
        if (ownsCheck()) {
          accessBusyRef.current = false;
          setAccessCheck(previous => previous ? { ...previous, busy: false } : null);
        }
      }
    },[token,scope,conversationId,currentReader]);
  const checking=Boolean(matchingCheck&&accessCheck.busy),checkError=matchingCheck?accessCheck.error:"";
  return useMemo(()=>({
    ...snapshot,
    historyReads:historyReads.snapshot,
    permissionBlocked: permissionDenied || snapshot.permissionBlocked || historyReads.snapshot.permissionBlocked,
    paused: !visibility.visible,
    recheckingAccess: checking,recheckError:checkError,
    retry,loadMore,refresh,recheckAccess,
  }),[snapshot,historyReads.snapshot,permissionDenied,visibility.visible,checking,checkError,retry,loadMore,refresh,recheckAccess]);
}
