"""Run configuration: one YAML file per run family (configs/smoke.yaml, configs/main.yaml).

`grpo:` is passed verbatim to `trl.GRPOConfig` (unknown keys are rejected before any GPU time is spent);
everything else configures the harness around it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from rs_core.diff import digest
from rs_core.models import Frozen

from .detectors import DETECTOR_NAMES
from .env import EnvOptions
from .labels import LabelThresholds
from .scripted import PolicyModel


class LoraSettings(Frozen):
    r: int = 16
    alpha: int = 32
    dropout: float = 0.0
    target_modules: str | tuple[str, ...] = "all-linear"  # every linear projection; PEFT leaves out lm_head


class DesignSettings(Frozen):
    design_seed: int = 0
    seeds: tuple[int, ...] = (0, 1, 2)
    heldout_fraction: float = 0.2
    strict_share: float = 0.25
    variants: tuple[str | dict[str, Any], ...] = ("strict",)
    prereg_digest: str | None = None  # digest of the preregistered predictions, set before the first run
    # The prediction test's N: the fewest cells (template x seed) any non-strict variant may get. A design
    # below it is refused before a run whose outcomes the predictions score (analysis.achievable_n).
    min_cells_per_variant: int = 0


class DryRunSettings(Frozen):
    steps: tuple[int, ...] = (0, 10, 20, 30, 40)  # global steps whose generation batch the scripted policy plays
    eval_samples: int = 16  # held-out rollouts per template at the last checkpoint
    policy: PolicyModel = PolicyModel()


class GateSettings(Frozen):
    """Stop rules checked at every logged training step (gates.GateMonitor through trainer.GateCallback), and
    the base-sweep go/no-go (`rs-train --gate`). Windows count logged steps; the configs log every step."""

    zero_std_max: float = 0.6  # research/64 §3: more groups than this without reward variance -> SFT first
    zero_std_window: int = 10
    no_tool_call_steps: int = 5  # tools/call_frequency == 0 this many logged steps in a row
    unparsed_max: float = 0.2  # research/64 §4: tool-call parse failures under 20%
    unparsed_window: int = 5
    step_time_max: float | None = 180.0  # seconds per optimizer step, median (the plan assumes 1.5-3 min at main)
    step_time_window: int = 3
    logp_diff_max: float | None = 0.5  # sampling/sampling_logp_difference/mean: vLLM vs trainer, per token
    logp_diff_window: int = 3
    base_pass_range: tuple[float, float] = (0.1, 0.7)  # research/64 §3: per-task strict pass@G in 10-70%
    base_zero_variance_max: float = 0.6  # predicted share of all-equal groups, mean(p^G + (1 - p)^G)


class TrainConfig(Frozen):
    name: str
    model: str
    world: str = "toy"  # "toy" or "package.module:function" returning a rs_train.world.World
    train_variant: str = "cell"  # "cell": each cell's own verifier (multiplexed); or one variant id for all
    system_prompt: str
    lora: LoraSettings = LoraSettings()
    env: dict[str, Any] = {}  # EnvOptions fields
    log_verifiers: tuple[str, ...] = ("strict",)  # logged at weight 0 next to the training reward
    detectors: tuple[str, ...] = DETECTOR_NAMES  # logged at weight 0
    keep_states: bool = False  # store both snapshots in rollouts.jsonl (else rescoring replays)
    # Base-sweep rollouts per template, spread over its instances: base_samples / G rows of G generations
    # each, so it must be a multiple of num_generations.
    base_samples: int = 64
    # Repeat a seed's training rows until they fill whole generation batches. Only for tiny worlds with fewer
    # rows than prompts per batch, which otherwise give TRL an empty sampler (0 training steps).
    tile_rows: bool = False
    golden_margin: float = 1.5  # pre-flight: golden tokens and calls x this must fit the completion budget
    design: DesignSettings = DesignSettings()
    grpo: dict[str, Any]
    gates: GateSettings = GateSettings()
    labels: LabelThresholds = LabelThresholds()  # preregistered label rules (margin, alpha, ...); in the digest
    dry_run: DryRunSettings = DryRunSettings()

    def env_options(self) -> EnvOptions:
        return EnvOptions(**self.env)

    def digest(self) -> str:
        return digest(self.model_dump(mode="json"))


def load_config(path: str | Path) -> TrainConfig:
    return TrainConfig.model_validate(yaml.safe_load(Path(path).read_text()))
