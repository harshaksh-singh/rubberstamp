"""Run configuration: one YAML file per run family (configs/smoke.yaml, configs/main.yaml).

`grpo:` is passed verbatim to `trl.GRPOConfig` (unknown keys are rejected before any GPU time is spent);
everything else configures the harness around it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from wf_core.diff import digest
from wf_core.models import Frozen

from .detectors import DETECTOR_NAMES
from .env import EnvOptions
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


class DryRunSettings(Frozen):
    steps: tuple[int, ...] = (0, 10, 20, 30, 40)  # checkpoints at which the scripted policy is sampled
    group_size: int = 8  # rollouts per cell per checkpoint (G)
    eval_samples: int = 16  # held-out rollouts per template at the last checkpoint
    policy: PolicyModel = PolicyModel()


class TrainConfig(Frozen):
    name: str
    model: str
    world: str = "toy"  # "toy" or "package.module:function" returning a wf_train.world.World
    train_variant: str = "cell"  # "cell": each cell's own verifier (multiplexed); or one variant id for all
    system_prompt: str
    lora: LoraSettings = LoraSettings()
    env: dict[str, Any] = {}  # EnvOptions fields
    log_verifiers: tuple[str, ...] = ("strict",)  # logged at weight 0 next to the training reward
    detectors: tuple[str, ...] = DETECTOR_NAMES  # logged at weight 0
    keep_states: bool = False  # store both snapshots in rollouts.jsonl (else rescoring replays)
    base_samples: int = 64  # base-sweep rollouts per template (>= 59 so "0 of n" bounds p0 below 5%)
    design: DesignSettings = DesignSettings()
    grpo: dict[str, Any]
    dry_run: DryRunSettings = DryRunSettings()

    def env_options(self) -> EnvOptions:
        return EnvOptions(**self.env)

    def digest(self) -> str:
        return digest(self.model_dump(mode="json"))


def load_config(path: str | Path) -> TrainConfig:
    return TrainConfig.model_validate(yaml.safe_load(Path(path).read_text()))
