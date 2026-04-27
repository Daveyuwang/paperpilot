import React from "react";

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends React.Component<{ children: React.ReactNode }, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("[PaperPilot] Uncaught error:", error, info.componentStack);
  }

  render() {
    if (!this.state.hasError) return this.props.children;

    return (
      <div className="flex min-h-screen items-center justify-center bg-surface-50 px-6">
        <div className="max-w-md text-center">
          <h1 className="text-lg font-semibold text-surface-800">Something went wrong</h1>
          <p className="mt-2 text-sm text-surface-500">
            An unexpected error occurred. You can try reloading the page.
          </p>
          {this.state.error && (
            <pre className="mt-4 max-h-32 overflow-auto rounded-lg bg-surface-100 px-4 py-3 text-left text-xs text-surface-600">
              {this.state.error.message}
            </pre>
          )}
          <button
            onClick={() => window.location.reload()}
            className="mt-6 rounded-lg bg-accent-600 px-5 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-700"
          >
            Reload
          </button>
        </div>
      </div>
    );
  }
}
