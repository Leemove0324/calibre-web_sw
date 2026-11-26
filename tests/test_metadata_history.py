# -*- coding: utf-8 -*-
"""F7 (#9): 일괄 가져오기 변경 이력과 되돌리기 — cps/services/metadata_history.py와 그 화면·라우트.

지표 M9 "되돌리기 후 원래 값과 100% 일치"를 여기서 확인한다 (test_revert_restores_everything_m9).
외부 제공처는 부르지 않는다. 검색 결과와 표지 내려받기는 가짜로 대신한다.
"""
import collections
import json
import os
import uuid

import pytest

from cps.services.Metadata import MetaRecord, MetaSourceInfo


def record(title, authors, source="fake", **fields):
    r = MetaRecord(id=title, title=title, authors=authors, url="",
                   source=MetaSourceInfo(id=source, description=source, link=""))
    r.cover = fields.pop("cover", "")
    for key, value in fields.items():
        setattr(r, key, value)
    return r


FULL = dict(publisher="창비", publishedDate="2007-10-30", description="<p>세 편의 연작 소설</p><script>x()</script>",
            identifiers={"isbn": "978-89-364-3374-5"}, tags=["소설", "한국소설", "소설"],
            cover="https://example.invalid/cover.jpg")


@pytest.fixture
def mh():
    from cps.services import metadata_history
    return metadata_history


@pytest.fixture
def mf():
    from cps.tasks import metadata_fetch
    return metadata_fetch


@pytest.fixture
def covers(monkeypatch, app, calibre_library):
    """표지 내려받기 흉내: cover.jpg에 URL을 담은 바이트를 쓴다. 내려받은 URL 목록을 돌려준다."""
    from cps import config, helper
    downloaded = []

    def save_cover_from_url(url, book_path):
        downloaded.append(url)
        with open(os.path.join(config.get_book_path(), book_path, "cover.jpg"), "wb") as f:
            f.write(("JPEG " + url).encode("utf-8"))
        return True, None
    monkeypatch.setattr(helper, "save_cover_from_url", save_cover_from_url)
    monkeypatch.setattr(helper, "replace_cover_thumbnail_cache", lambda book_id: None)
    return downloaded


@pytest.fixture
def library_db(app, calibre_library):
    """서재를 새 세션으로 열어 본다: with library_db() as (calibre_db, session): ..."""
    import contextlib
    from cps import config, db

    @contextlib.contextmanager
    def open_db():
        with app.app_context():
            calibre_db = db.CalibreDB(app)
            calibre_db.create_functions(config)
            try:
                yield calibre_db, calibre_db.session
            finally:
                calibre_db.session.close()
    return open_db


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def cover_file(book):
    from cps import config
    return os.path.join(config.get_book_path(), book.path, "cover.jpg")


def snapshot(session, book_id):
    """한 권의 메타데이터를 비교할 수 있는 형태로 (M9 확인용)."""
    from cps import db
    from cps.tasks import metadata_fetch as mf
    book = session.query(db.Books).filter(db.Books.id == book_id).one()
    path = cover_file(book)
    cover = read_bytes(path) if os.path.isfile(path) else None
    return {
        "title": book.title,
        "authors": [a.name for a in book.authors],
        "publishers": sorted(p.name for p in book.publishers),
        "pubdate": mf.current_value(book, "pubdate"),
        "comments_rows": len(book.comments),
        "comments": mf.current_value(book, "comments"),
        "identifiers": sorted((i.type, i.val) for i in book.identifiers),
        "tags": sorted(t.name for t in book.tags),
        "has_cover": bool(book.has_cover),
        "cover": cover,
    }


def run_task(mf, mh, book_ids, search, user_id=1, **kwargs):
    task = mf.TaskFetchMetadata(book_ids, user_id=user_id, search=search,
                                recorder_factory=mh.make_recorder_factory(), **kwargs)
    task.run(None)
    return task


def search_full(item):
    return [(record(item["title"], item["authors"], **FULL), 0.97)]


# ---------------------------------------------------------------------------
# 기록
# ---------------------------------------------------------------------------

