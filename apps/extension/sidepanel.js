"use strict";
const API = "http://127.0.0.1:8765/api/v1";
const DASHBOARD = "http://127.0.0.1:8765";
const $ = (id) => document.getElementById(id);
let token = "",
  status = null,
  currentTarget = null,
  currentTask = null,
  pendingId = null,
  busy = false,
  settingsOpen = false,
  taskId = null,
  records = [];
let pollInFlight = false;
let modeInitialized = false;
const terminal = new Set([
  "completed",
  "failed",
  "stopped",
  "blocked",
  "outcome_unknown",
]);
const text = (id, value) => {
  $(id).textContent = value ?? "";
};
const show = (id, visible) => {
  $(id).hidden = !visible;
};
function error(message) {
  text("error", message || "");
  show("error", !!message);
}
function readError(data, response) {
  return typeof data.detail === "string"
    ? data.detail
    : JSON.stringify(
        data.detail || { message: "Companion returned " + response.status },
      );
}
async function request(path, body, method) {
  const response = await fetch(API + path, {
    method: method || (body === undefined ? "GET" : "POST"),
    headers: {
      "X-Guard-Origin": chrome.runtime.getURL("").replace(/\/$/, ""),
      ...(token ? { Authorization: "Bearer " + token } : {}),
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error(
      "Could not read companion response. Is the local service running?",
    );
  }
  if (!response.ok) {
    if (response.status === 401) {
      token = "";
      await chrome.storage.session.remove("dpg_token");
      show("pairing", true);
      show("workspace", false);
    }
    throw new Error(readError(data, response));
  }
  return data;
}
async function action(fn) {
  if (busy) return;
  busy = true;
  error("");
  const buttons = [...document.querySelectorAll("button")];
  const disabled = buttons.map((b) => b.disabled);
  buttons.forEach((b) => (b.disabled = true));
  try {
    await fn();
  } catch (e) {
    error(e.message || String(e));
  } finally {
    buttons.forEach((b, i) => (b.disabled = disabled[i]));
    busy = false;
    await poll();
  }
}
function openDashboard(page = "activity") {
  if (typeof page !== "string") page = "activity";
  const params = new URLSearchParams({ page });
  if (taskId) params.set("task", taskId);
  chrome.tabs
    .create({ url: DASHBOARD + "#" + params })
    .catch((e) => error(e.message));
}
async function updateTarget() {
  currentTarget = null;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  text("tab-title", tab?.title || "No active tab");
  let url = "";
  try {
    url = new URL(tab?.url || "").origin;
  } catch {
    url = tab?.url || "";
  }
  text("tab-url", url);
  if (!tab?.id) {
    text("tab-title", "No active tab · enter a starting website");
    return;
  }
  const discovered = await chrome.debugger.getTargets();
  const target = discovered.find(
    (t) => t.tabId === tab.id && t.type === "page",
  );
  if (!target) {
    text("tab-url", "This tab cannot be controlled.");
    return;
  }
  const controlled = await request("/browser/tabs");
  const match = (controlled.tabs || []).find((t) => t.target_id === target.id);
  if (!match) {
    text("tab-url", "Enter a starting website to open an agent tab");
    return;
  }
  currentTarget = target.id;
  text("tab-url", url + " · connected");
}
async function loadRecords() {
  const data = await request("/record-catalog");
  records = data.records || [];
  for (const containerId of ["record-list", "resume-record-list"]) {
    const container = $(containerId);
    const previous = new Map(
      [...container.querySelectorAll("input")].map((i) => [i.value, i.checked]),
    );
    container.replaceChildren();
    if (!records.length) {
      const p = document.createElement("p");
      p.textContent =
        "No confirmed fields yet. Add a profile or review a document in the dashboard.";
      container.append(p);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "empty-records-link";
      button.textContent = "Open personal vault ↗";
      button.addEventListener("click", openDashboard);
      container.append(button);
      continue;
    }
    records.forEach((r) => {
      const label = document.createElement("label"),
        check = document.createElement("input"),
        name = document.createElement("span"),
        scope = document.createElement("small");
      check.type = "checkbox";
      check.value = r.id;
      check.checked = previous.has(r.id)
        ? previous.get(r.id)
        : !r.scope || r.scope === "profile";
      name.textContent = r.label;
      scope.textContent =
        (r.scope?.startsWith("document") ? "document" : r.scope || "profile") +
        " · " +
        r.id.slice(-4);
      label.append(check, name, scope);
      container.append(label);
    });
  }
}
function renderTask(task) {
  currentTask = task;
  taskId = task.id;
  show("task", !settingsOpen);
  show("ready", false);
  show("locked", false);
  text("task-status", (task.status || "created").replaceAll("_", " "));
  text("task-goal", task.goal);
  text("step", "Step " + (task.step || 0));
  text("task-id", "Task " + task.id.slice(0, 8));
  const p = task.pending;
  pendingId = p?.id || null;
  show("approval", !!p);
  if (p) {
    const visual = ["image", "visual", "screenshot"].includes(p.kind);
    show("image-review", visual);
    show("approve", !visual);
    text("approval-title", p.title || "Review this action");
    text("approval-payload", JSON.stringify(p.payload, null, 2));
    text(
      "approval-description",
      visual
        ? "Open the dashboard to inspect the actual redacted screenshot, add masks, and approve its transmission. The original stays on this device."
        : p.kind === "model"
          ? "Review what will reach the reasoning model. Deny if you see private information."
          : p.kind === "disclosure"
            ? "The selected references resolve to real values locally. This website may receive them as soon as they are entered."
            : "Review the final action and destination. Approving permits this submission.",
    );
    text(
      "approve",
      p.kind === "model"
        ? "Approve context"
        : p.kind === "submit"
          ? "Approve submit"
          : "Approve disclosure",
    );
  }
  text("task-error", task.error || "");
  show("task-error", !!task.error);
  text(
    "task-result",
    typeof task.result === "string"
      ? task.result
      : task.result
        ? JSON.stringify(task.result, null, 2)
        : "",
  );
  show("task-result", !!task.result);
  $("task-result").classList.toggle(
    "needs-input",
    task.status === "waiting_input",
  );
  const events = task.events || [];
  text("event-count", events.length + " events");
  $("events").replaceChildren();
  events.slice(-30).forEach((ev) => {
    const row = document.createElement("div");
    row.className = "event";
    const dot = document.createElement("span");
    dot.className = "event-dot";
    dot.textContent = ev.kind?.includes("error") ? "!" : "✓";
    const body = document.createElement("div"),
      message = document.createElement("p"),
      time = document.createElement("small");
    message.textContent = ev.message;
    time.textContent =
      (ev.time
        ? new Date(ev.time).toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
          }) + " · "
        : "") + (ev.kind || "event").replaceAll("_", " ");
    body.append(message, time);
    row.append(dot, body);
    $("events").append(row);
  });
  if (!events.length) {
    const p = document.createElement("p");
    p.textContent = "Waiting for the first task event…";
    $("events").append(p);
  }
  const resumable = ["paused", "waiting_input", "waiting_for_input"].includes(
    task.status,
  );
  show("resume-information", resumable);
  show("task-controls", !terminal.has(task.status));
  show("new-task", terminal.has(task.status));
  text("pause", resumable ? "Resume with fields" : "Pause");
}
async function poll() {
  if (!token || pollInFlight) return;
  pollInFlight = true;
  try {
    const wasUnlocked = status?.vault.unlocked;
    status = await request("/status");
    if (status.vault.unlocked && !wasUnlocked) {
      await loadRecords();
      await updateTarget();
    }
    text("connection-text", "Local companion connected");
    $("connection-dot").style.background = "";
    show("pairing", false);
    show("workspace", true);
    if (!settingsOpen) {
      show("settings", false);
      if (taskId) {
        try {
          renderTask(await request("/tasks/" + taskId));
        } catch {
          taskId = null;
          await chrome.storage.session.remove("dpg_task");
        }
      } else if (status.task && !terminal.has(status.task.status)) {
        taskId = status.task.id;
        await chrome.storage.session.set({ dpg_task: taskId });
        renderTask(status.task);
      } else {
        show("task", false);
        show("locked", !status.vault.unlocked);
        show("ready", status.vault.unlocked);
      }
    }
    if (!modeInitialized) {
      $("mode").value = status.provider.configured ? "remote" : "demo";
      modeInitialized = true;
    }
    show("remote-options", $("mode").value === "remote");
    const remote = $("mode").querySelector('option[value="remote"]');
    remote.disabled = !status.provider.configured;
    if (!status.provider.configured && $("mode").value === "remote")
      $("mode").value = "demo";
    $("start").disabled = busy || !status.vault.unlocked;
  } catch (e) {
    text("connection-text", "Companion is unavailable");
    $("connection-dot").style.background = "#e2bd83";
    error(e.message || "Start the companion to reconnect.");
  } finally {
    pollInFlight = false;
  }
}
async function start() {
  const saved = await chrome.storage.session.get(["dpg_token", "dpg_task"]);
  token = saved.dpg_token || "";
  taskId = saved.dpg_task || null;
  show("pairing", !token);
  show("workspace", !!token);
  if (token) {
    await poll();
    if (status?.vault.unlocked) {
      await Promise.allSettled([loadRecords(), updateTarget()]);
      await poll();
    }
  }
  setInterval(() => {
    void poll();
  }, 2000);
  setInterval(() => {
    if (token && status?.vault.unlocked && !busy && !settingsOpen)
      void loadRecords().catch(() => {});
  }, 10000);
}
$("pair-form").addEventListener("submit", (e) => {
  e.preventDefault();
  void action(async () => {
    const result = await request("/pair", { code: $("code").value.trim() });
    token = result.token;
    $("code").value = "";
    await chrome.storage.session.set({ dpg_token: token });
    await poll();
    if (status?.vault.unlocked) {
      await loadRecords();
      await updateTarget();
    }
  });
});
$("task-form").addEventListener("submit", (e) => {
  e.preventDefault();
  void action(async () => {
    const startUrl = $("start-url").value.trim();
    if (!startUrl)
      await updateTarget().catch(() => {
        currentTarget = null;
      });
    const task = await request("/tasks", {
      goal: $("goal").value,
      ...(startUrl
        ? { start_url: startUrl }
        : currentTarget
          ? { target_id: currentTarget }
          : {}),
      mode: $("mode").value,
      vision: $("mode").value === "remote" && $("vision").checked,
      review_text: $("mode").value === "demo" || $("review-text").checked,
      stop_before_submit: $("stop-before-submit").checked,
      record_ids: [...$("record-list").querySelectorAll("input:checked")].map(
        (i) => i.value,
      ),
    });
    taskId = task.id;
    await chrome.storage.session.set({ dpg_task: task.id });
    renderTask(task);
  });
});
for (const id of ["dashboard", "unlock", "task-dashboard"])
  $(id).addEventListener("click", openDashboard);
