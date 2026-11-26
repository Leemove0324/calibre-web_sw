# -*- coding: utf-8 -*-
"""F8 (#10): 관리자 설정 — 국내 제공처 키, 시간 제한, 일괄 가져오기 임계값.

완료 기준: 키를 넣거나 지우면 재시작 없이 제공처 활성 상태가 바로 바뀐다.
외부 API는 부르지 않는다 (requests는 가짜로 바꾼다).
"""
import json

import pytest
import requests
from sqlalchemy import text

from tests import helpers

KAKAO_KEY = "test-kakao-key-123456"
NLGO_KEY = "test-nlgo-key-654321"
FIELDS = {"config_kakao_api_key_e": "", "config_nlgo_api_key_e": "",
          "config_metadata_timeout": 5, "config_metadata_threshold": 0.85}


@pytest.fixture
def settings(app, monkeypatch):
    """F8 설정을 기본값으로 두고 시작하고, 테스트가 끝나면 원래대로 되돌린다."""
    from cps import config
    monkeypatch.delenv("KAKAO_REST_API_KEY", raising=False)
    monkeypatch.delenv("NLGO_CERT_KEY", raising=False)
    saved = {f: getattr(config, f) for f in FIELDS}
    for field, value in FIELDS.items():
        setattr(config, field, value)
    config.save()
    yield config
    for field, value in saved.items():
        setattr(config, field, value)
    config.save()


def save_config(client, **fields):
    """관리자 화면의 "저장"과 같은 요청. 기존 검사를 통과하도록 비밀번호 길이를 함께 보낸다."""
    data = {"config_password_min_length": "8"}
    data.update({k: str(v) for k, v in fields.items()})
    response = client.post("/admin/ajaxconfig", data=data)
    assert response.status_code == 200
    return response.get_json()


def errors(result):
    return [r["message"] for r in result.get("result", []) if r.get("type") == "danger"]


def providers(client):
    return {p["id"]: p for p in client.get("/metadata/provider").get_json()}


# ---------- 설정 값 읽기 (cps/services/metadata_settings.py) ----------

def test_defaults_without_any_setting(settings):
    from cps.services import metadata_settings as ms
    from cps.metadata_provider.kakao import Kakao
    assert ms.timeout() == 5 and ms.threshold() == pytest.approx(0.85)
    assert ms.api_key("kakao") == "" and ms.key_source("kakao") == ""
    assert Kakao().is_configured() is False


def test_saved_key_wins_over_environment(settings, monkeypatch):
    from cps.services import metadata_settings as ms
    monkeypatch.setenv("KAKAO_REST_API_KEY", "from-env")
    assert (ms.api_key("kakao"), ms.key_source("kakao")) == ("from-env", "env")
    settings.config_kakao_api_key_e = KAKAO_KEY
    assert (ms.api_key("kakao"), ms.key_source("kakao")) == (KAKAO_KEY, "config")


@pytest.mark.parametrize("value, expected", [(None, 5), ("x", 5), (0, 5), (12, 12)])
def test_timeout_falls_back_on_bad_values(settings, value, expected):
    from cps.services import metadata_settings as ms
    settings.config_metadata_timeout = value
    assert ms.timeout() == expected


@pytest.mark.parametrize("value, expected", [(None, 0.85), ("x", 0.85), (1.5, 0.85), (0.7, 0.7), (0, 0.0)])
def test_threshold_falls_back_on_bad_values(settings, value, expected):
    from cps.services import metadata_settings as ms
    settings.config_metadata_threshold = value
    assert ms.threshold() == pytest.approx(expected)


# ---------- 관리자 화면에서 저장 ----------

def test_admin_page_shows_new_fields_without_the_key(admin_client, calibre_library, settings):
    settings.config_kakao_api_key_e = KAKAO_KEY
    settings.save()
    html = admin_client.get("/admin/config").get_data(as_text=True)
    for field in ("config_kakao_api_key_e", "config_nlgo_api_key_e", "config_metadata_timeout",
                  "config_metadata_threshold", "config_kakao_api_key_e_delete"):
        assert 'name="{}"'.format(field) in html
    assert KAKAO_KEY not in html                       # 저장된 키는 화면에 다시 보내지 않는다
    assert "config_nlgo_api_key_e_delete" not in html  # 저장된 키가 없으면 삭제 칸도 없다


def test_key_is_stored_encrypted(admin_client, calibre_library, settings):
    assert errors(save_config(admin_client, config_kakao_api_key_e=KAKAO_KEY)) == []
    raw = settings._session.execute(text("SELECT config_kakao_api_key_e FROM settings")).scalar()
    assert raw and KAKAO_KEY.encode() not in (raw if isinstance(raw, bytes) else raw.encode())
    assert settings.config_kakao_api_key_e == KAKAO_KEY  # 읽을 때는 복호화된 값


