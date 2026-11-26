# -*- coding: utf-8 -*-
import pytest

from cps.services.Metadata import MetaRecord, MetaSourceInfo
from cps.services import book_search as bs

COVER = "https://example.com/cover.jpg"


def record(title, authors=(), isbn="", source="fake", **fields):
    r = MetaRecord(id=title, title=title, authors=list(authors), url="",
                   source=MetaSourceInfo(id=source, description=source, link=""))
    r.cover = fields.pop("cover", "")
    if isbn:
        r.identifiers = {source: title, "isbn": isbn}
    for key, value in fields.items():
        setattr(r, key, value)
    return r


def full_record(title, authors, isbn, source="fake"):
    """7개 항목이 모두 찬 결과."""
    return record(title, authors, isbn, source=source, publisher="출판사", publishedDate="2020-01-01",
                  description="소개", cover=COVER)


class FakeProvider:
    def __init__(self, provider_id, results=None, error=None, active=True, configured=True):
        self.__id__ = provider_id
        self.active = active
        self.results = results if results is not None else {}
        self.error = error
        self.configured = configured
        self.queries = []

    def is_configured(self):
        return self.configured

    def search(self, query, generic_cover="", locale=None):
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.results.get(query, [])


# ---------------------------------------------------------------------------
# 결과 읽기
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("isbn, expected", [
    ("9788936434120", "9788936434120"),
    ("8936434128", "9788936434120"),                   # ISBN-10은 13으로
    ("8936434128 9788936434120", "9788936434120"),     # 카카오처럼 두 개
    ("9788936434121", ""),                             # 검사 숫자 틀림
    ("", ""),
])
def test_record_isbn13(isbn, expected):
    assert bs.record_isbn13(record("책", isbn=isbn)) == expected


@pytest.mark.parametrize("cover, usable", [
    (COVER, True),
    ("http://example.com/a.jpg", True),
    ("/static/generic_cover.jpg", False),
    ("https://example.com/static/generic_cover.jpg", False),
    ("", False),
])
def test_usable_cover(cover, usable):
    assert bs.usable_cover(cover) is usable


def test_completeness_counts_seven_fields():
    assert bs.completeness(full_record("책", ["저자"], "9788936434120")) == 1.0
    assert bs.completeness(record("책")) == pytest.approx(1 / 7)
    assert bs.completeness(record("책", ["저자"], cover="/static/generic_cover.jpg")) == pytest.approx(2 / 7)


# ---------------------------------------------------------------------------
# 점수
# ---------------------------------------------------------------------------

def test_exact_match_scores_one():
    r = full_record("채식주의자", ["한강"], "9788936434120")
    assert bs.score_record(r, isbn="9788936434120", title="채식주의자", authors=["한강"]) == 1.0


def test_book_without_isbn_can_reach_threshold():
    # ISBN을 모르는 책: ISBN 항목을 빼고 다시 나누므로 제목·저자가 맞으면 0.85를 넘는다
    r = full_record("채식주의자", ["한강"], "9788936434120")
    assert bs.score_record(r, title="채식주의자", authors=["한강"]) == 1.0
    assert bs.score_record(record("채식주의자", ["한강"]), title="채식주의자", authors=["한강"]) >= 0.85


def test_unknown_author_uses_title_only():
    r = full_record("피로사회", ["한병철"], "9788932020785")
    assert bs.score_record(r, title="피로 사회") == 1.0       # 띄어쓰기는 무시


def test_isbn_only_query():
    right = full_record("책", ["저자"], "9788936434120")
    wrong = full_record("책", ["저자"], "9780306406157")
    assert bs.score_record(right, isbn="9788936434120") == 1.0
    assert bs.score_record(wrong, isbn="9788936434120") < 0.5


def test_wrong_isbn_lowers_score():
    right = full_record("채식주의자", ["한강"], "9788936434120")
    other = full_record("채식주의자", ["한강"], "9780306406157")
    args = dict(isbn="9788936434120", title="채식주의자", authors=["한강"])
    assert bs.score_record(right, **args) > bs.score_record(other, **args)


