# -*- coding: utf-8 -*-
"""F6 (#8): 선택한 책 정보 일괄 가져오기 — cps/tasks/metadata_fetch.py, POST/GET /metadata/bulk.

외부 제공처는 부르지 않는다. 검색 결과는 가짜 함수나 가짜 제공처로 대신한다.
"""
import json
from datetime import datetime

import pytest

from cps.services.Metadata import MetaRecord, MetaSourceInfo


def record(title, authors, source="fake", **fields):
    r = MetaRecord(id=title, title=title, authors=authors, url="",
                   source=MetaSourceInfo(id=source, description=source, link=""))
    r.cover = fields.pop("cover", "")
    for key, value in fields.items():
        setattr(r, key, value)
    return r


@pytest.fixture
def mf():
    from cps.tasks import metadata_fetch
    return metadata_fetch


@pytest.fixture
def calibre_session(calibre_session):
    """F9의 calibre_session + Calibre SQL 함수 등록.

    books의 UPDATE 트리거가 title_sort()를 부르므로, 실제 앱처럼 함수를 등록해야 책을 고칠 수 있다.
    (editbooks.do_edit_book과 TaskFetchMetadata는 db.CalibreDB.create_functions로 등록한다.)
    """
    from cps import config, db
    db.CalibreDB.create_functions(type("Holder", (), {"session": calibre_session})(), config)
    return calibre_session


def book_by_title(session, title):
    from cps import db
    return session.query(db.Books).filter(db.Books.title == title).one()


# ---------------------------------------------------------------------------
# 작은 함수들
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("2013-11-07", datetime(2013, 11, 7)),
    ("2013-11-07T00:00:00.000+09:00", datetime(2013, 11, 7)),  # 카카오 형식
    ("2013-11", datetime(2013, 11, 1)),
    ("2013", datetime(2013, 1, 1)),
    ("unknown", None),
    ("", None),
    (None, None),
])
def test_parse_pubdate(mf, text, expected):
    assert mf._parse_pubdate(text) == expected


def test_score_isbn_match_is_full(mf):
    item = {"title": "아무 제목", "authors": [], "isbn": "9788954651134"}
    assert mf.temporary_score(item, record("다른 제목", [], identifiers={"isbn": "978-89-546-5113-4"})) == 1.0


def test_score_same_title_and_author(mf):
    item = {"title": "채식주의자", "authors": ["한강"], "isbn": ""}
    assert mf.temporary_score(item, record("채식주의자", ["한강"])) == 1.0


def test_score_other_book_stays_below_threshold(mf):
    item = {"title": "채식주의자", "authors": ["한강"], "isbn": ""}
    assert mf.temporary_score(item, record("해리 포터와 마법사의 돌", ["J.K. 롤링"])) < mf.DEFAULT_THRESHOLD


def test_score_without_author_uses_title_only(mf):
    """리뷰 5번: 저자가 없는 책이 항상 검토 대기(최대 0.7)가 되지 않아야 한다."""
    item = {"title": "채식주의자", "authors": [], "isbn": ""}
    assert mf.temporary_score(item, record("채식주의자", ["한강"])) == 1.0


def test_unknown_author_is_not_an_author(mf):
    assert mf.known_authors(["Unknown", "알 수 없음", "한강", ""]) == ["한강"]


# ---------------------------------------------------------------------------
# default_search: 사용자 설정, 제공처 오류
# ---------------------------------------------------------------------------

class FakeProvider:
    def __init__(self, pid, records=None, error=None, active=True):
        self.__id__ = pid
        self.active = active
        self.records = records or []
        self.error = error
        self.queries = []

    def search(self, query, generic_cover="", locale="en"):
        self.queries.append(query)
        if self.error:
            raise self.error
        return list(self.records)


@pytest.fixture
def providers(monkeypatch, app):
    import cps.search_metadata as sm

    def install(*items):
        monkeypatch.setattr(sm, "cl", list(items))
        return items
    return install


ITEM = {"book_id": 1, "title": "채식주의자", "authors": ["한강"], "isbn": ""}


def test_search_sorts_by_score_and_uses_title_and_author(mf, providers):
    good = record("채식주의자", ["한강"])
    bad = record("전혀 다른 책", ["누구"])
    p, = providers(FakeProvider("a", [bad, good]))
    result = mf.default_search(ITEM)
    assert [r.title for r, _ in result] == ["채식주의자", "전혀 다른 책"]
    assert p.queries == ["채식주의자 한강"]


def test_search_prefers_isbn_query(mf, providers):
    p, = providers(FakeProvider("a"))
    mf.default_search(dict(ITEM, isbn="9788936434595"))
    assert p.queries == ["9788936434595"]


def test_search_honours_user_provider_choice(mf, providers):
    """리뷰 4번: 사용자가 끈 제공처와 관리자가 끈 제공처는 부르지 않는다."""
    on, off, inactive = providers(FakeProvider("on"), FakeProvider("off"), FakeProvider("x", active=False))
    mf.default_search(ITEM, provider_ids={"on", "x"})
    assert on.queries and not off.queries and not inactive.queries


