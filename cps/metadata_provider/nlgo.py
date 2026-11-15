# -*- coding: utf-8 -*-
 
#  This file is part of the Calibre-Web fork of 공개SW실무 4조
#  (https://github.com/janeczku/calibre-web)
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program. If not, see <http://www.gnu.org/licenses/>.
 
# 국립중앙도서관 ISBN 서지정보 API (계획서 표 25, 확장 기능 E1)
# 인증키(cert_key)는 코드에 적지 않고 환경 변수로만 읽는다.
#
# 주의: 이 파일은 계획서와 공개된 사용 예를 바탕으로 만든 초안이다.
# 응답의 항목 이름(docs, TITLE, AUTHOR ...)은 키를 받아 실제 응답을 저장해서
# 확인한 뒤 맞춰야 한다.
import os
from datetime import datetime
from typing import Dict, List, Optional
 
import requests
 
from cps import logger
from cps.services.Metadata import MetaRecord, MetaSourceInfo, Metadata
 
log = logger.create()
 
 
class NationalLibrary(Metadata):
    __name__ = "National Library of Korea"
    __id__ = "nlgo"
    DESCRIPTION = "National Library of Korea"
    META_URL = "https://www.nl.go.kr/seoji/"
    SEARCH_URL = "https://www.nl.go.kr/seoji/SearchApi.do"
    API_KEY_ENV = "NLGO_CERT_KEY"  # 환경 변수 이름
    TIMEOUT = 5  # 응답이 없을 때 무한정 기다리지 않도록 (초)
    PAGE_SIZE = 10  # 한 번에 가져올 책 수
 
    def search(
        self, query: str, generic_cover: str = "", locale: str = "en"
    ) -> Optional[List[MetaRecord]]:
        val = list()
        if not self.active:
            return val
 
        api_key = os.environ.get(NationalLibrary.API_KEY_ENV, "")
        if not api_key:
            log.warning(
                "NLGO: 환경 변수 %s 가 설정되지 않았습니다.",
                NationalLibrary.API_KEY_ENV,
            )
            return val
 
        query = query.strip()
        if not query:
            return val
 
        params = {
            "cert_key": api_key,
            "result_style": "json",
            "page_no": 1,
            "page_size": NationalLibrary.PAGE_SIZE,
        }
        if self._is_isbn(query):
            params["isbn"] = query.replace("-", "").replace(" ", "")
        else:
            params["title"] = query
 
        try:
            results = requests.get(
                NationalLibrary.SEARCH_URL,
                params=params,
                timeout=NationalLibrary.TIMEOUT,
            )
            results.raise_for_status()
            documents = results.json().get("docs", [])
        except Exception as e:
            log.warning(e)
            return []
 
        for result in documents:
            val.append(
                self._parse_search_result(result=result, generic_cover=generic_cover)
            )
        return val
 
    def _parse_search_result(self, result: Dict, generic_cover: str) -> MetaRecord:
        isbn = result.get("EA_ISBN", "") or ""
        record_id = isbn or result.get("CONTROL_NO", "") or result.get("TITLE", "")
 
        match = MetaRecord(
            id=record_id,
            title=result.get("TITLE", ""),
            authors=self._parse_authors(result.get("AUTHOR", "")),
            url=NationalLibrary.META_URL,
            source=MetaSourceInfo(
                id=self.__id__,
                description=NationalLibrary.DESCRIPTION,
                link=NationalLibrary.META_URL,
            ),
        )
 
        cover = result.get("TITLE_URL", "") or ""
        match.cover = cover.replace("http://", "https://") if cover else generic_cover
        match.publisher = result.get("PUBLISHER", "") or ""
        match.publishedDate = self._parse_date(result.get("PUBLISH_PREDATE", ""))
        match.series = result.get("SERIES_TITLE", "") or ""
        match.series_index = self._parse_series_index(result.get("VOL", ""))
        kdc = result.get("KDC", "")
        match.tags = ["KDC " + kdc] if kdc else []
 
        match.identifiers = {"nlgo": record_id}
        if isbn:
            match.identifiers["isbn"] = isbn
        return match
 
    @staticmethod
    def _is_isbn(query: str) -> bool:
        # 하이픈과 공백을 빼고 10자리 또는 13자리 숫자면 ISBN으로 본다.
        digits = query.replace("-", "").replace(" ", "")
        return len(digits) in (10, 13) and digits[:-1].isdigit()
 
    @staticmethod
    def _parse_authors(value: str) -> List[str]:
        # 저자가 "홍길동;김철수"처럼 구분자로 이어져 올 수 있어 나눠서 쓴다.
        if not value:
            return []
        parts = value.replace(";", ",").split(",")
        return [p.strip() for p in parts if p.strip()]
 
    @staticmethod
    def _parse_date(value: str) -> str:
        # "20151030" 형태를 "2015-10-30"으로 바꾼다. 형식이 다르면 빈 값으로 둔다.
        try:
            return datetime.strptime(value, "%Y%m%d").strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            return ""
 
    @staticmethod
    def _parse_series_index(value: str) -> float:
        # 권차가 숫자면 그 값을, 아니면 1을 쓴다.
        try:
            return float(value)
        except (ValueError, TypeError):
            return 1
 