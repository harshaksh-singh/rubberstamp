"""Run directories: attempts, arms, the output layout, the pinned design, and earlier attempts.

Layout under `--out`:

    manifest.json           the design, pinned by the first invocation (every arm derives its cells from it)
    base/                   the base sweep (one per --out: the base model is the same for every arm)
    <arm>/seed<k>/          one training run: rollouts.jsonl, checkpoint-*, final/

`<arm>` is "mixed", "pure_strict" or "single_variant-<variant id>". A run id is
`<config>-<arm>-s<seed>-<attempt>` (base: `<config>-base-<attempt>`), where the attempt is the UTC start
time, so a restarted run never reuses an episode id. A run directory that already holds rollouts or
checkpoints is refused unless the caller resumes from its last checkpoint or moves it aside.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .design import Manifest, load_manifest, save_manifest
from .rollouts import Arm, Phase, RolloutStore

DRY_ATTEMPT = "dry"  # the dry run's fixed attempt, so two dry runs give byte-identical tables


def attempt_id(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")


def arm_of(train_variant: str, manifest: Manifest) -> Arm:
    """'cell' -> mixed (each cell graded by its own variant); the strict id -> pure_strict; any other variant
    -> single_variant (every cell graded by it)."""
    if train_variant == "cell":
        return "mixed"
    if train_variant not in {v.id for v in manifest.variants}:
        raise KeyError(f"unknown train_variant {train_variant!r}: 'cell' or one of the manifest's variants")
    return "pure_strict" if train_variant == manifest.strict_id() else "single_variant"


def arm_label(arm: Arm, train_variant: str) -> str:
    """Directory and run-id label of an arm; single-variant arms also name their variant."""
    return f"{arm}-{train_variant}" if arm == "single_variant" else arm


def run_dir(out: Path, label: str, seed: int, phase: Phase) -> Path:
    return out / "base" if phase == "base" else out / label / f"seed{seed}"


def run_id(name: str, label: str, seed: int, phase: Phase, attempt: str) -> str:
    return f"{name}-base-{attempt}" if phase == "base" else f"{name}-{label}-s{seed}-{attempt}"


# ---------------------------------------------------------------- the pinned design


class ManifestMismatch(ValueError):
    pass


def pin_manifest(out: Path, manifest: Manifest, *, given: bool) -> Manifest:
    """The design for OUT: pinned in OUT/manifest.json by the first invocation and refused if it changes.

    `manifest` is either rebuilt from the config and world, or `given` with --manifest. A rebuilt design can
    drift silently (variants reordered, a template added: cells move and held-out templates change), so it
    must match the pinned one; passing the pinned file with --manifest keeps it after a deliberate change."""
    path = out / "manifest.json"
    if not path.exists():
        save_manifest(manifest, path)
        return manifest
    pinned = load_manifest(path)
    if pinned.digest() != manifest.digest():
        source = "the --manifest file" if given else "the design rebuilt from the config and world"
        raise ManifestMismatch(
            f"{path} pins design {pinned.digest()[:12]}, but {source} is {manifest.digest()[:12]}. Every run in "
            f"one --out must share one design: pass --manifest {path} to keep the pinned one, or use a new --out."
        )
    return pinned


# ---------------------------------------------------------------- earlier attempts


class RunDirInUse(RuntimeError):
    pass


@dataclass(frozen=True)
class Resume:
    checkpoint: Path
    step: int  # the checkpoint's global step: training continues from here
    dropped: int  # records generated at `step` or later by the earlier attempt, removed from rollouts.jsonl


def checkpoints(directory: Path) -> list[tuple[int, Path]]:
    """(global step, path) of every checkpoint-<step> directory, oldest first."""
    found = (
        (int(m.group(1)), p) for p in directory.glob("checkpoint-*") if (m := re.fullmatch(r"checkpoint-(\d+)", p.name))
    )
    return sorted(found)


def prepare_run_dir(out: Path, directory: Path, *, attempt: str, resume: bool, overwrite: bool) -> Resume | None:
    """Ready DIRECTORY for a new attempt. An earlier attempt (rollouts.jsonl or checkpoint-*) is refused unless
    `resume` (continue from its last checkpoint; its records from that step on are dropped, because those
    steps run again) or `overwrite` (move it to <out>.superseded/<attempt>/, outside OUT, so `--analyze`
    never reads it)."""
    if resume and overwrite:
        raise ValueError("--resume and --overwrite exclude each other")
    store = RolloutStore(directory / "rollouts.jsonl")
    saved = checkpoints(directory)
    earlier = [name for name, there in (("rollouts.jsonl", store.path.exists()), ("checkpoints", saved)) if there]
    if not earlier:
        if resume:
            raise RunDirInUse(f"--resume: {directory} holds no earlier attempt")
        return None
    if overwrite:
        target = out.parent / f"{out.name}.superseded" / attempt / directory.relative_to(out)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(directory, target)
        return None
    if resume:
        if not saved:
            raise RunDirInUse(f"--resume: {directory} has rollouts but no checkpoint; use --overwrite")
        step, path = saved[-1]
        return Resume(checkpoint=path, step=step, dropped=store.drop_from_step(step))
    raise RunDirInUse(
        f"{directory} already holds {' and '.join(earlier)} from an earlier attempt. Pass --resume to continue "
        f"from its last checkpoint, or --overwrite to move it to {out.name}.superseded/ and start again."
    )
