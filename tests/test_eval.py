# -*- coding: utf-8 -*-
# F9 (#11): 평가 지표 계산(scripts/eval/evallib.py)과 수집·채점 스크립트 검사.
import csv
import json
import os
import sys

import pytest

from tests import helpers

sys.path.insert(0, os.path.join(helpers.REPO_DIR, "scripts", "eval"))
import evallib  # noqa: E402

GOLD = {"id": "T01", "isbn13": "9788936434120", "title": "소년이 온다", "authors": "한강",
        "publisher": "창비", "pubdate": "2014-05-19"}


def rec(title="소년이 온다", authors=("한강",), isbn="9788936434120", publisher="창비",
        date="2014-05-19", cover="https://image.example/cover.jpg", description="소개", source="kakao"):
    return {"title": title, "authors": list(authors), "publisher": publisher, "publishedDate": date,
            "identifiers": {"isbn": isbn} if isbn else {}, "cover": cover, "description": description,
            "source": {"id": source}}


# ---------- 정규화 ----------

@pytest.mark.parametrize("value, expected", [
    ("978-0-306-40615-7", "9780306406157"),
    ("0-306-40615-2", "9780306406157"),       # ISBN-10 -> 13
    ("9780306406158", ""),                    # 검사 숫자 틀림
    ("030640615X", ""),
    ("", ""), (None, ""), ("abc", ""),
])
def test_isbn13(value, expected):
    assert evallib.isbn13(value) == expected


def test_normalize_title_ignores_brackets_spaces_and_width():
    assert evallib.normalize_title("채식주의자 (리마스터판)") == evallib.normalize_title("채식주의자")
    assert evallib.normalize_title("[개정판] 총 균 쇠") == evallib.normalize_title("총균쇠")
    assert evallib.normalize_title("ＡＢＣ") == "abc"


def test_split_authors_accepts_lists_and_joined_text():
    assert evallib.split_authors("기시미 이치로 & 고가 후미타케") == {"기시미이치로", "고가후미타케"}
    assert evallib.split_authors(["한강"]) == {"한강"}
    assert evallib.split_authors("") == set()
    # Douban 등은 한자·원어를 괄호로 붙여 준다 (조건 A 시험 수집에서 실제로 나온 형태)
    assert evallib.split_authors(["박경리（朴景利）"]) == {"박경리"}
    assert evallib.split_authors("한강 (지은이)") == {"한강"}


@pytest.mark.parametrize("value, expected", [
    ("2014-05-19T00:00:00.000+09:00", "2014-05-19"), ("2014-05", "2014-05"), ("2014", "2014"),
    ("", ""), ("날짜", ""),
])
def test_normalize_date(value, expected):
    assert evallib.normalize_date(value) == expected


# ---------- 정답 판정 ----------

def test_match_by_isbn_and_isbn_mismatch_is_not_rescued_by_title():
    assert evallib.match_kind(rec(), GOLD) == "isbn"
    # 다른 판본: 제목·저자가 같아도 ISBN이 다르면 정답이 아니다
    assert evallib.match_kind(rec(isbn="9788936434595"), GOLD) == ""


def test_match_by_title_and_author_only_without_isbn():
    assert evallib.match_kind(rec(isbn=None, title="소년이 온다 (양장)"), GOLD) == "title_author"
    assert evallib.match_kind(rec(isbn=None, authors=("다른 사람",)), GOLD) == ""


def test_filled_fields_counts_seven_items():
    assert evallib.filled_fields(rec()) == list(evallib.FIELDS)
    sparse = rec(publisher="", date="", isbn=None, cover="/static/generic_cover.jpg", description=" ")
    assert evallib.filled_fields(sparse) == ["title", "authors"]


def test_field_correct_uses_gold_precision_and_skips_unknown():
    assert evallib.field_correct(rec(date="2014-05-01"), dict(GOLD, pubdate="2014-05"), "pubdate")
    assert not evallib.field_correct(rec(date="2015-01-01"), GOLD, "pubdate")
    assert evallib.field_correct(rec(publisher="창비 "), GOLD, "publisher")
    assert evallib.field_correct(rec(), dict(GOLD, publisher=""), "publisher") is None


# ---------- 질의 하나 / 요약 ----------

def entry(results, elapsed=1.0, status="ok", **extra):
    return dict({"id": "T01", "query": "소년이 온다", "status": status, "elapsed_s": elapsed,
                 "results": results}, **extra)


