# -*- coding: utf-8 -*-
# F9 (#11): 테스트 준비 코드(conftest·helpers)와 F9 스크립트가 제대로 동작하는지 검사한다.
# 다른 기능의 테스트가 이 준비 코드를 믿고 쓰므로, 여기가 깨지면 먼저 고친다.
import json
import os
import sys

import pytest
import requests

from tests import helpers

REPO_DIR = helpers.REPO_DIR
sys.path.insert(0, os.path.join(REPO_DIR, "scripts"))
import check_coverage  # noqa: E402


# ---------- 격리: 테스트가 저장소에 파일을 만들지 않는다 ----------

def test_settings_live_in_temp_dir(app):
    from cps import cli_param, constants
    assert os.environ["CALIBRE_DBPATH"] == constants.CONFIG_DIR
    assert not os.path.abspath(constants.CONFIG_DIR).startswith(REPO_DIR)
    assert os.path.dirname(cli_param.settings_path) == constants.CONFIG_DIR


# ---------- 가짜 응답 ----------

def test_fake_response_json_and_status(fake_response):
    ok = fake_response({"documents": [1]})
    assert ok.json() == {"documents": [1]}
    ok.raise_for_status()

    bad = fake_response({"message": "no"}, status=429)
    with pytest.raises(requests.HTTPError):
        bad.raise_for_status()


def test_fake_response_non_json_body_raises_like_requests(fake_response):
    html = fake_response("<html>error</html>")
    with pytest.raises(ValueError):
        html.json()


def test_patch_requests_records_calls_and_can_raise(monkeypatch):
    calls = helpers.patch_requests(monkeypatch, helpers.FakeResponse({"a": 1}))
    assert requests.get("https://example.test/x", params={"q": "책"}, timeout=3).json() == {"a": 1}
    assert requests.Session().get("https://example.test/y").json() == {"a": 1}
    assert [c["url"] for c in calls] == ["https://example.test/x", "https://example.test/y"]
    assert calls[0]["params"] == {"q": "책"} and calls[0]["timeout"] == 3

    helpers.patch_requests(monkeypatch, requests.Timeout("느림"))
    with pytest.raises(requests.Timeout):
        requests.get("https://example.test/z")


# ---------- 녹화 응답 ----------

def test_recording_name_keeps_hangul_and_removes_bad_characters():
    assert helpers.recording_name("소년이 온다") == "소년이_온다"
    assert helpers.recording_name("a/b:c?") == "a_b_c"
    long_name = helpers.recording_name("가" * 100)
    assert len(long_name) <= 60
    assert long_name != helpers.recording_name("가" * 101)  # 잘려도 서로 다른 이름


def test_scrub_removes_keys_from_url_and_params():
    url = "https://www.googleapis.com/books/v1/volumes?q=abc&key=SECRET123"
    assert helpers.scrub_url(url).endswith("&key=REDACTED")
    assert helpers.scrub_params({"cert_key": "SECRET123", "kwd": "책"}) == {"cert_key": "REDACTED", "kwd": "책"}
    assert helpers.find_secrets('{"url": "...SECRET123"}', ["SECRET123", "", "abc"]) == ["SECRET123"]


def test_recorded_and_replay(tmp_path, monkeypatch, recorded, replay):
    data_dir = tmp_path / "data"
    path = helpers.recording_path(str(data_dir), "fakeprov", "소년이 온다")
    os.makedirs(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"provider": "fakeprov", "query": "소년이 온다", "body": {"documents": ["x"]}}, f,
                  ensure_ascii=False)
    import tests.conftest as conftest
    monkeypatch.setattr(conftest, "DATA_DIR", str(data_dir))

    assert recorded("fakeprov", "소년이 온다") == {"documents": ["x"]}
    calls = replay("fakeprov", "소년이 온다")
    assert requests.get("https://example.test").json() == {"documents": ["x"]}
    assert len(calls) == 1


def test_recorded_skips_when_missing(recorded):
    with pytest.raises(pytest.skip.Exception):
        recorded("없는제공처", "없는 책")


