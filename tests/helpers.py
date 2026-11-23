# -*- coding: utf-8 -*-
# F9: 테스트 준비 코드에서 쓰는 함수 모음 (#11)
# fixture는 conftest.py에 있고, 여기에는 fixture가 부르는 보통 함수만 둔다.
import hashlib
import json
import os
import re
import shutil
import sqlite3
import uuid

import requests

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EMPTY_LIBRARY_DB = os.path.join(REPO_DIR, "library", "metadata.db")

# ub.create_admin_user()가 처음 만드는 기본 계정 (TEAM.md "로컬 실행" 참고)
ADMIN_USER = "admin"
ADMIN_PASSWORD = "admin123"

# (제목, 저자, 이미 채워진 출판사) — F6 실행 시험에 쓴 서재와 같은 구성
SAMPLE_BOOKS = [
    ("채식주의자", "한강", None),
    ("소년이 온다", "한강", "테스트출판사"),
    ("82년생 김지영", "조남주", None),
    ("아몬드", "손원평", None),
    ("미움받을 용기", "기시미 이치로", None),
    ("존재하지않는책제목xyzqw", "무명저자", None),
]


# ---------------------------------------------------------------------------
# 외부 API 흉내
# ---------------------------------------------------------------------------

class FakeResponse:
    """requests.Response 중 제공처 코드가 쓰는 부분만 흉내 낸다."""

    def __init__(self, data=None, status=200, text=None):
        self._data = data
        self.status_code = status
        self.ok = status < 400
        if text is not None:
            self.text = text
        else:
            self.text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        self.content = self.text.encode("utf-8")

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError("{} Error".format(self.status_code), response=self)

    def json(self):
        if isinstance(self._data, (dict, list)):
            return self._data
        return json.loads(self.text)  # 문자열이면 진짜처럼 파싱 (JSON이 아니면 ValueError)


def patch_requests(monkeypatch, response):
    """requests.get·Session.get이 response를 돌려주게 한다. 요청 기록 리스트를 돌려준다.

    response가 예외 객체면 그 예외를 일으킨다 (시간 초과·연결 오류 흉내).
    """
    calls = []

    def fake_get(url, params=None, **kwargs):
        calls.append({"url": url, "params": params, "headers": kwargs.get("headers"),
                      "timeout": kwargs.get("timeout")})
        if isinstance(response, BaseException):
            raise response
        return response

    def fake_session_get(self, url, params=None, **kwargs):
        return fake_get(url, params, **kwargs)

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests.Session, "get", fake_session_get)
    return calls


# ---------------------------------------------------------------------------
# 녹화 응답 (scripts/record_responses.py가 만들고 테스트가 읽는다)
# ---------------------------------------------------------------------------

def recording_name(query):
    """검색어 -> 파일 이름. 한글은 그대로 두고 파일 이름에 못 쓰는 글자만 바꾼다."""
    name = re.sub(r'[\\/:*?"<>|\s]+', "_", query.strip()).strip("_")
    if not name or len(name) > 60:
        name = (name[:40] + "_" if name else "") + hashlib.sha1(query.encode("utf-8")).hexdigest()[:10]
    return name


def recording_path(data_dir, provider, query):
    return os.path.join(data_dir, provider, recording_name(query) + ".json")


