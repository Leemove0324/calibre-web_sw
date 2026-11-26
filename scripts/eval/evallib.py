# -*- coding: utf-8 -*-
"""F9 (#11): 평가 지표 M1~M6 계산 (서버·네트워크와 무관한 순수 함수).

collect.py가 모은 검색 결과와 정답표(eval/gold.csv)를 받아 질의별 판정과 요약을 만든다.
지표 정의는 중간보고서 표 24를 따른다.

  M1 1순위 적중률   정답 도서가 검색 결과 1순위인 비율
  M2 상위 5위 재현율 정답 도서가 상위 5개 안에 있는 비율
  M3 필드 완성도    1순위 결과의 7개 항목(제목·저자·출판사·출간일·ISBN·표지·소개) 중 채워진 수의 평균
  M4 필드 정확도    1순위가 정답 도서일 때, 채워진 항목 중 정답표와 일치하는 비율
                   (비교 대상: 제목·저자·출판사·출간일·ISBN. 표지·소개는 정답표로 비교할 수 없어 제외)
  M5 표지 확보율    1순위 결과에 쓸 수 있는 표지(http/https, 기본 표지 아님)가 있는 비율
                   (collect.py --check-covers면 실제로 이미지를 받아지는지까지 본다)
  M6 응답 시간      질의별 응답 시간의 95번째 백분위수(p95, 초)

"정답 도서"는 ISBN-13이 정답표(isbn13) 또는 같은 책의 다른 판(isbn13_alt)과 같은 결과다.
결과에 ISBN이 없을 때만 정규화한 제목이 같고 저자가 한 명 이상 겹치면 정답으로 본다
(판정 근거는 match 열에 isbn / isbn_alt / title_author 로 남긴다).
"""
import math
import re
import unicodedata

FIELDS = ("title", "authors", "publisher", "pubdate", "isbn", "cover", "description")
COMPARED_FIELDS = ("title", "authors", "publisher", "pubdate", "isbn")
GENERIC_COVER_HINTS = ("generic_cover", "academicpaper.svg")
TOP_K = 5


# ---------------------------------------------------------------------------
# 정규화
# ---------------------------------------------------------------------------

def normalize_text(text):
    """비교용: 유니코드 정규화(NFKC), 소문자, 글자·숫자만 남김 (한글 포함)."""
    text = unicodedata.normalize("NFKC", text or "").lower()
    return re.sub(r"[\W_]+", "", text)


def normalize_title(text):
    """제목 비교용: 괄호 안 부가 정보([개정판], (리커버) 등)를 빼고 정규화."""
    text = re.sub(r"[\(\[\{（【〔].*?[\)\]\}）】〕]", " ", text or "")
    return normalize_text(text)


def isbn13(value):
    """ISBN-10/13 문자열 -> ISBN-13 숫자열. 형식이 틀리거나 검사 숫자가 맞지 않으면 ''."""
    digits = re.sub(r"[^0-9Xx]", "", str(value or "")).upper()
    if len(digits) == 13 and digits.isdigit():
        total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits[:12]))
        return digits if (10 - total % 10) % 10 == int(digits[12]) else ""
    if len(digits) == 10 and digits[:9].isdigit():
        total = sum(int(d) * (10 - i) for i, d in enumerate(digits[:9]))
        check = (11 - total % 11) % 11
        if ("X" if check == 10 else str(check)) != digits[9]:
            return ""
        core = "978" + digits[:9]
        total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(core))
        return core + str((10 - total % 10) % 10)
    return ""


def split_authors(value):
    """'한강', ['한강'], '기시미 이치로 & 고가 후미타케', '김 ; 이' -> 정규화한 저자 집합.

    괄호 안 표기(한자·원어 이름, '(지은이)' 같은 역할)는 뺀다: '박경리（朴景利）' -> 박경리
    """
    if isinstance(value, (list, tuple)):
        parts = value
    else:
        parts = re.split(r"\s*(?:&|;|,|\|)\s*", value or "")
    return {normalize_title(p) for p in parts if normalize_title(p)}


def normalize_date(value):
    """'2014-05-19', '2014-05-19T00:00:00+09:00', '2014-05', '2014' -> 같은 형식의 앞부분."""
    match = re.match(r"\s*(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", str(value or ""))
    if not match:
        return ""
    return "-".join(p for p in match.groups() if p)


# ---------------------------------------------------------------------------
# 결과 한 건 읽기 (cps의 MetaRecord를 JSON으로 만든 형태)
# ---------------------------------------------------------------------------

def record_isbn(record):
    identifiers = record.get("identifiers") or {}
    for key, value in identifiers.items():
        if str(key).lower() == "isbn":
            return isbn13(value)
    return ""


def usable_cover(url):
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return False
    return not any(hint in url for hint in GENERIC_COVER_HINTS)


def filled_fields(record):
    """7개 항목 중 값이 있는 항목 이름 목록 (M3)."""
    values = {
        "title": record.get("title"),
        "authors": [a for a in (record.get("authors") or []) if str(a).strip()],
        "publisher": record.get("publisher"),
        "pubdate": normalize_date(record.get("publishedDate")),
        "isbn": record_isbn(record),
        "cover": usable_cover(record.get("cover")),
        "description": (record.get("description") or "").strip(),
    }
    return [f for f in FIELDS if values[f] and (not isinstance(values[f], str) or values[f].strip())]


# ---------------------------------------------------------------------------
# 정답 판정
# ---------------------------------------------------------------------------

def alt_isbns(gold):
    """정답표 isbn13_alt 열(같은 책의 다른 판 ISBN, 공백·쉼표 구분) -> ISBN-13 집합."""
    parts = re.split(r"[\s,;]+", gold.get("isbn13_alt") or "")
    return {isbn13(p) for p in parts if isbn13(p)} - {isbn13(gold.get("isbn13"))}


