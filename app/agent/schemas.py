# app/agent/schemas.py
from pydantic import BaseModel
from typing import Literal, Optional

Role = Literal["system", "user", "assistant", "tool"]


class Message(BaseModel):
    role: Role
    content: str


class ToolCall(BaseModel):
    tool: Literal["search", "fetch", "memory", "etl"]
    input: dict


class StepResult(BaseModel):
    type: Literal["tool_call", "final"]
    tool_call: Optional[ToolCall] = None
    final_answer: Optional[str] = None
    raw: Optional[str] = None
