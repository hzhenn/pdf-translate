from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    field_validator,
    model_validator,
)


class JobValidationError(Exception):
    pass


class EngineJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inputs: list[str]
    outputDir: str
    service: str

    langIn: str | None = None
    langOut: str | None = None
    pages: str | None = None
    dual: bool = True
    mono: bool = True
    qps: int | None = None
    reportInterval: float = 1.0
    ignoreCache: bool = False
    threads: int = 4

    @field_validator("inputs")
    @classmethod
    def _validate_inputs(cls, v: list[str]) -> list[str]:
        if not isinstance(v, list) or not v:
            raise ValueError("inputs must be a non-empty array of PDF paths")
        cleaned: list[str] = []
        for i, item in enumerate(v):
            if not isinstance(item, str):
                raise ValueError(f"inputs[{i}] must be a string")
            s = item.strip()
            if not s:
                raise ValueError(f"inputs[{i}] must be non-empty")
            cleaned.append(s)
        return cleaned

    @field_validator("outputDir")
    @classmethod
    def _validate_output_dir(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("outputDir must be a non-empty string")
        return v.strip()

    @field_validator("service", mode="before")
    @classmethod
    def _normalize_service(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip().lower()
        return v

    @field_validator("langIn", "langOut", "pages")
    @classmethod
    def _strip_optional_strings(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("must be a string")
        s = v.strip()
        if not s:
            raise ValueError("must be non-empty when provided")
        return s

    @field_validator("qps")
    @classmethod
    def _validate_qps(cls, v: int | None) -> int | None:
        if v is None:
            return None
        if not isinstance(v, int):
            raise ValueError("qps must be an integer")
        if v <= 0:
            raise ValueError("qps must be > 0")
        return v

    @field_validator("threads")
    @classmethod
    def _validate_threads(cls, v: int) -> int:
        if not isinstance(v, int):
            raise ValueError("threads must be an integer")
        if v <= 0:
            raise ValueError("threads must be > 0")
        return v

    @field_validator("reportInterval")
    @classmethod
    def _validate_report_interval(cls, v: float) -> float:
        if not isinstance(v, (int, float)):
            raise ValueError("reportInterval must be a number")
        fv = float(v)
        if fv <= 0:
            raise ValueError("reportInterval must be > 0")
        return fv

    @model_validator(mode="after")
    def _normalize_and_validate_paths(self) -> "EngineJob":
        if not self.dual and not self.mono:
            raise ValueError("Cannot disable both dual and mono")

        normalized_inputs: list[str] = []
        for raw in self.inputs:
            p = Path(raw).expanduser()
            try:
                p = p.resolve(strict=True)
            except FileNotFoundError:
                raise ValueError(f"Input file does not exist: {raw}")
            if p.suffix.lower() != ".pdf":
                raise ValueError(f"Input file is not a PDF: {str(p)}")
            normalized_inputs.append(str(p))
        self.inputs = normalized_inputs

        out = Path(self.outputDir).expanduser().resolve(strict=False)
        out.mkdir(parents=True, exist_ok=True)
        self.outputDir = str(out)
        return self


def load_job(job_path: str | Path) -> EngineJob:
    try:
        p = Path(job_path).expanduser().resolve(strict=True)
    except FileNotFoundError as e:
        raise JobValidationError(f"Job file not found: {job_path}") from e
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise JobValidationError(f"Invalid JSON: {e}") from e
    except OSError as e:
        raise JobValidationError(f"Failed to read job file: {e}") from e

    try:
        return EngineJob.model_validate(raw)
    except ValidationError as e:
        raise JobValidationError(str(e)) from e
