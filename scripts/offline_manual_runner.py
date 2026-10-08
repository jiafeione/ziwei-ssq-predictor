#!/usr/bin/env python3
"""Offline, manual-result runner layered on top of the frozen V3 core algorithm.

This program does not query the network. It accepts a user-attested previous
result and obtains the next target's day pillar/nayin from the frozen local
daily calendar. Its output is explicitly labelled manual-assisted: it is
reproducible for identical entered facts, but does not claim source verification.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from offline_calendar import CALENDAR_ID, WEEKDAYS, lookup, sha256_file
from v3_state_manager import next_scheduled_date, verify_state_digest
from zw_ssq_deterministic import canonical_json, read_json
from zw_ssq_residual9_v3 import (
    ALGORITHM_ID,
    SPEC_VERSION,
    generate,
    normalized_actual,
    parse_date,
    parse_issue,
    sha256_hex,
    validate_and_normalize_window,
    validate_target,
)

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "zw_ssq_v1_config.json"
DEFAULT_CALENDAR = ROOT / "offline_calendar" / "zw_daily_calendar_2026_2099.jsonl"
MANUAL_ALGORITHM_ID = "ZW-SSQ-RESIDUAL9-V3-MANUAL-ASSISTED"
MANUAL_STATE_VERSION = "ZW-SSQ-RESIDUAL9-MANUAL-STATE-v1.0.0"


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def without_manual_digest(state: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in state.items() if key != "manual_state_sha256"}


def seal_manual_state(state: dict[str, Any]) -> dict[str, Any]:
    result = dict(state)
    result["manual_state_sha256"] = sha256_hex(canonical_json(without_manual_digest(result)))
    return result


def verify_manual_state(state: dict[str, Any]) -> None:
    if state.get("manual_state_version") != MANUAL_STATE_VERSION:
        raise ValueError("不是受支持的手动离线状态版本")
    digest = state.get("manual_state_sha256")
    if not isinstance(digest, str):
        raise ValueError("手动离线状态缺少 manual_state_sha256")
    expected = sha256_hex(canonical_json(without_manual_digest(state)))
    if digest != expected:
        raise ValueError("手动离线状态摘要不匹配；不得继续使用已改动状态")
    if state.get("algorithm_id") != MANUAL_ALGORITHM_ID or state.get("core_algorithm_id") != ALGORITHM_ID:
        raise ValueError("手动离线状态算法标识不匹配")


def manual_entry_from_strict(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "position": record["position"],
        "issue": record["issue"],
        "draw_date": record["draw_date"],
        "ziwei_input": record["ziwei_input"],
        "baseline_prediction": record["baseline_prediction"],
        "actual": record["actual"],
        "accepted_for_calculation": True,
        "entry_assurance": "source_verified",
    }


def projection_record(record: dict[str, Any]) -> dict[str, Any]:
    required = ["position", "issue", "draw_date", "ziwei_input", "baseline_prediction", "actual"]
    if record.get("accepted_for_calculation") is not True or any(key not in record for key in required):
        raise ValueError("手动窗口记录不完整，或未标记 accepted_for_calculation=true")
    return {key: record[key] for key in required} | {"verified": True}


def projected_window(state: dict[str, Any], target_issue: int, target_date: date, config: dict[str, Any]) -> list[dict[str, Any]]:
    records = state.get("calculation_window")
    if not isinstance(records, list):
        raise ValueError("手动离线状态缺少 calculation_window")
    return validate_and_normalize_window([projection_record(record) for record in records], target_issue, target_date, config)


def calendar_metadata(calendar_path: Path) -> dict[str, str]:
    first = lookup(calendar_path, "2026-08-20")
    if first.get("calendar_id") != CALENDAR_ID:
        raise ValueError("离线日历 ID 不匹配")
    return {"path": str(calendar_path), "calendar_id": CALENDAR_ID, "sha256": sha256_file(calendar_path)}


def seed(strict_state: dict[str, Any], calendar_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    verify_state_digest(strict_state)
    pending = strict_state.get("pending_forecast")
    if not isinstance(pending, dict) or pending.get("verification_status") != "pending_verification":
        raise ValueError("严格状态必须包含待核验预测")
    target = pending.get("target")
    normalized_target, target_issue, target_date = validate_target(target)
    strict_window = validate_and_normalize_window(strict_state.get("verified_window"), target_issue, target_date, config)
    calendar = calendar_metadata(calendar_path)
    target_calendar = lookup(calendar_path, normalized_target["draw_date"])
    if (target_calendar["day_pillar"], target_calendar["nayin_element"]) != (
        normalized_target["day_pillar"], normalized_target["nayin_element"]
    ):
        raise ValueError("待核验目标的日柱/纳音与离线日历不一致，不能切换至离线模式")
    state = {
        "manual_state_version": MANUAL_STATE_VERSION,
        "algorithm_id": MANUAL_ALGORITHM_ID,
        "core_algorithm_id": ALGORITHM_ID,
        "core_spec_version": SPEC_VERSION,
        "calendar": calendar,
        "data_assurance": "初始9期来自严格V3状态；之后用户手动录入的结果将明确标记为 user_attested_not_source_verified。",
        "calculation_window": [manual_entry_from_strict(record) for record in strict_window],
        "pending_forecast": pending,
        "manual_input_format": "previous_result 包含 issue、draw_date、weekday、red_sorted、blue；next_target 包含 issue、draw_date、weekday。",
    }
    return seal_manual_state(state)


def manual_actual_payload(payload: dict[str, Any], pending_target: dict[str, Any]) -> dict[str, Any]:
    previous = payload.get("previous_result")
    if not isinstance(previous, dict):
        raise ValueError("手动输入缺少 previous_result")
    if previous.get("issue") != pending_target.get("issue") or previous.get("draw_date") != pending_target.get("draw_date"):
        raise ValueError("previous_result 的期号或开奖日与待核验预测不一致")
    previous_date = parse_date(previous["draw_date"], "previous_result.draw_date")
    expected_weekday = WEEKDAYS[previous_date.weekday()][1]
    if previous.get("weekday") != expected_weekday:
        raise ValueError(f"previous_result.weekday 必须为 {expected_weekday}")
    actual = normalized_actual(previous, "previous_result")
    return {
        "issue": previous["issue"],
        "draw_date": previous["draw_date"],
        "actual": actual,
    }


def next_target_from_calendar(payload: dict[str, Any], pending_target: dict[str, Any], calendar_path: Path) -> dict[str, str]:
    next_target = payload.get("next_target")
    if not isinstance(next_target, dict):
        raise ValueError("手动输入缺少 next_target")
    current_date = parse_date(pending_target.get("draw_date"), "pending_forecast.target.draw_date")
    current_issue = parse_issue(pending_target.get("issue"), "pending_forecast.target.issue")
    derived_date = next_scheduled_date(current_date)
    if derived_date.year != int(pending_target["issue"][:4]):
        raise ValueError("下一期跨年；离线模式拒绝猜测新年度首期期号")
    derived_issue = f"{derived_date.year}{int(pending_target['issue'][4:]) + 1:03d}"
    if next_target.get("issue") != derived_issue or next_target.get("draw_date") != derived_date.isoformat():
        raise ValueError("next_target 必须等于由待核验期自动推导的下一期号与日期")
    calendar_record = lookup(calendar_path, derived_date.isoformat())
    if next_target.get("weekday") != calendar_record["weekday_zh"]:
        raise ValueError(f"next_target.weekday 必须为 {calendar_record['weekday_zh']}")
    return {
        "issue": derived_issue,
        "draw_date": derived_date.isoformat(),
        "day_pillar": calendar_record["day_pillar"],
        "nayin_element": calendar_record["nayin_element"],
    }


def manual_entry_from_pending(pending: dict[str, Any], actual: dict[str, Any]) -> dict[str, Any]:
    target = pending["target"]
    return {
        "position": 1,
        "issue": target["issue"],
        "draw_date": target["draw_date"],
        "ziwei_input": {"day_pillar": target["day_pillar"], "nayin_element": target["nayin_element"]},
        "baseline_prediction": pending["baseline_prediction"],
        "actual": actual,
        "accepted_for_calculation": True,
        "entry_assurance": "user_attested_not_source_verified",
    }


def pending_from_core(forecast: dict[str, Any]) -> dict[str, Any]:
    target = forecast["target"]
    return {
        "target": {
            "issue": target["issue"],
            "draw_date": target["draw_date"],
            "day_pillar": target["day_pillar"],
            "nayin_element": target["nayin_element"],
        },
        "baseline_prediction": forecast["baseline"],
        "residual9_corrected": {
            "red": forecast["residual9_corrected"]["red"],
            "blue": forecast["residual9_corrected"]["blue"],
        },
        "forecast_metadata": {
            "config_sha256": forecast["config_sha256"],
            "residual_state_sha256": forecast["window"]["residual_state_sha256"],
            "core_algorithm_id": ALGORITHM_ID,
        },
        "verification_status": "pending_user_attested_result",
    }


def advance(manual_state: dict[str, Any], payload: dict[str, Any], calendar_path: Path, config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    verify_manual_state(manual_state)
    calendar = calendar_metadata(calendar_path)
    if manual_state.get("calendar", {}).get("sha256") != calendar["sha256"]:
        raise ValueError("当前离线日历摘要与状态封存摘要不一致；不得混用不同日历")
    pending = manual_state.get("pending_forecast")
    if not isinstance(pending, dict) or pending.get("verification_status") not in {"pending_verification", "pending_user_attested_result"}:
        raise ValueError("手动离线状态缺少可推进的待核验预测")
    pending_target, pending_issue, pending_date = validate_target(pending.get("target"))
    window = projected_window(manual_state, pending_issue, pending_date, config)

    # Before consuming the newly supplied result, independently recompute the
    # forecast that was sealed for the pending issue. This prevents a state with
    # a valid-looking digest but mismatched pending predictions from being rolled.
    pending_recomputed = generate({"target": pending_target, "verified_window": window}, config)
    expected_baseline = pending_recomputed["baseline"]
    expected_corrected = {
        "red": pending_recomputed["residual9_corrected"]["red"],
        "blue": pending_recomputed["residual9_corrected"]["blue"],
    }
    if pending.get("baseline_prediction") != expected_baseline:
        raise ValueError("待开奖期封存的基准组与当前9期窗口重算结果不一致")
    if pending.get("residual9_corrected") != expected_corrected:
        raise ValueError("待开奖期封存的修正组与当前9期窗口重算结果不一致")
    metadata = pending.get("forecast_metadata")
    if not isinstance(metadata, dict) or metadata.get("config_sha256") != pending_recomputed["config_sha256"] or metadata.get("residual_state_sha256") != pending_recomputed["window"]["residual_state_sha256"]:
        raise ValueError("待开奖期预测摘要与当前9期窗口重算结果不一致")

    current_records = manual_state.get("calculation_window")
    if not isinstance(current_records, list) or len(current_records) != 9:
        raise ValueError("手动离线状态必须恰好保存9期计算窗口")
    for raw_record, normalized_record in zip(current_records, window, strict=True):
        if projection_record(raw_record) != normalized_record:
            raise ValueError("手动离线状态记录与标准化9期窗口不一致")

    actual = manual_actual_payload(payload, pending_target)
    next_target = next_target_from_calendar(payload, pending_target, calendar_path)
    next_target, next_issue, next_date = validate_target(next_target)

    new_manual_record = manual_entry_from_pending(pending, actual["actual"])
    # The post-input window is precisely: newly entered actual result + the
    # eight most recent records that preceded it. The prior ninth record is retired.
    next_manual_window = [new_manual_record] + current_records[:8]
    for position, record in enumerate(next_manual_window, start=1):
        record["position"] = position
    projected_next_window = validate_and_normalize_window(
        [projection_record(record) for record in next_manual_window], next_issue, next_date, config
    )
    forecast = generate({"target": next_target, "verified_window": projected_next_window}, config)
    state = {
        "manual_state_version": MANUAL_STATE_VERSION,
        "algorithm_id": MANUAL_ALGORITHM_ID,
        "core_algorithm_id": ALGORITHM_ID,
        "core_spec_version": SPEC_VERSION,
        "calendar": calendar,
        "data_assurance": "本状态使用用户手动录入的实际结果；计算结果可复算，但不等同于双源核验结果。",
        "calculation_window": next_manual_window,
        "pending_forecast": pending_from_core(forecast),
        "manual_input_format": manual_state["manual_input_format"],
    }
    output = {
        "manual_assisted_algorithm_id": MANUAL_ALGORITHM_ID,
        "core_algorithm_id": ALGORITHM_ID,
        "data_assurance": "user_attested_not_source_verified",
        "calendar": calendar,
        "core_forecast": forecast,
    }
    return seal_manual_state(state), output


def main() -> int:
    parser = argparse.ArgumentParser(description="V3离线日历与手动结果推进工具")
    subparsers = parser.add_subparsers(dest="command", required=True)
    seed_parser = subparsers.add_parser("seed", help="从严格V3状态创建手动离线状态")
    seed_parser.add_argument("--strict-state", required=True, type=Path)
    seed_parser.add_argument("--state-output", required=True, type=Path)
    advance_parser = subparsers.add_parser("advance", help="用用户手动结果离线推进一轮")
    advance_parser.add_argument("--state", required=True, type=Path)
    advance_parser.add_argument("--manual-input", required=True, type=Path)
    advance_parser.add_argument("--state-output", required=True, type=Path)
    advance_parser.add_argument("--forecast-output", required=True, type=Path)
    for subparser in (seed_parser, advance_parser):
        subparser.add_argument("--calendar", type=Path, default=DEFAULT_CALENDAR)
        subparser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    try:
        config = read_json(args.config)
        if args.command == "seed":
            state = seed(read_json(args.strict_state), args.calendar, config)
            write_json(args.state_output, state)
        else:
            state, forecast = advance(read_json(args.state), read_json(args.manual_input), args.calendar, config)
            write_json(args.state_output, state)
            write_json(args.forecast_output, forecast)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
