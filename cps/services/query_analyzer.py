# -*- coding: utf-8 -*-
import dataclasses
import re
import unicodedata
from typing import List

__all__ = [
    "QueryAnalysis",
    "analyze_query",
    "clean_isbn",
    "compare_key",
    "is_valid_isbn10",
    "is_valid_isbn13",
    "looks_like_isbn",
    "normalize_title",
    "split_subtitle",
    "strip_edition",
    "to_isbn13",
]


# ---------------------------------------------------------------------------
# ISBN
# ---------------------------------------------------------------------------

_ISBN_PREFIX = re.compile(r"^\s*isbn(?:-1[03])?\s*:?\s*", re.IGNORECASE)
# ISBN처럼 보이는 질의: 숫자·하이픈·공백(과 끝자리 X)만으로 이루어진 것
_ISBN_SHAPE = re.compile(r"^[0-9][0-9\-\s]*[0-9Xx]$")


def clean_isbn(value):
    """'ISBN 978-89-364-3473-4' -> '9788936434734'. 숫자와 X(대문자)만 남김."""
    text = _ISBN_PREFIX.sub("", str(value or ""))
    return re.sub(r"[^0-9Xx]", "", text).upper()


def is_valid_isbn13(digits):
    if len(digits) != 13 or not digits.isdigit():
        return False
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits[:12]))
    return (10 - total % 10) % 10 == int(digits[12])


def is_valid_isbn10(digits):
    if len(digits) != 10 or not digits[:9].isdigit():
        return False
    if not (digits[9].isdigit() or digits[9] == "X"):
        return False
    total = sum(int(d) * (10 - i) for i, d in enumerate(digits[:9]))
    check = (11 - total % 11) % 11
    return ("X" if check == 10 else str(check)) == digits[9]


def _isbn13_check_digit(first12):
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(first12))
    return str((10 - total % 10) % 10)


def to_isbn13(value):
    """ISBN-10/13 문자열 -> 검사 숫자가 맞는 ISBN-13. 아니면 ''."""
    digits = clean_isbn(value)
    if is_valid_isbn13(digits):
        return digits
    if is_valid_isbn10(digits):
        core = "978" + digits[:9]
        return core + _isbn13_check_digit(core)
    return ""


def looks_like_isbn(query):
    """사용자가 ISBN을 입력한 것으로 보이는지 (검사 숫자와 무관하게 모양만 봄)."""
    text = _ISBN_PREFIX.sub("", str(query or "")).strip()
    if not _ISBN_SHAPE.match(text):
        return False
    return len(clean_isbn(text)) in (10, 13)


# ---------------------------------------------------------------------------
# 제목
# ---------------------------------------------------------------------------

# 판 표기. 괄호 안에 있거나, 제목 맨 앞·맨 뒤에 단독 낱말로 있을 때만 지움.
_EDITION_WORDS = (
    r"(?:전면\s*)?개정\s*(?:증보|신)?\s*(?:\d+\s*)?판",
    r"증보\s*판",
    r"제?\s*\d+\s*판",
    r"리마스터\s*판?",
    r"리커버\s*(?:판|에디션|특별판)?",
    r"(?:반\s*)?양장\s*(?:본|판)?",
    r"무선\s*(?:본|판)?",
    r"보급\s*판",
    r"특별\s*판",
    r"한정\s*판",
    r"기념\s*판",
    r"\d+\s*주년\s*(?:기념\s*)?(?:판|에디션)?",
    r"큰\s*글자\s*(?:도서|책|판)?",
    r"합본(?:\s*판)?",
    r"완역\s*(?:본|판)?",
    r"초판(?:\s*한정)?",
    r"(?:특별\s*)?에디션",
    r"세트",
    r"전\s*\d+\s*권(?:\s*세트)?",
    r"e\s*-?\s*book|전자책",
)
_EDITION = r"(?:%s)" % "|".join(_EDITION_WORDS)
_EDITION_FULL = re.compile(r"^\s*%s(?:\s*[/·,]\s*%s)*\s*$" % (_EDITION, _EDITION), re.IGNORECASE)
_BRACKETED = re.compile(r"[\[\(（【〔〈<]([^\]\)）】〕〉>]*)[\]\)）】〕〉>]")
_BRACKET_YEAR = re.compile(r"^\s*\d{4}\s*년?\s*")
_LEADING_EDITION = re.compile(r"^\s*%s(?=\s)" % _EDITION, re.IGNORECASE)
_TRAILING_EDITION = re.compile(r"(?<=\s)%s\s*$" % _EDITION, re.IGNORECASE)

