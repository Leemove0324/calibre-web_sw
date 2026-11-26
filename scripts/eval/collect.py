# -*- coding: utf-8 -*-
"""F9 (#11): 실행 중인 Calibre-Web에 표본 질의를 보내 검색 결과·응답 시간을 기록한다.

조건(A~D)마다 그 조건의 코드로 서버를 띄운 뒤 같은 표본으로 실행한다 (eval/README.md).
웹 화면의 "메타데이터 가져오기"와 같은 경로(POST /metadata/search)를 쓰므로,
조건별로 코드가 달라도 이 스크립트는 그대로 쓸 수 있다.

    python scripts/eval/collect.py --condition A --set dev --providers google,comicvine
    python scripts/eval/collect.py --condition B --set test --providers google,comicvine,kakao,nlgo --check-covers

- 비밀번호는 환경 변수 EVAL_PASSWORD 또는 입력 프롬프트로 받는다 (저장하지 않음).
- 결과: eval/results/raw/<조건>_<표본>_<시각>.jsonl (질의 한 줄씩) + 같은 이름의 .meta.json
- --providers로 준 제공처만 켜고 나머지는 끈다. 실행 후 원래 상태로 되돌린다.
"""
import argparse
import csv
import datetime
import getpass
import json
import os
import re
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(os.path.dirname(HERE))
EVAL_DIR = os.path.join(REPO_DIR, "eval")
DESCRIPTION_KEEP = 300  # 소개는 길어서 앞부분만 저장 (M3는 비었는지만 본다)


def read_samples(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f) if (r.get("id") or "").strip()]
    for r in rows:
        r["query"] = (r.get("query") or "").strip() or r["title"].strip()
    return rows


