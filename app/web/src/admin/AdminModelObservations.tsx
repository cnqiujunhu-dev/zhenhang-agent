import { StructuredData } from "../components";
import type { JsonValue } from "../types";
import type { AdminModelObservations as Observations } from "./types";
import { PurposeObservationMetrics } from "./PurposeObservationMetrics";

const number = (value: number | null) =>
  value === null ? "未记录" : value.toLocaleString("zh-CN");
const tokenNames: Record<string, string> = {
  input_tokens: "输入Token",
  output_tokens: "输出Token",
  total_tokens: "总Token",
  cached_input_tokens: "缓存输入",
  reasoning_tokens: "已计费用量中的推理Token",
};
export function AdminModelObservations({ data }: { data: Observations }) {
  return (
    <section className="panel admin-model-observations">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">理解 · 咨询 · 结果叙述 · 全局只读运维</span>
          <h2>模型与路由观测</h2>
        </div>
      </div>
      <div className="admin-panel-body">
        <PurposeObservationMetrics data={data.purpose_observations}/>
        <h3 className="admin-section-title">历史规划与确定性编译记录</h3>
        <p className="admin-small-note">以下为兼容保留的规划统计，不含统一需求理解和工具后叙述；当前调用请看上方分目的表。</p>
        <div className="admin-overview-grid model-metric-grid">
          <div>
            <span>规划调用记录</span>
            <strong>{number(data.planning_invocations)}</strong>
          </div>
          <div>
            <span>已知规划出站请求尝试</span>
            <strong>{number(data.observed_api_calls)}</strong>
            <small>{data.api_call_counts_unknown} 项调用计数未知</small>
          </div>
          <div>
            <span>规则回退记录</span>
            <strong>{number(data.fallback_count)}</strong>
            <small>
              {data.fallback_rate === null
                ? "回退率尚无样本"
                : `${(data.fallback_rate * 100).toFixed(1)}% 已记录调用`}
            </small>
          </div>
          <div>
            <span>路由降级记录</span>
            <strong>{number(data.route_fallback_count)}</strong>
            <small>{data.route_observations} 项路由观测</small>
          </div>
        </div>
        <div className="admin-summary-grid">
          {[
            { title: "规划结果", values: data.by_outcome },
            { title: "规划来源", values: data.by_provider },
            { title: "实际路由提供方", values: data.by_actual_provider },
          ].map((section) => (
            <div key={section.title}>
              <h3>{section.title}</h3>
              <div className="admin-distribution">
                {Object.keys(section.values).length ? (
                  Object.entries(section.values).map(([label, count]) => (
                    <div key={label}>
                      <span>{label}</span>
                      <strong>{count}</strong>
                    </div>
                  ))
                ) : (
                  <p>暂无已记录数据</p>
                )}
              </div>
            </div>
          ))}
        </div>
        <h3 className="admin-section-title">已记录Token用量</h3>
        <div className="admin-table-scroll">
          <table>
            <thead>
              <tr>
                <th>用量类别</th>
                <th>已知记录合计</th>
                <th>完整总量</th>
                <th>已知 / 未知记录</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(data.token_usage).map(([field, value]) => (
                <tr key={field}>
                  <td>{tokenNames[field] || field}</td>
                  <td>{number(value.observed_sum)}</td>
                  <td>{number(value.complete_total)}</td>
                  <td>
                    {value.known_observations} / {value.unknown_observations}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="admin-small-note">
          仅统计已记录的任务规划版本，包含规则与模型，不含咨询消息与图片处理调用，不是提供方全量账单。历史版本缺失的出站统计不补造。Token未知不当作零，不以HTTP可用性推断模型语义正确率，也不保留思维链。
        </p>
        <details className="structured-technical">
          <summary>统计范围与原始观测</summary>
          <StructuredData data={data as unknown as JsonValue} technical />
        </details>
      </div>
    </section>
  );
}
