"""Tests for the "See more" expansion (plans/v3/01).

Expansion is browser behaviour, so it is driven here by a fake page that
records clicks instead of a real Chrome. What is verified is the contract
the real page has to satisfy: only buttons are clicked, the DOM is
re-queried after every click, the budget is honoured, a stuck element does
not stop the loop, and a click that navigates ends expansion.
"""

from __future__ import annotations

import random

import pytest

from crawler_social import facebook


class FakeElement:
    def __init__(self, name: str, *, visible=True, raises=False, on_click=None):
        self.name = name
        self._visible = visible
        self._raises = raises
        self._on_click = on_click
        self.clicked = 0

    def is_visible(self, timeout=None):
        return self._visible

    def click(self, timeout=None, no_wait_after=None):
        if self._raises:
            raise RuntimeError("element is not stable")
        self.clicked += 1
        if self._on_click is not None:
            self._on_click()


class FakeLocator:
    def __init__(self, elements):
        self._elements = elements

    def count(self):
        return len(self._elements)

    def nth(self, index):
        return self._elements[index]


class FakePage:
    """A page whose matching buttons vanish as they are clicked."""

    def __init__(self, buttons, url="https://www.facebook.com/P"):
        self.buttons = list(buttons)
        self.url = url
        self.roles_queried: list[str] = []
        self.waits = 0
        self.went_back = 0

    def get_by_role(self, role, name=None):
        self.roles_queried.append(role)
        return FakeLocator([b for b in self.buttons if not b.clicked])

    def wait_for_timeout(self, ms):
        self.waits += 1

    def go_back(self, timeout=None, wait_until=None):
        self.went_back += 1
        self.url = "https://www.facebook.com/P"


OPTIONS = facebook.CaptureOptions()
RNG = random.Random(0)


def test_every_visible_see_more_is_clicked_once():
    page = FakePage([FakeElement("See more") for _ in range(3)])
    assert facebook.expand_post_text(page, OPTIONS, RNG) == 3
    assert [b.clicked for b in page.buttons] == [1, 1, 1]


def test_only_buttons_are_queried():
    """A link click could navigate away mid-capture; a button cannot (D2)."""
    page = FakePage([FakeElement("See more")])
    facebook.expand_post_text(page, OPTIONS, RNG)
    assert set(page.roles_queried) == {"button"}


def test_the_dom_is_requeried_after_each_click():
    page = FakePage([FakeElement("See more") for _ in range(3)])
    facebook.expand_post_text(page, OPTIONS, RNG)
    # One query per click, plus the final one that finds nothing left.
    assert len(page.roles_queried) == 4


def test_click_budget_is_a_hard_ceiling():
    page = FakePage([FakeElement("See more") for _ in range(10)])
    options = facebook.CaptureOptions(max_expand_clicks=4)
    assert facebook.expand_post_text(page, options, RNG) == 4
    assert sum(b.clicked for b in page.buttons) == 4


def test_a_failing_element_does_not_stop_the_others():
    good = FakeElement("See more")
    page = FakePage([FakeElement("See more", raises=True), good])
    assert facebook.expand_post_text(page, OPTIONS, RNG) == 1
    assert good.clicked == 1


def test_invisible_elements_are_skipped_and_end_the_loop():
    hidden = FakeElement("See more", visible=False)
    page = FakePage([hidden])
    assert facebook.expand_post_text(page, OPTIONS, RNG) == 0
    assert hidden.clicked == 0


def test_disabled_expansion_clicks_nothing():
    page = FakePage([FakeElement("See more")])
    options = facebook.CaptureOptions(expand_text=False)
    assert facebook.expand_post_text(page, options, RNG) == 0
    assert page.roles_queried == []


def test_a_click_that_navigates_goes_back_and_stops():
    def navigate():
        page.url = "https://www.facebook.com/somewhere/else"

    first = FakeElement("See more", on_click=navigate)
    page = FakePage([first, FakeElement("See more")])
    clicks = facebook.expand_post_text(
        page, OPTIONS, RNG, url="https://www.facebook.com/P"
    )
    assert clicks == 1
    assert page.went_back == 1
    assert page.buttons[1].clicked == 0


def test_a_dead_page_ends_the_loop_without_raising():
    class DeadPage(FakePage):
        def get_by_role(self, role, name=None):
            raise RuntimeError("Target page, context or browser has been closed")

    assert facebook.expand_post_text(DeadPage([]), OPTIONS, RNG) == 0


@pytest.mark.parametrize(
    "label, expanding",
    [
        ("See more", True),
        ("see more", True),
        ("Xem thêm", True),
        ("Voir plus", True),
        ("… More", True),
        # The trap this pattern exists to avoid: the Vietnamese label for
        # "view more comments" starts with the words for "see more".
        ("Xem thêm bình luận", False),
        ("View 5 more comments", False),
    ],
)
def test_see_more_pattern_does_not_match_comment_labels(label, expanding):
    assert bool(facebook.SEE_MORE_PATTERN.search(label)) is expanding
    if not expanding:
        assert facebook.MORE_COMMENTS_PATTERN.search(label)
