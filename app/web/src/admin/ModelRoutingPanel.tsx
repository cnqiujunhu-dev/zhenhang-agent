import { useEffect, useState } from "react";
import { request } from "../api";
import { Icon, Skeleton } from "../components";
import { useEndpoint } from "../useEndpoint";
import type { ModelRoutingSnapshot, RoutingPolicy } from "./types";

export function ModelRoutingPanel({ token }: { token: string }) {
  const endpoint = useEndpoint<ModelRoutingSnapshot>(
    "/api/v2/admin/model-routing",
    token,
  );
  const [policy, setPolicy] = useState<RoutingPolicy | null>(null);
  const [reason, setReason] = useState("");
  const [review, setReview] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  useEffect(() => {
    if (endpoint.data) setPolicy({ ...endpoint.data.policy });
  }, [endpoint.data]);
  const providers = endpoint.data?.registry.providers || [];
  const cloud = providers.filter((provider) => provider.is_cloud);
  const local = providers.filter((provider) => !provider.is_cloud);
  const selectedCloud = cloud.find(
    (provider) => provider.provider_id === policy?.cloud_provider,
  );
  async function save() {
    if (!policy || !endpoint.data || reason.trim().length < 4) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await request("/api/v2/admin/model-routing", token, "PUT", {
        expected_version: endpoint.data.policy.version,
        policy,
        reason: reason.trim(),
      });
      setReview(false);
      setReason("");
      endpoint.refresh();
      setNotice("路由配置已按版本保存，变更会进入运维审计。");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "路由保存未完成");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel admin-routing">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">受控注册模型</span>
          <h2>模型路由配置</h2>
        </div>
        <button className="button secondary compact" onClick={endpoint.refresh}>
          <Icon name="refresh" size={15} />
          刷新
        </button>
      </div>
      <div className="admin-panel-body">
        {endpoint.loading && !endpoint.data && <Skeleton lines={5} />}
        {endpoint.error && (
          <p className="login-error" role="alert">
            {endpoint.error}
          </p>
        )}
        {error && (
          <p className="login-error" role="alert">
            {error}
          </p>
        )}
        {notice && (
          <p className="login-notice" role="status">
            {notice}
          </p>
        )}
        {endpoint.data && policy && (
          <>
            <div className="routing-provider-grid">
              {providers.map((provider) => (
                <div className="routing-provider" key={provider.provider_id}>
                  <div>
                    <strong>{provider.provider_id}</strong>
                    <span>
                      {provider.is_cloud ? "云端注册提供方" : "本地提供方"}
                    </span>
                  </div>
                  <p>
                    {provider.configured ? "配置已登记" : "尚未配置"}
                    {provider.is_cloud
                      ? provider.credential_configured
                        ? " · 凭证已配置"
                        : " · 缺少凭证"
                      : ""}
                  </p>
                  <ul>
                    {provider.models.map((model) => (
                      <li key={model.model}>
                        <span>{model.model}</span>
                        <small>
                          {model.last_http_available === null
                            ? "HTTP 可用性未观测"
                            : model.last_http_available
                              ? "最近 HTTP 可用"
                              : "最近 HTTP 不可用"}
                        </small>
                        {model.circuit_status && (
                          <small>
                            {model.circuit_status === "OPEN"
                              ? "当前进程模型隔离中"
                              : "当前进程熔断未开启"}
                            {typeof model.availability_failures === "number"
                              ? ` · 连续可用性失败 ${model.availability_failures}`
                              : ""}
                            {typeof model.retry_after_seconds === "number"
                              ? ` · 可重试等待 ${model.retry_after_seconds.toFixed(0)} s`
                              : ""}
                            ；不代表共享提供方全局健康。
                          </small>
                        )}
                      </li>
                    ))}
                  </ul>
                  <small>
                    熔断状态：
                    {provider.circuit_status === "PER_MODEL_PROCESS_LOCAL"
                      ? "按模型观测、仅当前进程"
                      : provider.circuit_status === "NOT_ENABLED"
                        ? "未启用"
                        : provider.circuit_status}
                  </small>
                </div>
              ))}
            </div>
            <div className="admin-form routing-form">
              <label>
                路由方式
                <select
                  aria-label="模型路由方式"
                  value={policy.mode}
                  onChange={(event) =>
                    setPolicy({
                      ...policy,
                      mode: event.target.value as RoutingPolicy["mode"],
                    })
                  }
                >
                  <option value="AUTO">自动受控路由</option>
                  <option value="LOCAL_ONLY">仅本地模型</option>
                  <option value="PREFER_CLOUD">优先已注册云模型</option>
                  <option value="CLOUD_ONLY">
                    仅已注册云模型（不回退本地）
                  </option>
                </select>
              </label>
              <label>
                本地模型
                <select
                  aria-label="本地路由模型"
                  value={policy.local_model}
                  onChange={(event) =>
                    setPolicy({ ...policy, local_model: event.target.value })
                  }
                >
                  {local.flatMap((provider) =>
                    provider.models.map((model) => (
                      <option
                        key={`${provider.provider_id}-${model.model}`}
                        value={model.model}
                      >
                        {model.model}
                      </option>
                    )),
                  )}
                </select>
              </label>
              <label>
                云端提供方
                <select
                  aria-label="云端路由提供方"
                  value={policy.cloud_provider}
                  onChange={(event) => {
                    const provider = cloud.find(
                      (item) => item.provider_id === event.target.value,
                    );
                    setPolicy({
                      ...policy,
                      cloud_provider: event.target.value,
                      cloud_model: provider?.models[0]?.model || "",
                      ...(policy.cloud_secondary_model !== undefined
                        ? { cloud_secondary_model: null }
                        : {}),
                    });
                  }}
                >
                  {cloud.map((provider) => (
                    <option
                      key={provider.provider_id}
                      value={provider.provider_id}
                    >
                      {provider.provider_id}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                云端模型
                <select
                  aria-label="云端路由模型"
                  value={policy.cloud_model}
                  onChange={(event) =>
                    setPolicy({
                      ...policy,
                      cloud_model: event.target.value,
                      ...(policy.cloud_secondary_model === event.target.value
                        ? { cloud_secondary_model: null }
                        : {}),
                    })
                  }
                >
                  {selectedCloud?.models.map((model) => (
                    <option key={model.model} value={model.model}>
                      {model.model}
                    </option>
                  ))}
                </select>
              </label>
              {policy.cloud_secondary_model !== undefined && (
                <label>
                  云内次选模型（可选，不超出出站预算）
                  <select
                    aria-label="云内次选模型"
                    value={policy.cloud_secondary_model || ""}
                    onChange={(event) =>
                      setPolicy({
                        ...policy,
                        cloud_secondary_model: event.target.value || null,
                      })
                    }
                  >
                    <option value="">不指定次选模型</option>
                    {selectedCloud?.models
                      .filter((model) => model.model !== policy.cloud_model)
                      .map((model) => (
                        <option key={model.model} value={model.model}>
                          {model.model}
                        </option>
                      ))}
                  </select>
                </label>
              )}
              {policy.standard_read_fastpath !== undefined && (
                <label className="routing-enabled">
                  <input
                    type="checkbox"
                    aria-label="允许标准只读编译器路径"
                    checked={policy.standard_read_fastpath}
                    onChange={(event) =>
                      setPolicy({
                        ...policy,
                        standard_read_fastpath: event.target.checked,
                      })
                    }
                  />
                  <span>允许标准只读编译器（不等同Laya，不跳过写授权）</span>
                </label>
              )}
              <label className="routing-enabled">
                <input
                  type="checkbox"
                  aria-label="允许已注册云模型路由"
                  checked={policy.cloud_enabled}
                  onChange={(event) =>
                    setPolicy({
                      ...policy,
                      cloud_enabled: event.target.checked,
                    })
                  }
                />
                <span>允许已注册云端路由</span>
              </label>
              <label>
                单次规划出站尝试上限
                <input
                  type="number"
                  aria-label="模型出站尝试上限"
                  min={1}
                  max={3}
                  value={policy.max_http_attempts}
                  onChange={(event) =>
                    setPolicy({
                      ...policy,
                      max_http_attempts: Number(event.target.value),
                    })
                  }
                />
              </label>
              <label>
                单次规划耗时预算（秒）
                <input
                  type="number"
                  aria-label="模型耗时预算"
                  min={1}
                  max={360}
                  value={policy.max_elapsed_seconds}
                  onChange={(event) =>
                    setPolicy({
                      ...policy,
                      max_elapsed_seconds: Number(event.target.value),
                    })
                  }
                />
              </label>
              <label className="full-width">
                变更理由
                <textarea
                  aria-label="模型路由变更理由"
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  rows={2}
                  minLength={4}
                  maxLength={200}
                  placeholder="请说明为什么调整路由"
                />
              </label>
            </div>
            <p className="admin-small-note">
              {policy.cloud_secondary_model &&
                `已登记云内次选模型 ${policy.cloud_secondary_model}。`}
              {policy.standard_read_fastpath === true &&
                "标准只读编译器已由服务端登记，不能等同于Laya推理收益。"}
              {policy.mode === "CLOUD_ONLY" &&
                "仅在已登记云端路由内按预算尝试；云端故障不会自动切换本地模型。"}
              当前版本 v{endpoint.data.policy.version}
              。配置保存不发起模型测试；后续真实规划遵循该配置及受控预算。
            </p>
            {review ? (
              <div className="admin-change-review">
                <strong>确认保存路由策略</strong>
                <p>
                  {policy.mode} · 本地 {policy.local_model} · 云端{" "}
                  {policy.cloud_provider}/{policy.cloud_model} · 云路由
                  {policy.cloud_enabled ? "允许" : "关闭"} · 最多{" "}
                  {policy.max_http_attempts} 次 / {policy.max_elapsed_seconds}{" "}
                  秒
                </p>
                <p>理由：{reason}</p>
                <div>
                  <button
                    className="button primary"
                    onClick={save}
                    disabled={busy}
                  >
                    {busy ? "正在保存…" : "确认保存配置"}
                  </button>
                  <button
                    className="button secondary"
                    onClick={() => setReview(false)}
                    disabled={busy}
                  >
                    返回修改
                  </button>
                </div>
              </div>
            ) : (
              <button
                className="button primary"
                disabled={
                  reason.trim().length < 4 ||
                  !Number.isInteger(policy.max_http_attempts) ||
                  policy.max_http_attempts < 1 ||
                  policy.max_http_attempts > 3 ||
                  policy.max_elapsed_seconds <= 0 ||
                  !Number.isInteger(policy.max_elapsed_seconds) ||
                  policy.max_elapsed_seconds > 360
                }
                onClick={() => setReview(true)}
              >
                核对路由变更
                <Icon name="arrow" size={16} />
              </button>
            )}
          </>
        )}
      </div>
    </section>
  );
}
