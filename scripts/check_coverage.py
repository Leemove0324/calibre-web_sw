# -*- coding: utf-8 -*-
"""F9 (#11): 지표 M10 검사 — 신규 모듈의 줄 커버리지가 기준 이상인지 본다.

사용 (저장소 맨 위 폴더에서):
    python -m pytest --cov --cov-report=json:coverage.json
    python scripts/check_coverage.py coverage.json

- 대상 파일은 tests/coverage_targets.txt 에 적는다.
- 저장소에 아직 없는 대상은 건너뛴다 (병합 전 기능).
- 대상 전체를 합한 줄 커버리지가 기준(기본 80%) 미만이면 종료 코드 1.
  파일별 값도 표로 출력해서 어느 파일이 부족한지 보이게 한다.
"""
import argparse
import json
import os
import sys

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TARGETS = os.path.join(REPO_DIR, "tests", "coverage_targets.txt")


def read_targets(path):
    targets = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                targets.append(line.replace("\\", "/"))
    return targets


def summarize(report, targets, repo_dir=REPO_DIR):
    """(rows, total_percent) — rows: [(파일, 실행 줄, 전체 줄, 퍼센트 또는 None, 상태)]"""
    files = {name.replace("\\", "/"): data for name, data in report.get("files", {}).items()}
    rows = []
    covered = statements = 0
    for target in targets:
        if not os.path.exists(os.path.join(repo_dir, target)):
            rows.append((target, 0, 0, None, "아직 없음 (건너뜀)"))
            continue
        data = files.get(target)
        if data is None:
            # 파일은 있는데 테스트가 한 번도 import하지 않음 -> 0%로 센다
            rows.append((target, 0, 0, 0.0, "테스트 없음"))
            statements += _count_statements_fallback(os.path.join(repo_dir, target))
            continue
        summary = data["summary"]
        covered += summary["covered_lines"]
        statements += summary["num_statements"]
        rows.append((target, summary["covered_lines"], summary["num_statements"],
                     summary["percent_covered"], ""))
    total = 100.0 * covered / statements if statements else None
    return rows, total


def _count_statements_fallback(path):
    """coverage가 모르는 파일의 대략적인 줄 수 (빈 줄·주석 제외)."""
    count = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                count += 1
    return count


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("report", help="pytest --cov-report=json 으로 만든 파일")
    parser.add_argument("--targets", default=DEFAULT_TARGETS)
    parser.add_argument("--fail-under", type=float, default=80.0)
    args = parser.parse_args(argv)

    with open(args.report, encoding="utf-8") as f:
        report = json.load(f)
    rows, total = summarize(report, read_targets(args.targets))

    print("M10 신규 모듈 줄 커버리지 (기준 {:.0f}%)".format(args.fail_under))
    print("-" * 72)
    for target, cov, stmts, percent, note in rows:
        value = "   -  " if percent is None else "{:5.1f}%".format(percent)
        print("{:<44} {:>4}/{:<4} {}  {}".format(target, cov, stmts, value, note))
    print("-" * 72)
    if total is None:
        print("검사할 신규 모듈이 아직 없음 -> 통과")
        return 0
    print("합계 {:.1f}%".format(total))
    if total < args.fail_under:
        print("실패: 합계가 {:.0f}% 미만".format(args.fail_under))
        return 1
    print("통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
