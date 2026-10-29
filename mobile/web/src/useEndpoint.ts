import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, isAbortError, request } from "./api";
import { useReadVisibility } from "./useReadVisibility";

/** Read-only loading can be cancelled; cancelling never changes a bank task. */
export function useEndpoint<T>(
  path: string | null,
  token: string,
  revision = 0,
) {
  const visibility = useReadVisibility();
  const visibilityRef = useRef(visibility);
  visibilityRef.current = visibility;
  const [result, setResult] = useState<{
    path: string;
    token: string;
    value: T;
    epoch: number;
    receipt: number;
  } | null>(null);
  const [loading, setLoading] = useState(false);
  const [failure, setFailure] = useState<{
    path: string;
    token: string;
    message: string;
    status?: number;
  } | null>(null);
  const [cancelled, setCancelled] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const controller = useRef<AbortController | null>(null);
  const cancelledRef = useRef(false);
  const contextRef = useRef("");
  const lastRead = useRef({ epoch: -1, started: -Infinity, inFlight: false });
  const readSequence = useRef(0);
  const startedRead = useRef({ path: "", token: "", epoch: -1, receipt: 0 });
  useEffect(() => {
    if (!path || !token) return;
    const context = `${token}:${path}`;
    if (contextRef.current !== context) { contextRef.current = context; cancelledRef.current = false; }
    if (!visibility.visible || cancelledRef.current) { setLoading(false); return; }
    const abort = new AbortController();
    const receipt = ++readSequence.current;
    startedRead.current = { path, token, epoch: visibility.epoch, receipt };
    controller.current = abort;
    setLoading(true);
    setFailure(null);
    setCancelled(false);
    lastRead.current = { epoch: visibility.epoch, started: Date.now(), inFlight: true };
    request<T>(path, token, "GET", undefined, undefined, {
      signal: abort.signal,
    })
      .then((value) => {
        if (!abort.signal.aborted) setResult({ path, token, value, epoch: visibility.epoch, receipt });
      })
      .catch((reason) => {
        if (!abort.signal.aborted && reason instanceof ApiError && [401, 403, 404].includes(reason.status))
          setResult(null);
        if (!abort.signal.aborted && !isAbortError(reason))
          setFailure({
            path,
            token,
            message: reason instanceof Error ? reason.message : "读取暂未完成",
            status: reason instanceof ApiError ? reason.status : undefined,
          });
      })
      .finally(() => {
        if (controller.current === abort) { lastRead.current.inFlight = false; setLoading(false); }
      });
    return () => abort.abort();
  }, [path, token, revision, refreshKey, visibility.visible, visibility.epoch]);
  useEffect(() => {
    setResult(null);
  }, [path, token]);
  const cancel = useCallback(() => {
    controller.current?.abort();
    cancelledRef.current = true;
    lastRead.current.inFlight = false;
    setLoading(false);
    setCancelled(true);
  }, []);
  const refresh = useCallback((options?: { automatic?: boolean } | unknown) => {
    const automatic = Boolean(options && typeof options === "object" && "automatic" in options && options.automatic);
    if (automatic && (!visibilityRef.current.visible || cancelledRef.current || lastRead.current.inFlight ||
      lastRead.current.epoch !== visibilityRef.current.epoch || Date.now() - lastRead.current.started < 1000)) return;
    cancelledRef.current = false;
    setCancelled(false);
    setRefreshKey((value) => value + 1);
  }, []);
  const data =
    visibility.visible && result?.epoch === visibility.epoch && result.path === path && result.token === token ? result.value : null;
  const currentFailure =
    failure?.path === path && failure.token === token ? failure : null;
  return {
    data,
    // Monotonic only for this mounted hook; scoped exactly like its returned
    // data. Pending/failure transitions never mint a successful read receipt.
    readReceipt: data !== null ? result?.receipt ?? 0 : 0,
    readGeneration: startedRead.current.path === path && startedRead.current.token === token && startedRead.current.epoch === visibility.epoch ? startedRead.current.receipt : 0,
    getReadGeneration: () => startedRead.current.path === path && startedRead.current.token === token && startedRead.current.epoch === visibility.epoch ? startedRead.current.receipt : 0,
    loading,
    error: currentFailure?.message || "",
    errorStatus: currentFailure?.status,
    cancelled,
    paused: !visibility.visible && !cancelled,
    cancel,
    refresh,
  };
}
