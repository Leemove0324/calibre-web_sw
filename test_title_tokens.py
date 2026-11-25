# -*- coding: utf-8 -*-
import pytest

from cps.services.Metadata import Metadata


def tokens(title, strip_joiners=False):
    return list(Metadata.get_title_tokens(title, strip_joiners=strip_joiners))


@pytest.mark.parametrize("title, expected", [
    # 중간발표 6번 슬라이드의 D1 재현 사례
    ("Harry Potter: The Philosopher's Stone", ["Harry", "Potter", "The", "Philosopher's", "Stone"]),
    ("Clean Code: 애자일 소프트웨어 장인 정신", ["Clean", "Code", "애자일", "소프트웨어", "장인", "정신"]),
    ("해리 포터와 마법사의 돌 (양장본)", ["해리", "포터와", "마법사의", "돌", "양장본"]),
    ("《三体》", ["三体"]),
    # 한국어·동아시아 인용 부호
    ("《채식주의자》", ["채식주의자"]),
    ("「소년이 온다」", ["소년이", "온다"]),
    ("“82년생 김지영”", ["82년생", "김지영"]),
    ("《토지》 1", ["토지", "1"]),
    # 원래 패턴에서는 일반 구두점도 남았다
    ("해리 포터: 마법사의 돌", ["해리", "포터", "마법사의", "돌"]),
    ("총, 균, 쇠", ["총", "균", "쇠"]),
    ("Title: A Story", ["Title", "A", "Story"]),
    ("a/b", ["a", "b"]),
    ("Do it! 점프 투 파이썬", ["Do", "it", "점프", "투", "파이썬"]),
])
def test_special_characters_become_spaces(title, expected):
    assert tokens(title) == expected


@pytest.mark.parametrize("title, expected", [
    # 고치기 전에도 맞던 동작은 그대로여야 한다
    ("채식주의자", ["채식주의자"]),
    ("미움받을 용기", ["미움받을", "용기"]),
    ("1984", ["1984"]),
    ("1,000 Days", ["1000", "Days"]),          # 숫자 사이 쉼표는 지운다
    ("Dune (Paperback)", ["Dune"]),             # 판형 괄호는 지운다
    ("Spider-Man - Origins", ["Spider-Man", "Origins"]),  # 앞에 공백이 있는 하이픈만 지운다
])
def test_existing_behaviour_kept(title, expected):
    assert tokens(title) == expected


def test_strip_joiners_still_applies():
    assert tokens("The Lord and the Rings", strip_joiners=True) == ["Lord", "Rings"]


def test_empty_title():
    assert tokens("") == []
