import React, { useState } from "react";
import {
  BrowserRouter,
  Routes,
  Route,
  NavLink,
  Link,
  Navigate,
  useNavigate,
} from "react-router-dom";
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
} from "@tanstack/react-query";
import {
  LayoutDashboard,
  Activity,
  Database,
  Layers,
  ShieldCheck,
  Users,
  Bot,
  SlidersHorizontal,
  Workflow,
  Terminal,
  Briefcase,
  ScrollText,
  Search,
  Menu,
  LogOut,
  ArrowRight,
} from "lucide-react";
import { Button, Modal, Empty, ErrorBox, Loading } from "./src/components/ui";
import {
  ResourceList,
  ResourceRoute,
  resources,
  AccessSimulator,
} from "./src/features/resources";
import { Overview, Audit, Jobs } from "./src/features/operations";
import Onboarding from "./src/features/onboarding";
import Playground from "./src/features/playground";
import { queryOptions, control } from "./src/api";
import { signIn, signOut } from "./src/auth";
const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 15000, refetchOnWindowFocus: true },
    mutations: { retry: false },
  },
});
const nav = [
  ["overview", "Overview", LayoutDashboard, "blue"],
  ["sources", "Sources", Database, "blue"],
  ["datasets", "Data products", Layers, "teal"],
  ["collections", "Collections", Layers, "purple"],
  ["policies", "Policies", ShieldCheck, "red"],
  ["guardrails", "Guardrails", SlidersHorizontal, "orange"],
  ["clients", "Consumers", Users, "teal"],
  ["agents", "Agents & MCP", Bot, "purple"],
  ["indexes", "Indexes & pipelines", Workflow, "blue"],
  ["playground", "Playground", Terminal, "purple"],
  ["jobs", "Jobs & exports", Briefcase, "orange"],
  ["audit", "Audit", ScrollText, "teal"],
  ["monitoring", "Monitoring", Activity, "red"],
];
function Shell() {
  const [mobile, setMobile] = useState(false);
  const [command, setCommand] = useState(false);
  const [term, setTerm] = useState("");
  const [authError, setAuthError] = useState(null);
  const session = useQuery({
    ...queryOptions(["session"], `${control}/session`),
    enabled: location.pathname !== "/logout",
  });
  const navigate = useNavigate();
  React.useEffect(() => {
    function key(e) {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setCommand((v) => !v);
      }
    }
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);
  if (location.pathname === "/logout")
    return (
      <div className="auth-page">
        <div className="auth-card">
          <h1>You have been signed out</h1>
          <p>Your local Control Hub session has ended.</p>
          <a className="button primary" href="/">
            Sign back in
          </a>
        </div>
      </div>
    );
  if (session.isPending)
    return (
      <div className="auth-page">
        <Loading />
      </div>
    );
  if (session.isError)
    return (
      <div className="auth-page">
        <div className="auth-card">
          <span className="brand-mark">J&J</span>
          <div className="eyebrow">SMARTHUB · CONTROL HUB</div>
          <h1>
            Your data.
            <br />
            Governed.
          </h1>
          <p>
            Sign in with your enterprise identity to manage data products,
            policies and agents.
          </p>
          <ErrorBox error={authError || session.error} />
          <div className="actions">
            <Button
              variant="primary"
              onClick={async () => {
                try {
                  await signIn();
                  session.refetch();
                } catch (e) {
                  setAuthError(e);
                }
              }}
            >
              Sign in
              <ArrowRight size={16} />
            </Button>
            <Button onClick={() => session.refetch()}>Retry connection</Button>
          </div>
        </div>
      </div>
    );
  return (
    <div className="app-shell">
      <a href="#main" className="skip-link">
        Skip to content
      </a>
      <aside className={`sidebar ${mobile ? "mobile-open" : ""}`}>
        <Link
          to="/overview"
          className="brand"
          aria-label="SmartHub MCP & Agentic Gateway"
        >
          <span className="brand-mark">J&J</span>
          <div>
            <strong>SmartHub</strong>
            <small>MCP & Agentic Gateway</small>
          </div>
        </Link>
        <div className="nav-label">CONTROL HUB</div>
        <nav aria-label="Main navigation">
          {nav.map(([id, label, Icon, color]) => (
            <NavLink
              key={id}
              to={`/${id}`}
              onClick={() => setMobile(false)}
              className={({ isActive }) =>
                `nav-item ${isActive ? "active" : ""}`
              }
            >
              <span className={`nav-icon ${color}`}>
                <Icon size={18} />
              </span>
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          <ShieldCheck size={18} />
          <div>
            <strong>Governed access</strong>
            <small>Signed-in permissions apply</small>
          </div>
        </div>
      </aside>
      <div className="app-main">
        <header className="topbar">
          <Button
            variant="mobile-menu"
            aria-label="Toggle navigation"
            onClick={() => setMobile((v) => !v)}
          >
            <Menu size={20} />
          </Button>
          <span className="topbar-title">Enterprise Data Platform</span>
          <Button variant="command-trigger" onClick={() => setCommand(true)}>
            <Search size={16} />
            Go to a screen<kbd>⌘ K</kbd>
          </Button>
          <span className="environment">
            {session.data.environment?.toUpperCase()}
          </span>
          <span className="identity" title={session.data.subject}>
            {session.data.subject}
          </span>
          <Button
            aria-label="Sign out"
            onClick={async () => {
              await signOut();
              queryClient.clear();
              session.refetch();
            }}
          >
            <LogOut size={17} />
          </Button>
        </header>
        <main id="main">
          <Routes>
            <Route path="/" element={<Navigate to="/overview" replace />} />
            <Route path="/overview" element={<Overview />} />
            <Route path="/monitoring" element={<Overview monitoring />} />
            {Object.keys(resources).map((type) => (
              <React.Fragment key={type}>
                <Route
                  path={`/${type}`}
                  element={<ResourceList type={type} />}
                />
                <Route
                  path={`/${type}/:id`}
                  element={<ResourceRoute type={type} />}
                />
              </React.Fragment>
            ))}
            <Route path="/datasets/new" element={<Onboarding />} />
            <Route path="/playground" element={<Playground />} />
            <Route path="/audit" element={<Audit />} />
            <Route path="/jobs" element={<Jobs />} />
            <Route
              path="*"
              element={
                <Empty
                  title="Screen not found"
                  action={
                    <Link to="/overview" className="button">
                      Back to overview
                    </Link>
                  }
                />
              }
            />
          </Routes>
        </main>
      </div>
      {command && (
        <Modal title="Go to a screen" onClose={() => setCommand(false)}>
          <div className="modal-body">
            <FieldSearch term={term} setTerm={setTerm} />
            <div className="command-results">
              {nav
                .filter(([, label]) =>
                  label.toLowerCase().includes(term.toLowerCase()),
                )
                .map(([id, label, Icon]) => (
                  <button
                    key={id}
                    onClick={() => {
                      navigate(`/${id}`);
                      setCommand(false);
                      setTerm("");
                    }}
                  >
                    <Icon size={18} />
                    {label}
                    <ArrowRight size={15} />
                  </button>
                ))}
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
function FieldSearch({ term, setTerm }) {
  return (
    <input
      aria-label="Find screen"
      autoFocus
      placeholder="Search screens…"
      value={term}
      onChange={(e) => setTerm(e.target.value)}
    />
  );
}
class Boundary extends React.Component {
  state = { error: null };
  static getDerivedStateFromError(error) {
    return { error };
  }
  render() {
    return this.state.error ? (
      <div className="auth-page">
        <ErrorBox
          error={
            new Error(
              "This screen could not be displayed. Reload to try again.",
            )
          }
          retry={() => location.reload()}
        />
      </div>
    ) : (
      this.props.children
    );
  }
}
export default function EnterpriseControlHub() {
  return (
    <Boundary>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Shell />
        </BrowserRouter>
      </QueryClientProvider>
    </Boundary>
  );
}
