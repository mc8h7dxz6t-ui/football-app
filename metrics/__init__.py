"""Institutional verification metrics — Brier, Murphy, calibration, data room export."""

from metrics.brier import (
    brier_macro,
    brier_multiclass_event,
    brier_race,
    log_loss_macro,
    normalize_probs,
)
from metrics.calibration import calibration_table_all_legs, calibration_table_top_pick
from metrics.data_room import build_data_room_export, institutional_gates
from metrics.murphy import murphy_decomposition
from metrics.racing import evaluate_racing_window, racing_record_from_dict
from metrics.racing_emit import append_jsonl, build_settled_race_record

__all__ = [
    "brier_macro",
    "brier_multiclass_event",
    "brier_race",
    "log_loss_macro",
    "normalize_probs",
    "calibration_table_all_legs",
    "calibration_table_top_pick",
    "murphy_decomposition",
    "build_data_room_export",
    "institutional_gates",
    "evaluate_racing_window",
    "racing_record_from_dict",
    "append_jsonl",
    "build_settled_race_record",
]
