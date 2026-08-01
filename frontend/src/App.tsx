import { Component, type ReactNode } from "react";
import { Shell } from "./components/Shell";
import { useHashRoute } from "./lib/router";
import { useAuth } from "./state/store";
import { Analytics } from "./views/Analytics";
import { Chat } from "./views/Chat";
import { Dashboard } from "./views/Dashboard";
import { Edges } from "./views/Edges";
import { Login } from "./views/Login";
import { Logbook } from "./views/Logbook";
import { Morning } from "./views/Morning";
import { Positions } from "./views/Positions";
import { Research } from "./views/Research";
import { Settings } from "./views/Settings";
import { Signals } from "./views/Signals";

/* A view that throws must degrade to a readable message, not a dead page —
   "won't load" on a phone is undebuggable without this. */
class ViewBoundary extends Component<{ view: string; children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidUpdate(prev: { view: string }) {
    if (prev.view !== this.props.view && this.state.error) this.setState({ error: null });
  }
  render() {
    if (this.state.error) {
      return (
        <div className="empty">
          This view hit an error: {String(this.state.error.message || this.state.error)}
          <span className="hint">
            <button className="btn btn-ghost btn-sm" onClick={() => location.reload()}>
              Reload the app
            </button>
          </span>
        </div>
      );
    }
    return this.props.children;
  }
}

export default function App() {
  const { authed, booting } = useAuth();
  const route = useHashRoute();

  if (booting) return null;
  if (!authed) return <Login />;

  let body;
  let view = route.view;
  if (view === "inbox") view = "positions"; // old bookmarks
  if (view === "strangles") view = "morning"; // tab absorbed into the digest 2026-07-27
  switch (view) {
    case "dashboard":
      body = <Dashboard />;
      break;
    case "positions":
      body = <Positions />;
      break;
    case "signals":
      body = <Signals />;
      break;
    case "research":
      body = <Research symbolParam={route.param} />;
      break;
    case "logbook":
      body = <Logbook dateParam={route.param} />;
      break;
    case "analytics":
      body = <Analytics />;
      break;
    case "edges":
      body = <Edges />;
      break;
    case "chat":
      body = <Chat />;
      break;
    case "settings":
      body = <Settings />;
      break;
    case "morning":
    default:
      body = <Morning />;
      break;
  }

  return (
    <Shell view={view || "morning"}>
      <ViewBoundary view={view || "morning"}>{body}</ViewBoundary>
    </Shell>
  );
}
