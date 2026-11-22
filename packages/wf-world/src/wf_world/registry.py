"""The tool registry: one decorator turns a plain service function into a tool.

    @tool(app="crm", effect="write", idempotent=False, id_params=("order_id",))
    def create_refund(ctx: ToolContext, args: CreateRefundArgs) -> dict: ...

From that single definition the registry derives the wf-core `ToolSpec` (name, docstring, JSON Schema
of the args model, effect, idempotency, roles, id params), the MCP tool (see `mcp_server.py`), and
the call path every caller shares (`Registry.call`): role check -> argument validation -> one SQLite
transaction -> audit row for writes. Python callers, MCP clients and replays therefore behave alike.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, get_type_hints

from pydantic import BaseModel, ConfigDict, ValidationError
from wf_core.diff import canonical_json, digest
from wf_core.models import Json, ToolSpec

from .context import PermissionDenied, ToolContext, ToolError

ALL_ROLES: tuple[str, ...] = ("agent", "manager")

ToolFn = Callable[[ToolContext, Any], dict[str, Json]]


class Args(BaseModel):
    """Base class of tool argument models. Unknown arguments are an error, as in a strict API."""

    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class RegisteredTool:
    spec: ToolSpec
    fn: ToolFn
    args_model: type[Args]


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def tool(
        self,
        *,
        app: str,
        effect: Literal["read", "write"],
        idempotent: bool = True,
        roles: tuple[str, ...] = ALL_ROLES,
        id_params: tuple[str, ...] = (),
    ) -> Callable[[ToolFn], ToolFn]:
        """Register `fn(ctx, args)`. The args model is read from the type hint of the `args` parameter."""

        def register(fn: ToolFn) -> ToolFn:
            args_model = get_type_hints(fn)["args"]
            spec = ToolSpec(
                name=fn.__name__,
                app=app,
                description=inspect.getdoc(fn) or "",
                params=args_model.model_json_schema(),
                effect=effect,
                idempotent=idempotent,
                id_params=id_params,
                roles=roles,
            )
            if spec.name in self._tools:
                raise ValueError(f"tool {spec.name!r} registered twice")
            self._tools[spec.name] = RegisteredTool(spec=spec, fn=fn, args_model=args_model)
            return fn

        return register

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(self._tools[name].spec for name in self.names())

    def get(self, name: str) -> RegisteredTool:
        if name not in self._tools:
            raise ToolError(f"unknown tool {name!r}")
        return self._tools[name]

    def call(self, ctx: ToolContext, name: str, args: dict[str, Json]) -> dict[str, Json]:
        """Run one call as one transaction. Raises `ToolError` for anything the app would refuse;
        nothing is written in that case."""
        registered = self.get(name)
        spec = registered.spec
        if ctx.persona.role not in spec.roles:
            raise PermissionDenied(
                f"permission denied: {name} requires role {' or '.join(spec.roles)}; you are {ctx.persona.role}"
            )
        try:
            parsed = registered.args_model.model_validate(args)
        except ValidationError as exc:
            raise ToolError(f"invalid arguments for {name}: {_explain(exc)}") from None
        with ctx.conn:  # commits if the tool returns, rolls back if it raises
            result = registered.fn(ctx, parsed)
            if spec.effect == "write":
                _audit(ctx, spec, parsed, result)
        return result

    def manifest(self) -> dict[str, Json]:
        """Every ToolSpec as JSON plus a hash of them, committed as `tool_manifest.json`."""
        tools = [spec.model_dump(mode="json") for spec in self.specs()]
        return {"sha256": digest(tools), "tools": tools}


def _audit(ctx: ToolContext, spec: ToolSpec, parsed: Args, result: dict[str, Json]) -> None:
    """Append the audit row: who called which tool on which record, in a strictly increasing `seq`."""
    args = parsed.model_dump(mode="json", exclude_none=True)
    target = next((args[name] for name in spec.id_params if name in args), None)
    ctx.insert(
        "audit_log",
        {
            "seq": ctx.ids.seq(),
            "at": ctx.clock.iso(),
            "actor": ctx.persona.id,
            "role": ctx.persona.role,
            "tool": spec.name,
            "target_id": target,
            "record_id": result.get("id"),
            "args": canonical_json(args),
        },
    )


def _explain(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors():
        where = ".".join(str(part) for part in error["loc"]) or "arguments"
        parts.append(f"{where}: {error['msg']}")
    return "; ".join(parts)


REGISTRY = Registry()
tool = REGISTRY.tool
