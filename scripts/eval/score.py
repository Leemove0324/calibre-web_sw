# -*- coding: utf-8 -*-
"""F9 (#11): collect.py 결과를 정답표와 맞춰 M1~M6을 계산한다.

    python scripts/eval/score.py eval/results/raw/A_dev_20261123-153000.jsonl
    python scripts/eval/score.py eval/results/raw/*.jsonl          # 여러 조건을 한 표로

- 질의별 판정: 요약 파일과 같은 폴더(기본 eval/results/)의 <원본 이름>.per_query.csv
- 요약: eval/results/summary.csv 에 한 줄씩 추가 (같은 원본을 다시 채점하면 그 줄을 바꿈)
- 정답표(eval/gold.csv)에 없는 책이나 isbn13이 빈 책은 채점에서 빼고 개수를 알려 준다.
"""
import argparse
import csv
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import evallib  # noqa: E402

REPO_DIR = os.path.dirname(os.path.dirname(HERE))
EVAL_DIR = os.path.join(REPO_DIR, "eval")
SUMMARY_FIELDS = ["raw_file", "condition", "set", "providers", "started_at", "queries", "errors",
                  "unscored", "M1", "M2", "M3", "M4", "M5", "M6", "weak_matches", "alt_matches",
                  "cover_checked"]
ROW_FIELDS = ["id", "query", "status", "elapsed_s", "result_count", "rank", "match", "top1", "top5",
              "top1_source", "top1_score", "filled", "filled_fields", "compared", "correct", "wrong_fields", "cover"]


def read_gold(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return {r["id"].strip(): r for r in csv.DictReader(f) if (r.get("id") or "").strip()}


def read_raw(path):
    with open(path, encoding="utf-8") as f:
        entries = [json.loads(line) for line in f if line.strip()]
    meta_path = path[:-len(".jsonl")] + ".meta.json" if path.endswith(".jsonl") else path + ".meta.json"
    meta = {}
    if os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
    return entries, meta


def score_file(path, gold):
    entries, meta = read_raw(path)
    rows, unscored = [], []
    for entry in entries:
        answer = gold.get(entry["id"])
        if not answer or not evallib.isbn13(answer.get("isbn13")):
            unscored.append(entry["id"])
            continue
        rows.append(evallib.judge_query(entry, answer))
    summary = evallib.summarize(rows)
    summary.update({
        "raw_file": os.path.relpath(path, REPO_DIR).replace("\\", "/"),
        "condition": meta.get("condition", ""), "set": meta.get("set", ""),
        "providers": ",".join(meta.get("providers", [])), "started_at": meta.get("started_at", ""),
        "unscored": len(unscored), "cover_checked": bool(meta.get("check_covers")),
    })
    return rows, summary, unscored


def fmt(metric, value):
    if value is None:
        return "-"
    if metric in ("M3",):
        return "{:.2f}".format(value)
    if metric == "M6":
        return "{:.2f}s".format(value)
    return "{:.1f}%".format(100 * value)


def write_rows(path, rows, out_dir):
    out = os.path.join(out_dir, os.path.basename(path).replace(".jsonl", ".per_query.csv"))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ROW_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return out


def update_summary(summaries, path):
    existing = []
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig", newline="") as f:
            existing = list(csv.DictReader(f))
    new_files = {s["raw_file"] for s in summaries}
    kept = [r for r in existing if r["raw_file"] not in new_files]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(kept)
        for s in summaries:
            writer.writerow({k: (round(v, 4) if isinstance(v, float) else v) for k, v in s.items()})


def markdown_table(summaries):
    lines = ["| 조건 | 표본 | 질의 | M1 1순위 | M2 상위5 | M3 완성도 | M4 정확도 | M5 표지 | M6 p95 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for s in summaries:
        cells = []
        for m in ("M1", "M2", "M3", "M4", "M5", "M6"):
            mark = {True: " ✅", False: " ❌", None: ""}[evallib.passes(m, s[m])]
            cells.append(fmt(m, s[m]) + mark)
        lines.append("| {} | {} | {} | {} |".format(s["condition"], s["set"], s["queries"], " | ".join(cells)))
    targets = " / ".join("{} {}{}".format(m, "≤" if m == "M6" else "≥", fmt(m, v))
                         for m, v in evallib.TARGETS.items())
    lines.append("")
    lines.append("목표: " + targets)
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="평가 채점 (F9)")
    parser.add_argument("raw", nargs="+", help="collect.py가 만든 .jsonl (와일드카드 가능)")
    parser.add_argument("--gold", default=os.path.join(EVAL_DIR, "gold.csv"))
    parser.add_argument("--summary", default=os.path.join(EVAL_DIR, "results", "summary.csv"))
    args = parser.parse_args(argv)

    paths = sorted({p for pattern in args.raw for p in (glob.glob(pattern) or [pattern])})
    gold = read_gold(args.gold)
    summaries = []
    for path in paths:
        rows, summary, unscored = score_file(path, gold)
        out = write_rows(path, rows, os.path.dirname(os.path.abspath(args.summary)))
        summaries.append(summary)
        print("{}: 채점 {}건 -> {}".format(os.path.basename(path), len(rows), os.path.relpath(out, REPO_DIR)))
        if unscored:
            print("  정답표에 없거나 ISBN이 비어 채점에서 뺀 책 {}건: {}".format(len(unscored), ", ".join(unscored)))
        if summary["weak_matches"]:
            print("  ISBN 없이 제목·저자로만 정답 판정한 질의 {}건 (per_query.csv의 match 열)".format(
                summary["weak_matches"]))
        if summary["alt_matches"]:
            print("  같은 책의 다른 판(isbn13_alt)으로 정답 판정한 질의 {}건".format(summary["alt_matches"]))
    update_summary(summaries, args.summary)
    print()
    print(markdown_table(summaries))
    return 0


if __name__ == "__main__":
    sys.exit(main())
