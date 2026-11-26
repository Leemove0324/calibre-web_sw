# 테스트 안내 (F9, #11)

`main`으로 가는 PR은 GitHub Actions(`.github/workflows/tests.yml`)에서 아래 테스트를 자동으로 돌린다.
TEAM.md 규칙 2의 "CI 통과"가 이 검사를 뜻한다.

## 실행

```
pip install -r requirements.txt -r requirements-test.txt
python -m pytest                      # 전체
python -m pytest tests/test_kakao.py  # 파일 하나
python -m pytest --cov --cov-report=json:coverage.json
python scripts/check_coverage.py coverage.json   # M10: 신규 모듈 커버리지 80% 검사
```

- 테스트는 실제 외부 API를 부르지 않는다. 키가 없어도 된다.
- 실제 API를 부르는 테스트에는 `@pytest.mark.live`를 붙인다. 이 테스트는 기본으로 건너뛰고,
  `python -m pytest -m live --run-live`로만 실행한다 (키는 환경 변수로).
- 테스트 중 만들어지는 app.db·로그는 임시 폴더에 생기고, 끝나면 지워진다. 저장소에는 남지 않는다.

### Windows에서 python-magic 오류가 날 때

`pip install -r requirements.txt` 뒤 `python-magic`이 `python-magic-bin`의 파일을 덮어써서
"Cannot import python-magic"이 날 수 있다. 아래 두 줄을 순서대로 실행한다.

```
pip install --no-deps python-magic
pip install --force-reinstall --no-deps python-magic-bin
```

경로에 한글이 있는 폴더에서는 libmagic이 `magic.mgc`를 못 읽어 멈출 수 있다. 영어 경로에 clone한다.

## 공용 fixture (`tests/conftest.py`)

| fixture | 하는 일 |
|---|---|
| `fake_response` | `fake_response(data, status=200)` → `requests.Response` 흉내. `status>=400`이면 `raise_for_status()`가 예외, 문자열 본문이면 `.json()`이 `ValueError` |
| `recorded` | `recorded("kakao", "소년이 온다")` → `tests/data/kakao/소년이_온다.json`의 응답 본문. 파일이 없으면 그 테스트만 건너뜀 |
| `replay` | `replay("kakao", "소년이 온다")` → 이후 `requests.get`/`Session.get`이 녹화 응답을 돌려줌. 요청 기록 리스트를 반환 |
| `app` | 라우트까지 등록된 Flask 앱 (서버는 안 띄움) |
| `calibre_library` | 한국 책 6권이 든 임시 서재를 앱에 연결 (테스트마다 새로). `{"path", "books": {제목: id}}` |
| `calibre_session` | 그 서재의 metadata.db 세션. Calibre SQL 함수(`title_sort` 등)가 등록돼 있어 책을 고치고 commit할 수 있음 |
| `client` / `admin_client` | 로그인 안 한 / 관리자(admin)로 로그인한 테스트 클라이언트 |

예외를 흉내 낼 때는 `helpers.patch_requests(monkeypatch, requests.Timeout("느림"))`을 쓴다.

```python
def test_kakao_title_search(monkeypatch, replay):
    monkeypatch.setenv("KAKAO_REST_API_KEY", "dummy")
    calls = replay("kakao", "소년이 온다")
    records = Kakao().search("소년이 온다")
    assert records[0].publisher == "창비"
    assert calls[0]["params"]["query"] == "소년이 온다"
```

서재가 연결되지 않은 앱은 거의 모든 요청을 `/admin/dbconfig`로 보낸다.
책·메타데이터 화면을 시험할 때는 `admin_client`와 `calibre_library`를 같이 쓴다.

## 녹화 응답 만들기 (`scripts/record_responses.py`)

```
$env:KAKAO_REST_API_KEY = Read-Host "Kakao key"
python scripts/record_responses.py kakao "소년이 온다" "9788936434120"
python scripts/record_responses.py kakao --file 검색어목록.txt
```

- 결과는 `tests/data/<제공처>/<검색어>.json`에 저장된다. 응답 본문과 검색 조건, 녹화 시각이 들어간다.
- 키·인증 헤더는 저장하지 않는다. 저장 직전에 환경 변수의 비밀값이 파일에 남았는지 다시 검사하고,
  남아 있으면 저장하지 않고 멈춘다 (TEAM.md 규칙 4). 그래도 커밋 전에 한 번 열어 확인한다.

## M10 커버리지 대상 (`tests/coverage_targets.txt`)

- 기능 PR에서 **새로 만든 파일**은 이 목록에 한 줄 추가한다.
- CI는 목록에 있는 파일 전체를 합한 줄 커버리지가 80% 미만이면 실패한다.
- 파일별 값도 표로 출력된다. Actions 실행 결과의 artifact(`coverage-m10.txt`)에서 볼 수 있다.
- 저장소에 아직 없는 파일(병합 전 기능)은 건너뛴다.
