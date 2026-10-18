"""Doctor readiness checks."""

from sortaquant.cli import main
from sortaquant.runtime.doctor import run_doctor


def test_doctor_offline_passes():
    report = run_doctor(probe_feed=False)
    assert report.ok is True
    names = {c.name for c in report.checks}
    assert "imports" in names
    assert "gate1" in names
    assert "simulate_smoke" in names
    assert "feed" not in names


def test_cli_doctor_offline():
    assert main(["doctor", "--offline"]) == 0


def test_cli_walkforward():
    assert main(["walkforward", "--ticks", "250", "--splits", "2", "--purge", "10", "--embargo", "10"]) == 0
