import { Component, type ErrorInfo, type ReactNode } from "react";
import { reportError } from "../errors";

interface Props { children: ReactNode; name?: string; resetKey?: string; full?: boolean; silent?: boolean }
interface State { error: Error | null }

/**
 * Catches a screen that crashes while drawing, so the rest of the app (tabs, navigation) keeps working: shows a calm
 * card with "Reload this screen" and sends the details to the server for Studio → Help → Troubleshooting.
 * `resetKey` (usually the route) clears the error when the person moves to another screen.
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    reportError("render", `${this.props.name ? this.props.name + ": " : ""}${error?.message ?? String(error)}`,
      `${error?.stack ?? ""}\n--- component stack ---${info?.componentStack ?? ""}`);
  }

  componentDidUpdate(prev: Props) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) this.setState({ error: null });
  }

  render() {
    if (!this.state.error) return this.props.children;
    if (this.props.silent) return null;                     // invisible helpers (key handling): just drop out
    const box = (
      <div className="card" role="alert" style={{ margin: 20, gap: 10 }}>
        <b style={{ fontWeight: 500, fontSize: 17 }}>Something went wrong on this screen</b>
        <span className="mute" style={{ fontSize: 14 }}>The rest of the app still works. It's been noted so it can be fixed.</span>
        <div className="row" style={{ justifyContent: "flex-start", gap: 10 }}>
          <button className="btn pri" onClick={() => this.setState({ error: null })}>Try again</button>
          <button className="btn" onClick={() => location.reload()}>Reload the app</button>
        </div>
      </div>
    );
    return this.props.full ? <div className="screen" style={{ justifyContent: "center" }}>{box}</div> : box;
  }
}