def load_recording(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


SECRET_PARAM_NAMES = ("key", "api_key", "apikey", "cert_key", "certkey", "serviceKey", "token")
SECRET_HEADER_NAMES = ("authorization", "x-api-key")


def scrub_url(url):
    """URL 쿼리의 키 값을 지운다: ...&key=abc -> ...&key=REDACTED"""
    pattern = r"([?&](?:{})=)[^&#]*".format("|".join(SECRET_PARAM_NAMES))
    return re.sub(pattern, r"\1REDACTED", url or "", flags=re.IGNORECASE)


def scrub_params(params):
    if not params:
        return params
    return {k: ("REDACTED" if k.lower() in [n.lower() for n in SECRET_PARAM_NAMES] else v)
            for k, v in dict(params).items()}


def find_secrets(text, secrets):
    """text 안에 남아 있는 비밀값 목록 (녹화 파일 저장 전 마지막 검사)."""
    return [s for s in secrets if s and len(s) >= 6 and s in text]


# ---------------------------------------------------------------------------
# 시험용 Calibre 서재
# ---------------------------------------------------------------------------

def make_library(path, books):
    """빈 metadata.db를 복사해 books를 넣는다. {"path", "books": {제목: id}}를 돌려준다."""
    os.makedirs(path, exist_ok=True)
    db_path = os.path.join(path, "metadata.db")
    shutil.copy(EMPTY_LIBRARY_DB, db_path)
    conn = sqlite3.connect(db_path)
    # metadata.db의 트리거가 부르는 Calibre 함수 (실제 앱은 db.CalibreDB.create_functions가 등록)
    conn.create_function("title_sort", 1, lambda t: t)
    conn.create_function("uuid4", 0, lambda: str(uuid.uuid4()))
    ids = {}
    for title, author, publisher in books:
        conn.execute("INSERT OR IGNORE INTO authors(name, sort) VALUES (?, ?)", (author, author))
        author_id = conn.execute("SELECT id FROM authors WHERE name=?", (author,)).fetchone()[0]
        cur = conn.execute("INSERT INTO books(title, author_sort, pubdate, path) "
                           "VALUES (?, ?, '0101-01-01 00:00:00+00:00', '')", (title, author))
        book_id = cur.lastrowid
        book_path = "{}/{} ({})".format(author, title, book_id)
        conn.execute("UPDATE books SET path=? WHERE id=?", (book_path, book_id))
        os.makedirs(os.path.join(path, book_path), exist_ok=True)
        conn.execute("INSERT INTO books_authors_link(book, author) VALUES (?, ?)", (book_id, author_id))
        if publisher:
            conn.execute("INSERT OR IGNORE INTO publishers(name, sort) VALUES (?, ?)", (publisher, publisher))
            pub_id = conn.execute("SELECT id FROM publishers WHERE name=?", (publisher,)).fetchone()[0]
            conn.execute("INSERT INTO books_publishers_link(book, publisher) VALUES (?, ?)", (book_id, pub_id))
        ids[title] = book_id
    conn.commit()
    conn.close()
    return {"path": path, "books": ids}


def use_library(path):
    """앱이 path의 서재를 쓰게 한다. 이전 서재 경로를 돌려준다 (되돌릴 때 사용)."""
    from cps import cli_param, config, db
    previous = config.config_calibre_dir
    config.config_calibre_dir = path
    config.db_configured = bool(path)
    db.CalibreDB.update_config(config, path, cli_param.settings_path)
    return previous


# ---------------------------------------------------------------------------
# 앱
# ---------------------------------------------------------------------------

def create_test_app():
    """cps.main.main()과 같은 순서로 앱을 만들되 웹 서버는 띄우지 않는다."""
    import sys
    saved_argv = sys.argv
    sys.argv = ["cps.py"]  # cli_param이 pytest의 인자를 읽지 않도록
    try:
        import cps
        from cps import limiter
        app = cps.create_app()
    finally:
        sys.argv = saved_argv

    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, RATELIMIT_ENABLED=False)
    if limiter is not None:
        limiter.enabled = False  # 로그인 3회/분 제한 때문에 테스트끼리 막히지 않도록

    from cps.web import web
    from cps.basic import basic
    from cps.admin import admi
    from cps.editbooks import editbook
    from cps.about import about
    from cps.search import search
    from cps.search_metadata import meta
    from cps.shelf import shelf
    from cps.tasks_status import tasks
    from cps.jinjia import jinjia
    from cps.error_handler import init_errorhandler
    init_errorhandler()
    for blueprint in (search, tasks, web, basic, jinjia, about, shelf, admi, meta, editbook):
        if blueprint.name not in app.blueprints:
            app.register_blueprint(blueprint)
    return app


def login(test_client, username, password):
    response = test_client.post("/login", data={"username": username, "password": password},
                                follow_redirects=False)
    assert response.status_code in (302, 303), "로그인 실패: {}".format(response.status_code)
    return response
