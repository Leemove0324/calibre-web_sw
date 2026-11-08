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
 
# Kakao 책 검색 API 문서: https://developers.kakao.com/docs/latest/ko/daum-search/dev-guide
# 개발자 사이트에서 발급받은 REST API 키는 코드에 적지 않고 환경 변수로만 읽는다.
import os
from datetime import datetime
from typing import Dict, List, Optional
 
import requests
 
from cps import logger
from cps.isoLanguages import get_lang3, get_language_name
from cps.services.Metadata import MetaRecord, MetaSourceInfo, Metadata
 
log = logger.create()
 
 
class Kakao(Metadata):
    __name__ = "Kakao Books"
    __id__ = "kakao"
    DESCRIPTION = "Kakao Books"
    META_URL = "https://developers.kakao.com/"
    SEARCH_URL = "https://dapi.kakao.com/v3/search/book"
    API_KEY_ENV = "KAKAO_REST_API_KEY"  # 환경 변수 이름
    TIMEOUT = 5  # 카카오가 응답하지 않을 때 무한정 기다리지 않도록 (초)
    PAGE_SIZE = 10  # 한 번에 가져올 책 수
 
    def search(
        self, query: str, generic_cover: str = "", locale: str = "en"
    ) -> Optional[List[MetaRecord]]:
        val = list()
        if not self.active:
            return val
 
        api_key = os.environ.get(Kakao.API_KEY_ENV, "")
        if not api_key:
            log.warning("Kakao: 환경 변수 %s 가 설정되지 않았습니다.", Kakao.API_KEY_ENV)
            return val
 
        query = query.strip()
        if not query:
            return val
 
        params = {"query": query, "size": Kakao.PAGE_SIZE}
        if self._is_isbn(query):
            params["target"] = "isbn"
 
        try:
            results = requests.get(
                Kakao.SEARCH_URL,
                params=params,
                headers={"Authorization": "KakaoAK " + api_key},
                timeout=Kakao.TIMEOUT,
            )
            results.raise_for_status()
        except Exception as e:
            log.warning(e)
            return []
 
        for result in results.json().get("documents", []):
            val.append(
                self._parse_search_result(
                    result=result, generic_cover=generic_cover, locale=locale
                )
            )
        return val
 
    def _parse_search_result(
        self, result: Dict, generic_cover: str, locale: str
    ) -> MetaRecord:
        isbn = self._parse_isbn(result)
        record_id = isbn or result.get("url", "")
 
        match = MetaRecord(
            id=record_id,
            title=result.get("title", ""),
            authors=result.get("authors", []),
            url=result.get("url", ""),
            source=MetaSourceInfo(
                id=self.__id__,
                description=Kakao.DESCRIPTION,
                link=Kakao.META_URL,
            ),
        )
 
        match.cover = result.get("thumbnail") or generic_cover
        match.description = result.get("contents", "")
        match.languages = [get_language_name(locale, get_lang3("ko"))]
        match.publisher = result.get("publisher", "")
        match.publishedDate = self._parse_date(result.get("datetime", ""))
        match.rating = 0
        match.series, match.series_index = "", 1
        match.tags = []
 
        match.identifiers = {"kakao": record_id}
        if isbn:
            match.identifiers["isbn"] = isbn
        return match
 
    @staticmethod
    def _is_isbn(query: str) -> bool:
        # 하이픈과 공백을 빼고 10자리 또는 13자리 숫자면 ISBN으로 본다.
        digits = query.replace("-", "").replace(" ", "")
        return len(digits) in (10, 13) and digits[:-1].isdigit()
 
    @staticmethod
    def _parse_isbn(result: Dict) -> str:
        # 카카오는 "ISBN10 ISBN13"처럼 공백으로 이어서 준다. 13자리를 우선한다.
        isbns = result.get("isbn", "").split()
        for isbn in isbns:
            if len(isbn) == 13:
                return isbn
        return isbns[0] if isbns else ""
 
    @staticmethod
    def _parse_date(value: str) -> str:
        # "2015-10-30T00:00:00.000+09:00" 형태에서 앞의 10글자(날짜)만 쓴다.
        date = value[:10]
        try:
            datetime.strptime(date, "%Y-%m-%d")
            return date
        except ValueError:
            return ""