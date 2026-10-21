"""Unit tests for scripts/bootstrap_backups.py (no AWS calls)."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "bootstrap_backups.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("bootstrap_backups", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bootstrap_backups = _load_module()


def test_parse_args_defaults_to_dry_run() -> None:
    args = bootstrap_backups.parse_args([])
    assert args.apply is False
    assert args.bucket == bootstrap_backups.DEFAULT_BUCKET
    assert args.region == bootstrap_backups.DEFAULT_REGION
    assert args.retention_days == 30


def test_parse_args_apply_sets_flag() -> None:
    args = bootstrap_backups.parse_args(["--apply"])
    assert args.apply is True


def test_dry_run_and_apply_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        bootstrap_backups.parse_args(["--dry-run", "--apply"])


@pytest.mark.parametrize(
    "name",
    ["my-backups", "team.backups-01", "abc"],
)
def test_valid_bucket_names(name: str) -> None:
    assert bootstrap_backups.validate_bucket_name(name) is None


@pytest.mark.parametrize(
    "name",
    [
        "ab",  # too short
        "A-Upper-Case",  # uppercase not allowed
        "-leading-hyphen",  # must start alphanumeric
        "trailing-hyphen-",  # must end alphanumeric
        "a" * 64,  # too long
        "has..dots",  # consecutive dots
    ],
)
def test_invalid_bucket_names(name: str) -> None:
    assert bootstrap_backups.validate_bucket_name(name) is not None


def test_apply_with_placeholder_bucket_is_hard_error() -> None:
    # --apply against the committed placeholder default must refuse.
    assert bootstrap_backups.main(["--apply"]) == 1


def test_apply_with_placeholder_region_is_hard_error() -> None:
    args = bootstrap_backups.parse_args(
        ["--apply", "--bucket", "real-backups", "--kms-key", "key-123"]
    )
    # region is still PLACEHOLDER_AWS_REGION.
    errors = bootstrap_backups.validate_args(args)
    assert any("placeholder" in e.lower() for e in errors)


def test_empty_region_is_rejected() -> None:
    args = bootstrap_backups.parse_args(
        ["--apply", "--bucket", "real-backups", "--kms-key", "k", "--region", "  "]
    )
    errors = bootstrap_backups.validate_args(args)
    assert any("region" in e for e in errors)


def test_retention_days_must_be_positive() -> None:
    args = bootstrap_backups.parse_args(["--retention-days", "0"])
    errors = bootstrap_backups.validate_args(args)
    assert any("retention-days" in e for e in errors)


def test_dry_run_plan_names_30_day_expiry(capsys: pytest.CaptureFixture[str]) -> None:
    rc = bootstrap_backups.main([])
    captured = capsys.readouterr()
    assert rc == 0
    assert "expire backup objects after 30 days" in captured.out
    assert "dry run" in captured.out


def test_dry_run_plan_reflects_custom_retention(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = bootstrap_backups.main(["--bucket", "real-backups", "--retention-days", "7"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "expire backup objects after 7 days" in captured.out