class Client:
    def __init__(self, server, timeout=60):
        self.server = server.rstrip("/")
        self.http = requests.Session()
        self.timeout = timeout
        self.csrf = ""

    def _csrf_from(self, html):
        match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html) or \
            re.search(r'value="([^"]+)"[^>]*name="csrf_token"', html)
        return match.group(1) if match else ""

    def login(self, user, password):
        page = self.http.get(self.server + "/login", timeout=self.timeout)
        token = self._csrf_from(page.text)
        r = self.http.post(self.server + "/login", allow_redirects=False, timeout=self.timeout,
                           data={"username": user, "password": password, "csrf_token": token})
        if r.status_code not in (302, 303):
            raise SystemExit("로그인 실패 (HTTP {})".format(r.status_code))
        # 로그인 뒤 세션에 묶인 토큰을 다시 받는다
        self.csrf = self._csrf_from(self.http.get(self.server + "/me", timeout=self.timeout).text)
        if not self.csrf:
            raise SystemExit("CSRF 토큰을 찾지 못함 (서재가 연결돼 있는지 확인)")

    def providers(self):
        r = self.http.get(self.server + "/metadata/provider", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def set_provider(self, provider_id, active):
        # 화면(get_meta.js)과 같은 주소. "/metadata/provider"(id 없음)는 원본에서 500이 난다
        # (metadata_change_active_provider의 prov_name 인자에 기본값이 없음)
        r = self.http.post(self.server + "/metadata/provider/" + provider_id, json={"id": provider_id, "value": active},
                           headers={"X-CSRFToken": self.csrf}, timeout=self.timeout)
        r.raise_for_status()

    def search(self, query):
        r = self.http.post(self.server + "/metadata/search", data={"query": query},
                           headers={"X-CSRFToken": self.csrf}, timeout=self.timeout)
        return r

    def cover_ok(self, url):
        """표지 주소가 실제 이미지로 받아지는지 (M5 --check-covers)."""
        try:
            r = requests.get(url, timeout=10, stream=True)
            ok = r.status_code == 200 and r.headers.get("Content-Type", "").startswith("image/")
            r.close()
            return ok
        except requests.RequestException:
            return False


def trim(record):
    keep = {k: record.get(k) for k in ("title", "authors", "publisher", "publishedDate", "identifiers",
                                         "cover", "url")}
    description = record.get("description") or ""
    keep["description"] = description[:DESCRIPTION_KEEP]
    keep["description_length"] = len(description)
    keep["source"] = {"id": (record.get("source") or {}).get("id", "")}
    # F3(#22) 이후 /metadata/search가 붙여 주는 신뢰도 점수. 임계값 조정에 쓴다 (조건 A·B에서는 None)
    keep["score"] = record.get("score")
    return keep


def run(client, samples, check_covers=False, pause=0.0, log=print):
    for i, sample in enumerate(samples, 1):
        started = datetime.datetime.now().astimezone()
        t0 = time.perf_counter()
        entry = {"id": sample["id"], "query": sample["query"], "started_at": started.isoformat(timespec="seconds")}
        try:
            response = client.search(sample["query"])
            entry["elapsed_s"] = round(time.perf_counter() - t0, 3)
            entry["http_status"] = response.status_code
            data = response.json() if response.status_code == 200 else []
            entry["status"] = "ok" if response.status_code == 200 else "error"
        except (requests.RequestException, ValueError) as ex:
            entry["elapsed_s"] = round(time.perf_counter() - t0, 3)
            entry["status"] = "error"
            entry["error"] = str(ex)[:200]
            data = []
        entry["results"] = [trim(r) for r in data]
        entry["sources"] = sorted({r["source"]["id"] for r in entry["results"]})
        if check_covers and entry["results"]:
            cover = entry["results"][0].get("cover") or ""
            entry["cover_ok"] = bool(re.match(r"^https?://", cover)) and client.cover_ok(cover)
        log("[{}/{}] {} {:<24} {:>5.2f}s 결과 {}건 {}".format(
            i, len(samples), entry["id"], sample["query"][:24], entry["elapsed_s"], len(entry["results"]),
            entry["status"] if entry["status"] != "ok" else ""))
        yield entry
        if pause:
            time.sleep(pause)


def main(argv=None):
    parser = argparse.ArgumentParser(description="평가 검색 결과 수집 (F9)")
    parser.add_argument("--condition", required=True, choices=["A", "B", "C", "D"])
    parser.add_argument("--set", required=True, choices=["dev", "test"], help="dev(개발용 20권) / test(평가용 50권)")
    parser.add_argument("--providers", required=True, help="켤 제공처 id, 쉼표로 구분 (예: google,comicvine,kakao)")
    parser.add_argument("--server", default="http://127.0.0.1:8083")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--samples", help="표본 CSV (기본: eval/samples_<set>.csv)")
    parser.add_argument("--only", help="이 id들만 실행 (쉼표로 구분, 시험용)")
    parser.add_argument("--check-covers", action="store_true", help="1순위 표지를 실제로 받아 본다 (M5)")
    parser.add_argument("--pause", type=float, default=0.5, help="질의 사이 쉬는 시간(초), 제공처 호출 제한 대비")
    parser.add_argument("--out-dir", default=os.path.join(EVAL_DIR, "results", "raw"))
    args = parser.parse_args(argv)

    samples = read_samples(args.samples or os.path.join(EVAL_DIR, "samples_{}.csv".format(args.set)))
    if args.only:
        wanted = {s.strip() for s in args.only.split(",")}
        samples = [s for s in samples if s["id"] in wanted]
    if not samples:
        raise SystemExit("표본이 비어 있음")

    password = os.environ.get("EVAL_PASSWORD") or getpass.getpass("{} 비밀번호: ".format(args.user))
    client = Client(args.server)
    client.login(args.user, password)

    wanted = [p.strip() for p in args.providers.split(",") if p.strip()]
    available = client.providers()
    ids = {p["id"] for p in available}
    missing = [p for p in wanted if p not in ids]
    if missing:
        raise SystemExit("서버에 없는 제공처: {} (있는 것: {})".format(", ".join(missing), ", ".join(sorted(ids))))
    original = {p["id"]: p["active"] for p in available}
    for provider_id in ids:
        client.set_provider(provider_id, provider_id in wanted)

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    os.makedirs(args.out_dir, exist_ok=True)
    base = os.path.join(args.out_dir, "{}_{}_{}".format(args.condition, args.set, stamp))
    meta = {
        "condition": args.condition, "set": args.set, "providers": wanted,
        "server": args.server, "started_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "samples": len(samples), "check_covers": args.check_covers, "pause_s": args.pause,
        "git_commit": os.environ.get("EVAL_SERVER_COMMIT", ""),
    }
    try:
        with open(base + ".jsonl", "w", encoding="utf-8", newline="\n") as out:
            for entry in run(client, samples, args.check_covers, args.pause):
                out.write(json.dumps(entry, ensure_ascii=False) + "\n")
    finally:
        for provider_id, active in original.items():
            client.set_provider(provider_id, active)
    meta["finished_at"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    with open(base + ".meta.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("저장: {}.jsonl".format(os.path.relpath(base, REPO_DIR)))
    print("채점: python scripts/eval/score.py {}.jsonl".format(os.path.relpath(base, REPO_DIR)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
