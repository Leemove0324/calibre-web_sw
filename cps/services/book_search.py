# -*- coding: utf-8 -*-
import concurrent.futures
import copy
import re
import unicodedata
from difflib import SequenceMatcher

from cps.services.query_analyzer import analyze_query, compare_key, to_isbn13

__all__ = [
    "SearchUnavailable",
    "WEIGHTS",
    "completeness",
    "merge_records",
    "rank_records",
    "record_isbn13",
    "score_record",
    "search_book",
]

WEIGHTS = {"title": 0.40, "authors": 0.25, "isbn": 0.25, "completeness": 0.10}

# M3(완성도)에서 세는 7개 항목. F9 평가 코드(scripts/eval/evallib.py)와 같다.
COMPLETENESS_FIELDS = ("title", "authors", "publisher", "pubdate", "isbn", "cover", "description")
GENERIC_COVER_HINTS = ("generic_cover", "academicpaper.svg")

MAX_WORKERS = 5


class SearchUnavailable(Exception):
    """물어볼 제공처가 하나도 없거나(전부 꺼짐·키 없음), 물어본 제공처가 모두 오류를 냈다."""


# ---------------------------------------------------------------------------
# 결과 한 건 읽기
# ---------------------------------------------------------------------------

def record_isbn13(record):
    """결과의 ISBN을 ISBN-13으로. 카카오처럼 '8996991341 9788996991359' 두 개가 오면 맞는 쪽을 쓴다."""
    identifiers = getattr(record, "identifiers", None) or {}
    for key, value in identifiers.items():
        if str(key).lower() != "isbn":
            continue
        for part in re.split(r"[\s,;/]+", str(value or "")):
            isbn = to_isbn13(part)
            if isbn:
                return isbn
        return to_isbn13(value)
    return ""


def usable_cover(url):
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return False
    return not any(hint in url for hint in GENERIC_COVER_HINTS)


def _filled(record):
    """7개 항목 중 값이 있는 항목 이름 목록."""
    values = {
        "title": (record.title or "").strip(),
        "authors": [a for a in (record.authors or []) if str(a).strip()],
        "publisher": (record.publisher or "").strip(),
        "pubdate": re.match(r"\s*\d{4}", str(record.publishedDate or "")),
        "isbn": record_isbn13(record),
        "cover": usable_cover(record.cover),
        "description": (record.description or "").strip(),
    }
    return [field for field in COMPLETENESS_FIELDS if values[field]]


def completeness(record):
    """C: 7개 항목 중 채워진 비율 (0~1)."""
    return len(_filled(record)) / len(COMPLETENESS_FIELDS)


# ---------------------------------------------------------------------------
# 점수
# ---------------------------------------------------------------------------

_NUMBER = re.compile(r"\d+")


def _title_similarity(wanted, found):
    """T: 띄어쓰기·구두점·판 표기를 무시한 제목 유사도 (0~1).

    부제까지 비교한 값과 본제목끼리 비교한 값 중 큰 쪽을 쓴다.
    검색한 제목의 숫자(권차 '토지 2', 연도 '트렌드 코리아 2024')가 결과에 없으면 깎는다.
    """
    want = analyze_query(wanted)
    got = analyze_query(found)
    if not want.key or not got.key:
        return 0.0
    best = max(SequenceMatcher(None, want.key, got.key).ratio(),
               SequenceMatcher(None, want.main_key, got.main_key).ratio() if want.main_key and got.main_key else 0.0)
    numbers = set(_NUMBER.findall(want.title))
    if numbers and not numbers <= set(_NUMBER.findall(got.title)):
        best *= 0.6
    return best


def _author_key(name):
    """'한강', '한강 (지은이)', '박경리（朴景利）' -> '한강', '한강', '박경리'"""
    name = unicodedata.normalize("NFKC", str(name or ""))
    name = re.sub(r"[\(\[（【].*?[\)\]）】]", " ", name)
    return compare_key(name)


def _author_score(wanted, found):
    """A: 검색한 저자 중 결과에 있는 비율 (0~1). 한쪽 이름이 다른 쪽에 포함되면 같은 사람으로 본다."""
    want = [k for k in (_author_key(a) for a in wanted) if k]
    have = [k for k in (_author_key(a) for a in found or []) if k]
    if not want:
        return 0.0
    hits = sum(1 for w in want if any(w == h or w in h or h in w for h in have))
    return hits / len(want)


def score_record(record, isbn="", title="", authors=None):
    """검색 조건(isbn, title, authors)에 대한 결과 한 건의 점수 S (0~1, 소수 넷째 자리).

    검색 조건에 없는 항목은 식에서 빼고, 남은 가중치의 합으로 나눈다.
    """
    authors = [a for a in (authors or []) if str(a).strip()]
    wanted_isbn = to_isbn13(isbn)
    parts = {"completeness": completeness(record)}
    if title and compare_key(title):
        parts["title"] = _title_similarity(title, record.title)
    if authors:
        parts["authors"] = _author_score(authors, record.authors)
    if wanted_isbn:
        parts["isbn"] = 1.0 if record_isbn13(record) == wanted_isbn else 0.0
    total_weight = sum(WEIGHTS[name] for name in parts)
    score = sum(WEIGHTS[name] * value for name, value in parts.items()) / total_weight
    return round(score, 4)


# ---------------------------------------------------------------------------
# 합치기와 정렬
# ---------------------------------------------------------------------------

_MERGE_FIELDS = ("publisher", "publishedDate", "description", "series", "series_index",
                 "rating", "languages", "tags")