def test_saving_and_removing_key_switches_provider_without_restart(admin_client, calibre_library, settings):
    """F8 완료 기준: 키 입력·해제 시 제공처 활성 상태 즉시 반영."""
    assert providers(admin_client)["kakao"]["configured"] is False

    save_config(admin_client, config_kakao_api_key_e=KAKAO_KEY)
    assert providers(admin_client)["kakao"]["configured"] is True
    assert providers(admin_client)["nlgo"]["configured"] is False

    # 빈칸으로 저장하면 기존 키를 유지한다
    save_config(admin_client, config_kakao_api_key_e="")
    assert settings.config_kakao_api_key_e == KAKAO_KEY

    save_config(admin_client, config_kakao_api_key_e="", config_kakao_api_key_e_delete="on")
    assert settings.config_kakao_api_key_e == ""
    assert providers(admin_client)["kakao"]["configured"] is False


def test_timeout_and_threshold_are_saved(admin_client, calibre_library, settings):
    assert errors(save_config(admin_client, config_metadata_timeout=9, config_metadata_threshold=0.7)) == []
    assert settings.config_metadata_timeout == 9
    assert settings.config_metadata_threshold == pytest.approx(0.7)


@pytest.mark.parametrize("fields", [
    {"config_metadata_timeout": 0}, {"config_metadata_timeout": 61}, {"config_metadata_timeout": "abc"},
    {"config_metadata_threshold": -0.1}, {"config_metadata_threshold": 1.5}, {"config_metadata_threshold": "x"},
])
def test_out_of_range_values_are_rejected(admin_client, calibre_library, settings, fields):
    result = save_config(admin_client, config_kakao_api_key_e=KAKAO_KEY, **fields)
    assert errors(result)
    # 하나라도 잘못되면 아무것도 저장하지 않는다
    assert settings.config_metadata_timeout == 5
    assert settings.config_metadata_threshold == pytest.approx(0.85)
    assert settings.config_kakao_api_key_e == ""


# ---------- 제공처가 호출할 때 설정을 읽는다 ----------

def test_kakao_uses_saved_key_and_timeout(settings, monkeypatch):
    from cps.metadata_provider.kakao import Kakao
    settings.config_kakao_api_key_e = KAKAO_KEY
    settings.config_metadata_timeout = 7
    calls = helpers.patch_requests(monkeypatch, helpers.FakeResponse({"documents": []}))
    Kakao().search("소년이 온다", locale="ko")
    assert calls[0]["headers"]["Authorization"] == "KakaoAK " + KAKAO_KEY
    assert calls[0]["timeout"] == 7


def test_nlgo_uses_saved_key_and_timeout(settings, monkeypatch):
    from cps.metadata_provider.nlgo import NationalLibrary
    settings.config_nlgo_api_key_e = NLGO_KEY
    settings.config_metadata_timeout = 3
    calls = helpers.patch_requests(monkeypatch, helpers.FakeResponse({"docs": []}))
    NationalLibrary().search("소년이 온다", locale="ko")
    assert calls[0]["params"]["cert_key"] == NLGO_KEY
    assert calls[0]["timeout"] == 3


def test_search_route_skips_providers_without_key(admin_client, calibre_library, settings, monkeypatch):
    import cps.search_metadata as sm
    asked = []

    class Spy:
        def __init__(self, provider_id, configured):
            self.__id__, self.__name__, self._configured = provider_id, provider_id, configured

        def is_configured(self):
            return self._configured

        def search(self, query, generic_cover="", locale="en"):
            asked.append(self.__id__)
            return []

    monkeypatch.setattr(sm, "cl", [Spy("with_key", True), Spy("without_key", False)])
    response = admin_client.post("/metadata/search", data={"query": "소년이 온다"})
    assert response.status_code == 200
    assert asked == ["with_key"]


def test_bulk_task_uses_threshold_from_settings(admin_client, calibre_library, settings, monkeypatch):
    import cps.search_metadata as sm
    added = []
    monkeypatch.setattr(sm.WorkerThread, "add", staticmethod(lambda user, task: added.append(task)))
    settings.config_metadata_threshold = 0.6
    ids = list(calibre_library["books"].values())[:2]
    response = admin_client.post("/metadata/bulk", data=json.dumps({"book_ids": ids}),
                                 content_type="application/json")
    assert response.status_code == 202
    assert added[0].threshold == pytest.approx(0.6)


def test_bulk_search_skips_providers_without_key(settings, monkeypatch):
    from cps.tasks import metadata_fetch
    import cps.search_metadata as sm
    from cps.metadata_provider.kakao import Kakao
    monkeypatch.setattr(sm, "cl", [Kakao()])   # 키 없는 카카오만 있음
    with pytest.raises(metadata_fetch.SearchUnavailable):
        metadata_fetch.default_search({"title": "소년이 온다", "authors": ["한강"], "isbn": ""})