def test_task_records_history_before_change(mf, mh, covers, calibre_library):
    ids = calibre_library["books"]
    task = run_task(mf, mh, [ids["채식주의자"]], search_full)
    assert task.results[0]["result"] == "applied"

    rows = mh.task_history(str(task.id))
    assert [r.field for r in rows] == ["publisher", "pubdate", "comments", "isbn", "tags", "cover"]
    assert all(r.old_value == "" and not r.reverted for r in rows)
    assert all(r.user_id == 1 and r.book_id == ids["채식주의자"] for r in rows)
    assert all(r.source == "fake" and r.score == pytest.approx(0.97) for r in rows)
    by_field = {r.field: r for r in rows}
    # confirm(): metadata.db에 실제로 들어간 모양으로 저장 (HTML 정리, 중복 태그, ISBN 하이픈)
    assert "<script>" not in by_field["comments"].new_value and "연작 소설" in by_field["comments"].new_value
    assert sorted(by_field["tags"].new_value.split(",")) == ["소설", "한국소설"]
    assert by_field["isbn"].new_value == "9788936433745"
    assert by_field["cover"].new_value == "https://example.invalid/cover.jpg"
    assert mh.task_owner(str(task.id)) == 1
    assert covers == ["https://example.invalid/cover.jpg"]


def test_nothing_recorded_for_held_back_books(mf, mh, calibre_library):
    ids = calibre_library["books"]

    def search(item):
        if item["title"] == "82년생 김지영":
            return [(record("비슷한 책", ["누구"], publisher="X"), 0.4)]  # 기준 미달 → 검토 대기
        return []
    task = run_task(mf, mh, [ids["82년생 김지영"], ids["존재하지않는책제목xyzqw"]], search)
    assert [e["result"] for e in task.results] == ["pending", "no_match"]
    assert mh.task_history(str(task.id)) == [] and mh.task_owner(str(task.id)) is None


def test_cancel_drops_row_when_write_changes_nothing(mf, mh, monkeypatch, calibre_library):
    from cps import helper
    monkeypatch.setattr(helper, "save_cover_from_url", lambda url, path: (False, "404"))
    ids = calibre_library["books"]

    def search(item):
        return [(record(item["title"], item["authors"], publisher="창비", cover="https://example.invalid/x.jpg"), 0.99)]
    task = run_task(mf, mh, [ids["아몬드"]], search)
    assert task.results[0]["fields"] == ["publisher"]
    assert [r.field for r in mh.task_history(str(task.id))] == ["publisher"]  # 표지 기록은 지워짐


def test_discard_when_commit_fails(mf, mh, covers, monkeypatch, calibre_library, library_db):
    from cps import db
    ids = calibre_library["books"]
    with library_db() as (_, session):
        before = snapshot(session, ids["아몬드"])

    def broken(self, book_id):
        raise RuntimeError("metadata.db 잠김")
    monkeypatch.setattr(db.CalibreDB, "set_metadata_dirty", broken)
    task = run_task(mf, mh, [ids["아몬드"]], search_full)

    assert task.results[0]["result"] == "failed" and "잠김" in task.results[0]["error"]
    assert mh.task_history(str(task.id)) == []        # 기록도 같이 지움
    with library_db() as (_, session):
        assert snapshot(session, ids["아몬드"]) == before  # 내려받은 표지 파일까지 원래대로


def test_recording_failure_skips_field(mf, mh, calibre_library, library_db):
    """기록을 못 하면 그 칸은 바꾸지 않는다 (기록 없는 변경 금지)."""
    ids = calibre_library["books"]

    class Broken(mh.Recorder):
        def __call__(self, book, field, old, new):
            if field == "comments":
                raise RuntimeError("app.db 잠김")
            super().__call__(book, field, old, new)

    shared = mh.make_recorder_factory()

    def factory(task_id, user_id, source="", score=None):
        return Broken(task_id, user_id, source=source, score=score, session=shared(task_id, user_id).session)
    factory.close = shared.close
    task = mf.TaskFetchMetadata([ids["아몬드"]], user_id=1, recorder_factory=factory,
                                search=lambda item: [(record("아몬드", ["손원평"], publisher="창비",
                                                             description="소개"), 0.99)])
    task.run(None)
    assert task.results[0]["fields"] == ["publisher"]
    assert [r.field for r in mh.task_history(str(task.id))] == ["publisher"]
    with library_db() as (_, session):
        assert snapshot(session, ids["아몬드"])["comments"] == ""


# ---------------------------------------------------------------------------
# 되돌리기
# ---------------------------------------------------------------------------