def test_record_script_saves_scrubbed_file(tmp_path, monkeypatch):
    import record_responses
    from cps.services.Metadata import Metadata

    class FakeProvider(Metadata):
        __name__ = "Fake"
        __id__ = "fakeprov"

        def search(self, query, generic_cover="", locale="en"):
            response = requests.get("https://api.example.test/search?key=TOPSECRET99",
                                    params={"query": query, "cert_key": "TOPSECRET99"})
            return [response.json()]

    monkeypatch.setattr(record_responses, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(record_responses, "load_provider", lambda _id: FakeProvider())
    monkeypatch.setenv("FAKE_API_KEY", "TOPSECRET99")
    helpers.patch_requests(monkeypatch, helpers.FakeResponse({"documents": [{"title": "소년이 온다"}]}))

    assert record_responses.main(["fakeprov", "소년이 온다"]) == 0
    saved_path = helpers.recording_path(str(tmp_path), "fakeprov", "소년이 온다")
    text = open(saved_path, encoding="utf-8").read()
    assert "TOPSECRET99" not in text
    saved = json.loads(text)
    assert saved["body"] == {"documents": [{"title": "소년이 온다"}]}
    assert saved["params"]["cert_key"] == "REDACTED"


def test_record_script_refuses_to_save_leaked_secret(tmp_path, monkeypatch):
    import record_responses
    monkeypatch.setattr(record_responses, "DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FAKE_API_KEY", "TOPSECRET99")
    exchanges = [{"url": "u", "params": None, "status": 200, "body": {"echo": "TOPSECRET99"}}]
    with pytest.raises(SystemExit):
        record_responses.save("fakeprov", "q", exchanges, [])
    assert not os.listdir(tmp_path)


# ---------- 시험용 서재와 앱 ----------

def test_calibre_library_has_sample_books(calibre_session, calibre_library):
    from cps import db
    titles = {b.title for b in calibre_session.query(db.Books).all()}
    assert titles == {title for title, _, _ in helpers.SAMPLE_BOOKS}

    book = calibre_session.get(db.Books, calibre_library["books"]["소년이 온다"])
    assert [a.name for a in book.authors] == ["한강"]
    assert [p.name for p in book.publishers] == ["테스트출판사"]
    assert os.path.isdir(os.path.join(calibre_library["path"], book.path))


def test_each_test_gets_a_fresh_library(calibre_library):
    assert calibre_library["path"].endswith("library")
    assert os.path.exists(os.path.join(calibre_library["path"], "metadata.db"))


def test_login_required_without_session(client):
    response = client.get("/metadata/provider")
    assert response.status_code in (302, 401)


def test_without_library_admin_is_sent_to_db_config(admin_client):
    response = admin_client.get("/metadata/provider")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/dbconfig")


def test_admin_client_reaches_metadata_provider_list(admin_client, calibre_library):
    response = admin_client.get("/metadata/provider")
    assert response.status_code == 200
    providers = response.get_json()
    # 어떤 제공처가 보이는지는 선택 패키지 설치 여부에 따라 다르다. google은 항상 있다.
    assert "google" in {p["id"] for p in providers}
    assert all({"name", "active", "id"} <= set(p) for p in providers)


def test_admin_client_sees_sample_books(admin_client, calibre_library):
    response = admin_client.get("/ajax/listbooks?offset=0&limit=50")
    assert response.status_code == 200
    titles = {row["title"] for row in response.get_json()["rows"]}
    assert "82년생 김지영" in titles


def test_wrong_password_is_rejected(client):
    response = client.post("/login", data={"username": "admin", "password": "wrong"})
    assert response.status_code == 401  # 로그인 화면을 다시 보여 줌 (리다이렉트 아님)
    assert client.get("/metadata/provider").status_code == 302


# ---------- M10 커버리지 검사 스크립트 ----------

def _report(files):
    return {"files": {name: {"summary": {"covered_lines": c, "num_statements": n,
                                         "percent_covered": 100.0 * c / n}}
                      for name, (c, n) in files.items()}}


def test_check_coverage_sums_existing_targets(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 2\n")
    rows, total = check_coverage.summarize(_report({"a.py": (9, 10), "b.py": (7, 10)}),
                                           ["a.py", "b.py", "not_yet.py"], repo_dir=str(tmp_path))
    assert total == pytest.approx(80.0)
    assert rows[2][4] == "아직 없음 (건너뜀)"


def test_check_coverage_counts_untested_file_as_zero(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "untested.py").write_text("# 메모\nx = 1\ny = 2\n", encoding="utf-8")
    rows, total = check_coverage.summarize(_report({"a.py": (2, 2)}), ["a.py", "untested.py"],
                                           repo_dir=str(tmp_path))
    assert total == pytest.approx(50.0)
    assert rows[1][4] == "테스트 없음"


def test_check_coverage_exit_codes(tmp_path, capsys):
    targets = tmp_path / "targets.txt"
    report = tmp_path / "coverage.json"

    targets.write_text("# 아직 아무것도 없음\nnot_there.py  # F99\n", encoding="utf-8")
    report.write_text(json.dumps(_report({})), encoding="utf-8")
    assert check_coverage.main([str(report), "--targets", str(targets)]) == 0

    targets.write_text("tests/helpers.py\n", encoding="utf-8")
    report.write_text(json.dumps(_report({"tests/helpers.py": (1, 10)})), encoding="utf-8")
    assert check_coverage.main([str(report), "--targets", str(targets)]) == 1
    report.write_text(json.dumps(_report({"tests/helpers.py": (9, 10)})), encoding="utf-8")
    assert check_coverage.main([str(report), "--targets", str(targets)]) == 0
    assert "통과" in capsys.readouterr().out