def test_judge_query_rank_and_accuracy():
    other = rec(title="소년", isbn="9780306406157", authors=("누군가",))
    row = evallib.judge_query(entry([other, rec(publisher="창작과비평")]), GOLD)
    assert row["rank"] == 2 and not row["top1"] and row["top5"]
    assert row["compared"] == 0                     # 1순위가 정답이 아니면 M4에 넣지 않음

    row = evallib.judge_query(entry([rec(publisher="창작과비평")]), GOLD)
    assert row["top1"] and row["filled"] == 7
    assert (row["compared"], row["correct"], row["wrong_fields"]) == (5, 4, "publisher")


def test_score_from_ranked_search_is_kept():
    """F3(#22)가 /metadata/search 결과에 붙이는 score를 수집·채점 결과에 남긴다 (#18 리뷰 2번)."""
    ranked = dict(rec(), score=0.93)
    assert collect.trim(ranked)["score"] == 0.93
    assert evallib.judge_query(entry([ranked, rec(isbn=None)]), GOLD)["top1_score"] == 0.93
    assert evallib.judge_query(entry([rec()]), GOLD)["top1_score"] is None
    assert evallib.judge_query(entry([]), GOLD)["top1_score"] is None


def test_judge_query_without_results():
    row = evallib.judge_query(entry([]), GOLD)
    assert row["rank"] is None and row["filled"] == 0 and not row["cover"]


def test_cover_check_result_overrides_url_guess():
    assert not evallib.judge_query(entry([rec()], cover_ok=False), GOLD)["cover"]


def test_summarize_and_targets():
    rows = [evallib.judge_query(entry([rec()], elapsed=t), GOLD) for t in (1, 2, 3, 4)]
    rows.append(evallib.judge_query(entry([], elapsed=None, status="error"), GOLD))
    s = evallib.summarize(rows)
    assert s["queries"] == 5 and s["errors"] == 1
    assert s["M1"] == pytest.approx(0.8) and s["M2"] == pytest.approx(0.8)
    assert s["M3"] == pytest.approx(28 / 5)
    assert s["M4"] == pytest.approx(1.0)
    assert s["M6"] == 4                              # p95 of 1,2,3,4 (최근접 순위)
    assert evallib.passes("M1", s["M1"]) is False
    assert evallib.passes("M6", s["M6"]) is True
    assert evallib.passes("M4", None) is None


def test_percentile_nearest_rank():
    assert evallib.percentile(list(range(1, 101)), 95) == 95
    assert evallib.percentile([], 95) is None


# ---------- collect.py / score.py ----------

import collect  # noqa: E402
import score  # noqa: E402


class FakeClient:
    def __init__(self, answers):
        self.answers = answers

    def search(self, query):
        answer = self.answers[query]
        if isinstance(answer, Exception):
            raise answer
        return helpers.FakeResponse(answer, status=200)

    def cover_ok(self, url):
        return url.endswith(".jpg")


def test_read_samples_uses_title_when_query_is_empty(tmp_path):
    path = tmp_path / "s.csv"
    path.write_text("id,category,title,authors,query\nT01,문학,소년이 온다,한강,\nT02,문학,채식주의자,한강,채식주의자 리마스터\n,,,,\n",
                    encoding="utf-8-sig")
    samples = collect.read_samples(str(path))
    assert [(s["id"], s["query"]) for s in samples] == [("T01", "소년이 온다"), ("T02", "채식주의자 리마스터")]


def test_collect_run_records_results_errors_and_covers():
    import requests
    big = rec(description="가" * 1000)
    client = FakeClient({"소년이 온다": [big], "없는 책": [], "느린 책": requests.Timeout("timeout")})
    samples = [{"id": "T01", "query": "소년이 온다"}, {"id": "T02", "query": "없는 책"},
               {"id": "T03", "query": "느린 책"}]
    entries = list(collect.run(client, samples, check_covers=True, log=lambda *_: None))
    assert entries[0]["status"] == "ok" and entries[0]["sources"] == ["kakao"]
    assert len(entries[0]["results"][0]["description"]) == collect.DESCRIPTION_KEEP
    assert entries[0]["results"][0]["description_length"] == 1000
    assert entries[0]["results"][0]["score"] is None   # F3 이전 서버: 점수 없음
    assert entries[0]["cover_ok"] is True
    assert entries[1]["results"] == [] and "cover_ok" not in entries[1]
    assert entries[2]["status"] == "error" and "timeout" in entries[2]["error"]