def test_revert_restores_everything_m9(mf, mh, covers, calibre_library, library_db):
    """M9: 여러 권을 모든 칸에 걸쳐 바꾼 뒤 되돌리면 원래 값과 100% 같다."""
    ids = calibre_library["books"]
    titles = ["채식주의자", "소년이 온다", "아몬드", "미움받을 용기"]  # 소년이 온다는 출판사가 이미 있음
    with library_db() as (_, session):
        before = {t: snapshot(session, ids[t]) for t in titles}

    task = run_task(mf, mh, [ids[t] for t in titles], search_full)
    assert [e["result"] for e in task.results] == ["applied"] * 4
    with library_db() as (_, session):
        changed = {t: snapshot(session, ids[t]) for t in titles}
    assert all(changed[t] != before[t] for t in titles)
    assert changed["소년이 온다"]["publishers"] == ["테스트출판사"]  # 원래 있던 값은 덮어쓰지 않음

    with library_db() as (calibre_db, _):
        summary = mh.revert_task(str(task.id), calibre_db)
    rows = mh.task_history(str(task.id))
    assert summary["reverted"] == len(rows) == 4 * 6 - 1 and summary["skipped"] == summary["failed"] == 0
    assert all(r.reverted for r in rows)
    with library_db() as (_, session):
        after = {t: snapshot(session, ids[t]) for t in titles}
    assert after == before  # 100% 일치


def test_revert_twice_is_harmless(mf, mh, covers, calibre_library, library_db):
    ids = calibre_library["books"]
    task = run_task(mf, mh, [ids["채식주의자"]], search_full)
    with library_db() as (calibre_db, _):
        first = mh.revert_task(str(task.id), calibre_db)
    with library_db() as (calibre_db, session):
        second = mh.revert_task(str(task.id), calibre_db)
        state = snapshot(session, ids["채식주의자"])
    assert first["reverted"] == 6 and second["reverted"] == 0 and second["rows"] == []
    assert state["publishers"] == [] and state["tags"] == [] and state["cover"] is None


def test_revert_leaves_later_edits_alone(mf, mh, covers, calibre_library, library_db):
    """작업 뒤 사람이 직접 고친 칸은 되돌리지 않고 '건너뜀'으로 알린다."""
    from cps import db
    from cps.editbooks import modify_database_object
    ids = calibre_library["books"]
    task = run_task(mf, mh, [ids["채식주의자"]], search_full)

    with library_db() as (_, session):  # 사람이 출판사와 표지를 바꿈
        book = session.query(db.Books).filter(db.Books.id == ids["채식주의자"]).one()
        modify_database_object(["문학동네"], book.publishers, db.Publishers, session, "publisher")
        session.commit()
        with open(cover_file(book), "wb") as f:
            f.write(b"JPEG my own cover")

    with library_db() as (calibre_db, session):
        summary = mh.revert_task(str(task.id), calibre_db)
        state = snapshot(session, ids["채식주의자"])
    results = {r["field"]: r["result"] for r in summary["rows"]}
    assert results == {"publisher": "skipped", "cover": "skipped", "pubdate": "reverted",
                       "comments": "reverted", "isbn": "reverted", "tags": "reverted"}
    assert state["publishers"] == ["문학동네"] and state["cover"] == b"JPEG my own cover"
    assert state["tags"] == [] and state["pubdate"] == ""
    # 건너뛴 칸은 되돌리지 않은 상태로 남아, 다음 되돌리기에서도 다시 건너뜀
    left = [r.field for r in mh.task_history(str(task.id)) if not r.reverted]
    assert sorted(left) == ["cover", "publisher"]
    with library_db() as (calibre_db, _):
        again = mh.revert_task(str(task.id), calibre_db)
    assert again["skipped"] == 2 and again["reverted"] == 0


