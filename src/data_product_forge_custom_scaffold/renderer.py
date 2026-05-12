"""Jinja2 rendering engine for YAML/Jinja bundles.

Each :class:`TemplateEntry` is a 1:1 mapping from a Jinja template (under
the bundle's ``templates/`` dir) to one output file. The ``to_path`` is
itself Jinja-templated against the render context, so ``to`` can vary
based on contract content.

For per-environment or per-item iteration, bundle authors use Jinja's
``{% for env in environments %}`` inside a single template that emits
the whole multi-section file. For static / binary fixtures, drop them
into a top-level ``static/`` directory in the bundle — the engine copies
that directory verbatim alongside the rendered templates.

Determinism is the contract: same context ⇒ same bytes, every time.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Mapping

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateError

from .manifest import STATIC_DIRNAME, PatternEntry, TemplateEntry


class RenderError(RuntimeError):
    """Raised when a template fails to render or a path escapes the output root."""


@dataclass(frozen=True)
class RenderedFile:
    """One file emitted by the renderer."""

    path: str
    content: bytes
    mode: int = 0o644
    description: str = ""


def _check_path_safety(rendered_path: str) -> None:
    """Reject absolute paths and ``..`` traversal in rendered output paths."""
    p = Path(rendered_path)
    if p.is_absolute():
        raise RenderError(f"output path must be relative, got absolute: {rendered_path!r}")
    if any(part == ".." for part in p.parts):
        raise RenderError(f"output path must not contain '..': {rendered_path!r}")


class Renderer:
    """Renders a :class:`PatternEntry` against a context.

    Also handles the ``static/`` directory convention: any file under
    ``<bundle>/static/`` is copied byte-for-byte to the output (preserving
    its sub-path).
    """

    def __init__(self, bundle_root: Path) -> None:
        self.bundle_root = Path(bundle_root).resolve()
        if not self.bundle_root.is_dir():
            raise RenderError(f"bundle_root must be a directory, got {self.bundle_root}")
        self._env = Environment(
            loader=FileSystemLoader(str(self.bundle_root)),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
            autoescape=False,
        )

    def render_pattern(
        self,
        pattern: PatternEntry,
        context: Mapping[str, Any],
    ) -> List[RenderedFile]:
        """Walk ``pattern.templates`` and emit one :class:`RenderedFile` each.

        Also includes files under the bundle's ``static/`` directory,
        copied verbatim.
        """
        out: List[RenderedFile] = []
        for tmpl in pattern.templates:
            out.append(self._render_one(tmpl, context))
        out.extend(self._collect_static_files())
        return sorted(out, key=lambda r: r.path)

    # ── private helpers ─────────────────────────────────────────────

    def _render_one(self, tmpl: TemplateEntry, context: Mapping[str, Any]) -> RenderedFile:
        # 1. Resolve the source path (may itself contain Jinja vars).
        src_rel = self._render_string(tmpl.from_path, context)
        src_abs = (self.bundle_root / src_rel).resolve()
        try:
            src_abs.relative_to(self.bundle_root)
        except ValueError as e:
            raise RenderError(f"template source {src_rel!r} escapes bundle root") from e
        if not src_abs.is_file():
            raise RenderError(f"template source not found: {src_rel}")

        # 2. Resolve the destination path.
        dest = self._render_string(tmpl.to_path, context)
        _check_path_safety(dest)

        # 3. Render content.
        try:
            jinja_template = self._env.from_string(src_abs.read_text(encoding="utf-8"))
            content_str = jinja_template.render(**context)
        except TemplateError as e:
            raise RenderError(f"failed to render {src_rel!r}: {e}") from e

        return RenderedFile(
            path=dest,
            content=content_str.encode("utf-8"),
            mode=0o644,
            description=tmpl.raw.get("description", ""),
        )

    def _collect_static_files(self) -> List[RenderedFile]:
        """Walk ``<bundle>/static/`` and copy every regular file verbatim.

        Symlinks are **refused outright** — they have no legitimate use case
        in a bundle's static directory, and following them would let a
        malicious bundle exfiltrate arbitrary files from the invoking
        user's filesystem (e.g. ``static/aws-creds`` →
        ``~/.aws/credentials``). Bundles that genuinely need to ship a
        file should commit the bytes, not a pointer.
        """
        static_dir = self.bundle_root / STATIC_DIRNAME
        if not static_dir.is_dir():
            return []
        out: List[RenderedFile] = []
        for path in sorted(static_dir.rglob("*")):
            if path.is_symlink():
                raise RenderError(
                    f"static/ entry {path.relative_to(static_dir).as_posix()!r} is "
                    f"a symlink — symlinks are not permitted in bundle static/ "
                    f"directories (they can leak files from outside the bundle)."
                )
            if not path.is_file():
                continue
            rel = path.relative_to(static_dir).as_posix()
            _check_path_safety(rel)
            out.append(
                RenderedFile(
                    path=rel,
                    content=path.read_bytes(),
                    mode=0o644,
                    description="static file",
                )
            )
        return out

    def _render_string(self, s: str, context: Mapping[str, Any]) -> str:
        """Render an inline string (path field) through Jinja."""
        try:
            return self._env.from_string(s).render(**context)
        except TemplateError as e:
            raise RenderError(f"failed to render path {s!r}: {e}") from e
