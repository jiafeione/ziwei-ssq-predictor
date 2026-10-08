#!/usr/bin/env python3
"""Generate and query the frozen ZW-DAILY-CALENDAR-2026-2099-V1 calendar."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

CALENDAR_ID = "ZW-DAILY-CALENDAR-2026-2099-V1"
TIMEZONE = "Asia/Shanghai"
START_DATE = date(2026, 8, 1)
END_DATE = date(2099, 12, 31)
ANCHOR_DATE = date(2026, 8, 20)
ANCHOR_INDEX = 2  # 2026-08-20 = 丙寅, where 甲子=0

STEMS = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]
BRANCHES = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
WEEKDAYS = [("Monday", "星期一"), ("Tuesday", "星期二"), ("Wednesday", "星期三"),
            ("Thursday", "星期四"), ("Friday", "星期五"), ("Saturday", "星期六"),
            ("Sunday", "星期日")]

# Each entry corresponds to a pair of consecutive sexagenary indices 0/1, 2/3, ..., 58/59.
NAYIN_PAIRS = [
    ("海中金", "金"), ("炉中火", "火"), ("大林木", "木"), ("路旁土", "土"), ("剑锋金", "金"),
    ("山头火", "火"), ("涧下水", "水"), ("城头土", "土"), ("白蜡金", "金"), ("杨柳木", "木"),
    ("泉中水", "水"), ("屋上土", "土"), ("霹雳火", "火"), ("松柏木", "木"), ("长流水", "水"),
    ("沙中金", "金"), ("山下火", "火"), ("平地木", "木"), ("壁上土", "土"), ("金箔金", "金"),
    ("佛灯火", "火"), ("天河水", "水"), ("大驿土", "土"), ("钗钏金", "金"), ("桑柘木", "木"),
    ("大溪水", "水"), ("沙中土", "土"), ("天上火", "火"), ("石榴木", "木"), ("大海水", "水"),
]


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("日期必须使用 YYYY-MM-DD 格式") from exc
    if parsed.isoformat() != value:
        raise ValueError("日期必须使用零填充 YYYY-MM-DD 格式")
    return parsed


def ganzhi(index: int) -> str:
    return STEMS[index % 10] + BRANCHES[index % 12]


def record_for(target: date) -> dict[str, Any]:
    if target < START_DATE or target > END_DATE:
        raise ValueError(f"日期超出冻结范围：{START_DATE.isoformat()} 至 {END_DATE.isoformat()}")
    index = (ANCHOR_INDEX + (target - ANCHOR_DATE).days) % 60
    nayin_name, nayin_element = NAYIN_PAIRS[index // 2]
    weekday, weekday_zh = WEEKDAYS[target.weekday()]
    return {
        "calendar_id": CALENDAR_ID,
        "date": target.isoformat(),
        "weekday": weekday,
        "weekday_zh": weekday_zh,
        "ganzhi_index": index,
        "day_pillar": ganzhi(index),
        "nayin_name": nayin_name,
        "nayin_element": nayin_element,
        "timezone": TIMEZONE,
    }


def generate(output: Path) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    current = START_DATE
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        while current <= END_DATE:
            handle.write(canonical_json(record_for(current)) + "\n")
            current += timedelta(days=1)
            count += 1
    return count


def lookup(calendar_path: Path, requested_date: str) -> dict[str, Any]:
    target = parse_date(requested_date)
    if target < START_DATE or target > END_DATE:
        raise ValueError(f"日期超出冻结范围：{START_DATE.isoformat()} 至 {END_DATE.isoformat()}")
    if not calendar_path.is_file():
        raise ValueError(f"日历文件不存在：{calendar_path}")
    with calendar_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            record = json.loads(line)
            if record.get("date") == target.isoformat():
                expected = record_for(target)
                if record != expected:
                    raise ValueError(f"日历第 {line_number} 行与冻结推导规则不一致")
                return record
    raise ValueError("日历文件中找不到目标日期")


def main() -> int:
    parser = argparse.ArgumentParser(description="离线每日日柱纳音日历生成与查表工具")
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate_parser = subparsers.add_parser("generate", help="生成完整冻结日历 JSONL")
    generate_parser.add_argument("--output", required=True, type=Path)
    lookup_parser = subparsers.add_parser("lookup", help="按日期查找日柱和纳音")
    lookup_parser.add_argument("--calendar", required=True, type=Path)
    lookup_parser.add_argument("--date", required=True, dest="requested_date")
    checksum_parser = subparsers.add_parser("checksum", help="输出日历文件 SHA-256")
    checksum_parser.add_argument("--calendar", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "generate":
            count = generate(args.output)
            print(canonical_json({"calendar": str(args.output), "records": count, "sha256": sha256_file(args.output)}))
        elif args.command == "lookup":
            print(canonical_json(lookup(args.calendar, args.requested_date)))
        else:
            if not args.calendar.is_file():
                raise ValueError(f"日历文件不存在：{args.calendar}")
            print(sha256_file(args.calendar))
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