_QUOTES = re.compile(r"[《》〈〉「」『』“”‘’\"]")
# 'Re:Zero'처럼 붙어 있는 콜론은 제목의 일부로 보고, 뒤에 공백이 있을 때만 부제로 나눔.
_SUBTITLE_SEPARATOR = re.compile(r"\s*:\s+|\s*：\s*|\s+[-–—~]\s+")
_SPACES = re.compile(r"\s+")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([:,.!?])")


def strip_edition(title):
    """판 표기를 지움. -> (지운 제목, 지운 표기 목록)

    '[개정증보판] 총 균 쇠' -> ('총 균 쇠', ['개정증보판'])
    '채식주의자 리마스터판' -> ('채식주의자', ['리마스터판'])
    '토지 2'               -> ('토지 2', [])          권차는 남긴다
    """
    removed = []
    text = str(title or "")

    def drop_bracket(match):
        inner = match.group(1)
        # '(2014 개정판)'처럼 연도가 붙은 판 표기도 지움. 연도만 있는 '(2024)'는 남김.
        if _EDITION_FULL.match(_BRACKET_YEAR.sub("", inner)):
            removed.append(inner.strip())
            return " "
        return match.group(0)

    text = _BRACKETED.sub(drop_bracket, text)

    # 앞·뒤의 단독 판 표기는 여러 개일 수 있음 ('양장본 개정판 코스모스')
    while True:
        text = text.strip()
        match = _LEADING_EDITION.search(text) or _TRAILING_EDITION.search(text)
        if not match:
            break
        rest = (text[:match.start()] + text[match.end():]).strip()
        if not rest:            # 제목 전체가 판 표기처럼 보이면 지우지 않음 ('세트')
            break
        removed.append(match.group(0).strip())
        text = rest

    return _SPACES.sub(" ", text).strip(), removed


def split_subtitle(title):
    """'사피엔스: 유인원에서 사이보그까지' -> ('사피엔스', '유인원에서 사이보그까지')

    구분자가 맨 앞이거나 앞쪽 제목이 비면 나누지 않음.
    """
    text = str(title or "").strip()
    parts = _SUBTITLE_SEPARATOR.split(text, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return parts[0].strip(), parts[1].strip()
    return text, ""


def _normalize(title):
    """normalize_title()과 analyze_query()가 같이 씀. -> (제목, 지운 판 표기 목록)"""
    text = unicodedata.normalize("NFKC", str(title or ""))
    text = _QUOTES.sub(" ", text)
    text, removed = strip_edition(text)
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)     # '사피엔스 : 부제' -> '사피엔스: 부제'
    return _SPACES.sub(" ", text).strip(), removed


def normalize_title(title):
    """검색용 제목: 유니코드 정규화(NFKC) → 인용 부호 제거 → 판 표기 제거 → 공백 정리.

    부제는 남긴다 (나누는 것은 split_subtitle).
    """
    return _normalize(title)[0]


def compare_key(text):
    """비교용 키: NFKC, 소문자, 글자·숫자만 (띄어쓰기·구두점 무시). '피로 사회' -> '피로사회'."""
    text = unicodedata.normalize("NFKC", str(text or "")).lower()
    return re.sub(r"[\W_]+", "", text)


# ---------------------------------------------------------------------------
# 질의 분석
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class QueryAnalysis:
    raw: str                    # 사용자가 입력한 그대로
    isbn13: str = ""            # 유효한 ISBN이면 ISBN-13, 아니면 ''
    isbn_like: bool = False     # ISBN 모양이었는지 (검사 숫자가 틀려도 True)
    title: str = ""             # 판 표기·인용 부호를 지운 제목 (부제 포함)
    main_title: str = ""        # 부제를 뺀 제목
    subtitle: str = ""
    removed: List[str] = dataclasses.field(default_factory=list)  # 지운 판 표기

    @property
    def invalid_isbn(self):
        """ISBN처럼 입력했지만 검사 숫자가 틀린 경우."""
        return self.isbn_like and not self.isbn13

    @property
    def key(self):
        return compare_key(self.title)

    @property
    def main_key(self):
        return compare_key(self.main_title)


def analyze_query(query):
    """검색어 하나를 분석함.

    - ISBN 모양이면 검사 숫자를 확인, 유효하면 isbn13을 채우고 제목은 비움.
      틀리면 isbn13은 ''이고, 제목도 비움 (숫자열로 제목 검색을 해도 소용없음).
    - 그 밖에는 제목으로 보고 정규화함.
    """
    raw = str(query or "")
    if looks_like_isbn(raw):
        return QueryAnalysis(raw=raw, isbn13=to_isbn13(raw), isbn_like=True)

    title, removed = _normalize(raw)
    main_title, subtitle = split_subtitle(title)
    return QueryAnalysis(raw=raw, title=title, main_title=main_title,
                         subtitle=subtitle, removed=removed)
