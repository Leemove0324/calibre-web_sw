# -*- coding: utf-8 -*-
# F9: 모든 테스트가 같이 쓰는 준비 코드 (#11)
#
# 실행 (저장소 맨 위 폴더에서):
#     python -m pytest
#
# cps 패키지는 import하는 순간 설정·로그 파일 위치를 정하므로, 그보다 먼저
# CALIBRE_DBPATH를 임시 폴더로 돌려 저장소에 app.db·로그가 생기지 않게 한다.
import os
import shutil
import sys
import tempfile

import pytest

_CONFIG_DIR = tempfile.mkdtemp(prefix="cw-tests-")
os.environ["CALIBRE_DBPATH"] = _CONFIG_DIR

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_DIR not in sys.path:
    sys.path.insert(0, REPO_DIR)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

from tests import helpers  # noqa: E402  (CALIBRE_DBPATH를 먼저 정해야 함)


def pytest_addoption(parser):
    parser.addoption("--run-live", action="store_true", default=False,
                     help="실제 외부 API를 부르는 @pytest.mark.live 테스트도 실행 (키는 환경 변수로)")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-live"):
        return
    skip_live = pytest.mark.skip(reason="실제 API 테스트: --run-live 를 붙여야 실행")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


def pytest_sessionfinish(session, exitstatus):
    # create_app()이 띄운 업데이트 확인 스레드는 데몬이 아니라서 멈추지 않으면 pytest가 끝나지 않는다
    if "cps" in sys.modules:
        from cps import updater_thread
        updater_thread.stop()
        if updater_thread.is_alive():
            updater_thread.join(timeout=5)
    shutil.rmtree(_CONFIG_DIR, ignore_errors=True)


# ---------------------------------------------------------------------------
# 외부 API 흉내
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_response():
    """fake_response(data, status=200, text=None) -> requests.Response 흉내."""
    return helpers.FakeResponse


@pytest.fixture
def recorded():
    """recorded("kakao", "소년이 온다") -> 녹화된 응답 본문. 파일이 없으면 그 테스트만 건너뛴다."""
    def load(provider, query):
        path = helpers.recording_path(DATA_DIR, provider, query)
        if not os.path.exists(path):
            pytest.skip("녹화 응답이 없음: {} (scripts/record_responses.py로 만든다)".format(
                os.path.relpath(path, REPO_DIR)))
        return helpers.load_recording(path)["body"]
    return load


@pytest.fixture
def replay(monkeypatch, recorded):
    """replay("kakao", "소년이 온다") -> 그 뒤의 requests.get/Session.get이 녹화 응답을 돌려준다.

    돌려받은 리스트에 실제로 요청된 (url, params)가 쌓인다.
    """
    def install(provider, query, status=200):
        body = recorded(provider, query)
        return helpers.patch_requests(monkeypatch, helpers.FakeResponse(body, status=status))
    return install


# ---------------------------------------------------------------------------
# 앱·서재
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def app():
    """라우트까지 등록된 Flask 앱 (테스트 세션 동안 하나). 서버는 띄우지 않는다."""
    return helpers.create_test_app()


@pytest.fixture
def calibre_library(app, tmp_path):
    """한국 책 몇 권이 든 임시 Calibre 서재를 앱에 연결한다. 테스트마다 새로 만든다.

    반환값: {"path": 서재 폴더, "books": {제목: 책 id}}
    """
    library = helpers.make_library(str(tmp_path / "library"), helpers.SAMPLE_BOOKS)
    previous = helpers.use_library(library["path"])
    yield library
    helpers.use_library(previous)


@pytest.fixture
def calibre_session(app, calibre_library):
    """임시 서재의 metadata.db 세션 (app context 안에서). 책을 고치고 commit할 수 있다.

    books의 INSERT/UPDATE 트리거가 Calibre SQL 함수(title_sort·uuid4)를 부르므로,
    실제 앱(editbooks.do_edit_book, TaskFetchMetadata)처럼 연결에 함수를 등록해 둔다.
    """
    from cps import config, db
    with app.app_context():
        calibre_db = db.CalibreDB(app)
        session = calibre_db.session
        calibre_db.create_functions(config)
        yield session
        session.close()


@pytest.fixture
def client(app):
    """로그인하지 않은 테스트 클라이언트."""
    return app.test_client()


@pytest.fixture
def admin_client(app):
    """기본 관리자(admin)로 로그인한 테스트 클라이언트.

    서재가 연결되지 않은 앱은 거의 모든 요청을 서재 설정 화면(/admin/dbconfig)으로 보내므로,
    책·메타데이터 화면을 시험할 때는 calibre_library fixture도 같이 쓴다.
    """
    test_client = app.test_client()
    helpers.login(test_client, helpers.ADMIN_USER, helpers.ADMIN_PASSWORD)
    return test_client