def test_edition_labels_and_quotes_are_ignored():
    r = full_record("이방인", ["알베르 카뮈"], "9788937460012")
    assert bs.score_record(r, title="[리커버] 《이방인》") == 1.0


def test_author_role_and_hanja_are_ignored():
    r = full_record("토지 1", ["박경리（朴景利） (지은이)"], "9788934972211")
    assert bs.score_record(r, title="토지 1", authors=["박경리"]) == 1.0


def test_volume_number_matters():
    one = full_record("토지 1", ["박경리"], "9788934972211")
    two = full_record("토지 2", ["박경리"], "9788934972228")
    assert bs.score_record(two, title="토지 2") > bs.score_record(one, title="토지 2")


def test_subtitle_on_one_side_still_matches():
    r = full_record("사피엔스: 유인원에서 사이보그까지, 인간 역사의 대담하고 위대한 질문", ["유발 하라리"],
                    "9788934972464")
    assert bs.score_record(r, title="사피엔스") >= 0.85


def test_unrelated_title_scores_low():
    r = full_record("코스모스", ["칼 세이건"], "9788983711892")
    assert bs.score_record(r, title="채식주의자", authors=["한강"]) < 0.5


def test_score_is_between_zero_and_one():
    for r in (record(""), full_record("책", ["저자"], "9788936434120")):
        for args in ({}, {"title": "책"}, {"isbn": "9788936434120"}, {"authors": ["x"]}):
            assert 0.0 <= bs.score_record(r, **args) <= 1.0


# ---------------------------------------------------------------------------
# 합치기와 정렬
# ---------------------------------------------------------------------------

def test_same_isbn_is_merged_and_blanks_are_filled():
    kakao = record("채식주의자", ["한강"], "9788936434120 8936434128", source="kakao", publisher="창비")
    nlgo = record("채식주의자", ["한강"], "9788936434120", source="nlgo", cover=COVER,
                  publishedDate="2007-10-30", description="소개")
    ranked = bs.rank_records([kakao, nlgo], title="채식주의자", authors=["한강"])
    assert len(ranked) == 1
    merged, score = ranked[0]
    assert merged.publisher == "창비" and merged.cover == COVER
    assert merged.publishedDate == "2007-10-30" and merged.description == "소개"
    assert merged.identifiers["isbn"] == "9788936434120"
    assert score == 1.0


def test_merge_keeps_source_of_best_record():
    weak = record("채식주의자", source="google")
    strong = full_record("채식주의자", ["한강"], "9788936434120", source="kakao")
    weak.identifiers = {"isbn": "9788936434120"}
    merged, _ = bs.rank_records([weak, strong], title="채식주의자", authors=["한강"])[0]
    assert merged.source.id == "kakao"


def test_merge_does_not_change_original_records():
    a = record("책", ["저자"], "9788936434120", source="a")
    b = record("책", ["저자"], "9788936434120", source="b", publisher="출판사")
    bs.rank_records([a, b], title="책")
    assert a.publisher is None


def test_records_without_isbn_are_not_merged():
    ranked = bs.rank_records([record("책"), record("책")], title="책")
    assert len(ranked) == 2


def test_rank_puts_best_first():
    wrong = full_record("채식주의자 읽기", ["누군가"], "9780306406157", source="google")
    right = full_record("채식주의자", ["한강"], "9788936434120", source="kakao")
    ranked = bs.rank_records([wrong, right], title="채식주의자", authors=["한강"])
    assert [r.source.id for r, _ in ranked] == ["kakao", "google"]
    assert ranked[0][1] >= ranked[1][1]


def test_rank_empty():
    assert bs.rank_records([], title="책") == []
    assert bs.rank_records(None) == []


# ---------------------------------------------------------------------------
# search_book (F6가 부르는 함수)
# ---------------------------------------------------------------------------

def test_search_book_uses_isbn_first():
    hit = full_record("채식주의자", ["한강"], "9788936434120")
    provider = FakeProvider("kakao", {"9788936434120": [hit]})
    result = bs.search_book("89-364-3412-8", "채식주의자", ["한강"], providers=[provider], locale="ko")
    assert provider.queries == ["9788936434120"]
    assert result[0][1] == 1.0


