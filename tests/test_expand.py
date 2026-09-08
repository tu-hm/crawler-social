"""Tests for the "See more" expansion.

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
    def __init__(
        self,
        name: str,
        *,
        text=None,
        attrs=None,
        visible=True,
        raises=False,
        on_click=None,
    ):
        self.name = name
        #: A real expander's label is its own visible text; a menu button has none.
        self.text = name if text is None else text
        self.attrs = dict(attrs or {})
        self._visible = visible
        self._raises = raises
        self._on_click = on_click
        self.clicked = 0

    def is_visible(self, timeout=None):
        return self._visible

    def get_attribute(self, attr, timeout=None):
        return self.attrs.get(attr)

    def inner_text(self, timeout=None):
        return self.text

    def click(self, timeout=None, no_wait_after=None):
        if self._raises:
            raise RuntimeError("element is not stable")
        self.clicked += 1
        if self._on_click is not None:
            self._on_click()


class FakeLocator:
    def __init__(self, elements):
        self._elements = elements

    @property
    def first(self):
        return self._elements[0] if self._elements else FakeElement("", visible=False)

    def count(self):
        return len(self._elements)

    def nth(self, index):
        return self._elements[index]


class FakeScope:
    """What the feed/article selector resolves to: a sub-tree to search."""

    def __init__(self, page, elements):
        self.page = page
        self.elements = elements

    def count(self):
        return len(self.elements)

    def get_by_role(self, role, name=None):
        return self.page._buttons(self.elements, role, name)


class FakeKeyboard:
    def __init__(self):
        self.pressed: list[str] = []

    def press(self, key):
        self.pressed.append(key)


class FakePage:
    """A page whose matching buttons vanish as they are clicked.

    `buttons` sit inside post articles; `chrome` sit outside them, where the
    group header and tab bar live.
    """

    def __init__(
        self,
        buttons,
        url="https://www.facebook.com/P",
        chrome=(),
        content=True,
        popup=False,
    ):
        self.buttons = list(buttons)
        self.chrome = list(chrome)
        self.has_content = content
        self.popup = popup
        self.url = url
        self.roles_queried: list[str] = []
        self.waits = 0
        self.went_back = 0
        self.keyboard = FakeKeyboard()

    def _buttons(self, elements, role, name):
        self.roles_queried.append(role)
        return FakeLocator(
            [
                element
                for element in elements
                if not element.clicked
                and (name is None or name.search(element.name))
            ]
        )

    def get_by_role(self, role, name=None):
        return self._buttons(self.buttons + self.chrome, role, name)

    def locator(self, selector):
        if selector == facebook._CONTENT_ROOTS:
            self.roles_queried.append("content")
            return FakeScope(self, self.buttons if self.has_content else [])
        return FakeLocator([FakeElement("popup")] if self.popup else [])

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


def test_only_buttons_are_clicked():
    """A link click could navigate away mid-capture; a button cannot (D2)."""
    page = FakePage([FakeElement("See more")])
    facebook.expand_post_text(page, OPTIONS, RNG)
    assert "link" not in page.roles_queried
    assert "button" in page.roles_queried


def test_the_dom_is_requeried_after_each_click():
    page = FakePage([FakeElement("See more") for _ in range(3)])
    facebook.expand_post_text(page, OPTIONS, RNG)
    # One query per click, plus the final one that finds nothing left.
    assert page.roles_queried.count("button") == 4


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

        def locator(self, selector):
            raise RuntimeError("Target page, context or browser has been closed")

    # The scope lookup fails too, so expansion falls back to the dead page.

    assert facebook.expand_post_text(DeadPage([]), OPTIONS, RNG) == 0


@pytest.mark.parametrize(
    "label, expanding",
    [
        ("See more", True),
        ("see more", True),
        ("Xem thêm", True),
        ("Voir plus", True),
        ("… More", True),
        # The trap: "view more comments" in Vietnamese starts with "see more".
        ("Xem thêm bình luận", False),
        ("View 5 more comments", False),
    ],
)
def test_see_more_pattern_does_not_match_comment_labels(label, expanding):
    assert bool(facebook.SEE_MORE_PATTERN.search(label)) is expanding
    if not expanding:
        assert facebook.MORE_COMMENTS_PATTERN.search(label)


def test_a_menu_button_named_see_more_is_not_clicked():
    """The group options kebab: labelled "Xem thêm", opens a menu."""
    kebab = FakeElement("Xem thêm", text="", attrs={"aria-haspopup": "menu"})
    page = FakePage([kebab])
    assert facebook.expand_post_text(page, OPTIONS, RNG) == 0
    assert kebab.clicked == 0


def test_an_icon_only_button_is_not_clicked():
    """No visible text of its own means the label came from aria-label."""
    icon = FakeElement("See more", text="")
    page = FakePage([icon])
    assert facebook.expand_post_text(page, OPTIONS, RNG) == 0
    assert icon.clicked == 0


def test_an_expanded_toggle_is_not_clicked():
    toggle = FakeElement("More", attrs={"aria-expanded": "false"})
    page = FakePage([toggle])
    assert facebook.expand_post_text(page, OPTIONS, RNG) == 0
    assert toggle.clicked == 0


def test_group_chrome_outside_the_feed_is_never_reached():
    """Expansion searches the feed, where the only real expanders are."""
    header = FakeElement("Xem thêm")
    body = FakeElement("Xem thêm")
    page = FakePage([body], chrome=[header])
    assert facebook.expand_post_text(page, OPTIONS, RNG) == 1
    assert body.clicked == 1
    assert header.clicked == 0


def test_the_whole_page_is_searched_when_there_is_no_feed():
    loose = FakeElement("See more")
    page = FakePage([], chrome=[loose], content=False)
    assert facebook.expand_post_text(page, OPTIONS, RNG) == 1
    assert loose.clicked == 1


def test_a_menu_opened_by_a_click_is_dismissed():
    """Belt and braces: whatever slips through does not swallow later clicks."""
    page = FakePage([FakeElement("See more")], popup=True)
    facebook.expand_post_text(page, OPTIONS, RNG)
    assert page.keyboard.pressed == ["Escape"]


def test_a_stray_button_does_not_stop_the_real_ones():
    kebab = FakeElement("Xem thêm", text="", attrs={"aria-haspopup": "menu"})
    real = FakeElement("Xem thêm")
    page = FakePage([kebab, real])
    assert facebook.expand_post_text(page, OPTIONS, RNG) == 1
    assert real.clicked == 1


@pytest.mark.parametrize(
    "label, expanding",
    [
        ("See more", True),
        ("... More", True),
        ("Xem thêm", True),
        ("More options", False),
        ("Xem thêm tùy chọn", False),
        ("See more options", False),
        ("Xem thêm về nhóm này", False),
    ],
)
def test_see_more_pattern_is_anchored(label, expanding):
    assert bool(facebook.SEE_MORE_PATTERN.search(label)) is expanding


def selector_parts() -> set[str]:
    return {part.strip() for part in facebook._CONTENT_ROOTS.split(",")}


def test_the_search_scope_covers_a_feed_unit():
    """The regression this constant exists for.

    A Facebook Page feed has no `role="feed"` element at all, and leaves
    `role="article"` to comments -- so a scope of those two searched the
    comments and never saw the story's own "See more". The button lives in
    the `aria-posinset` unit, and without it every long body on a Page was
    stored truncated at "… See more".
    """
    assert "[aria-posinset]" in selector_parts()


def test_the_search_scope_still_covers_the_older_surfaces():
    """A permalink page has articles and no feed; older feeds had both."""
    assert {'[role="feed"]', '[role="article"]'} <= selector_parts()


def test_expansion_falls_back_to_the_whole_page_when_nothing_matches():
    """A surface none of the roots match must not silently expand nothing."""
    page = FakePage([FakeElement("See more")], content=False)
    assert facebook.expand_post_text(page, OPTIONS, RNG) == 1