def match_kind(record, gold):
    """record가 정답 도서인지: 'isbn', 'isbn_alt', 'title_author', 또는 ''.

    isbn_alt: 정답표가 함께 인정한 같은 책의 다른 판(isbn13_alt)과 ISBN이 같음 (PR #23 리뷰 1번).
    """
    gold_isbn = isbn13(gold.get("isbn13"))
    found_isbn = record_isbn(record)
    if found_isbn:
        if gold_isbn and found_isbn == gold_isbn:
            return "isbn"
        return "isbn_alt" if found_isbn in alt_isbns(gold) else ""
    same_title = normalize_title(record.get("title")) == normalize_title(gold.get("title"))
    shared_author = split_authors(record.get("authors")) & split_authors(gold.get("authors"))
    return "title_author" if same_title and shared_author else ""


def field_correct(record, gold, field):
    """채워진 항목 하나가 정답표와 맞는지 (M4). 정답표가 비어 있으면 None (판정 불가).

    결과가 다른 판(isbn13_alt)이면 출판사·출간일은 정답표(대표 판)와 다를 수 있으므로
    비교하지 않는다(None). ISBN은 그 다른 판의 ISBN이면 맞은 것으로 본다.
    """
    other_edition = record_isbn(record) in alt_isbns(gold)
    if field in ("publisher", "pubdate") and other_edition:
        return None
    if field == "title":
        if not gold.get("title"):
            return None
        return normalize_title(record.get("title")) == normalize_title(gold.get("title"))
    if field == "authors":
        wanted = split_authors(gold.get("authors"))
        return bool(wanted & split_authors(record.get("authors"))) if wanted else None
    if field == "publisher":
        if not gold.get("publisher"):
            return None
        return normalize_text(record.get("publisher")) == normalize_text(gold.get("publisher"))
    if field == "pubdate":
        wanted = normalize_date(gold.get("pubdate"))
        if not wanted:
            return None
        # 정답표에 적힌 정밀도(연 / 연-월 / 연-월-일)까지만 비교
        return normalize_date(record.get("publishedDate"))[:len(wanted)] == wanted
    if field == "isbn":
        wanted = isbn13(gold.get("isbn13"))
        if not wanted:
            return None
        return record_isbn(record) == wanted or other_edition
    raise ValueError(field)


# ---------------------------------------------------------------------------
# 질의 하나 / 전체 요약
# ---------------------------------------------------------------------------

def judge_query(entry, gold):
    """collect.py의 한 줄(entry)과 그 책의 정답(gold) -> 질의별 판정 dict."""
    results = entry.get("results") or []
    kinds = [match_kind(r, gold) for r in results]
    rank = next((i + 1 for i, k in enumerate(kinds) if k), None)
    row = {
        "id": entry["id"],
        "query": entry.get("query", ""),
        "status": entry.get("status", ""),
        "elapsed_s": entry.get("elapsed_s"),
        "result_count": len(results),
        "rank": rank,
        "match": kinds[rank - 1] if rank else "",
        "top1": bool(rank == 1),
        "top5": bool(rank and rank <= TOP_K),
        "top1_source": ((results[0].get("source") or {}).get("id", "") if results else ""),
        "top1_score": (results[0].get("score") if results else None),  # F3 점수 (없으면 None)
        "filled": 0,
        "filled_fields": "",
        "compared": 0,
        "correct": 0,
        "wrong_fields": "",
        "cover": False,
    }
    if not results:
        return row
    top = results[0]
    filled = filled_fields(top)
    row["filled"] = len(filled)
    row["filled_fields"] = ",".join(filled)
    cover_ok = entry.get("cover_ok")  # --check-covers로 실제 확인했으면 그 값을 쓴다
    row["cover"] = bool(cover_ok) if cover_ok is not None else usable_cover(top.get("cover"))
    if row["top1"]:
        wrong = []
        for field in COMPARED_FIELDS:
            if field not in filled:
                continue
            ok = field_correct(top, gold, field)
            if ok is None:
                continue
            row["compared"] += 1
            if ok:
                row["correct"] += 1
            else:
                wrong.append(field)
        row["wrong_fields"] = ",".join(wrong)
    return row


def percentile(values, pct):
    """최근접 순위(nearest-rank) 백분위수. 값이 없으면 None."""
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    rank = max(1, math.ceil(pct / 100.0 * len(values)))
    return values[rank - 1]


def summarize(rows):
    """질의별 판정 목록 -> M1~M6 요약 dict (비율은 0~1)."""
    n = len(rows)
    answered = [r for r in rows if r["status"] == "ok"]
    compared = sum(r["compared"] for r in rows)
    return {
        "queries": n,
        "errors": n - len(answered),
        "M1": sum(r["top1"] for r in rows) / n if n else None,
        "M2": sum(r["top5"] for r in rows) / n if n else None,
        "M3": sum(r["filled"] for r in rows) / n if n else None,
        "M4": sum(r["correct"] for r in rows) / compared if compared else None,
        "M5": sum(r["cover"] for r in rows) / n if n else None,
        "M6": percentile([r["elapsed_s"] for r in answered], 95),
        "weak_matches": sum(r["match"] == "title_author" for r in rows),
        "alt_matches": sum(r["match"] == "isbn_alt" for r in rows),
    }


TARGETS = {"M1": 0.90, "M2": 0.95, "M3": 6.0, "M4": 0.95, "M5": 0.95, "M6": 5.0}


def passes(metric, value):
    if value is None:
        return None
    return value <= TARGETS[metric] if metric == "M6" else value >= TARGETS[metric]