def test_search_book_falls_back_to_title():
    hit = full_record("이방인", ["알베르 카뮈"], "9788937460012")
    provider = FakeProvider("kakao", {"이방인": [hit]})
    result = bs.search_book("9788936434120", "[리커버] 이방인", ["알베르 카뮈"], providers=[provider], locale="ko")
    assert provider.queries == ["9788936434120", "이방인"]    # ISBN에서 못 찾으면 정리한 제목으로
    assert result and result[0][0].title == "이방인"


def test_search_book_does_not_add_authors_to_query():
    provider = FakeProvider("nlgo")
    bs.search_book("", "채식주의자", ["한강"], providers=[provider], locale="ko")
    assert provider.queries == ["채식주의자"]


def test_search_book_honours_provider_choice():
    on, off = FakeProvider("kakao"), FakeProvider("google")
    inactive, no_key = FakeProvider("douban", active=False), FakeProvider("nlgo", configured=False)
    bs.search_book("", "책", [], provider_ids=["kakao", "nlgo", "douban"],
                   providers=[on, off, inactive, no_key], locale="ko")
    assert on.queries == ["책"]
    assert not off.queries and not inactive.queries and not no_key.queries


def test_search_book_partial_failure_returns_rest():
    good = FakeProvider("kakao", {"책": [full_record("책", ["저자"], "9788936434120")]})
    broken = FakeProvider("google", error=RuntimeError("down"))
    result = bs.search_book("", "책", [], providers=[good, broken], locale="ko")
    assert len(result) == 1


def test_search_book_all_failed_raises():
    broken = [FakeProvider("kakao", error=RuntimeError("down")), FakeProvider("google", error=OSError("x"))]
    with pytest.raises(bs.SearchUnavailable):
        bs.search_book("", "책", [], providers=broken, locale="ko")


def test_search_book_no_provider_raises():
    with pytest.raises(bs.SearchUnavailable):
        bs.search_book("", "책", [], providers=[FakeProvider("kakao", active=False)], locale="ko")


def test_search_book_nothing_found_is_empty_not_error():
    assert bs.search_book("", "없는 책", [], providers=[FakeProvider("kakao")], locale="ko") == []


def test_search_book_without_isbn_or_title():
    provider = FakeProvider("kakao")
    assert bs.search_book("", "", ["한강"], providers=[provider], locale="ko") == []
    assert provider.queries == []


# ---------------------------------------------------------------------------
# POST /metadata/search
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_providers(monkeypatch):
    import cps.search_metadata as sm
    kakao = FakeProvider("kakao")
    google = FakeProvider("google")
    monkeypatch.setattr(sm, "cl", [kakao, google])
    return kakao, google


def test_route_merges_and_sorts(admin_client, calibre_library, fake_providers):
    kakao, google = fake_providers
    kakao.results["이방인"] = [record("이방인", ["알베르 카뮈"], "9788937460012", source="kakao", publisher="민음사")]
    google.results["이방인"] = [
        record("이방인을 위한 안내서", ["다른 사람"], "9780306406157", source="google"),
        record("이방인", ["알베르 카뮈"], "8937460017", source="google", cover=COVER),
    ]
    resp = admin_client.post("/metadata/search", data={"query": "[리커버] 이방인"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert kakao.queries == ["이방인"]                       # F2로 정리한 검색어
    assert len(data) == 2                                   # 같은 ISBN 두 건은 하나로
    assert data[0]["title"] == "이방인" and data[0]["publisher"] == "민음사" and data[0]["cover"] == COVER
    assert data[0]["score"] >= data[1]["score"]


def test_route_isbn_query(admin_client, calibre_library, fake_providers):
    kakao, _ = fake_providers
    kakao.results["9788936434120"] = [record("채식주의자", ["한강"], "9788936434120", source="kakao")]
    resp = admin_client.post("/metadata/search", data={"query": "89-364-3412-8"})
    assert kakao.queries == ["9788936434120"]
    assert resp.get_json()[0]["title"] == "채식주의자"


def test_route_empty_query(admin_client, calibre_library, fake_providers):
    resp = admin_client.post("/metadata/search", data={"query": ""})
    assert resp.status_code == 200 and resp.get_json() == []
