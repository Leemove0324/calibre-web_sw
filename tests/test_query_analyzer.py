# -*- coding: utf-8 -*-
import pytest

from cps.services.query_analyzer import (
    analyze_query,
    clean_isbn,
    compare_key,
    is_valid_isbn10,
    is_valid_isbn13,
    looks_like_isbn,
    normalize_title,
    split_subtitle,
    strip_edition,
    to_isbn13,
)


# ---------------------------------------------------------------------------
# ISBN
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    ("978-89-364-3412-0", "9788936434120"),
    ("978 89 364 3412 0", "9788936434120"),
    ("ISBN 978-89-364-3412-0", "9788936434120"),
    ("ISBN-13: 9788936434120", "9788936434120"),
    ("080442957x", "080442957X"),
])
def test_clean_isbn(value, expected):
    assert clean_isbn(value) == expected


@pytest.mark.parametrize("digits, valid", [
    ("9788936434120", True),
    ("9780306406157", True),
    ("9791162243336", True),     # 979로 시작하는 국내 ISBN
    ("9780306406158", False),    # 검사 숫자 틀림
    ("978030640615", False),     # 12자리
    ("978030640615X", False),    # ISBN-13에는 X가 없다
])
def test_is_valid_isbn13(digits, valid):
    assert is_valid_isbn13(digits) is valid


@pytest.mark.parametrize("digits, valid", [
    ("0306406152", True),
    ("080442957X", True),        # 검사 숫자가 10이면 X
    ("8936434128", True),
    ("0306406153", False),       # 검사 숫자 틀림
    ("X306406152", False),       # X는 끝자리에만
    ("030640615", False),        # 9자리
])
def test_is_valid_isbn10(digits, valid):
    assert is_valid_isbn10(digits) is valid


@pytest.mark.parametrize("value, expected", [
    ("0-306-40615-2", "9780306406157"),       # ISBN-10 -> ISBN-13
    ("89-364-3412-8", "9788936434120"),
    ("080442957X", "9780804429573"),
    ("9788936434120", "9788936434120"),       # 이미 ISBN-13
    ("9780306406158", ""),                    # 검사 숫자 틀림
    ("0306406153", ""),
    ("12345", ""),
    ("", ""),
    (None, ""),
])
def test_to_isbn13(value, expected):
    assert to_isbn13(value) == expected


@pytest.mark.parametrize("query, expected", [
    ("9788936434120", True),
    ("978-89-364-3412-0", True),
    ("ISBN 89-364-3412-8", True),
    ("9780306406158", True),     # 모양만 보므로 검사 숫자가 틀려도 True
    ("12345", False),
    ("1984", False),             # 숫자만 있는 제목
    ("트렌드 코리아 2024", False),
    ("", False),
])
def test_looks_like_isbn(query, expected):
    assert looks_like_isbn(query) is expected


# ---------------------------------------------------------------------------
# 제목
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title, expected, removed", [
    ("[리커버] 이방인", "이방인", ["리커버"]),
    ("코스모스 (양장본)", "코스모스", ["양장본"]),
    ("아몬드(반양장)", "아몬드", ["반양장"]),
    ("이것이 자바다 (개정판)", "이것이 자바다", ["개정판"]),
    ("정의란 무엇인가 (2014 개정판)", "정의란 무엇인가", ["2014 개정판"]),
    ("나미야 잡화점의 기적 (양장, 특별판)", "나미야 잡화점의 기적", ["양장, 특별판"]),
    ("데미안 리마스터판", "데미안", ["리마스터판"]),
    ("양장본 개정판 코스모스", "코스모스", ["양장본", "개정판"]),
    ("수상한 아파트 세트", "수상한 아파트", ["세트"]),
    ("해리 포터와 마법사의 돌 1 (20주년 기념판)", "해리 포터와 마법사의 돌 1", ["20주년 기념판"]),
])
def test_strip_edition_removes_edition_labels(title, expected, removed):
    assert strip_edition(title) == (expected, removed)


@pytest.mark.parametrize("title", [
    "토지 2",                    # 권차
    "트렌드 코리아 2024",        # 제목 속 연도
    "트렌드 코리아 (2024)",      # 괄호 안 연도만 있으면 남긴다
    "1984",
    "세트",                      # 제목 전체가 판 표기처럼 보이면 지우지 않는다
    "Do it! 점프 투 파이썬",
    "이것이 취업을 위한 코딩 테스트다 with 파이썬",
])
def test_strip_edition_keeps_title(title):
    assert strip_edition(title) == (title, [])