def test_view_marks_fields_edited_later(mf, mh, covers, admin_client, calibre_library, worker_tasks):
    """사람이 나중에 고친 칸은 '나중에 고침, 유지'로 보이고, 되돌릴 것이 없으면 버튼이 사라진다."""
    from cps import db
    from cps.editbooks import modify_database_object
    ids = calibre_library["books"]
    task = run_task(mf, mh, [ids["아몬드"]],
                    lambda item: [(record("아몬드", ["손원평"], publisher="창비", description="소개"), 0.99)])
    with admin_client.application.app_context():
        session = db.CalibreDB(admin_client.application).session
        db.CalibreDB.create_functions(type("H", (), {"session": session})(), __import__("cps").config)
        book = session.query(db.Books).filter(db.Books.id == ids["아몬드"]).one()
        modify_database_object(["문학동네"], book.publishers, db.Publishers, session, "publisher")
        session.commit()
        session.close()
    html = admin_client.get("/metadata/bulk/{}/view".format(task.id)).get_data(as_text=True)
    assert html.count("bulk-kept") == 1 and 'id="bulk_revert"' in html  # 소개는 아직 되돌릴 수 있음
    admin_client.post("/metadata/bulk/{}/revert".format(task.id))
    html = admin_client.get("/metadata/bulk/{}/view".format(task.id)).get_data(as_text=True)
    assert html.count("bulk-kept") == 1 and 'id="bulk_revert"' not in html  # 남은 것은 유지된 칸뿐


def test_tags_compare_ignores_order(mh):
    assert mh._same_value("tags", "한국소설,소설", "소설,한국소설")
    assert not mh._same_value("tags", "소설", "소설,한국소설")
    assert mh._same_value("publisher", "", None)
    # 일반 편집 화면에서 저장하면 소개의 HTML 모양만 바뀐다 (<b> → <strong>): 사람이 고친 것이 아님
    assert mh._same_value("comments", "<p>외계 <strong>문명</strong>&amp;접촉</p>", "<p>외계 <b>문명</b>&amp;접촉</p>")
    assert mh._same_value("comments", "<p>한 줄</p>\n", "<p>한  줄</p>")
    assert not mh._same_value("comments", "<p>사람이 고친 소개</p>", "<p>원래 소개</p>")


def test_existing_cover_backed_up_and_restored(mf, mh, covers, calibre_library, library_db):
    """이미 표지가 있는 책을 덮어쓴 경우 (only_empty=False): 백업한 표지로 되돌린다."""
    from cps import db
    ids = calibre_library["books"]
    task_id = "test-" + uuid.uuid4().hex
    with library_db() as (calibre_db, session):
        book = session.query(db.Books).filter(db.Books.id == ids["아몬드"]).one()
        with open(cover_file(book), "wb") as f:
            f.write(b"JPEG original")
        book.has_cover = 1
        session.commit()
        before = snapshot(session, book.id)

        factory = mh.make_recorder_factory()
        recorder = factory(task_id, 1, source="fake", score=1.0)
        changed = mf.apply_record(session, book, record("아몬드", ["손원평"], cover="https://example.invalid/new.jpg"),
                                  fields=("cover",), only_empty=False, recorder=recorder)
        session.commit()
        recorder.confirm(book)
        assert changed == ["cover"] and read_bytes(cover_file(book)) != b"JPEG original"
        backup = mh.task_history(task_id)[0].old_value
        assert os.path.isfile(backup) and read_bytes(backup) == b"JPEG original"
        assert not backup.startswith(calibre_library["path"])  # 백업은 서재 밖 (app.db 옆)
        factory.close()
        assert factory.session is None

    with library_db() as (calibre_db, session):
        assert mh.revert_task(task_id, calibre_db)["reverted"] == 1
        assert snapshot(session, ids["아몬드"]) == before


def test_revert_missing_book_and_failure(mf, mh, monkeypatch, calibre_library, library_db):
    from cps import ub
    ids = calibre_library["books"]
    task_id = "test-" + uuid.uuid4().hex
    session = ub.get_new_session_instance()
    session.add(ub.MetadataFetchHistory(task_id=task_id, user_id=1, book_id=99999, field="publisher",
                                        old_value="", new_value="창비", reverted=False))
    session.commit()
    task = run_task(mf, mh, [ids["아몬드"]],
                    lambda item: [(record("아몬드", ["손원평"], publisher="창비"), 0.99)])
    for row in mh.task_history(str(task.id)):  # 같은 작업으로 묶기
        row_obj = session.get(ub.MetadataFetchHistory, row.id)
        row_obj.task_id = task_id
    session.commit()

    def broken(*args, **kwargs):
        raise RuntimeError("쓰기 실패")
    monkeypatch.setattr(mf, "_write_field", broken)
    with library_db() as (calibre_db, _):
        summary = mh.revert_task(task_id, calibre_db)
    assert (summary["missing"], summary["failed"], summary["reverted"]) == (1, 1, 0)
    monkeypatch.undo()
    with library_db() as (calibre_db, session2):
        retry = mh.revert_task(task_id, calibre_db)  # 실패한 칸은 다음에 다시 시도된다
        assert snapshot(session2, ids["아몬드"])["publishers"] == []
    assert (retry["reverted"], retry["missing"]) == (1, 0)
    session.remove()
    session.get_bind().dispose()


