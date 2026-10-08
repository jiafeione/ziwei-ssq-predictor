#!/usr/bin/env python3
"""Persistent-state helper for ZW-SSQ-RESIDUAL9-V3.

After bootstrap, `advance` needs only the pending issue's verified actual result
plus the next target's externally frozen Ziwei input. Historical window data is
reused locally and no longer needs to be fetched again.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from zw_ssq_deterministic import canonical_json, read_json
from zw_ssq_residual9_v3 import (
    ALGORITHM_ID,
    SPEC_VERSION,
    SCHEDULED_WEEKDAYS,
    generate,
    normalized_actual,
    parse_date,
    parse_issue,
    sha256_hex,
    target_to_v1_input,
    validate_and_normalize_window,
    validate_target,
)

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "zw_ssq_v1_config.json"


def write_json(path: Path, value: dict[str, Any], pretty: bool = True) -> None:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2 if pretty else None,
                      separators=None if pretty else (",", ":"))
    path.write_text(text + "\n", encoding="utf-8")


def state_without_digest(state: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in state.items() if key != "state_sha256"}


def seal_state(state: dict[str, Any]) -> dict[str, Any]:
    sealed = dict(state)
    sealed["state_sha256"] = sha256_hex(canonical_json(state_without_digest(sealed)))
    return sealed


def verify_state_digest(state: dict[str, Any]) -> None:
    digest = state.get("state_sha256")
    if not isinstance(digest, str):
        raise ValueError("状态缺少 state_sha256")
    expected = sha256_hex(canonical_json(state_without_digest(state)))
    if digest != expected:
        raise ValueError("状态摘要不匹配；状态可能被未记录地修改")


def next_scheduled_date(after: date) -> date:
    candidate = after + timedelta(days=1)
    while candidate.weekday() not in SCHEDULED_WEEKDAYS:
        candidate += timedelta(days=1)
    return candidate


def pending_object(target: dict[str, str], forecast: dict[str, Any]) -> dict[str, Any]:
    return {
        "target": target,
        "baseline_prediction": forecast["baseline"],
        "residual9_corrected": {
            "red": forecast["residual9_corrected"]["red"],
            "blue": forecast["residual9_corrected"]["blue"],
        },
        "forecast_metadata": {
            "config_sha256": forecast["config_sha256"],
            "residual_state_sha256": forecast["window"]["residual_state_sha256"],
        },
        "verification_status": "pending_verification",
    }


def bootstrap(input_data: dict[str, Any], config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    target, target_issue, target_date = validate_target(input_data.get("target"))
    window = validate_and_normalize_window(input_data.get("verified_window"), target_issue, target_date, config)
    forecast = generate({"target": target, "verified_window": window}, config)
    state = {
        "state_version": "ZW-SSQ-RESIDUAL9-STATE-v1.0.0",
        "algorithm_id": ALGORITHM_ID,
        "spec_version": SPEC_VERSION,
        "source_registry_file": "RESULT_SOURCE_REGISTRY.json",
        "verified_window": window,
        "pending_forecast": pending_object(target, forecast),
        "incremental_rule": "常规每期只联网核验 pending_forecast.target.issue；核验通过后滚动最近9期窗口。",
    }
    return seal_state(state), forecast


def normalize_actual_payload(actual_payload: dict[str, Any], pending: dict[str, Any]) -> dict[str, Any]:
    target = pending.get("target")
    if not isinstance(target, dict):
        raise ValueError("pending_forecast 缺少 target")
    issue = actual_payload.get("issue")
    draw_date = actual_payload.get("draw_date")
    if issue != target.get("issue") or draw_date != target.get("draw_date"):
        raise ValueError("实际结果的期号或开奖日与 pending_forecast 不一致")
    verification = actual_payload.get("verification")
    if not isinstance(verification, dict) or verification.get("status") != "verified" or verification.get("all_fields_agree") is not True:
        raise ValueError("实际结果必须已完成双源核验：status=verified 且 all_fields_agree=true")
    actual = normalized_actual(actual_payload, "actual_result")
    return {
        "issue": issue,
        "draw_date": draw_date,
        "actual": actual,
    }


def advance(
    state: dict[str, Any], actual_payload: dict[str, Any], next_ziwei: dict[str, Any], config: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    verify_state_digest(state)
    pending = state.get("pending_forecast")
    if not isinstance(pending, dict) or pending.get("verification_status") != "pending_verification":
        raise ValueError("状态不存在待核验的 pending_forecast")
    target = pending.get("target")
    if not isinstance(target, dict):
        raise ValueError("pending_forecast.target 缺失")
    target_issue = parse_issue(target.get("issue"), "pending_forecast.target.issue")
    target_date = parse_date(target.get("draw_date"), "pending_forecast.target.draw_date")
    current_window = validate_and_normalize_window(state.get("verified_window"), target_issue, target_date, config)
    actual = normalize_actual_payload(actual_payload, pending)
    baseline = pending.get("baseline_prediction")
    if not isinstance(baseline, dict):
        raise ValueError("pending_forecast 缺少 baseline_prediction")

    new_record = {
        "position": 1,
        "issue": target["issue"],
        "draw_date": target["draw_date"],
        "ziwei_input": {
            "day_pillar": target["day_pillar"],
            "nayin_element": target["nayin_element"],
        },
        "baseline_prediction": baseline,
        "actual": actual["actual"],
        "verified": True,
    }
    raw_next_window = [new_record] + current_window[:8]
    for position, record in enumerate(raw_next_window, start=1):
        record["position"] = position

    next_date = next_scheduled_date(target_date)
    if next_date.year != int(target["issue"][:4]):
        raise ValueError("下一开奖日跨年；必须先查新年度官方首期期号，工具拒绝猜测")
    next_issue = f"{next_date.year}{int(target['issue'][4:]) + 1:03d}"
    if not isinstance(next_ziwei, dict):
        raise ValueError("next_ziwei 必须是对象")
    if next_ziwei.get("draw_date") != next_date.isoformat():
        raise ValueError("next_ziwei.draw_date 与自动推导的下一开奖日不一致")
    next_target = {
        "issue": next_issue,
        "draw_date": next_date.isoformat(),
        "day_pillar": next_ziwei.get("day_pillar"),
        "nayin_element": next_ziwei.get("nayin_element"),
    }
    # Validate next target and the rolled window before forecasting.
    normalized_target, next_issue_int, next_date_value = validate_target(next_target)
    normalized_window = validate_and_normalize_window(raw_next_window, next_issue_int, next_date_value, config)
    forecast = generate({"target": normalized_target, "verified_window": normalized_window}, config)
    next_state = {
        "state_version": state["state_version"],
        "algorithm_id": ALGORITHM_ID,
        "spec_version": SPEC_VERSION,
        "source_registry_file": state.get("source_registry_file", "RESULT_SOURCE_REGISTRY.json"),
        "verified_window": normalized_window,
        "pending_forecast": pending_object(normalized_target, forecast),
        "incremental_rule": state.get("incremental_rule"),
    }
    return seal_state(next_state), forecast


def main() -> int:
    parser = argparse.ArgumentParser(description="V3 persistent state manager")
    subparsers = parser.add_subparsers(dest="command", required=True)
    bootstrap_parser = subparsers.add_parser("bootstrap")
    bootstrap_parser.add_argument("--input", required=True, type=Path)
    bootstrap_parser.add_argument("--state-output", required=True, type=Path)
    bootstrap_parser.add_argument("--forecast-output", required=True, type=Path)
    advance_parser = subparsers.add_parser("advance")
    advance_parser.add_argument("--state", required=True, type=Path)
    advance_parser.add_argument("--actual", required=True, type=Path)
    advance_parser.add_argument("--next-ziwei", required=True, type=Path)
    advance_parser.add_argument("--state-output", required=True, type=Path)
    advance_parser.add_argument("--forecast-output", required=True, type=Path)
    for subparser in (bootstrap_parser, advance_parser):
        subparser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    try:
        config = read_json(args.config)
        if args.command == "bootstrap":
            state, forecast = bootstrap(read_json(args.input), config)
        else:
            state, forecast = advance(read_json(args.state), read_json(args.actual), read_json(args.next_ziwei), config)
        write_json(args.state_output, state)
        write_json(args.forecast_output, forecast)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
