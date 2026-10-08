#!/usr/bin/env python3
"""ZW-SSQ-DETERMINISTIC-V1 reference implementation.

This program is intentionally dependency-free. It implements the frozen rules in
SPECIFICATION.md and emits deterministic JSON for any fixed input/configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "zw_ssq_v1_config.json"


def canonical_json(value: Any) -> str:
    """Return the specification's canonical JSON representation."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 JSON 文件 {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"JSON 根对象必须是对象: {path}")
    return payload


def validate_config(config: dict[str, Any]) -> None:
    """Validate the minimum frozen schema before generating any output."""
    required = (
        "spec_version", "algorithm_id", "element_order", "number_element_rule",
        "stem_element", "branch_element", "star_element", "four_transformations",
        "weights", "group_rules", "hash_tiebreak",
    )
    missing = [key for key in required if key not in config]
    if missing:
        raise ValueError("配置缺少字段: " + ", ".join(missing))
    expected_elements = ["水", "火", "木", "金", "土"]
    if config["element_order"] != expected_elements:
        raise ValueError("element_order 必须精确为：水、火、木、金、土")
    mapping = config["number_element_rule"].get("mapping", {})
    if mapping != {"1": "水", "2": "火", "3": "木", "4": "金", "0": "土"}:
        raise ValueError("number_element_rule.mapping 不符合冻结版映射")
    for stem, transformations in config["four_transformations"].items():
        if stem not in config["stem_element"]:
            raise ValueError(f"四化表存在未知天干: {stem}")
        if set(transformations) != {"禄", "权", "科", "忌"}:
            raise ValueError(f"天干 {stem} 的四化字段必须恰好为禄、权、科、忌")
        for star in transformations.values():
            if star not in config["star_element"]:
                raise ValueError(f"四化表引用了未知星曜: {star}")
    if config["group_rules"]["corrected"].get("cluster_quota") != [3, 2, 1]:
        raise ValueError("修正组 cluster_quota 必须精确为 [3, 2, 1]")


