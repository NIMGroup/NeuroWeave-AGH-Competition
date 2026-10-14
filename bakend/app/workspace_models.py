"""Workspace records and strictly typed filesystem requests (never shell fragments)."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Workspace(BaseModel):
    # Derived on reads. The persisted legacy workspace ID remains compatible.
    manageable: bool = False
    permission_reason: str = ""
    id: str
    name: str
    root_path: str
    created_at: str
    updated_at: str
    status: Literal["active", "archived", "deleted"] = "active"
    description: str = ""


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    root_path: str = Field(min_length=1, max_length=4096)
    description: str = Field(default="", max_length=2000)


class WorkspaceUpdate(BaseModel):
    # A root is immutable: changing it would silently rebind old conversations.
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    status: Literal["active", "archived"] | None = None


class FileOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    path: str = Field(min_length=1, max_length=4096)
    destination: str | None = Field(default=None, max_length=4096)
    conversation_id: str | None = None
    overwrite: bool = False
    permanent: bool = False


class DirectoryCreationRequest(FileOperationRequest):
    # Only mkdir can be proposed before registering an output directory.
    workspace_id: str | None = None
    root_path: str | None = Field(default=None, min_length=1, max_length=4096)


class ConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: Literal[True]
    conversation_id: str | None = None


class RestoreRequest(BaseModel):
    conversation_id: str | None = None
    destination: str | None = None


class WorkflowOutputRequest(BaseModel):
    workspace_id: str | None = None
    conversation_id: str | None = None
    expected_revision: int = Field(ge=1)
    relative_path: str = Field(default=".", min_length=1, max_length=4096)


class DatasetWorkspaceRequest(BaseModel):
    workspace_id: str | None = None
