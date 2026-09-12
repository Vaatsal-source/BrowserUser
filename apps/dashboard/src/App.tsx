import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import {
  Activity,
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  Check,
  CheckCheck,
  ChevronRight,
  CircleHelp,
  Copy,
  Database,
  Eye,
  EyeOff,
  FileText,
  Fingerprint,
  FolderLock,
  Globe2,
  HardDrive,
  KeyRound,
  LayoutDashboard,
  LoaderCircle,
  LockKeyhole,
  LogOut,
  MoreHorizontal,
  Play,
  Plus,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
  Square,
  Trash2,
  Upload,
  X,
  Zap,
  Pause,
  RefreshCw,
  AlertCircle,
  Terminal,
} from "lucide-react";
import { api, getToken, post, remove, setToken } from "./api";
import { VoiceInput } from "./VoiceInput";
import { VisualApproval } from "./VisualApproval";
type Page = "overview" | "vault" | "documents" | "activity" | "settings";
type Task = {
  id: string;
  status: string;
  step: number;
  goal: string;
  events?: Array<{ time: string; message: string; kind: string }>;
  pending?: {
    id: string;
    kind: string;
    title: string;
    payload: unknown;
  } | null;
  request?: unknown;
  error?: string;
  result?: unknown;
};
type Status = {
  vault: { initialized: boolean; unlocked: boolean };
  browser: { connected: boolean };
  provider: { mode: string; model: string; configured: boolean };
  task: Task | null;
};
type RecordItem = {
  id: string;
  label: string;
  field_type: string;
  value: string;
  scope?: string;
  source?: string;
  version?: number;
};
type Doc = {
  id: string;
  name?: string;
  filename?: string;
  status?: string;
  created_at?: string;
  candidate_count?: number;
  candidates?: Candidate[];
  warnings?: string[];
};
type Candidate = {
  label: string;
  field_type: string;
  value: string;
  selected: boolean;
  scope: string;
  confidence?: number;
  source?: string;
};
const fieldTypes = [
  ["person_name", "Full name"],
  ["full_name", "Full name (document)"],
  ["email", "Email address"],
  ["phone", "Phone number"],
  ["address", "Address"],
  ["pan", "PAN"],
  ["money", "Amount / total"],
  ["amount", "Amount (document)"],
  ["statement_total", "Statement total"],
  ["date_of_birth", "Date of birth"],
  ["bank_account", "Bank account"],
  ["ifsc", "IFSC"],
  ["aadhaar", "Aadhaar"],
  ["postal_code", "Postal code"],
  ["date", "Date"],
  ["text", "Other text"],
];
const terminalStates = [
  "completed",
  "failed",
  "stopped",
  "blocked",
  "outcome_unknown",
];
const formatStatus = (s: string) => s.replaceAll("_", " ");
const formatScope = (scope?: string) =>
  scope?.startsWith("document:")
    ? "Document · " + scope.slice(-6)
    : scope || "profile";
const formatSource = (source?: string) =>
  source?.startsWith("document:")
    ? "Source document · " + source.slice(-6)
    : source?.startsWith("local sum")
      ? source.split(":")[0]
      : source || "Manually confirmed";
const displayValue = (v: unknown): string =>
  typeof v === "string" ? v : (JSON.stringify(v, null, 2) ?? "");