def test_search_one_provider_error_is_tolerated(mf, providers):
    providers(FakeProvider("broken", error=RuntimeError("timeout")), FakeProvider("ok", [record("채식주의자", ["한강"])]))
    assert len(mf.default_search(ITEM)) == 1


def test_search_all_providers_failing_is_an_error(mf, providers):
    """리뷰 3번: 모든 제공처가 실패하면 '결과 없음'이 아니라 오류로 알린다."""
    providers(FakeProvider("a", error=RuntimeError("429")), FakeProvider("b", error=ValueError("not json")))
    with pytest.raises(mf.SearchUnavailable):
        mf.default_search(ITEM)


def test_search_with_no_enabled_provider_is_an_error(mf, providers):
    providers(FakeProvider("a"))
    with pytest.raises(mf.SearchUnavailable):
        mf.default_search(ITEM, provider_ids=set())


def test_search_empty_query_returns_nothing(mf, providers):
    providers(FakeProvider("a"))
    assert mf.default_search({"title": "", "authors": [], "isbn": ""}) == []


# ---------------------------------------------------------------------------
# apply_record: 실제 metadata.db
# ---------------------------------------------------------------------------

@pytest.fixture
def no_cover_download(monkeypatch):
    from cps import helper
    saved = []
    monkeypatch.setattr(helper, "save_cover_from_url", lambda url, path: (saved.append(url) or True, None))
    monkeypatch.setattr(helper, "replace_cover_thumbnail_cache", lambda book_id: None)
    return saved


def test_apply_fills_only_empty_fields(mf, calibre_session, no_cover_download):
    book = book_by_title(calibre_session, "소년이 온다")  # 출판사 "테스트출판사"가 이미 있음
    rec = record("소년이 온다 (개정판)", ["다른 사람"], publisher="창비", publishedDate="2014-05-19",
                 description="소개", identifiers={"isbn": "9788936434120"}, tags=["소설", "한국"],
                 cover="https://example.invalid/c.jpg")
    calls = []
    changed = mf.apply_record(calibre_session, book, rec, recorder=lambda *a: calls.append(a))
    calibre_session.commit()

    assert set(changed) == {"pubdate", "comments", "isbn", "tags", "cover"}
    assert [p.name for p in book.publishers] == ["테스트출판사"]           # 덮어쓰지 않음
    assert book.title == "소년이 온다" and [a.name for a in book.authors] == ["한강"]  # 제목·저자 보호
    assert book.pubdate.date() == datetime(2014, 5, 19).date()
    assert book.comments[0].text == "소개"
    assert mf._book_isbn(book) == "9788936434120"
    assert sorted(t.name for t in book.tags) == ["소설", "한국"]
    assert book.has_cover and no_cover_download == ["https://example.invalid/c.jpg"]
    # F7 연결: 바꾸기 직전에 (책, 칸, 이전 값, 새 값) 순서로 기록을 부른다
    assert [c[1] for c in calls] == changed and calls[0][2] == "" and calls[0][0] is book


def test_apply_skips_field_when_recording_fails(mf, calibre_session):
    book = book_by_title(calibre_session, "아몬드")

    def recorder(book, field, old, new):
        if field == "comments":
            raise RuntimeError("app.db 잠김")
    changed = mf.apply_record(calibre_session, book, record("아몬드", ["손원평"], publisher="창비",
                                                             description="소개"), recorder=recorder)
    assert changed == ["publisher"] and not book.comments


def test_apply_overwrites_only_when_asked(mf, calibre_session):
    book = book_by_title(calibre_session, "소년이 온다")
    changed = mf.apply_record(calibre_session, book, record("소년이 온다", ["한강"], publisher="창비"),
                              fields=("publisher",), only_empty=False)
    assert changed == ["publisher"] and [p.name for p in book.publishers] == ["창비"]


def test_apply_nothing_when_candidate_is_empty(mf, calibre_session):
    book = book_by_title(calibre_session, "아몬드")
    assert mf.apply_record(calibre_session, book, record("아몬드", ["손원평"])) == []


def test_failed_cover_download_is_not_counted(mf, calibre_session, monkeypatch):
    from cps import helper
    monkeypatch.setattr(helper, "save_cover_from_url", lambda url, path: (False, "404"))
    book = book_by_title(calibre_session, "아몬드")
    assert mf.apply_record(calibre_session, book, record("아몬드", ["손원평"], cover="https://x.invalid/a.jpg"),
                           fields=("cover",)) == []
    assert not book.has_cover


# ---------------------------------------------------------------------------
# 작업 전체: 책별 결과
# ---------------------------------------------------------------------------

def fake_search(item):
    title = item["title"]
    if title == "채식주의자":
        return [(record("채식주의자", ["한강"], publisher="창비", publishedDate="2007-10-30"), 0.97)]
    if title == "82년생 김지영":
        return [(record("비슷한 책", ["누구"], publisher="X"), 0.4)]
    if title == "존재하지않는책제목xyzqw":
        return []
    if title == "미움받을 용기":
        raise RuntimeError("timeout")
    if title == "소년이 온다":
        return [(record("소년이 온다", ["한강"], publisher="다른출판사"), 0.99)]  # 출판사는 이미 있음
    return []


