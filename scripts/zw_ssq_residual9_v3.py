#!/usr/bin/env python3
"""Reference implementation for ZW-SSQ-RESIDUAL9-V3.

The baseline is exactly V1 baseline. The second group adjusts V1 target scores
using only the frozen residuals between the preceding nine V1 baselines and
verified actual results.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from zw_ssq_deterministic import canonical_json, generate as generate_v1, number_element, read_json

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "zw_ssq_v1_config.json"
ALGORITHM_ID = "ZW-SSQ-RESIDUAL9-V3"
SPEC_VERSION = "ZW-SSQ-RESIDUAL9-V3.0.0"
WINDOW_WEIGHTS = [9, 8, 7, 6, 5, 4, 3, 2, 1]
ZIWEI_MULTIPLIER = 6
SCHEDULED_WEEKDAYS = {1, 3, 6}


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_date(value: Any, field: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{field} 必须是 YYYY-MM-DD 字符串")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} 必须是有效的 YYYY-MM-DD 日期") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{field} 必须采用零填充 YYYY-MM-DD 格式")
    return parsed


def parse_issue(value: Any, field: str) -> int:
    if not isinstance(value, str) or len(value) != 7 or not value.isdigit():
        raise ValueError(f"{field} 必须是 7 位数字期号")
    return int(value)


def parse_ball(value: Any, low: int, high: int, field: str) -> int:
    if not isinstance(value, str) or len(value) != 2 or not value.isdigit():
        raise ValueError(f"{field} 必须是两位数字字符串")
    number = int(value)
    if not low <= number <= high:
        raise ValueError(f"{field} 必须在 {low:02d}–{high:02d} 范围内")
    return number


def normalized_actual(actual: Any, field: str) -> dict[str, Any]:
    if not isinstance(actual, dict):
        raise ValueError(f"{field} 必须是对象")
    values = actual.get("red_sorted")
    if not isinstance(values, list) or len(values) != 6:
        raise ValueError(f"{field}.red_sorted 必须恰好有 6 枚红球")
    red = [parse_ball(value, 1, 33, f"{field}.red_sorted") for value in values]
    if len(set(red)) != 6 or red != sorted(red):
        raise ValueError(f"{field}.red_sorted 必须去重且升序")
    blue = parse_ball(actual.get("blue"), 1, 16, f"{field}.blue")
    return {"red_sorted": [f"{number:02d}" for number in red], "blue": f"{blue:02d}"}


def target_to_v1_input(target: dict[str, Any]) -> dict[str, str]:
    return {
        "draw_date": target["draw_date"],
        "day_pillar": target["day_pillar"],
        "nayin_element": target["nayin_element"],
    }


def validate_target(target: Any) -> tuple[dict[str, str], int, date]:
    if not isinstance(target, dict):
        raise ValueError("输入缺少 target 对象")
    issue = parse_issue(target.get("issue"), "target.issue")
    draw_date = parse_date(target.get("draw_date"), "target.draw_date")
    if draw_date.weekday() not in SCHEDULED_WEEKDAYS:
        raise ValueError("target.draw_date 必须为周二、周四或周日")
    pillar = target.get("day_pillar")
    nayin = target.get("nayin_element")
    if not isinstance(pillar, str) or not isinstance(nayin, str):
        raise ValueError("target.day_pillar 与 target.nayin_element 必须是字符串")
    return {
        "issue": f"{issue:07d}",
        "draw_date": draw_date.isoformat(),
        "day_pillar": pillar,
        "nayin_element": nayin,
    }, issue, draw_date


def baseline_from_input(ziwei_input: dict[str, str], config: dict[str, Any]) -> dict[str, Any]:
    generated = generate_v1(ziwei_input, config)
    return {
        "red": generated["baseline"]["red"],
        "blue": generated["baseline"]["blue"],
        "v1_canonical_input": generated["canonical_input"],
        "element_scores": generated["element_scores"],
        "config_sha256": generated["config_sha256"],
    }


def validate_and_normalize_window(
    records: Any,
    target_issue: int,
    target_date: date,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(records, list) or len(records) != 9:
        raise ValueError("verified_window 必须恰好包含最近9期记录")
    normalized: list[dict[str, Any]] = []
    previous_date: date | None = None
    for position, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise ValueError(f"verified_window[{position}] 必须是对象")
        if record.get("position") != position:
            raise ValueError(f"verified_window[{position}].position 必须为 {position}")
        issue = parse_issue(record.get("issue"), f"verified_window[{position}].issue")
        if issue != target_issue - position:
            raise ValueError("verified_window 必须从目标期前最近一期开始按期号连续递减")
        draw_date = parse_date(record.get("draw_date"), f"verified_window[{position}].draw_date")
        if draw_date.weekday() not in SCHEDULED_WEEKDAYS or draw_date >= target_date:
            raise ValueError("verified_window 开奖日必须早于目标期且为周二、周四或周日")
        if previous_date is not None and draw_date >= previous_date:
            raise ValueError("verified_window 必须按最近到最早的日期顺序排列")
        previous_date = draw_date
        if record.get("verified") is not True:
            raise ValueError("verified_window 每条记录都必须标记 verified=true")

        ziwei = record.get("ziwei_input")
        if not isinstance(ziwei, dict):
            raise ValueError(f"verified_window[{position}].ziwei_input 必须是对象")
        pillar = ziwei.get("day_pillar")
        nayin = ziwei.get("nayin_element")
        if not isinstance(pillar, str) or not isinstance(nayin, str):
            raise ValueError("窗口记录的日柱和纳音必须为字符串")
        historical_baseline = baseline_from_input(
            {"draw_date": draw_date.isoformat(), "day_pillar": pillar, "nayin_element": nayin}, config
        )
        stored_baseline = record.get("baseline_prediction")
        expected_prediction = {"red": historical_baseline["red"], "blue": historical_baseline["blue"]}
        if stored_baseline is not None and stored_baseline != expected_prediction:
            raise ValueError(f"verified_window[{position}] 保存的基准组与V1重算结果不一致")
        actual = normalized_actual(record.get("actual"), f"verified_window[{position}].actual")
        normalized.append(
            {
                "position": position,
                "issue": f"{issue:07d}",
                "draw_date": draw_date.isoformat(),
                "ziwei_input": {"day_pillar": pillar, "nayin_element": nayin},
                "baseline_prediction": expected_prediction,
                "actual": actual,
                "verified": True,
            }
        )
    return normalized


def residual_scores(window: list[dict[str, Any]], upper_bound: int, ball_type: str) -> dict[int, int]:
    scores = {number: 0 for number in range(1, upper_bound + 1)}
    for record, weight in zip(window, WINDOW_WEIGHTS, strict=True):
        if ball_type == "red":
            predicted = {int(number) for number in record["baseline_prediction"]["red"]}
            actual = {int(number) for number in record["actual"]["red_sorted"]}
        else:
            predicted = {int(record["baseline_prediction"]["blue"])}
            actual = {int(record["actual"]["blue"])}
        for number in actual - predicted:
            if number <= upper_bound:
                scores[number] += weight
        for number in predicted - actual:
            if number <= upper_bound:
                scores[number] -= weight
    return scores


def canonical_state_object(target_v1: dict[str, Any], target_issue: str, window: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "algorithm_id": ALGORITHM_ID,
        "target_issue": target_issue,
        "target_v1_canonical_input": target_v1["canonical_input"],
        "verified_window": window,
        "window_weights": WINDOW_WEIGHTS,
        "ziwei_multiplier": ZIWEI_MULTIPLIER,
    }


def tie_hash(canonical_state: str, number: int) -> str:
    return sha256_hex(f"{canonical_state}|residual9_v3|{number}")


def choose_group(
    upper_bound: int,
    element_scores: dict[str, int],
    residual: dict[int, int],
    canonical_state: str,
    config: dict[str, Any],
    count: int,
) -> tuple[list[int], dict[int, dict[str, int | str]]]:
    audit: dict[int, dict[str, int | str]] = {}
    for number in range(1, upper_bound + 1):
        element = number_element(number, config)
        base = element_scores[element]
        residual_value = residual[number]
        audit[number] = {
            "element": element,
            "ziwei_base": base,
            "residual_score": residual_value,
            "final_score": ZIWEI_MULTIPLIER * base + residual_value,
            "tie_hash": tie_hash(canonical_state, number),
        }
    ranked = sorted(
        range(1, upper_bound + 1),
        key=lambda number: (
            -int(audit[number]["final_score"]),
            -int(audit[number]["ziwei_base"]),
            str(audit[number]["tie_hash"]),
            number,
        ),
    )
    return sorted(ranked[:count]), audit


def compact_audit(numbers: list[int], audit: dict[int, dict[str, int | str]]) -> dict[str, dict[str, int | str]]:
    return {f"{number:02d}": audit[number] for number in numbers}


def generate(input_data: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    target, target_issue, target_date = validate_target(input_data.get("target"))
    target_v1 = generate_v1(target_to_v1_input(target), config)
    window = validate_and_normalize_window(input_data.get("verified_window"), target_issue, target_date, config)
    state_object = canonical_state_object(target_v1, target["issue"], window)
    canonical_state = canonical_json(state_object)

    red_residual = residual_scores(window, 33, "red")
    blue_residual = residual_scores(window, 16, "blue")
    red, red_audit = choose_group(33, target_v1["element_scores"], red_residual, canonical_state, config, 6)
    blue_values, blue_audit = choose_group(16, target_v1["element_scores"], blue_residual, canonical_state, config, 1)
    blue = blue_values[0]

    return {
        "algorithm_id": ALGORITHM_ID,
        "spec_version": SPEC_VERSION,
        "base_algorithm_id": target_v1["algorithm_id"],
        "base_spec_version": target_v1["spec_version"],
        "config_sha256": target_v1["config_sha256"],
        "target": {"issue": target["issue"], **target_v1["canonical_input"]},
        "candidate_coverage": {
            "red_domain": "01-33",
            "blue_domain": "01-16",
            "red_candidates_evaluated": 33,
            "blue_candidates_evaluated": 16,
            "permanent_exclusions": []
        },
        "window": {
            "length": 9,
            "order": "newest_to_oldest",
            "weights": WINDOW_WEIGHTS,
            "residual_state_sha256": sha256_hex(canonical_state),
            "issues": [record["issue"] for record in window],
        },
        "element_scores": target_v1["element_scores"],
        "baseline": target_v1["baseline"],
        "residual9_corrected": {
            "red": [f"{number:02d}" for number in red],
            "blue": f"{blue:02d}",
            "red_selection_audit": compact_audit(red, red_audit),
            "blue_selection_audit": compact_audit([blue], blue_audit),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="ZW-SSQ-RESIDUAL9-V3 reference implementation")
    parser.add_argument("--input", required=True, type=Path, help="V3 bootstrap or state input JSON")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="frozen V1 config JSON")
    parser.add_argument("--pretty", action="store_true", help="pretty print output JSON")
    args = parser.parse_args()
    try:
        result = generate(read_json(args.input), read_json(args.config))
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    if args.pretty:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
