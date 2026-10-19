"""Shared schema objects: project metadata, data provenance and result envelopes."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Base model that rejects unknown keys so typos in input files are reported."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class SourceType(str, Enum):
    synthetic = "synthetic"
    public = "public"
    own_test = "own_test"
    enterprise_private = "enterprise_private"
    literature_digitized = "literature_digitized"


PUBLISHABLE_SOURCES = {SourceType.synthetic, SourceType.public, SourceType.own_test}


class ProjectInfo(StrictModel):
    name: str
    source_type: SourceType = Field(
        description="Origin of the data: synthetic, public, own_test, enterprise_private, literature_digitized"
    )
    data_access: Literal["public", "internal", "confidential"] = "public"
    author: str | None = None
    description: str = ""


class ResultStatus(str, Enum):
    success = "success"
    warning = "warning"
    rejected = "rejected"  # inputs insufficient or outside validity; no result claimed
    failed = "failed"  # numerical or data failure


def worst_status(*statuses: ResultStatus) -> ResultStatus:
    order = [ResultStatus.success, ResultStatus.warning, ResultStatus.rejected, ResultStatus.failed]
    return max(statuses, key=order.index, default=ResultStatus.success)


class Message(StrictModel):
    """A warning or rejection reason. ``code`` is stable for programmatic use."""

    code: str
    text: str
    level: Literal["info", "warning", "rejected", "failed"] = "warning"
    context: dict[str, Any] = Field(default_factory=dict)
