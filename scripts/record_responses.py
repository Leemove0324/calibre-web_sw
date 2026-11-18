# -*- coding: utf-8 -*-
"""F9 (#11): 실제 제공처 응답을 녹화해 tests/data/<제공처>/<검색어>.json 으로 저장한다.

테스트는 이 파일을 다시 읽으므로 CI에는 API 키가 필요 없다.

사용 (저장소 맨 위 폴더에서, 키는 환경 변수로만):
    $env:KAKAO_REST_API_KEY = Read-Host "Kakao key"
    python scripts/record_responses.py kakao "소년이 온다" "9788936434120"
    python scripts/record_responses.py kakao --file eval/dev_queries.txt

- 키·인증 헤더는 저장하지 않는다. URL·파라미터의 키 값은 REDACTED로 바꾼다.
- 저장하기 직전에 환경 변수의 비밀값이 파일 내용에 남아 있는지 한 번 더 검사하고,
  남아 있으면 저장하지 않고 멈춘다 (TEAM.md 규칙 4).
"""
import argparse
import datetime
import importlib
import inspect
import json
import os
import sys
import tempfile

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_DIR, "tests", "data")
SECRET_ENV_WORDS = ("KEY", "TOKEN", "SECRET", "CERT")

# cps를 import하기 전에: 앱 설정·로그가 저장소에 생기지 않게
os.environ.setdefault("CALIBRE_DBPATH", tempfile.mkdtemp(prefix="cw-record-"))
sys.path.insert(0, REPO_DIR)

import requests  # noqa: E402

from tests import helpers  # noqa: E402


def load_provider(provider_id):
    from cps.services.Metadata import Metadata
    module = importlib.import_module("cps.metadata_provider." + provider_id)
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if issubclass(obj, Metadata) and obj is not Metadata and obj.__module__ == module.__name__:
            return obj()
    raise SystemExit("제공처를 찾지 못함: cps/metadata_provider/{}.py".format(provider_id))


def secret_values():
    return [v for k, v in os.environ.items() if any(w in k.upper() for w in SECRET_ENV_WORDS) and v]


def record(provider, query):
    """provider.search(query)를 실제로 실행하고, 그동안 오간 HTTP 응답을 모은다."""
    original_get = requests.get
    original_session_get = requests.Session.get
    exchanges = []

    def capture(response, url, params):
        try:
            body = response.json()
        except ValueError:
            body = response.text
        exchanges.append({"url": helpers.scrub_url(url), "params": helpers.scrub_params(params),
                          "status": response.status_code, "body": body})
        return response

    def get(url, params=None, **kwargs):
        return capture(original_get(url, params=params, **kwargs), url, params)

    def session_get(self, url, params=None, **kwargs):
        return capture(original_session_get(self, url, params=params, **kwargs), url, params)

    requests.get, requests.Session.get = get, session_get
    try:
        records = provider.search(query, "", "ko")
    finally:
        requests.get, requests.Session.get = original_get, original_session_get
    return exchanges, records


def save(provider_id, query, exchanges, records):
    if not exchanges:
        print("  [건너뜀] HTTP 요청이 없음 (키가 없거나 검색어가 비었을 수 있음)")
        return None
    last = exchanges[-1]
    data = {
        "provider": provider_id,
        "query": query,
        "recorded_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "url": last["url"],
        "params": last["params"],
        "status": last["status"],
        "result_count": len(records or []),
        "body": last["body"],
    }
    if len(exchanges) > 1:
        data["earlier_requests"] = exchanges[:-1]
    text = json.dumps(data, ensure_ascii=False, indent=2)
    leaked = helpers.find_secrets(text, secret_values())
    if leaked:
        raise SystemExit("중단: 녹화 내용에 비밀값이 남아 있어 저장하지 않음 ({}개)".format(len(leaked)))
    path = helpers.recording_path(DATA_DIR, provider_id, query)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text + "\n")
    print("  저장: {} (상태 {}, 결과 {}건)".format(os.path.relpath(path, REPO_DIR), data["status"],
                                              data["result_count"]))
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description="제공처 응답 녹화 (F9)")
    parser.add_argument("provider", help="cps/metadata_provider 안의 파일 이름 (예: kakao, nlgo, google)")
    parser.add_argument("queries", nargs="*", help="검색어 (제목 또는 ISBN)")
    parser.add_argument("--file", help="검색어를 한 줄에 하나씩 적은 파일 (# 뒤는 메모)")
    args = parser.parse_args(argv)

    queries = list(args.queries)
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            queries += [line.split("#", 1)[0].strip() for line in f]
    queries = [q for q in queries if q]
    if not queries:
        parser.error("검색어가 없음")

    saved_argv, sys.argv = sys.argv, ["cps.py"]
    try:
        provider = load_provider(args.provider)
    finally:
        sys.argv = saved_argv

    failed = 0
    for query in queries:
        print("[{}] {}".format(args.provider, query))
        try:
            exchanges, records = record(provider, query)
            if save(args.provider, query, exchanges, records) is None:
                failed += 1
        except SystemExit:
            raise
        except Exception as ex:  # 한 권이 실패해도 나머지는 계속
            failed += 1
            print("  [실패] {}".format(ex))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