function Brand() {
  return (
    <div className="brand">
      <span className="brand-mark">
        <Fingerprint size={25} />
      </span>
      <div>
        dev<span className="brand-dot">.</span>
        <span className="brand-secondary">privacy guard</span>
      </div>
    </div>
  );
}
function Button({
  children,
  className = "",
  busy = false,
  ...rest
}: {
  children: ReactNode;
  className?: string;
  busy?: boolean;
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...rest}
      className={"button " + className}
      disabled={rest.disabled || busy}
    >
      {busy ? <LoaderCircle className="spin" size={16} /> : null}
      {children}
    </button>
  );
}
function Empty({
  icon,
  title,
  detail,
  children,
}: {
  icon: ReactNode;
  title: string;
  detail: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <span className="empty-icon">{icon}</span>
      <h3>{title}</h3>
      <p>{detail}</p>
      {children}
    </div>
  );
}
function Pill({
  children,
  tone = "muted",
}: {
  children: ReactNode;
  tone?: string;
}) {
  return (
    <span className={"pill " + tone}>
      <span />
      {children}
    </span>
  );
}
function App() {
  const [paired, setPaired] = useState(!!getToken()),
    [status, setStatus] = useState<Status | null>(null),
    [page, setPage] = useState<Page>(() => {
      const route = new URLSearchParams(location.hash.slice(1));
      const page = route.get("page");
      return [
        "overview",
        "vault",
        "documents",
        "activity",
        "settings",
      ].includes(page || "")
        ? (page as Page)
        : "overview";
    });
  const [records, setRecords] = useState<RecordItem[]>([]),
    [documents, setDocuments] = useState<Doc[]>([]),
    [tasks, setTasks] = useState<Task[]>([]),
    [selectedTask, setSelectedTask] = useState<Task | null>(null);
  const [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(""),
    [online, setOnline] = useState(true),
    [taskModal, setTaskModal] = useState(
      new URLSearchParams(location.hash.slice(1)).has("new"),
    );
  const selectedRef = useRef<string | null>(
    new URLSearchParams(location.hash.slice(1)).get("task"),
  );
  const load = useCallback(async (full = false) => {
    if (!getToken()) return;
    try {
      const s = await api<Status>("/status");
      setStatus(s);
      setOnline(true);
      if (s.task) {
        const current = s.task;
        setTasks((previous) =>
          previous.some((t) => t.id === current.id)
            ? previous.map((t) => (t.id === current.id ? current : t))
            : [current, ...previous],
        );
      }
      if (selectedRef.current) {
        try {
          setSelectedTask(await api<Task>("/tasks/" + selectedRef.current));
        } catch {
          selectedRef.current = null;
        }
      } else if (s.task) setSelectedTask(s.task);
      if (full) {
        const results = await Promise.allSettled([
          s.vault.unlocked ? api("/records") : Promise.resolve({ records: [] }),
          s.vault.unlocked
            ? api("/documents")
            : Promise.resolve({ documents: [] }),
          api("/tasks"),
        ]);
        results.forEach((r, i) => {
          if (r.status === "fulfilled") {
            if (i === 0) setRecords(r.value.records ?? []);
            if (i === 1) setDocuments(r.value.documents ?? []);
            if (i === 2) setTasks(r.value.tasks ?? []);
          }
        });
      }
    } catch (e) {
      setOnline(false);
      if (full) setError((e as Error).message);
    }
  }, []);
  useEffect(() => {
    const unpair = () => {
      setPaired(false);
      setStatus(null);
      setRecords([]);
      setDocuments([]);
      setTasks([]);
      setSelectedTask(null);
      selectedRef.current = null;
    };
    window.addEventListener("dpg:unpaired", unpair);
    return () => window.removeEventListener("dpg:unpaired", unpair);
  }, []);
  useEffect(() => {
    if (paired) {
      void load(true);
      const timer = setInterval(() => void load(), 2000);
      return () => clearInterval(timer);
    }
  }, [paired, load]);
  useEffect(() => {
    if (status && !status.vault.unlocked) {
      setRecords([]);
      setDocuments([]);
    }
  }, [status?.vault.unlocked]);
  useEffect(() => {
    if (notice) {
      const timer = setTimeout(() => setNotice(""), 5000);
      return () => clearTimeout(timer);
    }
  }, [notice]);
  const act = async (
    name: string,
    fn: () => Promise<unknown>,
    success?: string,
  ) => {
    setBusy(name);
    setError("");
    try {
      await fn();
      await load(true);
      if (success) setNotice(success);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  };
  const navigate = (p: Page) => {
    setPage(p);
    history.replaceState(null, "", "#page=" + p);
    setError("");
    void load(true);
  };
  const chooseTask = (task: Task) => {
    selectedRef.current = task.id;
    setSelectedTask(task);
    setPage("activity");
  };
  const activeTask =
    status?.task && !terminalStates.includes(status.task.status)
      ? status.task
      : null;
  const context = {
    status,
    records,
    documents,
    tasks,
    busy,
    act,
    navigate,
    chooseTask,
    setTaskModal,
  };
  if (!paired)
    return (
      <Pairing
        onPaired={() => {
          setPaired(true);
          setError("");
        }}
      />
    );
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Brand />
        <div className="workspace-label">
          YOUR WORKSPACE <span>LOCAL</span>
        </div>
        <nav aria-label="Main navigation">
          {(
            [
              { id: "overview", label: "Overview", icon: LayoutDashboard },
              { id: "vault", label: "Personal vault", icon: FolderLock },
              { id: "documents", label: "Documents", icon: FileText },
              { id: "activity", label: "Task activity", icon: Activity },
            ] as const
          ).map((n) => (
            <button
              key={n.id}
              aria-label={n.label}
              className={"nav-item " + (page === n.id ? "active" : "")}
              onClick={() => navigate(n.id)}
            >
              <n.icon size={19} />
              <span>{n.label}</span>
              {n.id === "activity" && activeTask ? (
                <span className="notification-dot" />
              ) : null}
              {page === n.id ? <ChevronRight size={15} /> : null}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="local-note">
            <ShieldCheck size={21} />
            <strong>A private place to work.</strong>
            <p>
              Your vault lives on this device. You control every disclosure.
            </p>
            <span>
              <span className={"status-dot " + (online ? "" : "offline")} />
              {online ? "Local companion connected" : "Companion offline"}
            </span>
          </div>
          <button
            className={"nav-item " + (page === "settings" ? "active" : "")}
            onClick={() => navigate("settings")}
          >
            <Settings2 size={18} />
            Connection & settings
          </button>
          <div className="sidebar-footer">
            <span className="avatar">ME</span>
            <div>
              <strong>Personal workspace</strong>
              <small>On this device</small>
            </div>
            <button
              aria-label="Lock vault"
              title="Lock vault"
              onClick={() => void act("lock", () => post("/vault/lock"))}
            >
              <LockKeyhole size={17} />
            </button>
          </div>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <div className="breadcrumb">
            Workspace <ChevronRight size={13} />
            <span>
              {
                {
                  overview: "Overview",
                  vault: "Personal vault",
                  documents: "Documents",
                  activity: "Task activity",
                  settings: "Connection & settings",
                }[page]
              }
            </span>
          </div>
          <div className="topbar-right">
            <Pill tone={online ? "green" : "amber"}>
              {online ? "Runs locally" : "Offline"}
            </Pill>
            <span className="topbar-divider" />
            <span className="version">PROTOTYPE / 02</span>
          </div>
        </header>
        <div className="content">
          {!online && (
            <div className="alert">
              <AlertCircle size={17} />
              <div>
                <strong>Local companion is unavailable.</strong> Start it in
                your terminal, then this workspace will reconnect automatically.
              </div>
              <Button className="small" onClick={() => void load(true)}>
                <RefreshCw size={14} />
                Retry
              </Button>
            </div>
          )}
          {error && (
            <div className="alert error" role="alert">
              <AlertCircle size={18} />
              <span>{error}</span>
              <button aria-label="Dismiss error" onClick={() => setError("")}>
                <X size={16} />
              </button>
            </div>
          )}
          {!status ? (
            <div className="loading">
              <LoaderCircle className="spin" />
              Connecting to your local workspace…
            </div>
          ) : !status.vault.unlocked ? (
            <VaultGate
              initialized={status.vault.initialized}
              busy={busy}
              submit={async (passphrase) => {
                await act(
                  "unlock",
                  () =>
                    post(
                      status.vault.initialized
                        ? "/vault/unlock"
                        : "/vault/initialize",
                      { passphrase },
                    ),
                  "Vault is ready.",
                );
              }}
            />
          ) : (
            <>
              {page === "overview" && <Overview {...context} />}
              {page === "vault" && (
                <Vault records={records} busy={busy} act={act} />
              )}
              {page === "documents" && (
                <Documents documents={documents} busy={busy} act={act} />
              )}
              {page === "activity" && (
                <ActivityPage
                  task={selectedTask || status.task}
                  records={records}
                  tasks={tasks}
                  chooseTask={chooseTask}
                  busy={busy}
                  act={act}
                  start={() => setTaskModal(true)}
                  navigate={navigate}
                />
              )}
              {page === "settings" && (
                <Settings
                  status={status}
                  busy={busy}
                  act={act}
                  unpair={() => {
                    setToken("");
                    setPaired(false);
                  }}
                />
              )}
            </>
          )}
        </div>
        <footer className="main-footer">
          <span>
            <ShieldCheck size={13} /> Your data. Your device. Your decision.
          </span>
          <span>
            DEV PRIVACY GUARD <span className="footer-sep">/</span> LOCAL
            WORKSPACE
          </span>
        </footer>
      </main>
      {notice && (
        <div className="toast" role="status">
          <CheckCheck size={18} />
          {notice}
        </div>
      )}
      {taskModal && status && (
        <NewTask
          status={status}
          records={records}
          close={() => setTaskModal(false)}
          busy={busy}
          act={act}
          onTask={(t) => {
            setTaskModal(false);
            chooseTask(t);
          }}
        />
      )}
    </div>
  );
}
function Pairing({ onPaired }: { onPaired: () => void }) {
  const [code, setCode] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function pair(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await post<{ token: string }>("/pair", {
        code: code.trim(),
      });
      setToken(result.token);
      onPaired();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="onboarding">
      <div className="onboarding-brand">
        <Brand />
        <Pill tone="green">Local workspace</Pill>
      </div>
      <div className="pair-grid">
        <section className="pair-story">
          <span className="eyebrow">PRIVATE BY DESIGN</span>
          <h1>
            Let the agent work.
            <br />
            <em>Keep your data close.</em>
          </h1>
          <p>
            A browser assistant with a vault on your device. Share references
            with the model. Reveal real details only where you choose.
          </p>
          <div className="privacy-diagram">
            <div>
              <Database size={24} />
              <strong>Your local vault</strong>
              <small>Real information</small>
            </div>
            <span className="diagram-line">→</span>
            <div className="diagram-model">
              <Sparkles size={24} />
              <strong>AI reasoning</strong>
              <small>Private references</small>
            </div>
            <span className="diagram-line">→</span>
            <div>
              <Globe2 size={24} />
              <strong>Your browser</strong>
              <small>Approved actions</small>
            </div>
          </div>
          <div className="pair-features">
            <span>
              <Check size={16} />
              Local document extraction
            </span>
            <span>
              <Check size={16} />
              Review before disclosure
            </span>
            <span>
              <Check size={16} />
              You stay in control
            </span>
          </div>
        </section>
        <section className="pair-card">
          <span className="large-icon">
            <KeyRound size={28} />
          </span>
          <span className="eyebrow">CONNECT THIS WORKSPACE</span>
          <h2>A quick, local handshake.</h2>
          <p>
            Enter the pairing code printed by the companion in your terminal.
            This authorizes this dashboard on your device.
          </p>
          <form onSubmit={pair}>
            <label htmlFor="pair-code">Pairing code</label>
            <input
              id="pair-code"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="Enter terminal pairing code"
              autoComplete="off"
              spellCheck={false}
              required
              autoFocus
            />
            {error && (
              <div className="form-error" role="alert">
                {error}
              </div>
            )}
            <Button className="primary full" busy={busy} type="submit">
              Connect workspace <ArrowRight size={17} />
            </Button>
          </form>
          <div className="hint">
            <Terminal size={17} />
            <span>
              Start the companion using the project’s setup instructions. Keep
              it running while you work.
            </span>
          </div>
          <div className="pair-card-footer">
            <LockKeyhole size={13} />
            Session credentials stay in this browser session.
          </div>
        </section>
      </div>
      <div className="onboarding-footer">
        DEV PRIVACY GUARD{" "}
        <span>Local companion · Browser Use · Your approval</span>
      </div>
    </div>
  );
}
function VaultGate({
  initialized,
  busy,
  submit,
}: {
  initialized: boolean;
  busy: string;
  submit: (passphrase: string) => Promise<void>;
}) {
  const [pass, setPass] = useState(""),
    [confirm, setConfirm] = useState(""),
    [error, setError] = useState("");
  return (
    <div className="vault-gate">
      <div className="gate-illustration">
        <span>
          <FolderLock size={52} />
        </span>
        <span className="orbit orbit-one" />
        <span className="orbit orbit-two" />
      </div>
      <Pill tone="green">STORED ON THIS DEVICE</Pill>
      <h1>
        {initialized
          ? "Welcome back to your vault."
          : "Your private workspace starts here."}
      </h1>
      <p>
        {initialized
          ? "Unlock your vault to access saved information and continue your work."
          : "Create a passphrase to encrypt your personal details and documents locally."}
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!initialized && pass !== confirm) {
            setError("Passphrases do not match.");
            return;
          }
          setError("");
          void submit(pass).then(() => {
            setPass("");
            setConfirm("");
          });
        }}
      >
        <label htmlFor="vault-pass">
          {initialized ? "Vault passphrase" : "Create a passphrase"}
        </label>
        <input
          id="vault-pass"
          type="password"
          autoComplete={initialized ? "current-password" : "new-password"}
          value={pass}
          onChange={(e) => setPass(e.target.value)}
          minLength={10}
          required
          placeholder={
            initialized ? "Enter your passphrase" : "At least 10 characters"
          }
        />
        {!initialized && (
          <>
            <label htmlFor="vault-confirm">Confirm passphrase</label>
            <input
              id="vault-confirm"
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              required
            />
          </>
        )}
        {error && <span className="form-error">{error}</span>}
        <Button className="primary full" type="submit" busy={busy === "unlock"}>
          <LockKeyhole size={16} />
          {initialized ? "Unlock local vault" : "Create encrypted vault"}
        </Button>
      </form>
      <small>
        {initialized
          ? "The companion must remain running during your task."
          : "Keep your passphrase safe. There is no account-based recovery."}
      </small>
    </div>
  );
}
function Overview({
  status,
  records,
  documents,
  tasks,
  busy,
  act,
  navigate,
  chooseTask,
  setTaskModal,
}: any) {
  const task = status.task as Task | null;
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">YOUR LOCAL COMMAND CENTER</span>
          <h1>
            A little less work.
            <br />
            <span className="subtle-heading">A lot more control.</span>
          </h1>
          <p>Give your agent a task. Keep your personal information here.</p>
        </div>
        <Button className="primary" onClick={() => setTaskModal(true)}>
          <Plus size={17} />
          New task
        </Button>
      </div>
      <div className="overview-grid">
        <section className="hero-card">
          <div className="hero-card-copy">
            <Pill tone="green">Privacy boundary active</Pill>
            <h2>
              Your details stay local.
              <br />
              Your agent gets references.
            </h2>
            <p>
              Review screenshots before they reach the model. Choose which saved
              details the agent may enter into a website.
            </p>
            <button className="text-link" onClick={() => navigate("activity")}>
              Explore task activity <ArrowUpRight size={16} />
            </button>
          </div>
          <div className="vault-visual" aria-hidden="true">
            <div className="visual-grid" />
            <div className="vault-tile">
              <Fingerprint size={45} />
              <span>LOCAL VAULT</span>
              <i />
              <i />
              <i />
            </div>
            <div className="reference-chip">
              <span />
              ref_01 <LockKeyhole size={12} />
            </div>
            <div className="reference-chip second">
              <span />
              ref_02 <LockKeyhole size={12} />
            </div>
            <div className="visual-caption">
              <ShieldCheck size={13} /> Approved references only
            </div>
          </div>
        </section>
        <section className="connection-card">
          <div className="card-top">
            <Globe2 size={20} />
            <Pill tone={status.browser.connected ? "green" : "amber"}>
              {status.browser.connected ? "Connected" : "Not connected"}
            </Pill>
          </div>
          <h3>Automation browser</h3>
          <p>
            {status.browser.connected
              ? "Your dedicated browser is ready. Start a task on a selected tab."
              : "Launch a dedicated browser to keep automation separate from everyday browsing."}
          </p>
          <Button
            className="secondary full"
            busy={busy === "browser"}
            onClick={() =>
              void act(
                "browser",
                () => post("/browser/launch"),
                "Automation browser is ready.",
              )
            }
          >
            <Globe2 size={16} />
            {status.browser.connected
              ? "Check browser connection"
              : "Launch browser"}
            <ArrowUpRight size={15} />
          </Button>
          <small>
            <span className="status-dot" />
            Runs on your computer
          </small>
        </section>
      </div>
      <div className="stats-row">
        <button className="stat-card" onClick={() => navigate("vault")}>
          <span className="stat-icon">
            <Database size={20} />
          </span>
          <div>
            <span>Saved information</span>
            <strong>
              {records.length}
              <small>confirmed fields</small>
            </strong>
          </div>
          <ArrowUpRight size={17} />
        </button>
        <button className="stat-card" onClick={() => navigate("documents")}>
          <span className="stat-icon">
            <FileText size={20} />
          </span>
          <div>
            <span>Local documents</span>
            <strong>
              {documents.length}
              <small>in your vault</small>
            </strong>
          </div>
          <ArrowUpRight size={17} />
        </button>
        <button className="stat-card" onClick={() => navigate("activity")}>
          <span className="stat-icon">
            <CheckCheck size={20} />
          </span>
          <div>
            <span>Completed tasks</span>
            <strong>
              {tasks.filter((t: Task) => t.status === "completed").length}
              <small>with your oversight</small>
            </strong>
          </div>
          <ArrowUpRight size={17} />
        </button>
      </div>
      <div className="lower-grid">
        <section className="panel">
          <div className="section-heading">
            <div>
              <span className="eyebrow">WORK IN PROGRESS</span>
              <h2>Task activity</h2>
            </div>
            <button className="text-link" onClick={() => navigate("activity")}>
              View all <ArrowRight size={14} />
            </button>
          </div>
          {task ? (
            <button className="task-summary" onClick={() => chooseTask(task)}>
              <span className="task-icon">
                <Activity size={21} />
              </span>
              <div>
                <strong>{task.goal}</strong>
                <small>
                  Step {task.step ?? 0} · {formatStatus(task.status)}
                </small>
              </div>
              <ChevronRight size={18} />
            </button>
          ) : (
            <Empty
              icon={<Activity size={27} />}
              title="Your next task starts here"
              detail="Ask the agent to fill a form using your saved profile. Follow every step as it happens."
            >
              <Button className="secondary" onClick={() => setTaskModal(true)}>
                <Plus size={15} />
                Create a task
              </Button>
            </Empty>
          )}
        </section>
        <section className="panel getting-started">
          <div className="section-heading">
            <div>
              <span className="eyebrow">FIRST THINGS FIRST</span>
              <h2>Make it yours</h2>
            </div>
            <span className="step-count">
              {Number(records.length > 0) +
                Number(documents.length > 0) +
                Number(status.browser.connected)}{" "}
              / 3
            </span>
          </div>
          {[
            {
              done: records.length > 0,
              title: "Add your personal details",
              text: "A profile your agent can use.",
              page: "vault",
              icon: Database,
            },
            {
              done: documents.length > 0,
              title: "Bring in a document",
              text: "Extract and review locally.",
              page: "documents",
              icon: FileText,
            },
            {
              done: status.browser.connected,
              title: "Connect your browser",
              text: "Open your automation workspace.",
              page: "settings",
              icon: Globe2,
            },
          ].map((s, i) => (
            <button
              className="setup-step"
              key={s.title}
              onClick={() => navigate(s.page)}
            >
              <span className={"step-number " + (s.done ? "done" : "")}>
                {s.done ? <Check size={15} /> : String(i + 1).padStart(2, "0")}
              </span>
              <div>
                <strong>{s.title}</strong>
                <small>{s.text}</small>
              </div>
              <ChevronRight size={15} />
            </button>
          ))}
          <div className="demo-note">
            <Sparkles size={16} />
            <div>
              <strong>Just exploring?</strong>
              <p>Use synthetic details for a safe first run.</p>
              <button
                className="text-link"
                disabled={!!busy}
                onClick={() =>
                  void act(
                    "seed",
                    () => post("/demo/seed"),
                    "Synthetic profile added to your vault.",
                  )
                }
              >
                Load demo profile <ArrowRight size={13} />
              </button>
            </div>
          </div>
        </section>
      </div>
    </>
  );
}
function Vault({
  records,
  busy,
  act,
}: {
  records: RecordItem[];
  busy: string;
  act: any;
}) {
  const [search, setSearch] = useState(""),
    [reveal, setReveal] = useState(false),
    [edit, setEdit] = useState<RecordItem | null | undefined>(undefined),
    [deleting, setDeleting] = useState<string | null>(null),
    [calculating, setCalculating] = useState(false);
  return (
    <>
      <div className="page-heading compact">
        <div>
          <span className="eyebrow">PRIVATE INFORMATION, ON YOUR DEVICE</span>
          <h1>Personal vault</h1>
          <p>Confirmed details become references your agent can use.</p>
        </div>
        <div className="button-row">
          <Button className="secondary" onClick={() => setCalculating(true)}>
            Calculate total
          </Button>
          <Button className="primary" onClick={() => setEdit(null)}>
            <Plus size={17} />
            Add information
          </Button>
        </div>
      </div>
      <div className="info-strip">
        <LockKeyhole size={18} />
        <span>
          Values are encrypted at rest and masked here by default. The model
          receives references.
        </span>
        <Pill tone="green">Vault unlocked</Pill>
      </div>
      <section className="panel">
        <div className="table-toolbar">
          <div className="search-input">
            <Search size={17} />
            <input
              aria-label="Search records"
              placeholder="Find a saved field…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <Button className="ghost small" onClick={() => setReveal(!reveal)}>
            {reveal ? <EyeOff size={16} /> : <Eye size={16} />}{" "}
            {reveal ? "Hide values" : "Reveal values"}
          </Button>
        </div>
        {records.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>FIELD</th>
                  <th>VALUE · LOCAL ONLY</th>
                  <th>SCOPE & SOURCE</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {records
                  .filter((r) =>
                    (r.label + " " + r.field_type)
                      .toLowerCase()
                      .includes(search.toLowerCase()),
                  )
                  .map((r) => (
                    <tr key={r.id}>
                      <td>
                        <div className="field-cell">
                          <span className="field-icon">
                            <Fingerprint size={17} />
                          </span>
                          <div>
                            <strong>{r.label}</strong>
                            <small>{formatStatus(r.field_type)}</small>
                          </div>
                        </div>
                      </td>
                      <td>
                        <span
                          className={
                            "record-value " + (!reveal ? "masked" : "")
                          }
                        >
                          {reveal ? displayValue(r.value) : "••••••••••••"}
                        </span>
                      </td>
                      <td>
                        <span className="scope-tag" title={r.scope}>
                          {formatScope(r.scope)}
                        </span>
                        <small className="source-text" title={r.source}>
                          {formatSource(r.source)}
                          {r.version ? ` · v${r.version}` : ""}
                        </small>
                      </td>
                      <td>
                        <div className="row-actions">
                          <button
                            onClick={() => setEdit(r)}
                            aria-label={"Edit " + r.label}
                          >
                            Edit
                          </button>
                          <button
                            onClick={() => setDeleting(r.id)}
                            aria-label={"Delete " + r.label}
                          >
                            <Trash2 size={15} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            icon={<Database size={30} />}
            title="Meet your local memory"
            detail="Add your name, contact details, and other information once. Reuse them with your approval."
          >
            <Button className="secondary" onClick={() => setEdit(null)}>
              <Plus size={15} />
              Add your first field
            </Button>
          </Empty>
        )}
      </section>
      <div className="bottom-note">
        <CircleHelp size={16} />
        <span>
          Documents can suggest new fields. Reviewing them first keeps your
          profile accurate.
        </span>
      </div>
      {calculating && (
        <Calculation
          records={records}
          busy={busy}
          close={() => setCalculating(false)}
          save={(body) =>
            act(
              "calculation",
              async () => {
                await post("/calculations/sum", body);
                setCalculating(false);
              },
              "Total calculated and saved locally.",
            )
          }
        />
      )}
      {edit !== undefined && (
        <RecordEditor
          record={edit}
          busy={busy}
          close={() => setEdit(undefined)}
          save={(body) =>
            act(
              "record",
              async () => {
                await post("/records", body);
                setEdit(undefined);
              },
              "Information saved locally.",
            )
          }
        />
      )}
      {deleting && (
        <Modal title="Delete this saved field?" close={() => setDeleting(null)}>
          <p className="modal-description">
            This removes the field from your vault. Tasks using its reference
            may need new information. Original source documents are managed
            separately.
          </p>
          <div className="modal-actions">
            <Button className="secondary" onClick={() => setDeleting(null)}>
              Keep field
            </Button>
            <Button
              className="danger"
              busy={busy === "delete-record"}
              onClick={() =>
                void act(
                  "delete-record",
                  async () => {
                    await remove("/records/" + deleting);
                    setDeleting(null);
                  },
                  "Saved field deleted.",
                )
              }
            >
              Delete field
            </Button>
          </div>
        </Modal>
      )}
    </>
  );
}
function Calculation({
  records,
  busy,
  close,
  save,
}: {
  records: RecordItem[];
  busy: string;
  close: () => void;
  save: (v: unknown) => void;
}) {
  const [ids, setIds] = useState<string[]>([]),
    [label, setLabel] = useState("Statement total"),
    [currency, setCurrency] = useState("INR");
  const amounts = records.filter((r) =>
    ["money", "amount", "statement_total", "total"].includes(r.field_type),
  );
  return (
    <Modal title="Calculate a local total" close={close}>
      <p className="modal-description">
        Select reviewed amounts from the same period and currency. The companion
        adds them with decimal arithmetic and saves a new reference. Check for
        duplicate transactions before continuing.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          save({ record_ids: ids, label, currency });
        }}
      >
        <label>Amount records</label>
        <div className="available-records">
          {amounts.length ? (
            amounts.map((r) => (
              <label key={r.id}>
                <input
                  type="checkbox"
                  checked={ids.includes(r.id)}
                  onChange={(e) =>
                    setIds(
                      e.target.checked
                        ? [...ids, r.id]
                        : ids.filter((id) => id !== r.id),
                    )
                  }
                />
                <span>
                  {r.label} · {r.value}
                </span>
                <small>
                  {r.scope?.startsWith("document:")
                    ? "document"
                    : r.scope || "profile"}
                </small>
              </label>
            ))
          ) : (
            <p>
              No reviewed amount fields. Upload a document or add amount records
              to your vault.
            </p>
          )}
        </div>
        <div className="form-grid">
          <label>
            Result label
            <input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              required
            />
          </label>
          <label>
            Currency
            <select
              value={currency}
              onChange={(e) => setCurrency(e.target.value)}
            >
              <option value="INR">INR</option>
              <option value="USD">USD</option>
              <option value="EUR">EUR</option>
              <option value="GBP">GBP</option>
            </select>
          </label>
        </div>
        <div className="modal-actions">
          <Button type="button" className="secondary" onClick={close}>
            Cancel
          </Button>
          <Button
            type="submit"
            className="primary"
            disabled={!ids.length}
            busy={busy === "calculation"}
          >
            Calculate & save
          </Button>
        </div>
      </form>
    </Modal>
  );
}
function RecordEditor({
  record,
  close,
  save,
  busy,
}: {
  record: RecordItem | null;
  close: () => void;
  save: (v: unknown) => void;
  busy: string;
}) {
  const [label, setLabel] = useState(record?.label || ""),
    [type, setType] = useState(record?.field_type || "person_name"),
    [value, setValue] = useState(record?.value || ""),
    [scope, setScope] = useState(record?.scope || "profile");
  return (
    <Modal
      title={record ? "Update saved information" : "Add information"}
      close={close}
    >
      <p className="modal-description">
        The actual value stays in your local vault. Use a generic field label
        without private details.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          save({
            label,
            field_type: type,
            value,
            scope,
            source: record?.source || "manual",
            ...(record ? { record_id: record.id } : {}),
          });
        }}
      >
        <label>
          Field label
          <input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="e.g. Applicant full name"
            required
            maxLength={100}
          />
        </label>
        <div className="form-grid">
          <label>
            Information type
            <select value={type} onChange={(e) => setType(e.target.value)}>
              {fieldTypes.map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </label>
          <label>
            Scope
            <select value={scope} onChange={(e) => setScope(e.target.value)}>
              {!["profile", "document", "task"].includes(scope) && (
                <option value={scope}>{formatScope(scope)}</option>
              )}
              <option value="profile">Reusable profile</option>
              <option value="document">Document information</option>
              <option value="task">This task</option>
            </select>
          </label>
        </div>
        <label>
          Actual value · local only
          <textarea
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="Enter the private value"
            required
            rows={3}
          />
        </label>
        <div className="modal-actions">
          <Button className="secondary" type="button" onClick={close}>
            Cancel
          </Button>
          <Button className="primary" type="submit" busy={busy === "record"}>
            <Check size={16} />
            Save to vault
          </Button>
        </div>
      </form>
    </Modal>
  );
}
function Documents({
  documents,
  busy,
  act,
}: {
  documents: Doc[];
  busy: string;
  act: any;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [review, setReview] = useState<{
      document: Doc;
      candidates: Candidate[];
      warnings: string[];
    } | null>(null),
    [deleting, setDeleting] = useState<Doc | null>(null),
    [drag, setDrag] = useState(false);
  const upload = (file?: File) => {
    if (!file) return;
    void act("upload", async () => {
      const data = new FormData();
      data.append("file", file);
      const result = await api("/documents", { method: "POST", body: data });
      setReview({
        ...result,
        candidates: (result.candidates || []).map((c: any) => ({
          ...c,
          selected: false,
          scope: "document:" + result.document.id,
        })),
        warnings: result.warnings || [],
      });
    });
  };
  return (
    <>
      <div className="page-heading compact">
        <div>
          <span className="eyebrow">EXTRACT HERE. REVIEW HERE. KEEP HERE.</span>
          <h1>Documents</h1>
          <p>
            Turn your documents into useful information without uploading them
            to a model.
          </p>
        </div>
        <Pill tone="green">Local extraction</Pill>
      </div>
      <input
        ref={input}
        type="file"
        accept=".pdf,.png,.jpg,.jpeg,.txt,.csv,.webp"
        hidden
        onChange={(e) => {
          upload(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
      <button
        className={"upload-zone " + (drag ? "dragging" : "")}
        disabled={busy === "upload"}
        onClick={() => input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDrag(false);
          upload(e.dataTransfer.files[0]);
        }}
      >
        <span className="upload-icon">
          {busy === "upload" ? (
            <LoaderCircle className="spin" size={27} />
          ) : (
            <Upload size={27} />
          )}
        </span>
        <h3>
          {busy === "upload"
            ? "Extracting on your device…"
            : "Drop a document into your workspace"}
        </h3>
        <p>
          or <span>browse files</span> from your device
        </p>
        <small>PDF, images, TXT or CSV · Up to 10 MiB</small>
      </button>
      <div className="document-flow">
        <span>
          <FileText size={16} />
          Upload locally
        </span>
        <ChevronRight size={15} />
        <span>
          <Search size={16} />
          Extract fields
        </span>
        <ChevronRight size={15} />
        <span>
          <CheckCheck size={16} />
          Review & confirm
        </span>
        <ChevronRight size={15} />
        <span>
          <FolderLock size={16} />
          Save to vault
        </span>
      </div>
      <section className="panel">
        <div className="section-heading">
          <h2>
            Document library <span className="count">{documents.length}</span>
          </h2>
          <Pill>Encrypted originals</Pill>
        </div>
        {documents.length ? (
          <div className="document-list">
            {documents.map((d) => (
              <div className="document-row" key={d.id}>
                <span className="document-icon">
                  <FileText size={23} />
                </span>
                <div>
                  <strong>{d.name || d.filename || "Local document"}</strong>
                  <small>
                    {d.created_at
                      ? new Date(d.created_at).toLocaleDateString()
                      : "Stored on this device"}{" "}
                    · {d.status ? formatStatus(d.status) : "Local original"}
                  </small>
                </div>
                {d.status !== "reviewed" && d.candidates && (
                  <Button
                    className="secondary small"
                    onClick={() =>
                      setReview({
                        document: d,
                        candidates: d.candidates!.map((c) => ({
                          ...c,
                          selected: false,
                          scope: "document:" + d.id,
                        })),
                        warnings: d.warnings || [],
                      })
                    }
                  >
                    Review fields
                  </Button>
                )}
                <button
                  className="icon-button"
                  aria-label={"Delete " + (d.name || d.filename || "document")}
                  onClick={() => setDeleting(d)}
                >
                  <Trash2 size={17} />
                </button>
              </div>
            ))}
          </div>
        ) : (
          <Empty
            icon={<FileText size={28} />}
            title="Your documents belong here"
            detail="Upload a statement or screenshot. Review suggested fields before adding anything to your vault."
          />
        )}
      </section>
      {review && (
        <Modal
          title="Review extracted information"
          wide
          close={() => setReview(null)}
        >
          <p className="modal-description">
            Verify each value against your document. Only checked fields will be
            saved. Keep document facts separate from your reusable profile.
          </p>
          {review.warnings.map((w, i) => (
            <div className="alert" key={i}>
              <AlertCircle size={16} />
              {w}
            </div>
          ))}
          {review.candidates.length ? (
            <div className="candidate-list">
              {review.candidates.map((c, i) => (
                <div
                  key={i}
                  className={"candidate " + (c.selected ? "selected" : "")}
                >
                  <label className="candidate-check">
                    <input
                      type="checkbox"
                      checked={c.selected}
                      onChange={(e) =>
                        setReview({
                          ...review,
                          candidates: review.candidates.map((x, j) =>
                            j === i ? { ...x, selected: e.target.checked } : x,
                          ),
                        })
                      }
                    />
                    <span>{c.label}</span>
                    {c.confidence !== undefined ? (
                      <small>
                        {Math.round(c.confidence * 100)}% extraction hint
                      </small>
                    ) : null}
                  </label>
                  <small className="source-text">
                    {c.source || "Local extraction"}
                  </small>
                  <label>
                    Field label
                    <input
                      aria-label={"Label for candidate " + (i + 1)}
                      value={c.label}
                      onChange={(e) =>
                        setReview({
                          ...review,
                          candidates: review.candidates.map((x, j) =>
                            j === i ? { ...x, label: e.target.value } : x,
                          ),
                        })
                      }
                    />
                  </label>
                  <div className="form-grid">
                    <label>
                      Value
                      <input
                        aria-label={"Value for " + c.label}
                        value={c.value}
                        onChange={(e) =>
                          setReview({
                            ...review,
                            candidates: review.candidates.map((x, j) =>
                              j === i ? { ...x, value: e.target.value } : x,
                            ),
                          })
                        }
                      />
                    </label>
                    <label>
                      Save to
                      <select
                        value={c.scope}
                        onChange={(e) =>
                          setReview({
                            ...review,
                            candidates: review.candidates.map((x, j) =>
                              j === i ? { ...x, scope: e.target.value } : x,
                            ),
                          })
                        }
                      >
                        <option value={"document:" + review.document.id}>
                          Document information
                        </option>
                        <option value="profile">Reusable profile</option>
                        <option value="task">Task information</option>
                      </select>
                    </label>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <Empty
              icon={<Search size={25} />}
              title="No supported fields found"
              detail="The original is saved locally. Try a clearer document or add information manually in your vault."
            />
          )}
          <div className="modal-actions">
            <Button className="secondary" onClick={() => setReview(null)}>
              Keep original only
            </Button>
            <Button
              className="primary"
              disabled={!review.candidates.some((c) => c.selected)}
              busy={busy === "review"}
              onClick={() =>
                void act(
                  "review",
                  async () => {
                    await post("/documents/" + review.document.id + "/review", {
                      candidates: review.candidates.map(
                        ({ label, field_type, value, selected, scope }) => ({
                          label,
                          field_type,
                          value,
                          selected,
                          scope,
                        }),
                      ),
                    });
                    setReview(null);
                  },
                  "Reviewed information saved locally.",
                )
              }
            >
              <CheckCheck size={16} />
              Confirm selected fields
            </Button>
          </div>
        </Modal>
      )}
      {deleting && (
        <Modal title="Delete this document?" close={() => setDeleting(null)}>
          <p className="modal-description">
            The encrypted original and its extraction candidates will be
            removed. Previously confirmed vault fields are managed separately in
            Personal vault.
          </p>
          <div className="modal-actions">
            <Button className="secondary" onClick={() => setDeleting(null)}>
              Keep document
            </Button>
            <Button
              className="danger"
              busy={busy === "delete-document"}
              onClick={() =>
                void act(
                  "delete-document",
                  async () => {
                    await remove("/documents/" + deleting.id);
                    setDeleting(null);
                  },
                  "Document deleted.",
                )
              }
            >
              Delete original
            </Button>
          </div>
        </Modal>
      )}
    </>
  );
}
function Approval({ task, busy, act }: { task: Task; busy: string; act: any }) {
  const p = task.pending;
  if (!p) return null;
  if (["image", "visual", "screenshot"].includes(p.kind))
    return <VisualApproval key={p.id} task={task} busy={busy} act={act} />;
  return (
    <section className="approval-card">
      <div className="approval-header">
        <span className="approval-icon">
          <ShieldCheck size={23} />
        </span>
        <div>
          <span className="eyebrow">YOUR APPROVAL IS REQUIRED</span>
          <h2>
            {p.title ||
              {
                model: "Review model-bound context",
                disclosure: "Approve disclosure to this website",
                submit: "Approve final submission",
              }[p.kind] ||
              "Review proposed action"}
          </h2>
        </div>
        <Pill tone="amber">Paused for you</Pill>
      </div>
      <p>
        {p.kind === "model"
          ? "Inspect the prepared payload below before it is sent for reasoning. If you spot private information, deny the request."
          : p.kind === "disclosure"
            ? "These references will resolve to real values on this device and be entered into the listed website. The website may receive them immediately."
            : "Review the destination and exact action. Approval allows this consequential action to execute."}
      </p>
      <pre className="payload" tabIndex={0}>
        {JSON.stringify(p.payload, null, 2)}
      </pre>
      <div className="approval-actions">
        <span>
          <LockKeyhole size={14} />
          Approval applies to this request only.
        </span>
        <Button
          className="secondary"
          busy={busy === "deny"}
          onClick={() =>
            void act("deny", () =>
              post("/tasks/" + task.id + "/approve", {
                approval_id: p.id,
                approved: false,
              }),
            )
          }
        >
          Deny
        </Button>
        <Button
          className="primary"
          busy={busy === "approve"}
          onClick={() =>
            void act("approve", () =>
              post("/tasks/" + task.id + "/approve", {
                approval_id: p.id,
                approved: true,
              }),
            )
          }
        >
          <Check size={17} />
          {p.kind === "model"
            ? "Approve context"
            : p.kind === "submit"
              ? "Approve submission"
              : "Approve disclosure"}
        </Button>
      </div>
    </section>
  );
}
function ActivityPage({
  task,
  records,
  tasks,
  chooseTask,
  busy,
  act,
  start,
  navigate,
}: {
  task: Task | null;
  records: RecordItem[];
  tasks: Task[];
  chooseTask: (t: Task) => void;
  busy: string;
  act: any;
  start: () => void;
  navigate: (page: Page) => void;
}) {
  const [showPayload, setShowPayload] = useState(false),
    [resume, setResume] = useState(false);
  const resumable =
    !!task &&
    ["paused", "waiting_input", "waiting_for_input"].includes(task.status);
  return (
    <>
      <div className="page-heading compact">
        <div>
          <span className="eyebrow">EVERY STEP, WITH YOUR OVERSIGHT</span>
          <h1>Task activity</h1>
          <p>Follow the agent, inspect its context, and stay in control.</p>
        </div>
        <Button className="primary" onClick={start}>
          <Plus size={17} />
          New task
        </Button>
      </div>
      {!task ? (
        <section className="panel">
          <Empty
            icon={<Activity size={32} />}
            title="No tasks yet. You're in control."
            detail="Start from your browser extension or enter a website here. Your agent's progress will appear in this workspace."
          >
            <Button className="secondary" onClick={start}>
              <Plus size={16} />
              Start your first task
            </Button>
          </Empty>
        </section>
      ) : (
        <>
          <section className="task-detail-header">
            <span className="task-icon">
              <Activity size={24} />
            </span>
            <div>
              <Pill
                tone={
                  task.status === "completed"
                    ? "green"
                    : task.status === "failed"
                      ? "red"
                      : "amber"
                }
              >
                {formatStatus(task.status)}
              </Pill>
              <h2>{task.goal}</h2>
              <small>
                Task {task.id.slice(0, 8)} · Step {task.step ?? 0}
              </small>
            </div>
            {!terminalStates.includes(task.status) && (
              <div className="task-controls">
                <Button
                  className="secondary small"
                  busy={busy === "control"}
                  onClick={() => {
                    if (resumable) setResume(true);
                    else
                      void act("control", () =>
                        post("/tasks/" + task.id + "/control", {
                          action: "pause",
                        }),
                      );
                  }}
                >
                  {resumable ? <Play size={15} /> : <Pause size={15} />}{" "}
                  {resumable ? "Review & resume" : "Pause"}
                </Button>
                <Button
                  className="danger-outline small"
                  busy={busy === "stop"}
                  onClick={() =>
                    void act("stop", () =>
                      post("/tasks/" + task.id + "/control", {
                        action: "stop",
                      }),
                    )
                  }
                >
                  <Square size={13} />
                  Stop
                </Button>
              </div>
            )}
          </section>
          {resumable && (
            <div className="info-strip">
              <CircleHelp size={17} />
              <span>
                Add or review missing information in the vault or documents,
                then choose the fields to use when resuming.
              </span>
              <button
                className="button secondary small"
                onClick={() => navigate("vault")}
              >
                Add details
              </button>
              <button
                className="button secondary small"
                onClick={() => navigate("documents")}
              >
                Upload document
              </button>
            </div>
          )}
          {resume && (
            <ResumeInformation
              records={records}
              close={() => setResume(false)}
              busy={busy}
              resume={(record_ids) =>
                act("control", async () => {
                  await post("/tasks/" + task.id + "/control", {
                    action: "resume",
                    record_ids,
                  });
                  setResume(false);
                })
              }
            />
          )}
          <Approval task={task} busy={busy} act={act} />
          {task.error && (
            <div className="alert error">
              <AlertCircle size={18} />
              {task.error}
            </div>
          )}
          {task.result && (
            <div
              className={
                "result-card " +
                (task.status === "waiting_input" ? "needs-input" : "")
              }
            >
              {task.status === "waiting_input" ? (
                <CircleHelp size={23} />
              ) : (
                <CheckCheck size={23} />
              )}
              <div>
                <strong>
                  {task.status === "waiting_input"
                    ? "Information needed"
                    : "Task result"}
                </strong>
                <p>
                  {typeof task.result === "string"
                    ? task.result
                    : JSON.stringify(task.result, null, 2)}
                </p>
              </div>
            </div>
          )}
          <section className="panel">
            <div className="section-heading">
              <h2>Execution timeline</h2>
              <span className="count">{task.events?.length || 0} events</span>
            </div>
            {task.events?.length ? (
              <div className="timeline">
                {task.events.map((ev, i) => (
                  <div className="timeline-event" key={i}>
                    <span
                      className={
                        "timeline-dot " +
                        (i === task.events!.length - 1 ? "latest" : "")
                      }
                    >
                      {ev.kind?.includes("error") ? (
                        <AlertCircle size={13} />
                      ) : (
                        <Check size={12} />
                      )}
                    </span>
                    <div>
                      <strong>{ev.message}</strong>
                      <small>{formatStatus(ev.kind || "event")}</small>
                    </div>
                    <time>
                      {ev.time
                        ? new Date(ev.time).toLocaleTimeString([], {
                            hour: "2-digit",
                            minute: "2-digit",
                            second: "2-digit",
                          })
                        : ""}
                    </time>
                  </div>
                ))}
              </div>
            ) : (
              <Empty
                icon={<LoaderCircle size={23} />}
                title="Waiting for the first event"
                detail="The task's next step will appear here automatically."
              />
            )}
          </section>
          {task.request && (
            <section className="panel payload-section">
              <button
                className="section-heading payload-toggle"
                onClick={() => setShowPayload(!showPayload)}
              >
                <h2>Last sanitized request</h2>
                {showPayload ? <EyeOff size={18} /> : <Eye size={18} />}
              </button>
              {showPayload && (
                <pre className="payload">
                  {JSON.stringify(task.request, null, 2)}
                </pre>
              )}
            </section>
          )}
        </>
      )}
      {tasks.length > 0 && (
        <section className="panel history">
          <div className="section-heading">
            <h2>Recent tasks</h2>
          </div>
          {tasks.map((t) => (
            <button
              className={"history-row " + (task?.id === t.id ? "selected" : "")}
              key={t.id}
              onClick={() => chooseTask(t)}
            >
              <Activity size={17} />
              <span>{t.goal}</span>
              <Pill tone={t.status === "completed" ? "green" : "muted"}>
                {formatStatus(t.status)}
              </Pill>
              <ChevronRight size={16} />
            </button>
          ))}
        </section>
      )}
    </>
  );
}
function ResumeInformation({
  records,
  close,
  busy,
  resume,
}: {
  records: RecordItem[];
  close: () => void;
  busy: string;
  resume: (ids: string[]) => void;
}) {
  const [available, setAvailable] = useState(records),
    [ids, setIds] = useState(
      records.filter((r) => !r.scope || r.scope === "profile").map((r) => r.id),
    );
  useEffect(() => {
    void api("/records")
      .then((r) => setAvailable(r.records || []))
      .catch(() => {});
  }, []);
  return (
    <Modal title="Review available information" close={close}>
      <p className="modal-description">
        Choose the confirmed fields this task may use. Include newly reviewed
        document information when needed. Resuming captures a fresh page and
        requests new approvals.
      </p>
      <div className="available-records">
        {available.map((r) => (
          <label key={r.id}>
            <input
              type="checkbox"
              checked={ids.includes(r.id)}
              onChange={(e) =>
                setIds(
                  e.target.checked
                    ? [...ids, r.id]
                    : ids.filter((id) => id !== r.id),
                )
              }
            />
            <span>{r.label}</span>
            <small>
              {formatScope(r.scope)} · {r.id.slice(-5)}
            </small>
          </label>
        ))}
      </div>
      <div className="modal-actions">
        <Button className="secondary" onClick={close}>
          Keep paused
        </Button>
        <Button
          className="primary"
          busy={busy === "control"}
          onClick={() => resume(ids)}
        >
          <Play size={15} />
          Resume with selected fields
        </Button>
      </div>
    </Modal>
  );
}
function Settings({
  status,
  busy,
  act,
  unpair,
}: {
  status: Status;
  busy: string;
  act: any;
  unpair: () => void;
}) {
  const [mode, setMode] = useState(status.provider.mode || "remote"),
    [model, setModel] = useState(status.provider.model || "gemini-2.5-flash"),
    [key, setKey] = useState(""),
    [fallbackKey, setFallbackKey] = useState(""),
    [fallbackModel, setFallbackModel] = useState("gpt-4.1-mini"),
    [fallbackConfigured, setFallbackConfigured] = useState(false),
    [removeFallback, setRemoveFallback] = useState(false),
    [whisperKey, setWhisperKey] = useState(""),
    [whisperConfigured, setWhisperConfigured] = useState(false),
    [base, setBase] = useState(
      "https://generativelanguage.googleapis.com/v1beta/openai",
    ),
    [tabs, setTabs] = useState<any[]>([]);
  useEffect(() => {
    void api("/settings")
      .then((r) => {
        setMode(r.mode || "remote");
        setModel(r.model || "gemini-2.5-flash");
        setBase(
          r.base_url ||
            "https://generativelanguage.googleapis.com/v1beta/openai",
        );
        setWhisperConfigured(!!r.whisper_configured);
        setFallbackModel(r.fallback_model || "gpt-4.1-mini");
        setFallbackConfigured(!!r.fallback_configured);
      })
      .catch(() => {});
  }, []);
  return (
    <>
      <div className="page-heading compact">
        <div>
          <span className="eyebrow">CONNECTED ON YOUR TERMS</span>
          <h1>Connection & settings</h1>
          <p>Manage your local browser and the model behind your agent.</p>
        </div>
      </div>
      <div className="settings-grid">
        <section className="panel settings-card">
          <div className="section-heading">
            <h2>
              <Globe2 size={20} />
              Automation browser
            </h2>
            <Pill tone={status.browser.connected ? "green" : "amber"}>
              {status.browser.connected ? "Connected" : "Disconnected"}
            </Pill>
          </div>
          <p>
            A dedicated browser profile keeps agent work separate. The extension
            must be loaded in this browser to start tasks from a selected tab.
          </p>
          <div className="button-row">
            <Button
              className="primary"
              busy={busy === "browser"}
              onClick={() =>
                void act(
                  "browser",
                  () => post("/browser/launch"),
                  "Browser is ready.",
                )
              }
            >
              <Globe2 size={16} />
              Launch browser
            </Button>
            <Button
              className="secondary"
              busy={busy === "demo-browser"}
              onClick={() =>
                void act(
                  "demo-browser",
                  () => post("/browser/demo"),
                  "Synthetic form opened in the browser.",
                )
              }
            >
              <ArrowUpRight size={16} />
              Open demo form
            </Button>
          </div>
          <button
            className="text-link refresh-tabs"
            onClick={() =>
              void act("tabs", async () =>
                setTabs((await api("/browser/tabs")).tabs || []),
              )
            }
          >
            <RefreshCw size={14} />
            Refresh available tabs
          </button>
          {tabs.map((t) => (
            <div className="tab-row" key={t.target_id}>
              <Globe2 size={15} />
              <div>
                <strong>{t.title || "Untitled tab"}</strong>
                <small>{t.url}</small>
              </div>
              <span className="scope-tag">
                {String(t.target_id).slice(0, 7)}
              </span>
            </div>
          ))}
          <div className="hint">
            <CircleHelp size={17} />
            <span>
              If Chrome is already open in another profile, use the dedicated
              automation window. The agent binds a tab by its target ID.
            </span>
          </div>
        </section>
        <section className="panel settings-card">
          <div className="section-heading">
            <h2>
              <Sparkles size={20} />
              Reasoning provider
            </h2>
            <Pill
              tone={
                status.provider.configured || mode === "demo"
                  ? "green"
                  : "amber"
              }
            >
              {mode === "demo"
                ? "Demo available"
                : status.provider.configured
                  ? "Configured"
                  : "Setup needed"}
            </Pill>
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void act(
                "settings",
                async () => {
                  await post("/settings", {
                    mode,
                    model,
                    base_url: base,
                    ...(key ? { api_key: key } : {}),
                    fallback_model: fallbackModel,
                    ...(removeFallback
                      ? { fallback_api_key: null }
                      : fallbackKey
                        ? { fallback_api_key: fallbackKey }
                        : {}),
                    ...(whisperKey ? { whisper_api_key: whisperKey } : {}),
                  });
                  setKey("");
                  if (removeFallback) setFallbackConfigured(false);
                  else if (fallbackKey) setFallbackConfigured(true);
                  setFallbackKey("");
                  setRemoveFallback(false);
                  if (whisperKey) setWhisperConfigured(true);
                  setWhisperKey("");
                },
                "Provider settings saved locally.",
              );
            }}
          >
            <label>
              Reasoning mode
              <select value={mode} onChange={(e) => setMode(e.target.value)}>
                <option value="demo">Demo · deterministic form planner</option>
                <option value="remote">
                  Remote · Gemini / compatible provider
                </option>
              </select>
            </label>
            {mode === "remote" && (
              <>
                <label>
                  Model identifier
                  <input
                    value={model}
                    onChange={(e) => setModel(e.target.value)}
                    placeholder="gemini-2.5-flash"
                    required
                  />
                </label>
                <label>
                  API base URL
                  <input
                    type="url"
                    value={base}
                    onChange={(e) => setBase(e.target.value)}
                    placeholder="https://api.example.com/v1"
                    required
                  />
                </label>
                <label>
                  Primary API key · Gemini
                  <input
                    type="password"
                    autoComplete="off"
                    value={key}
                    onChange={(e) => setKey(e.target.value)}
                    placeholder={
                      status.provider.configured
                        ? "Stored locally · leave blank to keep"
                        : "Enter your provider API key"
                    }
                  />
                </label>
                <small className="settings-note">
                  The key is stored by the local companion. Visual checkpoints
                  require image review before transmission; text review is
                  optional.
                </small>
              </>
            )}
            <div className="whisper-settings">
              <h3>Optional fallback · OpenAI</h3>
              <p>
                Gemini is tried first for each new task. If it fails, a saved
                OpenAI key enables one switch to OpenAI for that task.
                Screenshot requests require fresh approval for OpenAI.
              </p>
              <label>
                OpenAI fallback model
                <input
                  value={fallbackModel}
                  onChange={(e) => setFallbackModel(e.target.value)}
                  required
                />
              </label>
              <label>
                Separate OpenAI API key
                <input
                  type="password"
                  autoComplete="off"
                  value={fallbackKey}
                  onChange={(e) => {
                    setFallbackKey(e.target.value);
                    setRemoveFallback(false);
                  }}
                  placeholder={
                    fallbackConfigured
                      ? "Configured · leave blank to keep"
                      : "Optional · no fallback without a key"
                  }
                />
              </label>
              <small className="settings-note">
                {removeFallback
                  ? "Fallback will be removed when you save."
                  : fallbackConfigured
                    ? "OpenAI fallback configured locally."
                    : "OpenAI fallback is not configured."}{" "}
                The Whisper key is separate.
              </small>
              {fallbackConfigured && (
                <button
                  type="button"
                  className="text-link"
                  onClick={() => {
                    setRemoveFallback(true);
                    setFallbackKey("");
                  }}
                >
                  Remove fallback on save
                </button>
              )}
            </div>
            <div className="whisper-settings">
              <h3>Voice transcription · OpenAI Whisper</h3>
              <p>
                Audio is sent unredacted to OpenAI when you explicitly choose
                Transcribe. Your editable transcript never starts a task
                automatically.
              </p>
              <label>
                Separate Whisper API key
                <input
                  type="password"
                  autoComplete="off"
                  value={whisperKey}
                  onChange={(e) => setWhisperKey(e.target.value)}
                  placeholder={
                    whisperConfigured
                      ? "Configured · leave blank to keep"
                      : "OpenAI API key for whisper-1"
                  }
                />
              </label>
              <small className="settings-note">
                {whisperConfigured
                  ? "Whisper key configured locally."
                  : "Whisper is not configured. Typed tasks still work."}
              </small>
              {whisperConfigured && (
                <button
                  type="button"
                  className="text-link"
                  onClick={() =>
                    void act("whisper-remove", async () => {
                      await post("/settings", {
                        mode,
                        model,
                        base_url: base,
                        whisper_api_key: null,
                      });
                      setWhisperConfigured(false);
                      setWhisperKey("");
                    })
                  }
                >
                  Remove Whisper key
                </button>
              )}
            </div>
            <Button
              type="submit"
              className="secondary"
              busy={busy === "settings"}
            >
              <Check size={16} />
              Save settings
            </Button>
          </form>
          {mode === "demo" && (
            <div className="hint">
              <Sparkles size={17} />
              <span>
                Demo mode uses a deterministic planner for supported form
                fields. It makes no model API calls.
              </span>
            </div>
          )}
        </section>
        <section className="panel settings-card security-settings">
          <div className="section-heading">
            <h2>
              <ShieldCheck size={20} />
              Workspace security
            </h2>
            <Pill tone="green">Local session</Pill>
          </div>
          <div className="setting-line">
            <div>
              <strong>Lock the vault</strong>
              <p>
                Remove decrypted data from the active workspace and pause
                private-value access.
              </p>
            </div>
            <Button
              className="secondary"
              busy={busy === "lock"}
              onClick={() => void act("lock", () => post("/vault/lock"))}
            >
              <LockKeyhole size={15} />
              Lock vault
            </Button>
          </div>
          <div className="setting-line">
            <div>
              <strong>Disconnect this dashboard</strong>
              <p>
                Forget this browser session's pairing credential. Your saved
                information stays in the vault.
              </p>
            </div>
            <Button className="secondary" onClick={unpair}>
              <LogOut size={15} />
              Disconnect
            </Button>
          </div>
        </section>
      </div>
    </>
  );
}
function NewTask({
  status,
  records,
  close,
  busy,
  act,
  onTask,
}: {
  status: Status;
  records: RecordItem[];
  close: () => void;
  busy: string;
  act: any;
  onTask: (t: Task) => void;
}) {
  const [recordIds, setRecordIds] = useState<string[]>(
    records.filter((r) => !r.scope || r.scope === "profile").map((r) => r.id),
  );
  const selectionInitialized = useRef(false);
  useEffect(() => {
    if (!selectionInitialized.current && records.length) {
      setRecordIds(
        records
          .filter((r) => !r.scope || r.scope === "profile")
          .map((r) => r.id),
      );
      selectionInitialized.current = true;
    }
  }, [records]);
  const [tabs, setTabs] = useState<any[]>([]),
    [target, setTarget] = useState(""),
    [goal, setGoal] = useState(""),
    [startUrl, setStartUrl] = useState(""),
    [vision, setVision] = useState(true),
    [reviewText, setReviewText] = useState(false),
    [stopBeforeSubmit, setStopBeforeSubmit] = useState(true),
    [mode, setMode] = useState(status.provider.configured ? "remote" : "demo"),
    [loading, setLoading] = useState(true),
    [loadError, setLoadError] = useState("");
  useEffect(() => {
    void api("/browser/tabs")
      .then((r) => {
        const available = (r.tabs || []).filter((t: any) =>
          /^https?:\/\//.test(t.url),
        );
        setTabs(available);
        setTarget("");
      })
      .catch((e) => setLoadError(e.message))
      .finally(() => setLoading(false));
  }, []);
  return (
    <Modal title="What would you like to get done?" close={close}>
      <p className="modal-description">
        Describe the task and optionally enter a starting website. The agent can
        open it for you, using only the reviewed information you select.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void act("create-task", async () =>
            onTask(
              await post<Task>("/tasks", {
                goal,
                ...(startUrl.trim()
                  ? { start_url: startUrl.trim() }
                  : target
                    ? { target_id: target }
                    : {}),
                mode,
                vision: mode === "remote" && vision,
                review_text: mode === "demo" || reviewText,
                stop_before_submit: stopBeforeSubmit,
                record_ids: recordIds,
              }),
            ),
          );
        }}
      >
        <label>
          Task
          <textarea
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            placeholder="Fill this application using my saved profile and reviewed statement total."
            required
            rows={4}
            autoFocus
            maxLength={5000}
          />
        </label>
        <VoiceInput onTranscript={setGoal} />
        <div className="portal-preparation">
          <button
            type="button"
            className="button secondary small"
            disabled={!!busy || !status.provider.configured}
            onClick={() =>
              void act("prepare-portal", async () => {
                const result = await post("/demo/seed?partial=true");
                selectionInitialized.current = true;
                setRecordIds(result.suggested_record_ids || []);
                setStartUrl("http://127.0.0.1:8766/portal.html");
                setGoal(
                  "Complete the fictional Meridian application using my selected profile. Ask me for missing details or documents. Continue to the review step and stop before final submission.",
                );
                setMode("remote");
                setVision(true);
                setStopBeforeSubmit(true);
              })
            }
          >
            Prepare portal demo
          </button>
          <p>
            Starts with only a fictional name, email, and phone. The agent will
            need details from{" "}
            <a
              href="http://127.0.0.1:8766/documents/portal-statement.txt"
              target="_blank"
              rel="noreferrer"
            >
              this sample statement
            </a>
            .{" "}
            {status.provider.configured
              ? "Uses your configured remote model."
              : "Configure a remote model first. The no-key Demo planner supports the original single form."}
          </p>
        </div>
        <label>
          Starting website <span className="optional">optional</span>
          <input
            type="url"
            placeholder="https://example.com/application"
            value={startUrl}
            onChange={(e) => setStartUrl(e.target.value)}
          />
        </label>
        <label>
          Or use a connected browser tab
          <select
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            disabled={loading || !!startUrl.trim()}
          >
            <option value="">
              {loading
                ? "Loading controlled tabs…"
                : tabs.length
                  ? "Use the website URL in my goal"
                  : "Use the website URL in my goal"}
            </option>
            {tabs.map((t) => (
              <option key={t.target_id} value={t.target_id}>
                {t.title || t.url} · {String(t.target_id).slice(0, 6)}
              </option>
            ))}
          </select>
        </label>
        {loadError && <div className="form-error">{loadError}</div>}
        {!loading && !tabs.length && (
          <div className="hint">
            <Globe2 size={17} />
            <span>
              You can start without an open tab. Enter a website URL or include
              a clear website address in your goal. Demo mode uses the existing
              form fixture.
            </span>
          </div>
        )}
        <label>Available information · choose reviewed fields</label>
        <div className="available-records">
          {records.length ? (
            records.map((r) => (
              <label key={r.id}>
                <input
                  type="checkbox"
                  checked={recordIds.includes(r.id)}
                  onChange={(e) =>
                    setRecordIds(
                      e.target.checked
                        ? [...recordIds, r.id]
                        : recordIds.filter((id) => id !== r.id),
                    )
                  }
                />
                <span>{r.label}</span>
                <small>{formatScope(r.scope)}</small>
              </label>
            ))
          ) : (
            <p>
              No saved fields yet. Add your profile or review a document first.
            </p>
          )}
        </div>
        <label>
          Reasoning
          <select value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="demo">Demo planner · no remote model</option>
            <option value="remote" disabled={!status.provider.configured}>
              Remote model
              {!status.provider.configured ? " · configure provider first" : ""}
            </option>
          </select>
        </label>
        {mode === "remote" && (
          <div className="task-options">
            <label className="check-option">
              <input
                type="checkbox"
                checked={vision}
                onChange={(e) => setVision(e.target.checked)}
              />
              <span>
                Visual checkpoints
                <small>
                  Review the redacted screenshot before every image send.
                </small>
              </span>
            </label>
            <label className="check-option">
              <input
                type="checkbox"
                checked={reviewText}
                onChange={(e) => setReviewText(e.target.checked)}
              />
              <span>
                Also review text-only model requests
                <small>
                  When off, sanitized text requests can proceed automatically.
                </small>
              </span>
            </label>
          </div>
        )}
        <label className="check-option">
          <input
            type="checkbox"
            checked={stopBeforeSubmit}
            onChange={(e) => setStopBeforeSubmit(e.target.checked)}
          />
          <span>Stop before final submission</span>
        </label>
        <div className="hint">
          <ShieldCheck size={18} />
          <span>
            Starting authorizes the selected details to be entered on the task
            website.{" "}
            {stopBeforeSubmit
              ? "The agent will stop before final submission."
              : "Consequential actions still need a separate approval."}{" "}
            The website may receive values as they are filled.
          </span>
        </div>
        <div className="modal-actions">
          <Button className="secondary" type="button" onClick={close}>
            Cancel
          </Button>
          <Button
            className="primary"
            type="submit"
            busy={busy === "create-task"}
            disabled={
              !goal.trim() || (mode === "remote" && !status.provider.configured)
            }
          >
            <Play size={15} />
            Start task
          </Button>
        </div>
      </form>
    </Modal>
  );
}
function Modal({
  title,
  children,
  close,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  close: () => void;
  wide?: boolean;
}) {
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
      if (e.key === "Tab" && panel.current) {
        const elements = panel.current.querySelectorAll<HTMLElement>(
          'button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex="0"]',
        );
        const first = elements[0],
          last = elements[elements.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last?.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", handler);
    document.body.style.overflow = "hidden";
    panel.current?.querySelector<HTMLElement>("input,textarea,button")?.focus();
    return () => {
      document.removeEventListener("keydown", handler);
      document.body.style.overflow = "";
      previous?.focus();
    };
  }, []);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <div
        className={"modal " + (wide ? "wide" : "")}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        ref={panel}
      >
        <div className="modal-heading">
          <h2>{title}</h2>
          <button aria-label="Close dialog" onClick={close}>
            <X size={19} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
export default App;