def test_score_end_to_end(tmp_path, monkeypatch, capsys):
    gold = tmp_path / "gold.csv"
    with open(gold, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(GOLD))
        writer.writeheader()
        writer.writerow(GOLD)
        writer.writerow(dict(GOLD, id="T02", isbn13=""))       # 정답표 미완성 -> 채점 제외
    raw = tmp_path / "A_dev_20261123-000000.jsonl"
    with open(raw, "w", encoding="utf-8") as f:
        f.write(json.dumps(entry([rec()]), ensure_ascii=False) + "\n")
        f.write(json.dumps(dict(entry([]), id="T02"), ensure_ascii=False) + "\n")
    (tmp_path / "A_dev_20261123-000000.meta.json").write_text(
        json.dumps({"condition": "A", "set": "dev", "providers": ["google"]}), encoding="utf-8")
    summary_csv = tmp_path / "out" / "summary.csv"
    summary_csv.parent.mkdir()

    assert score.main([str(raw), "--gold", str(gold), "--summary", str(summary_csv)]) == 0
    out = capsys.readouterr().out
    assert "채점에서 뺀 책 1건: T02" in out
    assert "| A | dev | 1 | 100.0% ✅" in out
    rows = list(csv.DictReader(open(summary_csv, encoding="utf-8-sig")))
    assert len(rows) == 1 and rows[0]["unscored"] == "1" and rows[0]["M1"] == "1.0"

    # 같은 원본을 다시 채점하면 요약 줄이 늘지 않고 바뀐다
    score.main([str(raw), "--gold", str(gold), "--summary", str(summary_csv)])
    assert len(list(csv.DictReader(open(summary_csv, encoding="utf-8-sig")))) == 1
    assert (tmp_path / "out" / "A_dev_20261123-000000.per_query.csv").exists()   # 요약 파일 옆


def test_gold_table_matches_samples_and_has_valid_isbns():
    """eval/gold.csv: 표본 70권과 id가 같고, 채워진 ISBN은 모두 검사 숫자가 맞아야 한다."""
    def read(name):
        with open(os.path.join(helpers.REPO_DIR, "eval", name), encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    samples = read("samples_dev.csv") + read("samples_test.csv")
    gold = read("gold.csv")
    assert [g["id"] for g in gold] == [s["id"] for s in samples]
    bad = [g["id"] for g in gold if g["isbn13"] and evallib.isbn13(g["isbn13"]) != g["isbn13"]]
    assert bad == []


# ---------- 같은 책의 다른 판 (isbn13_alt, PR #23 리뷰) ----------

ALT_GOLD = dict(GOLD, isbn13_alt="9788936473235 9780306406157")   # 다른 판 2개


def test_alt_edition_counts_as_correct_book():
    assert evallib.alt_isbns(ALT_GOLD) == {"9788936473235", "9780306406157"}
    assert evallib.alt_isbns(dict(GOLD, isbn13_alt="")) == set()
    assert evallib.match_kind(rec(isbn="9788936473235"), ALT_GOLD) == "isbn_alt"
    assert evallib.match_kind(rec(), ALT_GOLD) == "isbn"
    assert evallib.match_kind(rec(isbn="9788936434595"), ALT_GOLD) == ""   # 목록에 없는 판은 여전히 오답


def test_alt_edition_skips_publisher_and_date_in_m4():
    other = rec(isbn="9780306406157", publisher="다른출판사", date="2020-01-01")
    row = evallib.judge_query(entry([other]), ALT_GOLD)
    assert row["top1"] and row["match"] == "isbn_alt"
    # 출판사·출간일은 대표 판과 다를 수 있어 비교하지 않고, 제목·저자·ISBN만 비교
    assert (row["compared"], row["correct"], row["wrong_fields"]) == (3, 3, "")


def test_summary_counts_alt_matches():
    rows = [evallib.judge_query(entry([rec(isbn="9788936473235")]), ALT_GOLD),
            evallib.judge_query(entry([rec()]), ALT_GOLD)]
    summary = evallib.summarize(rows)
    assert summary["M1"] == pytest.approx(1.0) and summary["alt_matches"] == 1


def test_gold_alt_isbns_are_valid_and_distinct():
    with open(os.path.join(helpers.REPO_DIR, "eval", "gold.csv"), encoding="utf-8-sig", newline="") as f:
        gold = list(csv.DictReader(f))
    for row in gold:
        alts = (row.get("isbn13_alt") or "").split()
        assert all(evallib.isbn13(a) == a for a in alts), row["id"]
        assert row["isbn13"] not in alts, row["id"]
