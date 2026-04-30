import { Component, ReactNode } from "react";

type State = { error: Error | null };

export class ErrorBoundary extends Component<
  { children: ReactNode },
  State
> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error) {
    // eslint-disable-next-line no-console
    console.error("[ErrorBoundary]", error);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="content">
          <div className="empty">
            <div className="empty-emoji">⚠️</div>
            <div className="empty-title">یک خطای غیرمنتظره رخ داد</div>
            <div className="muted" style={{ direction: "ltr" }}>
              {this.state.error.message}
            </div>
            <button
              className="btn btn-secondary mt-3"
              style={{ width: "auto" }}
              onClick={() => window.location.reload()}
            >
              بارگذاری مجدد
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