$("refresh-tab").addEventListener(
  "click",
  () =>
    void action(async () => {
      await updateTarget();
      await loadRecords();
    }),
);
$("settings-toggle").addEventListener("click", () => {
  settingsOpen = true;
  show("settings", true);
  show("ready", false);
  show("task", false);
  show("locked", false);
});
$("settings-close").addEventListener("click", () => {
  settingsOpen = false;
  show("settings", false);
  void action(async () => {
    await poll();
    if (status?.vault.unlocked) {
      await updateTarget();
      await loadRecords();
    }
  });
});
$("disconnect").addEventListener(
  "click",
  () =>
    void action(async () => {
      token = "";
      taskId = null;
      status = null;
      records = [];
      await chrome.storage.session.clear();
      show("pairing", true);
      show("workspace", false);
      $("record-list").replaceChildren();
      settingsOpen = false;
    }),
);
$("launch").addEventListener(
  "click",
  () =>
    void action(async () => {
      await request("/browser/launch", {});
      await updateTarget();
    }),
);
$("demo").addEventListener(
  "click",
  () =>
    void action(async () => {
      await request("/browser/demo", {});
      await updateTarget();
    }),
);
$("pause").addEventListener(
  "click",
  () =>
    void action(() => {
      const resumable = [
        "paused",
        "waiting_input",
        "waiting_for_input",
      ].includes(currentTask?.status);
      return request("/tasks/" + taskId + "/control", {
        action: resumable ? "resume" : "pause",
        ...(resumable
          ? {
              record_ids: [
                ...$("resume-record-list").querySelectorAll("input:checked"),
              ].map((i) => i.value),
            }
          : {}),
      });
    }),
);
$("refresh-records").addEventListener("click", () => void action(loadRecords));
$("stop").addEventListener(
  "click",
  () =>
    void action(() =>
      request("/tasks/" + taskId + "/control", { action: "stop" }),
    ),
);
$("approve").addEventListener(
  "click",
  () =>
    void action(async () => {
      if (!pendingId) throw new Error("This approval is no longer available.");
      if (
        ["image", "visual", "screenshot"].includes(currentTask?.pending?.kind)
      ) {
        openDashboard("activity");
        return;
      }
      await request("/tasks/" + taskId + "/approve", {
        approval_id: pendingId,
        approved: true,
      });
    }),
);
$("deny").addEventListener(
  "click",
  () =>
    void action(async () => {
      if (!pendingId) throw new Error("This approval is no longer available.");
      await request("/tasks/" + taskId + "/approve", {
        approval_id: pendingId,
        approved: false,
      });
    }),
);
$("new-task").addEventListener(
  "click",
  () =>
    void action(async () => {
      taskId = null;
      currentTask = null;
      await chrome.storage.session.remove("dpg_task");
      $("goal").value = "";
      await updateTarget();
      await loadRecords();
    }),
);
for (const chip of document.querySelectorAll("[data-goal]"))
  chip.addEventListener("click", () => {
    $("goal").value = chip.dataset.goal;
    $("goal").focus();
  });
