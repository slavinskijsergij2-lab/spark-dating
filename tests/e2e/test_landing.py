"""E2E: landing page — visual elements, navigation, links."""
import re

import pytest
from playwright.sync_api import Page, expect


def test_landing_title(page: Page, live_server_url: str):
    page.goto(live_server_url)
    # Title is localised — just check it starts with "Spark"
    expect(page).to_have_title(re.compile(r"^Spark"))


def test_landing_hero_visible(page: Page, live_server_url: str):
    page.goto(live_server_url)
    expect(page.locator("h1")).to_contain_text("Spark")


def test_landing_register_button(page: Page, live_server_url: str):
    page.goto(live_server_url)
    btn = page.locator("a[href='/register']").first
    expect(btn).to_be_visible()


def test_landing_login_button(page: Page, live_server_url: str):
    page.goto(live_server_url)
    btn = page.locator("a[href='/login']").first
    expect(btn).to_be_visible()


def test_landing_stats_visible(page: Page, live_server_url: str):
    page.goto(live_server_url)
    stats = page.locator(".stat-card").first
    expect(stats).to_be_visible()


def test_landing_how_it_works(page: Page, live_server_url: str):
    page.goto(live_server_url)
    # At least one "how it works" step
    expect(page.locator(".step-dot").first).to_be_visible()


def test_landing_reviews(page: Page, live_server_url: str):
    page.goto(live_server_url)
    expect(page.locator(".review-card").first).to_be_visible()


def test_landing_floating_hearts(page: Page, live_server_url: str):
    page.goto(live_server_url)
    page.wait_for_timeout(1500)
    hearts = page.locator(".floating-heart")
    assert hearts.count() > 0


def test_landing_no_js_errors(page: Page, live_server_url: str):
    errors = []
    def _on_console(msg):
        if msg.type == "error":
            text = msg.text
            # 401 on push/geo check is expected for anonymous visitors
            if "401" not in text and "Unauthorized" not in text and "403" not in text:
                errors.append(text)
    page.on("console", _on_console)
    page.goto(live_server_url)
    page.wait_for_timeout(1000)
    assert errors == [], f"Unexpected JS errors on landing: {errors}"


def test_landing_privacy_link(page: Page, live_server_url: str):
    page.goto(live_server_url)
    link = page.locator("a[href='/privacy']")
    expect(link).to_be_visible()


def test_landing_csp_header(page: Page, live_server_url: str):
    import httpx
    r = httpx.get(live_server_url)
    assert "content-security-policy" in {k.lower() for k in r.headers}


def test_landing_meta_og_title(page: Page, live_server_url: str):
    page.goto(live_server_url)
    og = page.locator("meta[property='og:title']")
    expect(og).to_have_count(1)