@pytest.mark.parametrize("title, expected", [
    ("사피엔스: 유인원에서 사이보그까지", ("사피엔스", "유인원에서 사이보그까지")),
    ("Clean Code: 애자일 소프트웨어 장인 정신", ("Clean Code", "애자일 소프트웨어 장인 정신")),
    ("사피엔스：유인원에서 사이보그까지", ("사피엔스", "유인원에서 사이보그까지")),  # 전각 콜론
    ("데미안 - 헤르만 헤세", ("데미안", "헤르만 헤세")),
    ("Re:Zero", ("Re:Zero", "")),             # 붙은 콜론은 제목의 일부
    ("Spider-Man", ("Spider-Man", "")),       # 공백 없는 하이픈은 나누지 않는다
    (": 부제만", (": 부제만", "")),
])
def test_split_subtitle(title, expected):
    assert split_subtitle(title) == expected


@pytest.mark.parametrize("title, expected", [
    ("《채식주의자》", "채식주의자"),
    ("「소년이 온다」", "소년이 온다"),
    ("“82년생 김지영”", "82년생 김지영"),
    ("[리커버] 《이방인》", "이방인"),
    ("  피로   사회  ", "피로 사회"),
    ("ＡＢＣ 전각 문자", "ABC 전각 문자"),     # NFKC
])
def test_normalize_title(title, expected):
    assert normalize_title(title) == expected


@pytest.mark.parametrize("a, b", [
    ("피로 사회", "피로사회"),
    ("Do it! 점프 투 파이썬", "do it 점프투파이썬"),
    ("Clean Code", "CLEAN-CODE"),
])
def test_compare_key_ignores_spacing_case_and_punctuation(a, b):
    assert compare_key(a) == compare_key(b)


def test_compare_key_keeps_volume_numbers_apart():
    assert compare_key("토지 1") != compare_key("토지 2")


# ---------------------------------------------------------------------------
# analyze_query
# ---------------------------------------------------------------------------

def test_analyze_isbn10_query():
    result = analyze_query("ISBN 89-364-3412-8")
    assert result.isbn13 == "9788936434120"
    assert result.isbn_like and not result.invalid_isbn
    assert result.title == ""


def test_analyze_invalid_isbn_query():
    result = analyze_query("978-89-364-3412-1")
    assert result.isbn13 == ""
    assert result.invalid_isbn


def test_analyze_title_query_keeps_raw_input():
    result = analyze_query("[리커버] 이방인")
    assert result.raw == "[리커버] 이방인"
    assert result.title == "이방인"
    assert result.removed == ["리커버"]
    assert result.isbn13 == "" and not result.invalid_isbn


def test_analyze_title_with_subtitle():
    result = analyze_query("《사피엔스》: 유인원에서 사이보그까지 (양장본)")
    assert result.title == "사피엔스: 유인원에서 사이보그까지"
    assert result.main_title == "사피엔스"
    assert result.subtitle == "유인원에서 사이보그까지"
    assert result.main_key == "사피엔스"


def test_analyze_spacing_variant_has_same_key():
    assert analyze_query("피로 사회").key == analyze_query("피로사회").key


@pytest.mark.parametrize("query", ["", None, "   "])
def test_analyze_empty_query(query):
    result = analyze_query(query)
    assert result.title == "" and result.isbn13 == "" and not result.isbn_like


# ---------------------------------------------------------------------------
# POST /metadata/search: 제공처에 정리한 검색어를 보낸다
# ---------------------------------------------------------------------------

class RecordingProvider:
    __id__ = "fake"
    __name__ = "Fake"
    active = True

    def __init__(self):
        self.queries = []

    def is_configured(self):
        return True

    def search(self, query, generic_cover="", locale=None):
        self.queries.append(query)
        return []


@pytest.fixture
def recording_provider(monkeypatch):
    import cps.search_metadata as sm
    provider = RecordingProvider()
    monkeypatch.setattr(sm, "cl", [provider])
    return provider


@pytest.mark.parametrize("query, sent", [
    ("[리커버] 《이방인》", "이방인"),              # 판 표기·인용 부호 제거
    ("89-364-3412-8", "9788936434120"),            # 맞는 ISBN은 ISBN-13으로
    ("978-89-364-3412-1", "978-89-364-3412-1"),    # 틀린 ISBN은 입력 그대로
])
def test_route_sends_cleaned_query(admin_client, calibre_library, recording_provider, query, sent):
    resp = admin_client.post("/metadata/search", data={"query": query})
    assert resp.status_code == 200
    assert recording_provider.queries == [sent]
