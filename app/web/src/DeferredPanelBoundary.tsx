import { Component, type ReactNode } from "react";

/** A failed optional staff panel must not replace a customer's active conversation. */
export class DeferredPanelBoundary extends Component<{
  children: ReactNode; onClose?: () => void; onCloseLabel?: string; resetKey: string;
}, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidUpdate(previous: Readonly<{ resetKey: string }>) {
    if (previous.resetKey !== this.props.resetKey && this.state.failed) this.setState({ failed: false });
  }
  render() {
    if (!this.state.failed) return this.props.children;
    return <section className="deferred-panel-warning" role="alert">
      <h2>这部分页面暂未加载完成</h2>
      <p>已受理的办理不会因此取消，可以从原记录查回。尚未发送的内容请重新核对，网络恢复后再打开页面。</p>
      {this.props.onClose && <button className="button secondary" onClick={this.props.onClose}>{this.props.onCloseLabel || "关闭提示，返回当前页面"}</button>}
    </section>;
  }
}
