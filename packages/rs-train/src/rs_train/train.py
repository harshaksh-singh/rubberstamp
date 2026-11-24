"""Build GRPOConfig / GRPOTrainer from a config and a run manifest, or rehearse everything on a Mac.

    # GPU box (after `uv sync --extra gpu`): the base sweep, its go/no-go gate, then one run per seed
    uv run rs-train --config packages/rs-train/configs/main.yaml --out results/main/<date> --seed 0 --phase base
    uv run rs-train --config packages/rs-train/configs/main.yaml --out results/main/<date> --gate
    uv run rs-train --config packages/rs-train/configs/main.yaml --out results/main/<date> --seed 0
    # a run that crashed: continue from its last checkpoint, or move it aside and start it again
    uv run rs-train --config ... --out ... --seed 0 --resume        (or --overwrite)
    # Mac: everything except generation, with a scripted policy
    uv run rs-train --config packages/rs-train/configs/main.yaml --out var/dry-main --dry-run

    # before writing prereg.json: the cells, and the cells with something measured open, each variant gives the
    # prediction test (exit 1 below either target)
    uv run rs-train --config packages/rs-train/configs/main.yaml --out results/main/<date> --check-design

    # after the runs: rescore every stored rollout, label, summarise (same code path as the dry run)
    uv run rs-train --config packages/rs-train/configs/main.yaml --out results/main/<date> --analyze

Rollouts go to OUT/base/ and OUT/<arm>/seed<k>/rollouts.jsonl (runs.py) through a zero-weight recorder reward.
What each variant opens on each template is measured once per OUT and kept in OUT/opens.json, keyed by a digest
of the world, the variants and the rs_* package files (opens.cached_opens); `--verify-design` measures again.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import re
import sys
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .analysis import (
    ShortDesign,
    achievable_n,
    closed_cells,
    open_cells,
    own_open_cells,
    passing_design_seed,
    write_json,
)
from .config import TrainConfig, load_config
from .design import (
    Manifest,
    check_complete,
    latin_design,
    load_manifest,
    make_variant,
    single_variant_design,
)
from .env import EnvOptions, environment_name, make_env_factory
from .gates import GateMonitor, base_gate
from .opens import cached_opens, measured_opens
from .preflight import Limits, behaviour_problems, measure, problems, summary
from .rescore import rescore
from .rewards import CellVerifierReward, detector_rewards, stop_rewards
from .rollouts import Arm, Phase, RolloutRecorder, RolloutStore, require_unique
from .runs import (
    ManifestMismatch,
    RunDirInUse,
    arm_label,
    arm_of,
    attempt_id,
    pin_manifest,
    prepare_run_dir,
    real_runs,
    run_dir,
    run_id,
)
from .world import World, load_world

RESERVED_GRPO_KEYS = frozenset({"output_dir", "seed", "run_name", "reward_weights"})
OPENS_CACHE = "opens.json"  # OUT/opens.json: the measured matrix, keyed by opens.opens_key


# ---------------------------------------------------------------- manifests


def design_manifest(
    cfg: TrainConfig,
    world: World,
    *,
    with_opens: bool = True,
    cache: str | Path | None = None,
    remeasure: bool = False,
    design_seed: int | None = None,
    opens: Mapping[str, Mapping[str, Sequence[str]]] | None = None,
) -> Manifest:
    """The design the config describes: a pure function of design_seed and the sets of variants and templates,
    with what each variant pays on each template measured against the world's verifiers (opens.py; `with_opens=False`
    skips that when only the cell counts matter; `cache` reads and writes the measurement there, opens.cached_opens;
    `opens` passes a matrix already measured). Only the multiplexed arm needs every variant in every seed;
    single-variant arms use its split, variants and measured matrix. `design_seed` overrides the config's."""
    d = cfg.design
    if opens is None and with_opens:
        variants = [make_variant(v) for v in d.variants]
        opens = (
            cached_opens(world, variants, cache, remeasure=remeasure)
            if cache is not None
            else measured_opens(world, variants)
        )
    return latin_design(
        world.templates,
        d.variants,
        seeds=d.seeds,
        design_seed=d.design_seed if design_seed is None else design_seed,
        heldout_fraction=d.heldout_fraction,
        strict_share=d.strict_share,
        prereg_digest=d.prereg_digest,
        complete=cfg.train_variant == "cell",
        opens=opens,
    )