def _empty(value):
    return value is None or value == "" or value == [] or value == 0


def merge_records(scored):
    """[(record, score)] (점수 높은 순) -> 같은 ISBN-13끼리 합친 [record].

    대표는 그룹에서 점수가 가장 높은 결과다 (source도 그 결과의 것). 대표의 빈 항목은
    점수 순서대로 다른 결과의 값으로 채운다. 표지는 쓸 수 있는 표지(http, 기본 표지 아님)를
    가진 첫 결과의 것을 쓴다. 원본 결과는 바꾸지 않는다.
    """
    groups = {}
    order = []
    for record, _ in scored:
        isbn = record_isbn13(record)
        key = ("isbn", isbn) if isbn else ("single", id(record))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(record)

    merged = []
    for key in order:
        members = groups[key]
        best = copy.deepcopy(members[0])
        for other in members[1:]:
            for field in _MERGE_FIELDS:
                if _empty(getattr(best, field, None)) and not _empty(getattr(other, field, None)):
                    setattr(best, field, copy.deepcopy(getattr(other, field)))
            if not [a for a in (best.authors or []) if str(a).strip()] and other.authors:
                best.authors = list(other.authors)
            if not usable_cover(best.cover) and usable_cover(other.cover):
                best.cover = other.cover
            for name, value in (other.identifiers or {}).items():
                best.identifiers.setdefault(name, value)
        if key[0] == "isbn":
            best.identifiers["isbn"] = key[1]   # 대표 ISBN은 ISBN-13으로 맞춘다
        merged.append(best)
    return merged


def rank_records(records, isbn="", title="", authors=None):
    """결과 목록 -> 합치고 점수 높은 순으로 정렬한 [(record, score)].

    점수가 같으면 원래 순서를 지킨다. 합친 결과는 완성도가 바뀌므로 점수를 다시 매긴다.
    """
    records = [r for r in (records or []) if r]
    first = [(r, score_record(r, isbn, title, authors)) for r in records]
    first.sort(key=lambda pair: pair[1], reverse=True)
    ranked = [(r, score_record(r, isbn, title, authors)) for r in merge_records(first)]
    ranked.sort(key=lambda pair: pair[1], reverse=True)
    return ranked


# ---------------------------------------------------------------------------
# 검색 (F6가 부른다)
# ---------------------------------------------------------------------------

def _default_providers():
    from cps.search_metadata import cl  # 늦은 import: search_metadata가 F6를 통해 이 모듈을 부른다
    return cl


def _default_locale():
    from babel import Locale
    return Locale("ko")  # 제공처는 babel Locale을 받는다 (.language를 읽는다)


def _usable(provider, provider_ids):
    if not getattr(provider, "active", True):
        return False
    if provider_ids is not None and provider.__id__ not in provider_ids:
        return False
    # F8: API 키가 없는 제공처는 묻지 않는다 (메서드가 없는 가짜 제공처는 설정된 것으로 본다)
    return getattr(provider, "is_configured", lambda: True)()


def _ask_all(providers, query_for, locale):
    """모든 제공처에 동시에 묻는다. -> (결과 목록, 물어본 수, 오류 수)"""
    records, asked, errors = [], 0, 0
    jobs = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for provider in providers:
            query = query_for(provider)
            if not query:
                continue
            asked += 1
            jobs[executor.submit(provider.search, query, "", locale)] = provider
        for future in concurrent.futures.as_completed(jobs):
            try:
                found = future.result()
            except Exception:  # 제공처 하나가 고장 나도 나머지 결과는 쓴다
                errors += 1
                continue
            records.extend(r for r in (found or []) if r)
    return records, asked, errors


def search_book(isbn, title, authors, provider_ids=None, providers=None, locale=None):
    """책 한 권을 켜진 제공처에서 찾아 [(MetaRecord, score)]를 점수 높은 순으로 돌려준다.

    isbn, title, authors: 서재에 있는 그 책의 정보 (없으면 '' / []).
    provider_ids: 사용자가 켠 제공처 id 목록 (None이면 켜진 제공처 전부).
    providers, locale: 테스트용. 보통은 비워 둔다.

    - ISBN이 맞으면 ISBN으로 먼저 찾고, 아무것도 없으면 제목으로 다시 찾는다.
    - 제목은 F2로 정리해서(판 표기·인용 부호 제거) 보낸다. 저자는 검색어에 붙이지 않고 점수에만 쓴다.
      (국립중앙도서관은 검색어를 '제목' 칸에 넣어서, 저자를 붙이면 결과가 사라진다.)
    - 물어볼 제공처가 없거나 물어본 제공처가 모두 오류를 내면 SearchUnavailable을 낸다.
      일부만 실패하면 나머지 결과를 돌려준다.
    """
    authors = [a for a in (authors or []) if str(a).strip()]
    isbn13 = to_isbn13(isbn)
    search_title = analyze_query(title).title if title else ""
    providers = [p for p in (providers if providers is not None else _default_providers())
                 if _usable(p, provider_ids)]
    if not providers:
        raise SearchUnavailable("No metadata provider is enabled or configured")
    locale = locale or _default_locale()

    records, asked, errors = [], 0, 0
    if isbn13:
        records, asked, errors = _ask_all(providers, lambda p: isbn13, locale)
    if not records and search_title:
        found, more_asked, more_errors = _ask_all(providers, lambda p: search_title, locale)
        records.extend(found)
        asked += more_asked
        errors += more_errors
    if asked and errors == asked:
        raise SearchUnavailable("Every metadata provider failed")
    return rank_records(records, isbn=isbn13, title=title, authors=authors)
