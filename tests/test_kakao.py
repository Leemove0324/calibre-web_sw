# -*- coding: utf-8 -*-
# F1 카카오 제공처(cps/metadata_provider/kakao.py) 단위 테스트
#
# 실행 방법 (저장소 맨 위 폴더에서):
#     python -m pytest tests/test_kakao.py -v
#
# 실제 카카오 서버에는 요청하지 않는다. 키도 필요 없다.
import json
import os
import re

import pytest

from cps.metadata_provider.kakao import Kakao

# 카카오 응답 한 권을 흉내 낸 예시 (테스트용으로 만든 값)
SAMPLE_DOC = {
    "title": "미움받을 용기",
    "contents": "소개 글 예시",
    "url": "https://search.daum.net/search?w=bookpage&bookId=1",
    "isbn": "8996991341 9788996991359",
    "datetime": "2014-11-17T00:00:00.000+09:00",
    "authors": ["기시미 이치로", "고가 후미타케"],
    "publisher": "인플루엔셜",
    "translators": ["전경아"],
    "thumbnail": "https://search1.kakaocdn.net/thumb/R120x174.q85/?fname=sample",
}

# 실제 응답을 저장한 파일이 있으면 그것도 검사한다 (없으면 그 테스트만 건너뛴다)
RECORDED = os.path.join(os.path.dirname(__file__), "data", "kakao_response.json")


class FakeResponse:
    """requests.get이 돌려주는 응답을 흉내 내는 가짜"""

    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


# ---------- 작은 함수들 ----------

def test_is_isbn():
    assert Kakao._is_isbn("9788996991359")
    assert Kakao._is_isbn("978-89-96991-35-9")
    assert Kakao._is_isbn("8932917248")
    assert not Kakao._is_isbn("미움받을 용기")
    assert not Kakao._is_isbn("1984")


def test_parse_isbn_prefers_13_digits():
    assert Kakao._parse_isbn({"isbn": "8932917248 9788932917245"}) == "9788932917245"
    assert Kakao._parse_isbn({"isbn": " 9788996991359"}) == "9788996991359"
    assert Kakao._parse_isbn({"isbn": ""}) == ""
    assert Kakao._parse_isbn({}) == ""


def test_parse_date():
    assert Kakao._parse_date("2015-10-30T00:00:00.000+09:00") == "2015-10-30"
    assert Kakao._parse_date("") == ""
    assert Kakao._parse_date("날짜아님") == ""


# ---------- 한 권을 MetaRecord로 바꾸는 부분 ----------

def test_parse_search_result():
    record = Kakao()._parse_search_result(
        result=SAMPLE_DOC, generic_cover="generic.jpg", locale="en"
    )
    assert record.title == "미움받을 용기"
    assert record.authors == ["기시미 이치로", "고가 후미타케"]
    assert record.publisher == "인플루엔셜"
    assert record.publishedDate == "2014-11-17"
    assert record.description == "소개 글 예시"
    assert record.cover == SAMPLE_DOC["thumbnail"]
    assert record.identifiers["isbn"] == "9788996991359"
    assert record.source.id == "kakao"


def test_parse_search_result_with_missing_fields():
    # 카카오가 값을 안 준 책이어도 에러 없이 빈 칸으로 담겨야 한다
    record = Kakao()._parse_search_result(
        result={"title": "제목만 있는 책"}, generic_cover="generic.jpg", locale="en"
    )
    assert record.title == "제목만 있는 책"
    assert record.authors == []
    assert record.publisher == ""
    assert record.publishedDate == ""
    assert record.cover == "generic.jpg"


# ---------- search() 전체 흐름 (카카오에 실제 요청하지 않음) ----------

def test_search_without_key_returns_empty(monkeypatch):
    monkeypatch.delenv("KAKAO_REST_API_KEY", raising=False)
    assert Kakao().search("미움받을 용기") == []


def test_search_by_title(monkeypatch):
    monkeypatch.setenv("KAKAO_REST_API_KEY", "dummy-key")
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["params"] = params
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResponse({"documents": [SAMPLE_DOC]})

    monkeypatch.setattr("cps.metadata_provider.kakao.requests.get", fake_get)
    results = Kakao().search("미움받을 용기", locale="en")

    assert len(results) == 1
    assert results[0].title == "미움받을 용기"
    assert captured["headers"]["Authorization"] == "KakaoAK dummy-key"
    assert "target" not in captured["params"]  # 제목 검색은 target을 붙이지 않는다
    assert captured["timeout"] is not None  # 시간 제한이 걸려 있어야 한다


def test_search_by_isbn_sets_target(monkeypatch):
    monkeypatch.setenv("KAKAO_REST_API_KEY", "dummy-key")
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["params"] = params
        return FakeResponse({"documents": []})

    monkeypatch.setattr("cps.metadata_provider.kakao.requests.get", fake_get)
    Kakao().search("9788996991359", locale="en")
    assert captured["params"]["target"] == "isbn"


def test_search_returns_empty_on_request_error(monkeypatch):
    monkeypatch.setenv("KAKAO_REST_API_KEY", "dummy-key")

    def fake_get(*args, **kwargs):
        raise RuntimeError("네트워크 오류")

    monkeypatch.setattr("cps.metadata_provider.kakao.requests.get", fake_get)
    assert Kakao().search("미움받을 용기", locale="en") == []


# ---------- 저장해 둔 실제 응답 (있을 때만) ----------

@pytest.mark.skipif(not os.path.exists(RECORDED), reason="녹화 응답 파일이 없음")
def test_recorded_response():
    with open(RECORDED, encoding="utf-8") as f:
        data = json.load(f)
    documents = data.get("documents", [])
    assert documents, "녹화 응답에 documents가 비어 있다"
    for doc in documents:
        record = Kakao()._parse_search_result(
            result=doc, generic_cover="generic.jpg", locale="en"
        )
        assert record.title
        assert isinstance(record.authors, list)
        assert record.publishedDate == "" or re.fullmatch(
            r"\d{4}-\d{2}-\d{2}", record.publishedDate
        )
        isbn = record.identifiers.get("isbn")
        assert isbn is None or len(isbn) in (10, 13)