def design_check(cfg: TrainConfig, world: World, design: Manifest) -> dict[str, Any]:
    """What `--check-design` prints: cells and cells with something measured open per variant, of those the cells
    where the knob's own shortcut pays (informational), the targets, the non-strict cells with nothing open, and the
    smallest design seed that meets both targets (the documented rule for choosing design_seed; the same config and
    measured opens)."""
    d = cfg.design

    def rebuild(seed: int) -> Manifest:
        return design_manifest(cfg, world, design_seed=seed, opens=design.opens)

    return {
        "design_seed": design.design_seed,
        "cells_per_variant": achievable_n(design),
        "open_cells_per_variant": open_cells(design),
        "own_open_cells_per_variant": own_open_cells(design),
        "min_cells_per_variant": d.min_cells_per_variant,
        "min_open_cells_per_variant": d.min_open_cells_per_variant,
        "cells_with_nothing_open": closed_cells(design),
        "heldout_templates": list(design.heldout_templates),
        "smallest_passing_design_seed": passing_design_seed(
            design, rebuild, d.min_cells_per_variant, d.min_open_cells_per_variant
        ),
    }


def arm_manifest(cfg: TrainConfig, design: Manifest) -> Manifest:
    """The cells this config's arm trains: the design (multiplexed) or every template under one variant."""
    return check_complete(design) if cfg.train_variant == "cell" else single_variant_design(design, cfg.train_variant)


def build_manifest(cfg: TrainConfig, world: World, path: str | Path | None = None) -> Manifest:
    """The arm's manifest, from a saved design or from the config's."""
    return arm_manifest(cfg, load_manifest(path) if path is not None else design_manifest(cfg, world))


# ---------------------------------------------------------------- environments and rows


def all_tasks(world: World, manifest: Manifest) -> list[Any]:
    tasks = world.tasks_of([*manifest.train_templates, *manifest.heldout_templates])
    return list({task.id: task for task in tasks}.values())


def env_options(cfg: TrainConfig, world: World) -> EnvOptions:
    return dataclasses.replace(cfg.env_options(), answer=world.answer)


def env_factory(cfg: TrainConfig, world: World, manifest: Manifest) -> Any:
    return make_env_factory(world.executor, all_tasks(world, manifest), env_options(cfg, world))


def _rows(cfg: TrainConfig, world: World, manifest: Manifest, cells: list[tuple[Any, str, str, str]]) -> list[dict]:
    """TRL conversational rows. Every column reaches `reset(**row)` and every reward function."""
    multi = len({environment_name(t) for t in all_tasks(world, manifest)}) > 1
    rows = []
    for task, template, variant, cell in cells:
        prompt = [{"role": "system", "content": cfg.system_prompt}, {"role": "user", "content": task.prompt}]
        row = {"prompt": prompt, "task_id": task.id, "template": template, "variant": variant, "cell_id": cell}
        if multi:
            row["environment"] = environment_name(task)
        rows.append(row)
    return rows


def cell_rows(cfg: TrainConfig, world: World, manifest: Manifest, seed: int) -> list[dict[str, Any]]:
    """The seed's training rows: one per task instance of each of its cells."""
    cells = [(c.template, c.variant, c.cell_id) for c in manifest.run(seed).cells]
    return _rows(cfg, world, manifest, [(t, tpl, v, c) for tpl, v, c in cells for t in world.tasks_of([tpl])])


def template_rows(
    cfg: TrainConfig, world: World, manifest: Manifest, templates: list[str], n: int, tag: str
) -> list[dict[str, Any]]:
    """`n` rows per template, cycling through its instances, graded by strict, in cell `<template>::<tag>`."""
    strict = manifest.strict_id() or manifest.variants[0].id
    cells = []
    for template in templates:
        instances = world.tasks_of([template])
        cells += [(instances[i % len(instances)], template, strict, f"{template}::{tag}") for i in range(n)]
    return _rows(cfg, world, manifest, cells)


