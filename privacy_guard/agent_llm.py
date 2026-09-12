"""Single audited HTTP boundary for Browser Use's native message protocol.

Native images are never forwarded. Only an immutable artifact produced by the
local screenshot filter can enter this transport, after exact-payload approval.
"""

import json
import re
import time
from copy import deepcopy

import httpx

from .diagnostics import logger, traced
from .gateway import canonical, digest, validate_endpoint
from .privacy import _safe_url, contains_private_text, sanitize_text


def sanitize_agent_text(value, private):
    # Native messages can include URLs from page state and history. Query
    # strings and fragments commonly contain unknown session credentials.
    value = re.sub(r"https?://[^\s<>\"']+", lambda match: _safe_url(match.group(0), private), value)
    return sanitize_text(value, private)


def clean_strings(value, private):
    """Sanitize string leaves without changing JSON structure or action keys."""
    if isinstance(value, str):
        return sanitize_agent_text(value, private)
    if isinstance(value, list):
        return [clean_strings(item, private) for item in value]
    if isinstance(value, dict):
        return {key: clean_strings(item, private) for key, item in value.items()}
    return value


class GuardedChatModel:
    _verified_api_keys = True
    provider = "privacy-guard"

    def __init__(self, runtime):
        self.runtime = runtime
        self.model = runtime.manager.gateway.model
        self.base_url = validate_endpoint(runtime.manager.gateway.base_url)
        self._api_key = runtime.manager.gateway.api_key
        self._fallback = False
        self._settings = self._settings_snapshot()

    def _settings_snapshot(self):
        gateway = self.runtime.manager.gateway
        return (gateway.model, validate_endpoint(gateway.base_url), gateway.api_key,
                gateway.fallback_model, gateway.fallback_api_key)

    @property
    def name(self):
        return self.model

    @property
    def model_name(self):
        return self.model

    def _messages(self, messages, private):
        cleaned = []
        for message in messages:
            message = message.model_dump() if hasattr(message, "model_dump") else message
            role = message.get("role")
            if role not in ("system", "user", "assistant"):
                raise ValueError("Unsupported agent message role")
            content = message.get("content", "")
            if isinstance(content, list):
                # Browser Use may retain native screenshot history. Never send it.
                content = "\n".join(part["text"] for part in content if part.get("type") == "text")
            if not isinstance(content, str):
                raise ValueError("Unsupported agent message content")
            # Native DOM can contain uninspectable frames, closed components,
            # or values absent from our local observer. Replace the whole state
            # message, including native metadata, rather than filtering fragments.
            if role == "user" and "<browser_state>" in content:
                content = "<browser_state>\n" + self.runtime.model_observation() + "\n</browser_state>"
            try:
                structured = json.loads(content)
            except (ValueError, TypeError):
                pass
            else:
                content = json.dumps(clean_strings(structured, private), ensure_ascii=False)
            cleaned.append({"role": role, "content": sanitize_agent_text(content, private)})
        return cleaned

    def prepare(self, messages, output_format, private, artifact=None):
        if artifact:
            canonical_artifact = self.runtime.manager.gateway.image_store.get(artifact["id"])
            if artifact != canonical_artifact:
                raise ValueError("Screenshot artifact was altered")
            artifact = canonical_artifact
        cleaned = self._messages(messages, private)
        schema = output_format.model_json_schema() if output_format else None
        if schema:
            # json_object works with Browser Use's dynamically generated action
            # union, whose optional fields do not satisfy OpenAI strict schemas.
            cleaned.insert(
                0,
                {
                    "role": "system",
                    "content": (
                        "Return exactly one JSON object satisfying this schema. No markdown. "
                        + json.dumps(schema, ensure_ascii=False)
                    ),
                },
            )
        if artifact:
            self.runtime.manager.gateway.image_store.verify(artifact["base64"])
            cleaned.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "User-approved redacted screenshot. Black rectangles conceal private information; never infer concealed values.",
                        },
                        {"type": "image_url", "image_url": {"url": artifact["data_url"]}},
                    ],
                }
            )
        payload = {
            "model": self.model,
            "messages": cleaned,
            "response_format": {"type": "json_object"},
            "max_completion_tokens": 2400,
            "store": False,
        }
        self.check(payload, private, artifact)
        return payload

    def check(self, payload, private, artifact=None):
        if set(payload) != {"model", "messages", "response_format", "max_completion_tokens", "store"}:
            raise ValueError("Unexpected model envelope")
        if payload["store"] is not False or payload["response_format"] != {"type": "json_object"}:
            raise ValueError("Unexpected model options")
        if payload["max_completion_tokens"] != 2400 or payload["model"] != self.model:
            raise ValueError("Model settings changed")
        texts, images = [payload["model"]], []
        for message in payload["messages"]:
            if set(message) != {"role", "content"} or message["role"] not in ("system", "user", "assistant"):
                raise ValueError("Unexpected message metadata")
            content = message["content"]
            if isinstance(content, str):
                texts.append(content)
                continue
            if not isinstance(content, list):
                raise ValueError("Invalid message content")
            for part in content:
                if set(part) == {"type", "text"} and part["type"] == "text":
                    texts.append(part["text"])
                elif set(part) == {"type", "image_url"} and part["type"] == "image_url":
                    if set(part["image_url"]) != {"url"}:
                        raise ValueError("Unexpected image metadata")
                    images.append(part["image_url"]["url"])
                else:
                    raise ValueError("Unsupported model content")
        if images:
            if not artifact or images != [artifact["data_url"]]:
                raise ValueError("Unreviewed image in model request")
            canonical_artifact = self.runtime.manager.gateway.image_store.get(artifact["id"])
            if artifact != canonical_artifact or images != [canonical_artifact["data_url"]]:
                raise ValueError("Screenshot artifact was altered")
            self.runtime.manager.gateway.image_store.verify(images[0])
        if contains_private_text("\n".join(texts), private):
            raise ValueError("A known private value remains in the model request")
        if len(canonical(payload)) > 8_000_000:
            raise ValueError("Model context exceeded the size limit")

    @traced("agent_llm.send")
    async def send(self, payload, approved_hash, private, artifact=None):
        self.runtime.check()
        if digest(payload) != approved_hash:
            raise ValueError("Model payload changed after review")
        gateway = self.runtime.manager.gateway
        if self._settings_snapshot() != self._settings or not self._api_key:
            raise ValueError("Model settings changed; start a fresh task")
        self.check(payload, private + self.runtime.private(), artifact)
        endpoint = self.base_url + "/chat/completions"
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=90, follow_redirects=False, trust_env=False) as client:
                response = await client.post(
                    endpoint,
                    content=canonical(payload),
                    headers={"Authorization": "Bearer " + self._api_key, "Content-Type": "application/json"},
                )
        except httpx.TransportError:
            logger.warning("model.transport_failed", exc_info=True)
            if self._fallback or not gateway.fallback_api_key:
                raise
            self.runtime.check()
            return await self._send_fallback(payload, private, artifact, "Model connection failed")
        self.runtime.check()
        logger.info("model.response status=%s", response.status_code)
        if response.status_code != 200:
            reason = f"Model request failed (HTTP {response.status_code}); check Settings"
            if response.status_code in (400, 401, 403, 404, 408, 429) or response.status_code >= 500:
                return await self._send_fallback(payload, private, artifact, reason)
            raise ValueError(reason)
        if len(response.content) > 1_000_000:
            raise ValueError("Model response exceeded the size limit")
        try:
            content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError
        except (KeyError, IndexError, TypeError, ValueError):
            raise ValueError("The model returned invalid JSON; no action was executed") from None
        # Never retain provider headers or raw content with possible private echoes.
        gateway.sent.append(deepcopy(payload))
        gateway.sent = gateway.sent[-20:]
        metrics = self.runtime.task.setdefault("metrics", {})
        metrics["model_calls"] = metrics.get("model_calls", 0) + 1
        metrics["model_latency_ms"] = round(
            metrics.get("model_latency_ms", 0) + (time.monotonic() - started) * 1000
        )
        if artifact:
            metrics["image_calls"] = metrics.get("image_calls", 0) + 1
        return result

    @traced("agent_llm._send_fallback")
    async def _send_fallback(self, payload, private, artifact, reason):
        gateway = self.runtime.manager.gateway
        self.runtime.check()
        if self._settings_snapshot() != self._settings:
            raise ValueError("Model settings changed; start a fresh task")
        if self._fallback or not gateway.fallback_api_key or not gateway.fallback_model:
            raise ValueError(reason)
        # One provider switch per task; new tasks always begin with the primary.
        self._fallback = True
        self.model = gateway.fallback_model
        self.base_url = "https://api.openai.com/v1"
        self._api_key = gateway.fallback_api_key
        self.runtime.manager.event(self.runtime.task, reason + "; switching to OpenAI fallback.")
        if artifact:
            if not self.runtime.image_state:
                raise ValueError("Fallback image needs a fresh screenshot review")
            self.runtime._prepare_image_payload()
            await self.runtime.manager.approval(
                self.runtime.task, self.runtime.generation, "image",
                "Review screenshot for OpenAI fallback", self.runtime._image_payload(),
            )
            state = self.runtime.image_state
            await self.runtime.check_target()
            payload, artifact = state["payload"], state["artifact"]
        else:
            payload = deepcopy(payload)
            payload["model"] = self.model
            self.check(payload, private + self.runtime.private())
            self.runtime.task["request"] = payload
            if self.runtime.task.get("_review_text"):
                await self.runtime.manager.approval(
                    self.runtime.task, self.runtime.generation, "model",
                    "Review sanitized text for OpenAI fallback",
                    {"destination": self.base_url, "sha256": digest(payload), "request": payload},
                )
        self.runtime.task["status"] = "reasoning"
        return await self.send(payload, digest(payload), private, artifact)

    async def ainvoke(self, messages, output_format=None, **_kwargs):
        from browser_use.llm.views import ChatInvokeCompletion

        await self.runtime.observe_for_model()
        private = self.runtime.private()
        catalog = self.runtime.manager.catalog(self.runtime.task)
        messages = list(messages) + [
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "private_reference_catalog": catalog,
                        "instruction": "Use input_ref(index, value_ref) for private fields. Never guess or type literal private values. If references are missing, call request_information. Stop before final submission.",
                    }
                ),
            }
        ]
        payload = self.prepare(messages, output_format, private)
        self.runtime.task["request"] = payload
        if self.runtime.task.get("_review_text"):
            await self.runtime.manager.approval(
                self.runtime.task,
                self.runtime.generation,
                "model",
                "Review sanitized text context",
                {
                    "destination": self.base_url,
                    "sha256": digest(payload),
                    "request": payload,
                },
            )
        self.runtime.task["status"] = "reasoning"
        result = await self.send(payload, digest(payload), private)
        # Output is also sanitized before entering native agent history/logs.
        safe = clean_strings(result, private)
        try:
            completion = output_format.model_validate(safe) if output_format else json.dumps(safe)
        except ValueError:
            raise ValueError("The model returned an invalid action; no action was executed") from None
        return ChatInvokeCompletion(completion=completion, usage=None)
