"""Pydantic schemas for every structured Gemma 4 output. Validation failures trigger a repair attempt."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Ping(BaseModel):
    ok: bool
    message: str = ""


class IssueInterpretation(BaseModel):
    """Step 1: what the issue claims, in searchable terms."""
    summary: str = Field(description="One-sentence restatement of the problem")
    expected_behavior: str = ""
    actual_behavior: str = ""
    keywords: list[str] = Field(default_factory=list, description="Identifiers/terms likely present in the code")
    suspected_symbols: list[str] = Field(default_factory=list, description="Function/class/method names from the symbol list")
    suspected_files: list[str] = Field(default_factory=list)
    clarity: Literal["clear", "partial", "unclear"] = "partial"
    missing_information: list[str] = Field(default_factory=list)


class Hypothesis(BaseModel):
    file: str
    symbol: str = ""
    explanation: str
    evidence: list[str] = Field(default_factory=list, description="Concrete lines/facts from the provided context")
    confidence: float = Field(ge=0.0, le=1.0)
    validation: str = Field(default="", description="How a test can confirm or reject this hypothesis")


class ContextRequest(BaseModel):
    """Progressive expansion: the model asks for one more definition instead of whole files."""
    symbol: str
    file: str = ""
    reason: str


class RegressionTest(BaseModel):
    test_file: str = Field(description="Repository-relative path for a NEW test file, e.g. tests/test_oss_forge_regression.py")
    code: str = Field(description="Complete pytest module source")
    asserts_behavior: str = Field(description="The expected behaviour the test asserts")


class Diagnosis(BaseModel):
    """Step 2: ranked root-cause hypotheses plus a narrow regression test."""
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    regression_test: RegressionTest | None = None
    reproducible: bool = Field(default=True, description="False when the issue lacks the information needed to write a test")
    reason_if_not_reproducible: str = ""
    need_more_context: list[ContextRequest] = Field(default_factory=list)


class Edit(BaseModel):
    file: str = Field(description="Repository-relative path of an existing file")
    search: str = Field(description="Exact, contiguous text currently in the file (unique match)")
    replace: str = Field(description="Replacement text")


class PatchProposal(BaseModel):
    """Step 3: minimal search/replace edits."""
    root_cause: str
    explanation: str
    edits: list[Edit] = Field(default_factory=list)
    need_more_context: list[ContextRequest] = Field(default_factory=list)


class CIFailureAnalysis(BaseModel):
    failure_kind: Literal["code", "test", "environment", "flaky", "unknown"]
    failed_step: str = ""
    summary: str
    related_files: list[str] = Field(default_factory=list)
    suggested_fix: str = ""


class IssueTriage(BaseModel):
    number: int
    complexity: Literal["small", "medium", "large", "unknown"]
    automatable: bool
    rationale: str
    duplicate_of: int | None = None
