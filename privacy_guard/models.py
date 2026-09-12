"""Strict public request schemas. Private values never enter action proposals."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import DEFAULT_MODEL, DEFAULT_MODEL_BASE_URL


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProposedAction(StrictModel):
    action: Literal["input_ref", "select_ref", "click", "scroll", "wait", "done", "ask"]
    element_index: int | None = Field(default=None, ge=1)
    value_ref: str | None = Field(default=None, max_length=100)
    direction: Literal["up", "down"] = "down"
    message: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def appropriate_arguments(self):
        if self.action in ("input_ref", "select_ref", "click") and self.element_index is None:
            raise ValueError("This action requires an element index")
        if self.action in ("input_ref", "select_ref") and not self.value_ref:
            raise ValueError("Private input requires a reference")
        if self.action not in ("input_ref", "select_ref") and self.value_ref is not None:
            raise ValueError("This action cannot resolve references")
        return self


class PairRequest(StrictModel):
    code: str = Field(min_length=6, max_length=100)


class UnlockRequest(StrictModel):
    passphrase: str = Field(min_length=10, max_length=1024)


class RecordRequest(StrictModel):
    label: str = Field(min_length=1, max_length=120)
    field_type: str = Field(min_length=1, max_length=60)
    value: str = Field(min_length=1, max_length=10000)
    scope: str = Field(default="profile", max_length=100)
    source: str = Field(default="manual", max_length=200)
    record_id: str | None = None


class Candidate(RecordRequest):
    selected: bool = True
    scope: str = Field(default="document", max_length=100)


class ReviewRequest(StrictModel):
    candidates: list[Candidate] = Field(max_length=100)


class CalculationRequest(StrictModel):
    record_ids: list[str] = Field(min_length=1, max_length=100)
    label: str = Field(default="Statement total", min_length=1, max_length=120)
    currency: Literal["INR", "USD", "EUR", "GBP"] = "INR"


class TaskRequest(StrictModel):
    goal: str = Field(min_length=1, max_length=5000)
    target_id: str | None = Field(default=None, min_length=1, max_length=200)
    start_url: str | None = Field(default=None, min_length=1, max_length=2000)
    mode: Literal["demo", "remote"] = "demo"
    vision: bool = False
    stop_before_submit: bool = True
    review_text: bool = False
    record_ids: list[str] | None = Field(default=None, max_length=100)


class ControlRequest(StrictModel):
    action: Literal["pause", "resume", "stop"]
    record_ids: list[str] | None = Field(default=None, max_length=100)


class ApprovalRequest(StrictModel):
    approval_id: str
    approved: bool


class MaskRectangle(StrictModel):
    x: float = Field(ge=0, le=20000, allow_inf_nan=False)
    y: float = Field(ge=0, le=20000, allow_inf_nan=False)
    width: float = Field(gt=0, le=20000, allow_inf_nan=False)
    height: float = Field(gt=0, le=20000, allow_inf_nan=False)


class MaskRequest(StrictModel):
    approval_id: str = Field(min_length=1, max_length=100)
    masks: list[MaskRectangle] = Field(min_length=1, max_length=100)


class SettingsRequest(StrictModel):
    mode: Literal["demo", "remote"] = "remote"
    model: str = Field(default=DEFAULT_MODEL, max_length=200)
    base_url: str = Field(default=DEFAULT_MODEL_BASE_URL, max_length=300)
    api_key: str | None = Field(default=None, max_length=2000)
    fallback_model: str = Field(default="gpt-4.1-mini", min_length=1, max_length=200)
    fallback_api_key: str | None = Field(default=None, max_length=2000)
    whisper_api_key: str | None = Field(default=None, max_length=2000)