def base_rows(
    cfg: TrainConfig, world: World, manifest: Manifest, g: int, top_up: BaseTopUp | None = None
) -> list[dict[str, Any]]:
    """The base sweep: base_samples / G rows per template, each generating G completions, so every template
    gets exactly base_samples rollouts whatever its number of instances (the base model, not graded online:
    rescoring scores the sweep under every variant). A top-up samples only its templates, `samples` each."""
    templates = [*manifest.train_templates, *manifest.heldout_templates]
    samples = cfg.base_samples
    if top_up is not None:
        if unknown := sorted(set(top_up.templates) - set(templates)):
            raise ValueError(f"--top-up: {unknown} are not templates of the design")
        templates, samples = list(top_up.templates), top_up.samples
    if samples % g:
        raise ValueError(f"base samples per template ({samples}) must be a multiple of num_generations {g}")
    return template_rows(cfg, world, manifest, templates, samples // g, "base")


@dataclass(frozen=True)
class BaseTopUp:
    """More base samples for some templates, pooled with the first sweep by the analysis (labels pool every base
    run of a template). Needed to call a behaviour discovered: that needs UB95(p0) <= 0.5%, about 600 zero-hit
    samples, which only templates whose trained cells show the behaviour are worth paying for."""

    templates: tuple[str, ...]
    samples: int
    # The top-up's GRPOConfig.seed and sampling seed (`reseed_sampling`), different from the first sweep's, so the
    # top-up draws new samples rather than replaying the first sweep's random stream.
    generation_seed: int


def top_up_seed(attempt: str) -> int:
    """A generation seed derived from the attempt's UTC time: distinct per top-up, reproducible from the run id."""
    return int(re.sub(r"\D", "", attempt)) % (2**31 - 1)


def prompts_per_batch(args: Any) -> int:
    """Distinct prompts per generation batch, as GRPOTrainer._get_train_sampler sizes its RepeatSampler."""
    return args.generation_batch_size // args.num_generations


def fit_rows(rows: list[dict[str, Any]], k: int, tile: bool) -> list[dict[str, Any]]:
    """The rows, ready for TRL's RepeatSampler, which only yields whole chunks of k distinct rows: with fewer
    than k rows it yields nothing and training stops at step 0 without an error, and `len(rows) % k` rows sit
    out each epoch. `tile` repeats the rows of a tiny world up to a whole number of chunks."""
    if len(rows) < k:
        if not tile:
            raise ValueError(
                f"{len(rows)} training rows, but each generation batch needs {k} distinct prompts, so TRL would "
                f"train 0 steps. Use a world with more templates, or set tile_rows: true (tiny worlds only)."
            )
        return rows * (k // math.gcd(len(rows), k))
    if len(rows) % k:
        warnings.warn(
            f"{len(rows) % k} of {len(rows)} training rows sit out each epoch ({k} prompts per generation batch)",
            stacklevel=2,
        )
    return rows


def train_rows(cfg: TrainConfig, world: World, manifest: Manifest, seed: int, args: Any) -> list[dict[str, Any]]:
    return fit_rows(cell_rows(cfg, world, manifest, seed), prompts_per_batch(args), cfg.tile_rows)


# ---------------------------------------------------------------- TRL pieces


def reward_functions(
    cfg: TrainConfig, world: World, manifest: Manifest, recorder: RolloutRecorder | None = None
) -> tuple[list[Any], list[float]]:
    """[cell verifier (weight 1), logged verifiers, detectors, how rollouts ended, recorder (all weight 0)]."""
    variants = {v.id: v for v in manifest.variants}
    funcs: list[Any] = [CellVerifierReward(world.verifier, variants, name="train_verifier")]
    funcs += [CellVerifierReward(world.verifier, variants, f"{v}_verifier", fixed=v) for v in cfg.log_verifiers]
    funcs += detector_rewards(world.profiles, world.executor.schema, world.executor.tools, cfg.detectors)
    funcs += stop_rewards()
    if recorder is not None:
        funcs.append(recorder)
    return funcs, [1.0] + [0.0] * (len(funcs) - 1)


def no_accelerator() -> bool:
    """True on a CPU-only machine (no CUDA device and no Apple GPU)."""
    import torch

    mps = getattr(torch.backends, "mps", None)
    return not torch.cuda.is_available() and not (mps is not None and mps.is_available())


def grpo_kwargs(
    cfg: TrainConfig, out: Path, seed: int, weights: list[float], run_name: str | None = None
) -> dict[str, Any]:
    """GRPOConfig keyword arguments; unknown keys fail here, not after the GPU is rented."""
    from trl.trainer.grpo_config import GRPOConfig

    known = {f.name for f in dataclasses.fields(GRPOConfig)}
    if unknown := sorted(set(cfg.grpo) - known):
        raise ValueError(f"unknown GRPOConfig keys in {cfg.name}: {unknown}")
    if clash := sorted(RESERVED_GRPO_KEYS & set(cfg.grpo)):
        raise ValueError(f"set by the harness, not the config: {clash}")
    grpo = dict(cfg.grpo)
    if grpo.get("bf16") and no_accelerator():
        # A machine with neither CUDA nor an Apple GPU only validates and rehearses (dry run, CI, analysis):
        # transformers refuses bf16 there. Any GPU keeps the configured precision, and fails loudly without it.
        grpo["bf16"] = False
    return {
        **grpo,
        "output_dir": str(out),
        "seed": seed,
        "run_name": run_name or f"{cfg.name}-s{seed}",
        "reward_weights": weights,
    }


def grpo_args(
    cfg: TrainConfig, out: Path, seed: int, weights: list[float], *, phase: Phase = "train", run_name: str | None = None
) -> Any:
    """GRPOConfig of a run. The base sweep runs trainer.evaluate() with G generations per row and no
    importance-sampling correction, whose extra logprob forward only matters when a policy is updated."""
    from trl.trainer.grpo_config import GRPOConfig

    kwargs = grpo_kwargs(cfg, out, seed, weights, run_name)
    if phase == "base":
        train = GRPOConfig(**kwargs)  # G and the generation batch, resolved the way TRL resolves them
        kwargs |= {
            "num_generations_eval": train.num_generations,
            "per_device_eval_batch_size": train.generation_batch_size,
            "vllm_importance_sampling_correction": False,
        }
    return GRPOConfig(**kwargs)


def lora_config(cfg: TrainConfig) -> Any:
    from peft import LoraConfig

    modules = cfg.lora.target_modules
    return LoraConfig(
        r=cfg.lora.r,
        lora_alpha=cfg.lora.alpha,
        lora_dropout=cfg.lora.dropout,
        target_modules=modules if isinstance(modules, str) else list(modules),
        task_type="CAUSAL_LM",
    )


def build_trainer(
    cfg: TrainConfig,
    world: World,
    manifest: Manifest,
    seed: int,
    out: Path,
    phase: Phase = "train",
    *,
    run: str | None = None,
    arm: Arm = "mixed",
    manifest_digest: str | None = None,
    model: Any = None,
    processing_class: Any = None,
    top_up: BaseTopUp | None = None,
):
    """GRPOTrainer with environment_factory, PEFT LoRA, (per config) colocated vLLM, the rollout recorder and
    the gate callback. `model` and `processing_class` replace cfg.model (the CPU rehearsal's tiny model)."""
    from datasets import Dataset
    from trl.trainer.grpo_trainer import GRPOTrainer

    from .trainer import BaseSweepTrainer, GateCallback

    run = run or (f"{cfg.name}-base" if phase == "base" else f"{cfg.name}-s{seed}")
    store = RolloutStore(out / "rollouts.jsonl")
    recorder = RolloutRecorder(
        store,
        world.executor.schema,
        run_id=run,
        seed=seed,
        phase=phase,
        arm=arm,
        manifest_digest=manifest_digest,
        keep_states=cfg.keep_states,
    )
    funcs, weights = reward_functions(cfg, world, manifest, recorder)
    generation_seed = top_up.generation_seed if top_up is not None else seed
    args = grpo_args(cfg, out, generation_seed, weights, phase=phase, run_name=run)
    if phase == "base":  # never trains; TRL still wants a train dataset (a dict factory routes rows by it)
        trainer_class, rows = BaseSweepTrainer, base_rows(cfg, world, manifest, args.num_generations, top_up)
    else:
        trainer_class, rows = GRPOTrainer, train_rows(cfg, world, manifest, seed, args)
    train = Dataset.from_list(rows)
    trainer = trainer_class(
        model=model if model is not None else cfg.model,
        processing_class=processing_class,
        reward_funcs=funcs,
        args=args,
        train_dataset=train,
        eval_dataset=train if phase == "base" else None,
        peft_config=lora_config(cfg),
        environment_factory=env_factory(cfg, world, manifest),
        callbacks=[GateCallback(GateMonitor(cfg.gates))],
    )
    reseed_sampling(args.seed)
    return trainer


def reseed_sampling(seed: int) -> None:
    """Seed Python, NumPy and torch (every CUDA device included) with the run's seed, after the trainer is built.

    TRL 1.18.2 builds the colocated engine as `LLM(..., seed=process_index // tensor_parallel_size,
    distributed_executor_backend="external_launcher")` (trl/generation/vllm_generation.py), so the engine is
    seeded with 0 on one GPU, whatever GRPOConfig.seed is, and vLLM re-seeds the global generators with it when
    it starts. Its per-request `SamplingParams.seed` is no way out: colocate sends each of the G copies of a
    prompt as its own request with n=1, so one seed would make the G completions identical. The external
    launcher runs vLLM's sampler in this process, where a request without a seed draws from torch's default
    generator; seeding it again here, after the engine exists, makes the sampling stream follow GRPOConfig.seed
    (`--seed`, or a top-up's generation seed), which the run saves in training_args.bin. LoRA initialisation and
    TRL's RepeatSampler order are seeded by TRL from the same value before this point. Not verifiable on a Mac
    (no vLLM): on the GPU box, two base sweeps with different seeds must give different completions for a prompt."""
    from transformers import set_seed

    set_seed(seed)


def training_problem(trainer: Any, store: RolloutStore) -> str | None:
    """Why a finished trainer.train() must not be saved as final: a gate stopped it, it trained fewer steps than
    max_steps (an empty sampler trains 0 steps and returns normally), or no rollout was recorded."""
    from .trainer import GateCallback

    gate = next((c for c in trainer.callback_handler.callbacks if isinstance(c, GateCallback)), None)
    if gate is not None and gate.reason:
        return f"stopped by a gate at {gate.reason}"
    if trainer.state.global_step < trainer.args.max_steps:
        return f"trained {trainer.state.global_step} of {trainer.args.max_steps} steps"
    if next(iter(store), None) is None:
        return f"no rollouts were recorded in {store.path}"
    return None


# ---------------------------------------------------------------- pre-flight and gate


def context_report(cfg: TrainConfig, world: World, manifest: Manifest, args: Any, *, offline: bool) -> dict[str, Any]:
    """preflight.py on this world with the configured model's tokenizer: every prompt, and every golden path
    against the context, completion and tool-iteration budgets. `offline` (the Mac dry run) skips it when the
    tokenizer is not cached; a GPU run needs the tokenizer anyway, so there a missing one is an error."""
    from transformers import AutoTokenizer

    try:
        tokenizer = AutoTokenizer.from_pretrained(cfg.model, local_files_only=offline)
    except (OSError, ValueError) as exc:
        if not offline:
            raise
        return {"model": cfg.model, "skipped": f"tokenizer not cached ({type(exc).__name__})", "problems": []}
    fits = measure(
        tokenizer,
        executor=world.executor,
        tasks=all_tasks(world, manifest),
        system_prompt=cfg.system_prompt,
        golden=world.golden_scripts(),
        options=env_options(cfg, world),
        chat_template_kwargs=args.chat_template_kwargs,
    )
    limits = Limits(
        max_completion_length=args.max_completion_length,
        max_tool_calling_iterations=args.max_tool_calling_iterations,
        vllm_max_model_length=args.vllm_max_model_length if args.use_vllm else None,
        margin=cfg.golden_margin,
    )
    return {
        "model": cfg.model,
        **summary(fits),
        "limits": limits.model_dump(),
        "problems": problems(fits, limits),
        "per_task": [f.model_dump() for f in fits],
    }


def gate_report(cfg: TrainConfig, world: World, manifest: Manifest, out: Path) -> dict[str, Any]:
    """The base-sweep go/no-go (gates.base_gate) on OUT/base for this config's arm (`manifest` is the arm's): the
    sweep is rescored by strict, for the template pass range, and by every grader the arm's cells train on, for
    each seed's predicted zero-variance share."""
    strict = manifest.strict_id()
    if strict is None:
        raise ValueError("the base-sweep gate needs a strict variant in the design")
    records = [r for path in sorted((out / "base").rglob("rollouts.jsonl")) for r in RolloutStore(path)]
    records = [r for r in require_unique(records) if r.phase == "base"]  # the first sweep and any top-ups
    graders = {strict} | {cell.variant for _, cell in manifest.cells()}
    variants = [v for v in manifest.variants if v.id in graders]
    rows = rescore(records, world, variants, detectors=(), signals=())
    g = grpo_args(cfg, out / "trl", manifest.seeds[0], [1.0]).num_generations
    cells = [(seed, cell.template, cell.variant) for seed, cell in manifest.cells()]
    report = base_gate(rows, strict=strict, templates=manifest.train_templates, g=g, settings=cfg.gates, cells=cells)
    return {"base_rollouts": len(records), "arm": arm_of(cfg.train_variant, manifest), **report}


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rs-train", description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--config", required=True, help="YAML run config (configs/smoke.yaml, configs/main.yaml)")
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument("--manifest", help="design manifest JSON to use (default: the config's, pinned in OUT)")
    parser.add_argument("--seed", type=int, help="which manifest seed (run) to train; GPU runs only")
    parser.add_argument("--phase", choices=["train", "base"], default="train", help="base = the step-0 sweep")
    parser.add_argument(
        "--top-up",
        metavar="TEMPLATES",
        help="with --phase base: a further base sweep of these comma-separated templates into OUT/base/topup-<attempt>",
    )
    parser.add_argument("--samples", type=int, help="with --top-up: base samples per template (default base_samples)")
    parser.add_argument("--train-variant", help="override the config: 'cell' or one variant id for every cell")
    parser.add_argument("--predictions", help="preregistered predictions JSON, Brier-scored by the analysis")
    parser.add_argument("--dry-run", action="store_true", help="scripted policy instead of a model (Mac)")
    parser.add_argument("--analyze", action="store_true", help="rescore, label and summarise OUT/**/rollouts.jsonl")
    parser.add_argument("--gate", action="store_true", help="base-sweep go/no-go on OUT/base; exit 1 on no-go")
    parser.add_argument(
        "--check-design",
        action="store_true",
        help="cells and open cells per variant for the prediction test; exit 1 below design.min_cells_per_variant "
        "or design.min_open_cells_per_variant",
    )
    parser.add_argument(
        "--verify-design",
        action="store_true",
        help=f"measure the opens again instead of reading OUT/{OPENS_CACHE}, and compare with the pinned design",
    )
    again = parser.add_mutually_exclusive_group()
    again.add_argument("--resume", action="store_true", help="continue this run from its last checkpoint")
    again.add_argument("--overwrite", action="store_true", help="move this run's earlier attempt aside, start again")
    args = parser.parse_args(argv)
    try:
        return _main(parser, args)
    except (ManifestMismatch, RunDirInUse, ShortDesign) as exc:
        print(f"rs-train: {exc}", file=sys.stderr)
        return 1


def _main(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    # Flag combinations that are wrong whatever the design: refused before the world loads and the opens are measured.
    if args.phase == "base" and args.resume:
        parser.error("--resume: the base sweep keeps no checkpoints; use --overwrite")
    if (args.top_up or args.samples) and args.phase != "base":
        parser.error("--top-up and --samples need --phase base")
    if args.samples and not args.top_up:
        parser.error("--samples needs --top-up")
    if args.seed is None and not (args.check_design or args.dry_run or args.analyze or args.gate):
        parser.error("--seed is required for a run (one of the design's seeds)")  # which seeds: checked below
    cfg = load_config(args.config)
    if args.train_variant:
        cfg = cfg.model_copy(update={"train_variant": args.train_variant})
    world = load_world(cfg.world)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pinned = out / "manifest.json"
    if args.dry_run and (found := real_runs(out)):
        raise RunDirInUse(
            f"--dry-run: {out} holds results of real runs ({', '.join(found[:3])}). A dry run rewrites manifest.json, "
            f"labels.json, metrics.json and run_card.json there and adds scripted rollouts the analysis would count: "
            f"give it its own --out (e.g. var/dry-{cfg.name})."
        )
    if args.manifest or (args.analyze and pinned.exists()):  # the analysis reads the design the runs used
        design = load_manifest(args.manifest or pinned)
        given = True
    else:  # measured once per OUT (OUT/opens.json), then read back while the world and the code are unchanged
        design = design_manifest(cfg, world, cache=out / OPENS_CACHE, remeasure=args.verify_design)
        given = False
    target, open_target = cfg.design.min_cells_per_variant, cfg.design.min_open_cells_per_variant
    if args.check_design:
        print(json.dumps(design_check(cfg, world, design), indent=2))
        achievable_n(design, target, open_target)
        return 0
    trains_cells = args.dry_run or (args.phase == "train" and not (args.analyze or args.gate))
    if args.predictions or (cfg.train_variant == "cell" and trains_cells):
        # predictions are used here, or this run produces the cell outcomes they will score
        achievable_n(design, target, open_target)
    if not args.dry_run:  # a rehearsal pins its own design in its own OUT (dryrun.dry_run)
        design = pin_manifest(out, design, given=given)
    manifest = arm_manifest(cfg, design)
    arm = arm_of(cfg.train_variant, design)
    label = arm_label(arm, cfg.train_variant)
    if args.dry_run:
        from .dryrun import dry_run

        report = dry_run(cfg, world, manifest, out, predictions=args.predictions, arm=arm, design=design)
        print(json.dumps(report, indent=2))
        return 0
    if args.analyze:
        from .analysis import analyze, collect

        print(json.dumps(analyze(cfg, world, manifest, out, collect(out), predictions=args.predictions), indent=2))
        return 0
    if args.gate:
        report = gate_report(cfg, world, manifest, out)
        write_json(out / "base_gate.json", report)  # strict JSON: an undefined rate (a cell with no samples) is null
        print((out / "base_gate.json").read_text(), end="")
        return 0 if report["ok"] else 1
    if args.seed is None or args.seed not in manifest.seeds:
        parser.error(f"--seed must be one of the manifest's seeds {list(manifest.seeds)}")
    attempt = attempt_id()
    top_up = None
    if args.top_up:
        templates = tuple(t for t in args.top_up.split(",") if t)
        top_up = BaseTopUp(templates, args.samples or cfg.base_samples, top_up_seed(attempt))
    directory = run_dir(out, label, args.seed, args.phase)
    if top_up is not None:  # pooled with the first sweep: a new directory, never a rerun of OUT/base
        directory = directory / f"topup-{attempt}"
    context = context_report(cfg, world, manifest, grpo_args(cfg, directory, args.seed, [1.0]), offline=False)
    if found := context["problems"] + behaviour_problems(world, all_tasks(world, manifest)):
        print("rs-train: the pre-flight failed:\n  " + "\n  ".join(found), file=sys.stderr)
        return 1
    resumed = prepare_run_dir(out, directory, attempt=attempt, resume=args.resume, overwrite=args.overwrite)
    if resumed is not None:
        print(f"rs-train: resuming {directory} from step {resumed.step}; dropped {resumed.dropped} later records")
    run = run_id(cfg.name, label, args.seed, args.phase, attempt) + ("-topup" if top_up is not None else "")
    trainer = build_trainer(
        cfg,
        world,
        manifest,
        args.seed,
        directory,
        args.phase,
        run=run,
        arm=arm,
        manifest_digest=design.digest(),
        top_up=top_up,
    )
    store = RolloutStore(directory / "rollouts.jsonl")
    if args.phase == "base":
        print(json.dumps(trainer.evaluate(), indent=2))
        expected = (
            len(top_up.templates) * top_up.samples
            if top_up is not None
            else (len(manifest.train_templates) + len(manifest.heldout_templates)) * cfg.base_samples
        )
        stored = sum(r.run_id == run for r in store)
        if stored != expected:
            print(f"rs-train: the base sweep stored {stored} of {expected} rollouts", file=sys.stderr)
            return 1
        return 0
    trainer.train(resume_from_checkpoint=str(resumed.checkpoint) if resumed is not None else None)
    if problem := training_problem(trainer, store):
        print(f"rs-train: {problem}; {directory / 'final'} was not saved", file=sys.stderr)
        return 1
    trainer.save_model(str(directory / "final"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
