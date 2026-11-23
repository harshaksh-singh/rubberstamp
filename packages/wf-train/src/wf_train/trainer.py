"""The TRL-side classes `train.build_trainer` uses. Importing this module imports torch, transformers and
TRL, so only build_trainer does; `import wf_train` never does.
"""

from __future__ import annotations

from typing import Any

from transformers.trainer_callback import TrainerCallback
from trl.trainer.grpo_trainer import GRPOTrainer

from .gates import GateMonitor


class GateCallback(TrainerCallback):
    """Stops training at the first logged step where a gate fires; `reason` says which and when."""

    def __init__(self, monitor: GateMonitor) -> None:
        self.monitor = monitor
        self.reason: str | None = None

    def on_log(self, args: Any, state: Any, control: Any, logs: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        if self.reason is None and logs and (why := self.monitor.update(logs)):
            self.reason = f"step {state.global_step}: {why}"
            control.should_training_stop = True
        return control


class BaseSweepTrainer(GRPOTrainer):
    """A GRPOTrainer whose evaluation only samples and scores. GRPOTrainer.prediction_step would also run a
    loss forward over every eval batch; the base sweep needs only the rollouts, which the recorder stores
    while the rewards are computed."""

    def prediction_step(self, model: Any, inputs: Any, prediction_loss_only: bool, ignore_keys: Any = None) -> Any:
        self._prepare_inputs(inputs)  # generate, run the tools, call every reward function
        return None, None, None