def test_list_tasks(mf, mh, covers, calibre_library):
    ids = calibre_library["books"]
    task = run_task(mf, mh, [ids["채식주의자"], ids["아몬드"]], search_full, user_id=1)
    mine = [t for t in mh.list_tasks(1) if t["task_id"] == str(task.id)]
    assert len(mine) == 1 and mine[0]["books"] == 2 and mine[0]["changes"] == 12 and mine[0]["reverted"] == 0
    assert mine[0]["applied_at"] is not None
    assert all(t["task_id"] != str(task.id) for t in mh.list_tasks(424242))


# ---------------------------------------------------------------------------
# 화면과 라우트
# ---------------------------------------------------------------------------

Queued = collections.namedtuple("Queued", "task user")


@pytest.fixture
def worker_tasks(monkeypatch):
    """WorkerThread가 아는 작업 목록을 바꿔 끼운다."""
    import cps.search_metadata as sm
    tasks = []
    monkeypatch.setattr(sm.WorkerThread, "get_instance", staticmethod(lambda: type("W", (), {"tasks": tasks})()))
    return tasks


@pytest.fixture
def other_client(app):
    """편집 권한이 있는 두 번째 사용자 (관리자 아님)."""
    from werkzeug.security import generate_password_hash
    from cps import constants, ub
    from tests import helpers
    name = "editor-" + uuid.uuid4().hex[:8]
    user = ub.User(name=name, email=name + "@example.invalid", password=generate_password_hash("pw-12345"),
                   role=constants.ROLE_EDIT | constants.ROLE_DOWNLOAD, view_settings={})
    ub.session.add(user)
    ub.session.commit()
    client = app.test_client()
    helpers.login(client, name, "pw-12345")
    client.user_id = user.id
    return client


def test_view_page_shows_per_book_results(mf, mh, covers, admin_client, calibre_library, worker_tasks):
    ids = calibre_library["books"]

    def search(item):
        if item["title"] == "82년생 김지영":
            return [(record("비슷한 책", ["누구"]), 0.4)]
        if item["title"] == "미움받을 용기":
            raise RuntimeError("timeout")
        return search_full(item)
    task = run_task(mf, mh, [ids["채식주의자"], ids["82년생 김지영"], ids["미움받을 용기"]], search)
    worker_tasks.append(Queued(task, "admin"))

    resp = admin_client.get("/metadata/bulk/{}/view".format(task.id))
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "채식주의자" in html and "82년생 김지영" in html and "미움받을 용기" in html
    assert 'data-result="applied"' in html and 'data-result="pending"' in html and 'data-result="failed"' in html
    assert "timeout" in html and "창비" in html and 'id="bulk_revert"' in html
    assert 'id="bulk_counts"' in html


def test_view_page_from_history_only(mf, mh, covers, admin_client, calibre_library, worker_tasks):
    """서버를 다시 켜서 작업이 메모리에 없어도 이력으로 보여 준다."""
    ids = calibre_library["books"]
    task = run_task(mf, mh, [ids["채식주의자"]], search_full)
    html = admin_client.get("/metadata/bulk/{}/view".format(task.id)).get_data(as_text=True)
    assert "채식주의자" in html and 'id="bulk_only_history"' in html and 'id="bulk_revert"' in html


def test_revert_route(mf, mh, covers, admin_client, calibre_library, worker_tasks, library_db):
    ids = calibre_library["books"]
    with library_db() as (_, session):
        before = snapshot(session, ids["채식주의자"])
    task = run_task(mf, mh, [ids["채식주의자"]], search_full)
    resp = admin_client.post("/metadata/bulk/{}/revert".format(task.id))
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["task_id"] == str(task.id) and data["reverted"] == 6 and data["skipped"] == 0
    with library_db() as (_, session):
        assert snapshot(session, ids["채식주의자"]) == before
    html = admin_client.get("/metadata/bulk/{}/view".format(task.id)).get_data(as_text=True)
    assert 'id="bulk_revert"' not in html and "Undone" in html  # 다 되돌린 뒤에는 버튼이 없다


