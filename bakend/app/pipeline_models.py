"""Public pipeline/draft request schemas; no storage, transport or model dependencies."""

from pydantic import BaseModel, ConfigDict, Field
from .workflow_models import ParameterSpec


class SourceLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step_id: str
    output_slot_id: str


class Link(SourceLink):
    additional_sources: list[SourceLink] = Field(default_factory=list, max_length=63)

    def sources(self):
        return [
            SourceLink(step_id=self.step_id, output_slot_id=self.output_slot_id),
            *self.additional_sources,
        ]


class ComposeStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,50}$")
    method_id: str
    parameters: dict = Field(default_factory=dict)
    inputs: dict[str, Link] = Field(default_factory=dict)
    position: dict[str, float] | None = None
    when: dict[str, list] = Field(default_factory=dict)


class InputPort(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step_id: str = Field(min_length=1, max_length=51)
    input_slot_id: str = Field(min_length=1, max_length=160)


class ComposeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    steps: list[ComposeStep] = Field(min_length=1, max_length=50)
    shared_inputs: list[list[InputPort]] = Field(default_factory=list, max_length=500)
    expected_version: int | None = None
    controls: list[ParameterSpec] = Field(default_factory=list, max_length=50)


class UsePipeline(BaseModel):
    conversation_id: str | None = None
    dataset_id: str | None = None
    workspace_id: str | None = None
    version: int | None = None


class DraftSave(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(default=0, ge=0)
    editor: dict
    pipeline_id: str | None = None
    pipeline_version: int | None = None


class DeletePipeline(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    expected_deletion_revision: int = Field(ge=0)


class RestorePipeline(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_deletion_revision: int = Field(ge=1)
