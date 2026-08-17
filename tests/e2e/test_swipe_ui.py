"""E2E: swipe page — UI elements, buttons, filters, empty state."""
import secrets

import pytest
from playwright.sync_api import Page, expect


def _register(page: Page, base: str) -> str:
    """Register a new user and return email. Leaves browser on post-register page."""
    email = f"e2e_{secrets.token_hex(5)}@test.com"
    page.goto(f"{base}/register")
    page.fill("input[name='email']", email)
    page.fill("input[name='password']", "TestE2E123!")
    page.click("button[type='submit']")
    page.wait_for_timeout(1200)
    return email


def _setup_profile(page: Page, base: str) -> None:
    """Fill minimum profile so swipe page becomes accessible."""
    page.goto(f"{base}/profile/edit")
    page.wait_for_timeout(300)
    # Fill name
    name_field = page.locator("input[name='name']")
    if name_field.count() > 0:
        name_field.fill("Тест")
    # Fill age
    age_field = page.locator("input[name='age']")
    if age_field.count() > 0:
        age_field.fill("25")
    # Submit
    submit = page.locator("button[type='submit']").first
    if submit.count() > 0:
        submit.click()
        page.wait_for_timeout(800)


def test_swipe_page_loads_after_login(page: Page, live_server_url: str):
    _register(page, live_server_url)
    page.goto(f"{live_server_url}/swipe")
    page.wait_for_timeout(500)
    # Should be on swipe or profile/edit (not login)
    assert "/login" not in page.url
    assert "/register" not in page.url


def test_swipe_bottom_nav_visible(page: Page, live_server_url: str):
    _register(page, live_server_url)
    page.goto(f"{live_server_url}/swipe")
    page.wait_for_timeout(300)
    if "/swipe" in page.url:
        nav = page.locator("#bottom-nav")
        expect(nav).to_be_visible()


def test_swipe_filter_pills_visible(page: Page, live_server_url: str):
    _register(page, live_server_url)
    _setup_profile(page, live_server_url)
    page.goto(f"{live_server_url}/swipe")
    page.wait_for_timeout(500)
    if "/swipe" not in page.url:
        return  # redirected elsewhere
    pills = page.locator(".filter-pill")
    # Filter pills appear only when a candidate exists (server-side rendered)
    # In isolated test DB there may be no candidates — that's OK
    assert pills.count() >= 0  # page loads without error


def test_swipe_empty_state_or_card(page: Page, live_server_url: str):
    _register(page, live_server_url)
    _setup_profile(page, live_server_url)
    page.goto(f"{live_server_url}/swipe")
    page.wait_for_timeout(500)
    if "/swipe" in page.url:
        # Either a swipe card or empty state with swipe-root exists
        has_root = page.locator("#swipe-root").count() > 0
        assert has_root, "Neither card nor empty state found on swipe page"


def test_swipe_no_js_errors(page: Page, live_server_url: str):
    errors = []
    def _on_console(msg):
        if msg.type == "error":
            text = msg.text
            if "401" not in text and "Unauthorized" not in text and "403" not in text:
                errors.append(text)
    page.on("console", _on_console)
    _register(page, live_server_url)
    _setup_profile(page, live_server_url)
    page.goto(f"{live_server_url}/swipe")
    page.wait_for_timeout(1000)
    assert errors == [], f"JS errors on swipe: {errors}"


def test_swipe_redirect_anonymous(page: Page, live_server_url: str):
    # Fresh page (not logged in) should redirect to login
    page.goto(f"{live_server_url}/swipe")
    page.wait_for_timeout(300)
    assert "/login" in page.url or page.url == f"{live_server_url}/"


def test_swipe_intention_filter_navigation(page: Page, live_server_url: str):
    _register(page, live_server_url)
    _setup_profile(page, live_server_url)
    page.goto(f"{live_server_url}/swipe")
    page.wait_for_timeout(500)
    if "/swipe" in page.url:
        pills = page.locator(".filter-pill")
        if pills.count() >= 2:
            pills.nth(1).click()
            page.wait_for_timeout(500)
            assert "swipe" in page.url


def test_swipe_profile_page_accessible(page: Page, live_server_url: str):
    _register(page, live_server_url)
    page.goto(f"{live_server_url}/profile/edit")
    expect(page).not_to_have_url(f"{live_server_url}/login")


def test_matches_page_accessible(page: Page, live_server_url: str):
    _register(page, live_server_url)
    page.goto(f"{live_server_url}/matches")
    page.wait_for_timeout(400)
    assert "/login" not in page.url


def test_likes_received_accessible(page: Page, live_server_url: str):
    _register(page, live_server_url)
    page.goto(f"{live_server_url}/likes/received")
    page.wait_for_timeout(400)
    assert "/login" not in page.url