def test_task_results_per_book(mf, app, calibre_library):
    from cps import db
    ids = calibre_library["books"]
    wanted = ["채식주의자", "82년생 김지영", "존재하지않는책제목xyzqw", "미움받을 용기", "소년이 온다"]
    recorded = []

    def factory(task_id, user_id, source, score):
        return lambda book, field, old, new: recorded.append((user_id, book.id, field, old, new, source, score))

    task = mf.TaskFetchMetadata([ids[t] for t in wanted] + [99999, ids["채식주의자"]], user_id=1,
                                search=fake_search, recorder_factory=factory)
    assert len(task.book_ids) == 6 and "6 books" in str(task)  # 중복 제거, __str__은 일반 문자열
    task.run(None)

    by_title = {e["title"]: e for e in task.results}
    assert by_title["채식주의자"]["result"] == "applied"
    assert by_title["82년생 김지영"]["result"] == "pending"
    assert by_title["존재하지않는책제목xyzqw"]["result"] == "no_match"
    assert by_title["미움받을 용기"]["result"] == "failed" and "timeout" in by_title["미움받을 용기"]["error"]
    assert by_title["소년이 온다"]["result"] == "unchanged"
    assert any(e["book_id"] == 99999 and e["result"] == "failed" for e in task.results)
    assert task.counts() == {"applied": 1, "pending": 1, "no_match": 1, "unchanged": 1, "failed": 2}
    assert task.progress == 1 and task.message.startswith("1 applied, 1 pending review")
    assert recorded[0][:5] == (1, ids["채식주의자"], "publisher", "", "창비") and recorded[0][5:] == ("fake", 0.97)

    with app.app_context():
        session = db.CalibreDB(app).session
        applied = book_by_title(session, "채식주의자")
        held = book_by_title(session, "82년생 김지영")
        assert [p.name for p in applied.publishers] == ["창비"] and applied.title == "채식주의자"
        assert held.publishers == []  # 검토 대기는 손대지 않음
        session.close()


def test_task_stops_when_cancelled(mf, app, calibre_library):
    from cps.services.worker import STAT_ENDED
    ids = list(calibre_library["books"].values())
    task = mf.TaskFetchMetadata(ids, user_id=1)

    def cancelling(item):
        task.stat = STAT_ENDED  # 사용자가 /tasks에서 취소한 것과 같음
        return []
    task.search = cancelling
    task.run(None)
    assert task.stat == STAT_ENDED and len(task.results) < len(ids)


def test_task_uses_the_users_providers(mf, providers):
    on, off = providers(FakeProvider("on"), FakeProvider("off"))
    task = mf.TaskFetchMetadata([1], user_id=1, provider_ids=["on"])
    task.search(ITEM)
    assert on.queries and not off.queries
    assert task.is_cancellable and str(task.name)


# ---------------------------------------------------------------------------
# 라우트
# ---------------------------------------------------------------------------

@pytest.fixture
def queued(monkeypatch):
    """작업 큐에 넣지 않고 붙잡는다 (실제 작업 스레드가 외부 제공처를 부르지 않도록)."""
    import cps.search_metadata as sm
    added = []
    monkeypatch.setattr(sm.WorkerThread, "add", staticmethod(lambda user, task: added.append((user, task))))
    return added


def post_bulk(client, payload):
    return client.post("/metadata/bulk", data=json.dumps(payload), content_type="application/json")


def test_route_starts_task(admin_client, calibre_library, queued):
    ids = list(calibre_library["books"].values())[:3]
    resp = post_bulk(admin_client, {"book_ids": ids})
    assert resp.status_code == 202 and resp.get_json()["books"] == 3
    user, task = queued[0]
    assert user == "admin" and task.book_ids == ids
    assert resp.get_json()["task_id"] == str(task.id)
    assert task.provider_ids is not None  # 사용자 설정을 넘김


@pytest.mark.parametrize("payload, message", [
    ({}, "No books selected"),
    ({"book_ids": ["x"]}, "list of numbers"),
    ({"book_ids": list(range(1, 202))}, "at most 200"),
])
def test_route_rejects_bad_input(admin_client, calibre_library, queued, payload, message):
    resp = post_bulk(admin_client, payload)
    assert resp.status_code == 400 and message in resp.get_json()["error"] and not queued


def test_route_status_unknown_task(admin_client, calibre_library):
    assert admin_client.get("/metadata/bulk/no-such-task").status_code == 404


def test_route_needs_login(client, calibre_library, queued):
    resp = post_bulk(client, {"book_ids": [1]})
    assert resp.status_code in (302, 401) and not queued


def test_new_tags_drop_repeats(mf):
    """제공처가 같은 태그를 두 번 주면 tags.name UNIQUE 오류로 책 전체가 실패했었다 (F7 시험 중 발견)."""
    rec = record("채식주의자", ["한강"], tags=["소설", "소설 ", "Fiction", "fiction", "a,b", ""])
    assert mf._new_value(rec, "tags") == "소설,Fiction,a b"