chrome.tabs.onActivated.addListener(() => {
  if (token && !busy)
    void updateTarget()
      .then(poll)
      .catch(() => {});
});
chrome.tabs.onUpdated.addListener((id, info) => {
  if (token && !busy && (info.status === "complete" || info.url))
    void updateTarget()
      .then(poll)
      .catch(() => {});
});
$("mode").addEventListener("change", () => {
  modeInitialized = true;
  show("remote-options", $("mode").value === "remote");
});
$("image-review").addEventListener("click", () => openDashboard("activity"));
$("missing-vault").addEventListener("click", () => openDashboard("vault"));
$("missing-documents").addEventListener("click", () =>
  openDashboard("documents"),
);
$("portal-prepare").addEventListener("click", () =>
  chrome.tabs.create({ url: DASHBOARD + "#page=activity&new=1" }),
);
$("voice-settings").addEventListener("click", () => openDashboard("settings"));
$("voice-open").addEventListener("click", async () => {
  try {
    const [source] = await chrome.tabs.query({
      active: true,
      currentWindow: true,
    });
    const url = new URL(chrome.runtime.getURL("capture.html"));
    if (source?.id) url.searchParams.set("source_tab", String(source.id));
    await chrome.tabs.create({ url: url.href });
  } catch (e) {
    error(e.message);
  }
});
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (
    sender.id !== chrome.runtime.id ||
    message.type !== "dpg-transcript" ||
    typeof message.text !== "string"
  )
    return;
  if (message.text.length > 5000) {
    respond({
      error: "Shorten the transcript to 5,000 characters before using it.",
    });
    return;
  }
  $("goal").value = message.text;
  show("transcript-note", true);
  $("goal").focus();
  respond({ received: true });
});
void start().catch((e) => error(e.message));
