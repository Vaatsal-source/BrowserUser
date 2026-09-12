"""One controlled task at a time; every model send and private entry has an approval."""

import asyncio
import re
import secrets
import time
from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .browser import SELECTION_REJECTIONS, BrowserError
from .diagnostics import logger, task_id
from .gateway import ModelGateway, digest, normalize
from .models import TaskRequest
from .privacy import sanitize_observation, sanitize_text


def now():
    return datetime.now(timezone.utc).isoformat()


def origin(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username:
        raise ValueError("Only ordinary HTTP(S) pages can be automated")
    return f"{parsed.scheme}://{parsed.netloc}"


class TaskManager:
    def __init__(self, vault, browser, gateway: ModelGateway, demo_port=8766, app_port=8765):
        self.vault, self.browser, self.gateway = vault, browser, gateway
        self.demo_port, self.app_port = demo_port, app_port
        self.tasks: dict[str, dict] = {}
        self.workers: dict[str, asyncio.Task] = {}
        self.approvals: dict[str, asyncio.Future] = {}
        self.generation = 0
        self.max_steps = 20
        self.approval_seconds = 300

    def event(self, task: dict, message: str, kind="info"):
        logger.log(40 if kind == "error" else 30 if kind == "warning" else 20,
                   "task.event id=%s status=%s step=%s kind=%s",
                   task.get("id", "-"), task.get("status", "-"), task.get("step", 0), kind)
        # Callers use static text or already-sanitized messages only.
        task["events"].append({"time": now(), "message": message, "kind": kind})
        task["events"] = task["events"][-100:]
        task["updated_at"] = now()
        self.persist()

    def public(self, task: dict) -> dict:
        return deepcopy({k: v for k, v in task.items() if not k.startswith("_")})

    def persist(self):
        if self.vault.unlocked and hasattr(self.vault, "save_blob"):
            self.vault.save_blob("task_history", [self.public(t) for t in list(self.tasks.values())[-30:]])

    def restore(self):
        if not hasattr(self.vault, "load_blob"):
            return
        data = self.vault.load_blob("task_history", [])
        for task in data:
            if task["id"] not in self.tasks:
                if task["status"] not in ("completed", "stopped", "failed", "blocked", "outcome_unknown"):
                    task["status"] = "stopped"
                    task["result"] = (
                        "Service restarted. Review the browser and start a fresh task; no action was replayed."
                    )
                task["pending"] = None
                self.tasks[task["id"]] = task

    def current(self):
        values = list(self.tasks.values())
        return self.public(values[-1]) if values else None

    async def start(self, request: TaskRequest):
        if not self.vault.unlocked:
            raise ValueError("Unlock the local vault first")
        if any(not worker.done() for worker in self.workers.values()):
            raise ValueError("Pause or stop the active task before starting another")
        start_url = getattr(request, "start_url", None)
        if not start_url and not request.target_id:
            match = re.search(r"https?://[^\s<>\"']+", request.goal)
            start_url = match.group(0).rstrip(".,;)") if match else None
        if start_url:
            destination = origin(start_url)
            parsed = urlsplit(start_url)
            if parsed.hostname in ("127.0.0.1", "localhost", "::1") and parsed.port != self.demo_port:
                raise ValueError("The agent cannot access the dashboard or other local services")
            if request.mode == "remote" and not self.gateway.settings()["configured"]:
                raise ValueError("Configure a hosted model and API key first")
            if not self.browser.status().get("running"):
                await self.browser.launch()
            tab = await self.browser.new_page(start_url)
        else:
            tabs = await self.browser.tabs()
            tab = next((t for t in tabs if t["target_id"] == request.target_id), None)
        if not tab:
            raise ValueError("The selected tab is not in the controlled browser")
        destination = origin(tab["url"])
        parsed = urlsplit(tab["url"])
        if parsed.hostname in ("127.0.0.1", "localhost", "::1") and parsed.port != self.demo_port:
            raise ValueError("The agent cannot access the dashboard or other local services")
        if request.mode == "remote" and not self.gateway.settings()["configured"]:
            raise ValueError("Configure a hosted model and API key first")
        identifier = secrets.token_hex(8)
        task = {
            "id": identifier,
            "goal": sanitize_text(request.goal, self.vault.secrets()),
            "status": "created",
            "step": 0,
            "events": [],
            "pending": None,
            "created_at": now(),
            "updated_at": now(),
            "mode": request.mode,
            "target_id": tab["target_id"],
            "destination": destination,
            "_goal": request.goal,
            "_origin": destination,
            "_vision": request.vision,
            "_stop_before_submit": getattr(request, "stop_before_submit", True),
            "_review_text": getattr(request, "review_text", False),
            "_record_ids": request.record_ids,
            "_history": [],
            "_refs": {},
            "_ref_ids": {},
        }
        self.tasks[identifier] = task
        self.event(
            task,
            "Task website opened in a controlled tab."
            if start_url
            else "Task bound to the selected browser tab.",
        )
        self.launch(task)
        return self.public(task)

    def launch(self, task):
        self.generation += 1
        generation = self.generation
        task["_generation"] = generation
        self.workers[task["id"]] = asyncio.create_task(self.run(task, generation))

    def check(self, task, generation):
        if generation != self.generation or not self.vault.unlocked:
            raise asyncio.CancelledError

    def catalog(self, task):
        catalog, bindings = [], {}
        for record in self.vault.records():
            ids = task.get("_record_ids")
            if (ids is not None and record["id"] not in ids) or (
                ids is None and record.get("scope") != "profile"
            ):
                continue
            if not record.get("reviewed", True):
                continue
            ref = task["_ref_ids"].setdefault(record["id"], "ref_" + secrets.token_hex(4))
            bindings[ref] = {
                "id": record["id"],
                "version": record["version"],
                "label": record["label"],
                "type": record["field_type"],
            }
            catalog.append(
                {
                    "id": ref,
                    "label": sanitize_text(record["label"], self.vault.secrets()),
                    "type": record["field_type"],
                }
            )
        task["_refs"] = bindings
        return catalog

    async def approval(self, task, generation, kind, title, payload):
        self.check(task, generation)
        identifier = secrets.token_hex(12)
        future = asyncio.get_running_loop().create_future()
        self.approvals[identifier] = future
        task["pending"] = {
            "id": identifier,
            "kind": kind,
            "title": title,
            "payload": deepcopy(payload),
            "expires_at": time.time() + self.approval_seconds,
        }
        task["status"] = "awaiting_approval"
        self.event(task, title, "approval")
        try:
            approved = await asyncio.wait_for(future, self.approval_seconds)
            self.check(task, generation)
            if not approved:
                raise PermissionError("You declined the request. The task has been blocked.")
        except TimeoutError as exc:
            raise PermissionError("Approval expired. Start a fresh observation before continuing.") from exc
        finally:
            task["pending"] = None
            for key, value in list(self.approvals.items()):
                if value is future:
                    self.approvals.pop(key, None)

    def replace_approval(self, task, old_id, payload):
        """Invalidate a screenshot receipt when the user adds local masks."""
        pending = task.get("pending")
        future = self.approvals.get(old_id)
        if not pending or pending["id"] != old_id or not future or future.done():
            raise ValueError("This approval is no longer valid")
        if time.time() > pending["expires_at"]:
            raise ValueError("This approval has expired")
        identifier = secrets.token_hex(12)
        self.approvals.pop(old_id)
        self.approvals[identifier] = future
        pending["id"] = identifier
        pending["payload"] = deepcopy(payload)
        self.event(task, "Additional masks applied. Review the updated screenshot before approving.")

    def image_preview(self, task_id):
        task = self.tasks[task_id]
        runtime = task.get("_runtime")
        if not runtime:
            raise ValueError("No screenshot review is pending")
        return runtime.image_preview()

    def update_masks(self, task_id, approval_id, masks):
        task = self.tasks[task_id]
        runtime = task.get("_runtime")
        if not runtime:
            raise ValueError("No screenshot review is pending")
        return runtime.update_masks(approval_id, masks)

    def approve(self, task_id, approval_id, approved):
        task = self.tasks[task_id]
        pending = task.get("pending")
        future = self.approvals.get(approval_id)
        if not pending or pending["id"] != approval_id or not future or future.done():
            raise ValueError("This approval is no longer valid")
        if time.time() > pending["expires_at"]:
            raise ValueError("This approval has expired")
        future.set_result(approved)
        return self.public(task)

    async def control(self, task_id, action, record_ids=None):
        task = self.tasks[task_id]
        if task["status"] in ("completed", "stopped", "failed", "blocked", "outcome_unknown"):
            if action == "stop":
                return self.public(task)
            raise ValueError("This task has ended. Inspect the page and start a fresh task")
        if action == "resume":
            if task["status"] not in ("paused", "waiting_input") or "_goal" not in task:
                raise ValueError(
                    "Only a paused task can resume; start a new task after restart or an uncertain action"
                )
            if not self.vault.unlocked:
                raise ValueError("Unlock the vault before resuming")
            if any(not w.done() for w in self.workers.values()):
                raise ValueError("Another task is active")
            if record_ids is not None:
                available = {record["id"] for record in self.vault.records()}
                if not set(record_ids).issubset(available):
                    raise ValueError("Some selected records are no longer available")
                task["_record_ids"] = list(record_ids)
            task["status"] = "observing"
            self.launch(task)
            return self.public(task)
        worker = self.workers.get(task_id)
        if not worker or worker.done():
            task["status"] = "paused" if action == "pause" else "stopped"
            task["pending"] = None
            self.event(task, "Task paused." if action == "pause" else "Task stopped.")
            return self.public(task)
        was_executing = task["status"] == "executing"
        self.generation += 1
        if hasattr(self.browser, "invalidate"):
            self.browser.invalidate()
        task["status"] = (
            "outcome_unknown" if was_executing else ("paused" if action == "pause" else "stopped")
        )
        worker = self.workers.get(task_id)
        if worker and not worker.done():
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
        task["pending"] = None
        self.event(
            task,
            "Execution authorization revoked. Inspect any in-flight browser action."
            if was_executing
            else ("Task paused. Resume will capture fresh state." if action == "pause" else "Task stopped."),
        )
        return self.public(task)

    async def stop_all(self):
        for task in list(self.tasks.values()):
            if task["id"] in self.workers and not self.workers[task["id"]].done():
                await self.control(task["id"], "pause")

    def resolve(self, task, ref):
        binding = task["_refs"].get(ref)
        if not binding:
            raise ValueError("The action requested an unknown or out-of-task reference")
        record = next((r for r in self.vault.records() if r["id"] == binding["id"]), None)
        if not record or record["version"] != binding["version"]:
            raise ValueError("A referenced value changed. Resume with a fresh observation")
        return record

    @staticmethod
    def compatible(field, record):
        kind = normalize(record["field_type"])
        label = normalize(field.get("label", ""))
        input_type = field.get("input_type", "text")
        if input_type in ("password", "hidden", "file", "submit", "button", "checkbox", "radio"):
            return False
        if input_type == "email" and kind not in ("email", "emailaddress"):
            return False
        if input_type == "tel" and kind not in ("phone", "telephone", "mobile"):
            return False
        # Unrecognized fields are reviewed individually, but clear type conflicts are rejected.
        families = {
            "email": {"email", "emailaddress"},
            "pan": {"pan", "taxid"},
            "phone": {"phone", "telephone", "mobile"},
            "fullname": {"name", "personname", "fullname"},
            "statementtotal": {"money", "amount", "total", "statementtotal"},
        }
        return label not in families or kind in families[label]

    async def run(self, task, generation):
        token = task_id.set(task["id"])
        logger.info("task.run.started")
        try:
            if task["mode"] == "remote":
                from .agent_runtime import BrowserAgentRuntime

                runtime = task.get("_runtime")
                if runtime is None:
                    runtime = task["_runtime"] = BrowserAgentRuntime(self, task)
                await runtime.run(generation)
                return
            while task["step"] < self.max_steps:
                self.check(task, generation)
                task["step"] += 1
                task["status"] = "observing"
                raw = await self.browser.observe(task["target_id"])
                self.check(task, generation)
                if origin(raw["url"]) != task["_origin"]:
                    raise PermissionError(
                        "The page left the approved website; start a task for the new destination"
                    )
                if raw.get("unsupported_frames"):
                    raise PermissionError(
                        "This page contains embedded frames, which this version cannot safely automate"
                    )
                for field in raw.get("fields", []):
                    field["filled"] = bool(field.get("value"))
                    rect = field.get("rect", {})
                    field["in_viewport"] = 0 <= rect.get("y", 0) < raw.get("height", 10000) and 0 <= rect.get(
                        "x", 0
                    ) < raw.get("width", 10000)
                task["status"] = "sanitizing"
                private = self.vault.secrets() + ([self.gateway.api_key] if self.gateway.api_key else [])
                private += [
                    str(field["value"])
                    for field in raw.get("fields", [])
                    if field.get("value") and field.get("tag") in ("input", "textarea", "select")
                ]
                observation = await asyncio.to_thread(sanitize_observation, raw, private, task["_vision"])
                self.check(task, generation)
                observation["_image_sanitized"] = bool(observation.get("screenshot"))
                refs = self.catalog(task)
                payload = self.gateway.prepare(task["_goal"], observation, refs, task["_history"], private)
                fingerprint = digest(payload)
                task["redaction_report"] = observation.get("report", {})
                task["request"] = payload
                await self.approval(
                    task,
                    generation,
                    "model",
                    "Review sanitized context"
                    if task["mode"] == "demo"
                    else "Approve sending sanitized context to your model",
                    {
                        "destination": "Local demo planner"
                        if task["mode"] == "demo"
                        else self.gateway.base_url,
                        "sha256": fingerprint,
                        "request": payload,
                    },
                )
                self.check(task, generation)
                # URL/title alone are not sufficient; the driver also validates the original observation at dispatch.
                task["status"] = "reasoning"
                fresh_private = self.vault.secrets() + private
                proposal = await self.gateway.call(payload, fingerprint, fresh_private, task["mode"])
                self.check(task, generation)
                action = proposal.model_dump(exclude_none=True)
                action["message"] = sanitize_text(action["message"], private)
                if action["action"] == "done":
                    fresh = await self.browser.observe(task["target_id"])
                    if origin(fresh["url"]) != task["_origin"]:
                        raise PermissionError(
                            "The page changed destination before completion could be verified"
                        )
                    if "fill" in task["_goal"].lower() and any(
                        field.get("required")
                        and not field.get("value")
                        and field.get("tag") in ("input", "textarea", "select")
                        for field in fresh.get("fields", [])
                    ):
                        task["status"] = "waiting_input"
                        task["result"] = (
                            "Required fields remain empty. Review the page and available information before resuming."
                        )
                        self.event(task, task["result"], "question")
                        return
                    task["status"] = "completed"
                    task["result"] = action["message"] or "Task completed. Review the browser."
                    self.event(task, task["result"], "success")
                    return
                if action["action"] == "ask":
                    task["status"] = "waiting_input"
                    task["result"] = (
                        action["message"] or "Add the missing information in the dashboard and resume."
                    )
                    self.event(task, task["result"], "question")
                    return
                field = next((f for f in raw["fields"] if f["index"] == action.get("element_index")), None)
                resolved = None
                if action["action"] in ("input_ref", "select_ref"):
                    if not field:
                        raise ValueError("The requested field is not present in this observation")
                    record = self.resolve(task, action["value_ref"])
                    if not self.compatible(field, record):
                        raise ValueError("The reference type does not match this field")
                    safe_label = sanitize_text(field["label"], private)
                    await self.approval(
                        task,
                        generation,
                        "disclosure",
                        "Allow a private value to be entered on this website",
                        {
                            "destination": task["_origin"],
                            "field": safe_label,
                            "reference": action["value_ref"],
                            "record_label": sanitize_text(record["label"], private),
                            "record_version": record["version"],
                            "notice": "The website may receive the value immediately when it is entered.",
                        },
                    )
                    self.check(task, generation)
                    resolved = self.resolve(task, action["value_ref"])["value"]
                elif action["action"] == "click":
                    if not field:
                        raise ValueError("The requested control is absent")
                    await self.approval(
                        task,
                        generation,
                        "submit",
                        "Approve this browser click",
                        {
                            "destination": task["_origin"],
                            "control": sanitize_text(field["label"], private),
                            "notice": "This click may submit the form or change the page. Inspect it before approving.",
                        },
                    )
                    action["_approved"] = True
                elif action["action"] == "scroll":
                    action["delta_y"] = -500 if action["direction"] == "up" else 500
                self.check(task, generation)
                self.browser.can_execute = lambda: generation == self.generation and self.vault.unlocked
                task["status"] = "executing"
                try:
                    result = await self.browser.execute(
                        task["target_id"], raw, action, resolved_value=resolved
                    )
                except BrowserError as exc:
                    if str(exc) == "stale_observation" and task.get("_stale_retries", 0) < 2:
                        resolved = None
                        task["_stale_retries"] = task.get("_stale_retries", 0) + 1
                        task["status"] = "observing"
                        self.event(
                            task,
                            "The page changed before execution. Capturing it again; no value was entered.",
                            "warning",
                        )
                        continue
                    raise
                resolved = None
                self.check(task, generation)
                if not result.get("ok", result.get("success", False)):
                    raise ValueError("The browser could not confirm the action")
                task["_history"].append(
                    {
                        "action": action["action"],
                        "element_index": action.get("element_index"),
                        "value_ref": action.get("value_ref"),
                        "result": "success",
                    }
                )
                self.event(task, f"Step {task['step']}: {action['action']} completed.", "success")
            task["status"] = "blocked"
            task["result"] = "The 20-step limit was reached. Review the page before starting another task."
            self.event(task, task["result"], "warning")
        except asyncio.CancelledError:
            raise
        except PermissionError as exc:
            logger.warning("task.blocked", exc_info=True)
            task["status"] = "blocked"
            task["error"] = (
                sanitize_text(str(exc), self.vault.secrets()) if self.vault.unlocked else "Vault locked."
            )
            self.event(task, task["error"], "warning")
        except Exception as exc:
            logger.error("task.failed status=%s step=%s", task["status"], task["step"], exc_info=True)
            # Exception messages can include page values in dependencies; do not publish raw errors.
            if isinstance(exc, BrowserError) and str(exc) in SELECTION_REJECTIONS:
                task["status"] = "blocked"
                task["error"] = (
                    "No dropdown option was selected. The supplied reference did not identify one enabled "
                    "option. Review the available choices and update the local record before starting a fresh task."
                )
            elif task["status"] == "executing":
                task["status"] = "outcome_unknown"
                task["error"] = (
                    "The action outcome could not be confirmed. Inspect the page; it will not be repeated automatically."
                )
                if isinstance(exc, BrowserError):
                    task["error"] += " Browser status: " + str(exc)
            else:
                task["status"] = "failed"
                task["error"] = "Task paused safely: " + (
                    sanitize_text(str(exc), self.vault.secrets())
                    if isinstance(exc, ValueError) and self.vault.unlocked
                    else type(exc).__name__
                )
            self.event(task, task["error"], "error")
        finally:
            logger.info("task.run.finished status=%s step=%s", task["status"], task["step"])
            task_id.reset(token)
            task["pending"] = None
            self.persist()
