# -*- coding: utf-8 -*-
# F1 국립중앙도서관 제공처(cps/metadata_provider/nlgo.py) 단위 테스트
#
# 실행 방법 (저장소 맨 위 폴더에서):
#     python -m pytest tests/test_nlgo.py -v
#
# 실제 국립중앙도서관 서버에는 요청하지 않는다. 키도 필요 없다.
from cps.metadata_provider.nlgo import NationalLibrary

# 국립중앙도서관 응답 한 권을 흉내 낸 예시 (테스트용으로 만든 값)
SAMPLE_DOC = {
    "TITLE": "미움받을 용기",
    "AUTHOR": "기시미 이치로;고가 후미타케",
    "PUBLISHER": "인플루엔셜",
    "EA_ISBN": "9788996991359",
    "PUBLISH_PREDATE": "20141117",
    "TITLE_URL": "http://example.com/cover.jpg",
    "SERIES_TITLE": "예시 총서",
    "VOL": "2",
    "KDC": "818",
    "CONTROL_NO": "CIP0000001",
}


class FakeResponse:
    """requests.get이 돌려주는 응답을 흉내 내는 가짜"""

    def __init__(self, data=None, error=None):
        self._data = data
        self._error = error

    def raise_for_status(self):
        pass

    def json(self):
        if self._error:
            raise self._error
        return self._data


# ---------- 작은 함수들 ----------

def test_is_isbn():
    assert NationalLibrary._is_isbn("9788996991359")
    assert NationalLibrary._is_isbn("978-89-96991-35-9")
    assert NationalLibrary._is_isbn("8932917248")
    assert not NationalLibrary._is_isbn("미움받을 용기")
    assert not NationalLibrary._is_isbn("1984")


def test_parse_authors():
    assert NationalLibrary._parse_authors("기시미 이치로;고가 후미타케") == [
        "기시미 이치로",
        "고가 후미타케",
    ]
    assert NationalLibrary._parse_authors("홍길동, 김철수") == ["홍길동", "김철수"]
    assert NationalLibrary._parse_authors("홍길동") == ["홍길동"]
    assert NationalLibrary._parse_authors("") == []
    assert NationalLibrary._parse_authors(None) == []


def test_parse_date():
    assert NationalLibrary._parse_date("20151030") == "2015-10-30"
    assert NationalLibrary._parse_date("") == ""
    assert NationalLibrary._parse_date("2015") == ""
    assert NationalLibrary._parse_date("날짜아님") == ""
    assert NationalLibrary._parse_date(None) == ""


def test_parse_series_index():
    assert NationalLibrary._parse_series_index("3") == 3
    assert NationalLibrary._parse_series_index("2.5") == 2.5
    assert NationalLibrary._parse_series_index("") == 1
    assert NationalLibrary._parse_series_index("상") == 1
    assert NationalLibrary._parse_series_index(None) == 1


# ---------- 한 권을 MetaRecord로 바꾸는 부분 ----------

def test_parse_search_result():
    record = NationalLibrary()._parse_search_result(
        result=SAMPLE_DOC, generic_cover="generic.jpg"
    )
    assert record.title == "미움받을 용기"
    assert record.authors == ["기시미 이치로", "고가 후미타케"]
    assert record.publisher == "인플루엔셜"
    assert record.publishedDate == "2014-11-17"
    assert record.cover == "https://example.com/cover.jpg"  # http는 https로 바꾼다
    assert record.series == "예시 총서"
    assert record.series_index == 2
    assert record.tags == ["KDC 818"]
    assert record.identifiers["isbn"] == "9788996991359"
    assert record.source.id == "nlgo"


def test_parse_search_result_with_missing_fields():
    # 값을 안 준 책이어도 에러 없이 빈 칸으로 담겨야 한다
    record = NationalLibrary()._parse_search_result(
        result={"TITLE": "제목만 있는 책"}, generic_cover="generic.jpg"
    )
    assert record.title == "제목만 있는 책"
    assert record.authors == []
    assert record.publisher == ""
    assert record.publishedDate == ""
    assert record.series == ""
    assert record.series_index == 1
    assert record.tags == []
    assert record.cover == "generic.jpg"
    assert "isbn" not in record.identifiers


# ---------- search() 전체 흐름 (서버에 실제 요청하지 않음) ----------

def test_search_without_key_returns_empty(monkeypatch):
    monkeypatch.delenv("NLGO_CERT_KEY", raising=False)
    assert NationalLibrary().search("미움받을 용기") == []


def test_search_by_title(monkeypatch):
    monkeypatch.setenv("NLGO_CERT_KEY", "dummy-key")
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["params"] = params
        captured["timeout"] = timeout
        return FakeResponse({"docs": [SAMPLE_DOC]})

    monkeypatch.setattr("cps.metadata_provider.nlgo.requests.get", fake_get)
    results = NationalLibrary().search("미움받을 용기")

    assert len(results) == 1
    assert results[0].title == "미움받을 용기"
    assert captured["params"]["cert_key"] == "dummy-key"
    assert captured["params"]["result_style"] == "json"
    assert captured["params"]["title"] == "미움받을 용기"
    assert "isbn" not in captured["params"]  # 제목 검색에는 isbn을 붙이지 않는다
    assert captured["timeout"] is not None  # 시간 제한이 걸려 있어야 한다


def test_search_by_isbn(monkeypatch):
    monkeypatch.setenv("NLGO_CERT_KEY", "dummy-key")
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["params"] = params
        return FakeResponse({"docs": []})

    monkeypatch.setattr("cps.metadata_provider.nlgo.requests.get", fake_get)
    NationalLibrary().search("978-89-96991-35-9")

    assert captured["params"]["isbn"] == "9788996991359"  # 하이픈은 뺀다
    assert "title" not in captured["params"]


def test_search_returns_empty_on_request_error(monkeypatch):
    monkeypatch.setenv("NLGO_CERT_KEY", "dummy-key")

    def fake_get(*args, **kwargs):
        raise RuntimeError("네트워크 오류")

    monkeypatch.setattr("cps.metadata_provider.nlgo.requests.get", fake_get)
    assert NationalLibrary().search("미움받을 용기") == []


def test_search_returns_empty_on_bad_json(monkeypatch):
    # 서버가 JSON이 아닌 답을 줘도 에러로 죽지 않고 빈 목록을 돌려줘야 한다
    monkeypatch.setenv("NLGO_CERT_KEY", "dummy-key")

    def fake_get(*args, **kwargs):
        return FakeResponse(error=ValueError("JSON이 아님"))

    monkeypatch.setattr("cps.metadata_provider.nlgo.requests.get", fake_get)
    assert NationalLibrary().search("미움받을 용기") == []