def test_revert_refused_while_running(mf, mh, admin_client, calibre_library, worker_tasks):
    from cps.services.worker import STAT_STARTED
    task = mf.TaskFetchMetadata([1], user_id=1, search=lambda item: [])
    task.stat = STAT_STARTED
    worker_tasks.append(Queued(task, "admin"))
    assert admin_client.post("/metadata/bulk/{}/revert".format(task.id)).status_code == 409
    html = admin_client.get("/metadata/bulk/{}/view".format(task.id)).get_data(as_text=True)
    assert 'id="bulk_running"' in html and 'id="bulk_revert"' not in html and 'id="bulk_empty"' in html


def test_routes_404_for_unknown_task(admin_client, calibre_library, worker_tasks):
    assert admin_client.get("/metadata/bulk/nope/view").status_code == 404
    assert admin_client.post("/metadata/bulk/nope/revert").status_code == 404


def test_routes_only_for_owner_or_admin(mf, mh, covers, admin_client, other_client, calibre_library,
                                        worker_tasks):
    ids = calibre_library["books"]
    admins = run_task(mf, mh, [ids["채식주의자"]], search_full, user_id=1)
    assert other_client.get("/metadata/bulk/{}/view".format(admins.id)).status_code == 403
    assert other_client.post("/metadata/bulk/{}/revert".format(admins.id)).status_code == 403
    worker_tasks.append(Queued(admins, "admin"))
    assert other_client.get("/metadata/bulk/{}/view".format(admins.id)).status_code == 403

    mine = run_task(mf, mh, [ids["아몬드"]], search_full, user_id=other_client.user_id)
    assert other_client.get("/metadata/bulk/{}/view".format(mine.id)).status_code == 200
    assert admin_client.get("/metadata/bulk/{}/view".format(mine.id)).status_code == 200  # 관리자는 모두
    assert other_client.post("/metadata/bulk/{}/revert".format(mine.id)).get_json()["reverted"] == 6

    history = other_client.get("/metadata/bulk/history").get_data(as_text=True)
    assert str(mine.id) in history and str(admins.id) not in history
    admin_history = admin_client.get("/metadata/bulk/history").get_data(as_text=True)
    assert str(mine.id) in admin_history and str(admins.id) in admin_history


def test_revert_needs_edit_role(mf, mh, covers, app, calibre_library, worker_tasks):
    from werkzeug.security import generate_password_hash
    from cps import constants, ub
    from tests import helpers
    name = "reader-" + uuid.uuid4().hex[:8]
    user = ub.User(name=name, email=name + "@example.invalid", password=generate_password_hash("pw-12345"),
                   role=constants.ROLE_DOWNLOAD, view_settings={})
    ub.session.add(user)
    ub.session.commit()
    client = app.test_client()
    helpers.login(client, name, "pw-12345")
    task = run_task(mf, mh, [calibre_library["books"]["채식주의자"]], search_full, user_id=user.id)
    assert client.post("/metadata/bulk/{}/revert".format(task.id)).status_code == 403
    html = client.get("/metadata/bulk/{}/view".format(task.id)).get_data(as_text=True)
    assert 'id="bulk_revert"' not in html  # 볼 수는 있지만 되돌리기 버튼은 없다


def test_history_page_empty(app, calibre_library):
    from werkzeug.security import generate_password_hash
    from cps import constants, ub
    from tests import helpers
    name = "new-" + uuid.uuid4().hex[:8]
    ub.session.add(ub.User(name=name, email=name + "@example.invalid", password=generate_password_hash("pw-12345"),
                           role=constants.ROLE_EDIT, view_settings={}))
    ub.session.commit()
    client = app.test_client()
    helpers.login(client, name, "pw-12345")
    resp = client.get("/metadata/bulk/history")
    assert resp.status_code == 200 and 'id="bulk_history_empty"' in resp.get_data(as_text=True)


def test_bulk_route_turns_on_history(admin_client, calibre_library, monkeypatch):
    import cps.search_metadata as sm
    added = []
    monkeypatch.setattr(sm.WorkerThread, "add", staticmethod(lambda user, task: added.append(task)))
    resp = admin_client.post("/metadata/bulk", data=json.dumps({"book_ids": [1]}), content_type="application/json")
    assert resp.status_code == 202 and added[0].recorder_factory is not None


def test_book_table_links_history(admin_client, calibre_library):
    html = admin_client.get("/table").get_data(as_text=True)
    assert 'id="metadata_fetch_history"' in html and "/metadata/bulk/history" in html