def validate_input(data: dict[str, Any], config: dict[str, Any]) -> tuple[str, str, str, str]:
    required = ("draw_date", "day_pillar", "nayin_element")
    missing = [key for key in required if key not in data]
    if missing:
        raise ValueError("输入缺少字段: " + ", ".join(missing))

    draw_date = data["draw_date"]
    pillar = data["day_pillar"]
    nayin_element = data["nayin_element"]
    if not all(isinstance(item, str) for item in (draw_date, pillar, nayin_element)):
        raise ValueError("draw_date、day_pillar、nayin_element 必须均为字符串")
    try:
        parsed_date = datetime.strptime(draw_date, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("draw_date 必须是有效的 YYYY-MM-DD 公历日期") from exc
    if parsed_date.strftime("%Y-%m-%d") != draw_date:
        raise ValueError("draw_date 必须使用零填充的 YYYY-MM-DD 格式")
    if len(pillar) != 2:
        raise ValueError("day_pillar 必须恰好包含一个天干和一个地支，例如“丙寅”")
    stem, branch = pillar[0], pillar[1]
    if stem not in config["stem_element"]:
        raise ValueError(f"未知天干: {stem}")
    if branch not in config["branch_element"]:
        raise ValueError(f"未知地支: {branch}")
    if nayin_element not in config["element_order"]:
        raise ValueError(f"nayin_element 必须是 {config['element_order']} 之一")
    return draw_date, stem, branch, nayin_element


def number_element(number: int, config: dict[str, Any]) -> str:
    remainder = str(number % 5)
    return config["number_element_rule"]["mapping"][remainder]


def make_canonical_input(
    draw_date: str,
    stem: str,
    branch: str,
    nayin_element: str,
    config: dict[str, Any],
    config_digest: str,
) -> tuple[dict[str, str], str]:
    data = {
        "algorithm_id": config["algorithm_id"],
        "config_digest": config_digest,
        "day_pillar": stem + branch,
        "draw_date": draw_date,
        "nayin_element": nayin_element,
        "spec_version": config["spec_version"],
    }
    return data, canonical_json(data)


def score_elements(
    stem: str, branch: str, nayin_element: str, config: dict[str, Any]
) -> tuple[dict[str, int], dict[str, int], dict[str, str]]:
    elements = config["element_order"]
    score = {element: 0 for element in elements}
    ji_score = {element: 0 for element in elements}
    weights = config["weights"]

    score[config["stem_element"][stem]] += weights["day_stem"]
    score[config["branch_element"][branch]] += weights["day_branch"]
    score[nayin_element] += weights["nayin"]

    stars = config["four_transformations"][stem]
    for transformation in ("禄", "权", "科", "忌"):
        star = stars[transformation]
        element = config["star_element"][star]
        score[element] += weights["transformation"][transformation]
        if transformation == "忌":
            ji_score[element] += weights["transformation"][transformation]
    return score, ji_score, stars


def hash_rank(canonical_input: str, group: str, number: int) -> str:
    return sha256_hex(f"{canonical_input}|{group}|{number}")


def ordered_elements(score: dict[str, int], config: dict[str, Any]) -> list[str]:
    order_index = {element: index for index, element in enumerate(config["element_order"])}
    return sorted(config["element_order"], key=lambda element: (-score[element], order_index[element]))


def candidates_for_element(
    element: str, upper_bound: int, group: str, canonical_input: str, config: dict[str, Any]
) -> list[int]:
    candidates = [n for n in range(1, upper_bound + 1) if number_element(n, config) == element]
    return sorted(candidates, key=lambda n: (hash_rank(canonical_input, group, n), n))


def global_candidates(
    upper_bound: int, group: str, canonical_input: str, score: dict[str, int], config: dict[str, Any]
) -> list[int]:
    order_index = {element: index for index, element in enumerate(config["element_order"])}
    return sorted(
        range(1, upper_bound + 1),
        key=lambda n: (
            -score[number_element(n, config)],
            order_index[number_element(n, config)],
            hash_rank(canonical_input, group, n),
            n,
        ),
    )


def choose_baseline_red(score: dict[str, int], canonical_input: str, config: dict[str, Any]) -> list[int]:
    group = "baseline"
    selected: list[int] = []
    for element in ordered_elements(score, config):
        if score[element] <= 0:
            continue
        candidate = candidates_for_element(element, 33, group, canonical_input, config)[0]
        selected.append(candidate)
        if len(selected) == 6:
            break
    for candidate in global_candidates(33, group, canonical_input, score, config):
        if len(selected) == 6:
            break
        if candidate not in selected:
            selected.append(candidate)
    return sorted(selected)


def choose_corrected_red(score: dict[str, int], canonical_input: str, config: dict[str, Any]) -> list[int]:
    group = "corrected"
    positive = [element for element in ordered_elements(score, config) if score[element] > 0]
    if not positive:
        raise ValueError("没有正分五行，无法生成修正组")

    quotas = config["group_rules"]["corrected"]["cluster_quota"]
    selected: list[int] = []
    for index, quota in enumerate(quotas):
        element = positive[index % len(positive)]
        pool = candidates_for_element(element, 33, group, canonical_input, config)
        taken = 0
        for candidate in pool:
            if candidate not in selected:
                selected.append(candidate)
                taken += 1
                if taken >= quota:
                    break
    for candidate in global_candidates(33, group, canonical_input, score, config):
        if len(selected) == 6:
            break
        if candidate not in selected:
            selected.append(candidate)
    return sorted(selected[:6])


def choose_blue(
    group: str,
    score: dict[str, int],
    ji_score: dict[str, int],
    canonical_input: str,
    config: dict[str, Any],
) -> int:
    candidates = list(range(1, 17))
    order_index = {element: index for index, element in enumerate(config["element_order"])}
    has_ji = any(value > 0 for value in ji_score.values())
    if group == "corrected" and has_ji:
        key = lambda n: (
            -ji_score[number_element(n, config)],
            -score[number_element(n, config)],
            order_index[number_element(n, config)],
            hash_rank(canonical_input, group, n),
            n,
        )
    else:
        key = lambda n: (
            -score[number_element(n, config)],
            order_index[number_element(n, config)],
            hash_rank(canonical_input, group, n),
            n,
        )
    return min(candidates, key=key)


def two_digit(number: int) -> str:
    return f"{number:02d}"


def generate(input_data: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    validate_config(config)
    draw_date, stem, branch, nayin_element = validate_input(input_data, config)
    config_digest = sha256_hex(canonical_json(config))
    canonical_input_object, canonical_input = make_canonical_input(
        draw_date, stem, branch, nayin_element, config, config_digest
    )
    score, ji_score, stars = score_elements(stem, branch, nayin_element, config)
    baseline_red = choose_baseline_red(score, canonical_input, config)
    corrected_red = choose_corrected_red(score, canonical_input, config)
    baseline_blue = choose_blue("baseline", score, ji_score, canonical_input, config)
    corrected_blue = choose_blue("corrected", score, ji_score, canonical_input, config)

    return {
        "algorithm_id": config["algorithm_id"],
        "spec_version": config["spec_version"],
        "config_sha256": config_digest,
        "canonical_input": canonical_input_object,
        "element_scores": score,
        "ji_element_scores": ji_score,
        "four_transformations": stars,
        "baseline": {"red": [two_digit(n) for n in baseline_red], "blue": two_digit(baseline_blue)},
        "corrected": {"red": [two_digit(n) for n in corrected_red], "blue": two_digit(corrected_blue)},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="ZW-SSQ-DETERMINISTIC-V1 reference implementation")
    parser.add_argument("--input", required=True, type=Path, help="input JSON path")
    parser.add_argument("--config", default=DEFAULT_CONFIG, type=Path, help="frozen config JSON path")
    parser.add_argument("--pretty", action="store_true", help="pretty-print result JSON")
    args = parser.parse_args()

    try:
        input_data = read_json(args.input)
        config = read_json(args.config)
        output = generate(input_data, config)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    if args.pretty:
        print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print(canonical_json(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
