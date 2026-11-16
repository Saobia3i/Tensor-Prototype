"""CI gate: the held-out AI evaluation must pass all safety thresholds."""
from evals.run_evals import DEFAULT_THRESHOLDS, run


def test_heldout_eval_safety_gate():
    assert run("heldout", DEFAULT_THRESHOLDS) == 0


def test_dev_split_runs():
    assert run("dev", DEFAULT_THRESHOLDS) == 0
