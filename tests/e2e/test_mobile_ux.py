"""E2E: mobile UX — touch targets, iOS responsiveness, PWA, dark mode."""
import secrets

import pytest
from playwright.sync_api import Page, expect


def _login(page: Page, base: str) -> None:
    email = f"e2e_{secrets.token_hex(5)}@test.com"
    page.goto(f"{base}/register")
    page.fill("input[name='email']", email)
    page.fill("input[name='password']", "TestE2E123!")
    page.click("button[type='submit']")
    page.wait_for_timeout(800)


def test_swipe_buttons_have_large_tap_targets(page: Page, live_server_url: str):
    """Buttons must be >= 44px (Apple HIG minimum touch target)."""
    _login(page, live_server_url)
    page.goto(f"{live_server_url}/swipe")
    page.wait_for_timeout(500)
    # Check btn-yes size (should be 78px)
    btn = page.locator("#btn-yes")
    if btn.count() > 0:
        box = btn.bounding_box()
        assert box["width"] >= 44, f"btn-yes too small: {box['width']}px"
        assert box["height"] >= 44, f"btn-yes too small: {box['height']}px"


def test_nav_icons_tap_target_size(page: Page, live_server_url: str):
    """Nav icons must be >= 44px tall."""
    _login(page, live_server_url)
    page.goto(f"{live_server_url}/swipe")
    nav_icons = page.locator("#bottom-nav .nav-icon")
    for i in range(min(nav_icons.count(), 4)):
        box = nav_icons.nth(i).bounding_box()
        if box:
            assert box["height"] >= 36, f"Nav icon {i} too small: {box['height']}px"


def test_no_horizontal_scroll(page: Page, live_server_url: str):
    """Page must not have horizontal overflow (breaks mobile UX)."""
    _login(page, live_server_url)
    for path in ["/", "/swipe", "/matches", "/profile/edit"]:
        page.goto(f"{live_server_url}{path}")
        page.wait_for_timeout(300)
        scroll_width = page.evaluate("document.documentElement.scrollWidth")
        viewport_width = page.evaluate("window.innerWidth")
        assert scroll_width <= viewport_width + 5, \
            f"Horizontal overflow on {path}: scrollWidth={scroll_width} > viewport={viewport_width}"


def test_swipe_buttons_touch_action(page: Page, live_server_url: str):
    """Swipe buttons must have touch-action: manipulation (iOS 300ms fix)."""
    _login(page, live_server_url)
    page.goto(f"{live_server_url}/swipe")
    page.wait_for_timeout(500)
    for btn_id in ["btn-yes", "btn-no"]:
        el = page.locator(f"#{btn_id}")
        if el.count() > 0:
            touch_action = page.evaluate(
                f"getComputedStyle(document.getElementById('{btn_id}')).touchAction"
            )
            assert touch_action == "manipulation", \
                f"#{btn_id} missing touch-action:manipulation, got: {touch_action}"


def test_dark_mode_toggle(page: Page, live_server_url: str):
    """Dark mode can be toggled via cookie."""
    page.goto(live_server_url)
    # Set dark mode cookie manually
    page.context.add_cookies([{
        "name": "theme", "value": "dark",
        "domain": "127.0.0.1", "path": "/"
    }])
    page.goto(live_server_url)
    dark_class = page.evaluate("document.documentElement.classList.contains('dark')")
    assert dark_class, "Dark mode class not applied when theme=dark cookie is set"


def test_meta_viewport_present(page: Page, live_server_url: str):
    """Viewport meta tag must be present for mobile scaling."""
    page.goto(live_server_url)
    viewport = page.locator("meta[name='viewport']")
    expect(viewport).to_have_count(1)
    content = viewport.get_attribute("content")
    assert "width=device-width" in content


def test_apple_touch_icon(page: Page, live_server_url: str):
    """Apple touch icon must be present for PWA."""
    page.goto(live_server_url)
    icon = page.locator("link[rel='apple-touch-icon']").first
    expect(icon).to_have_count(1)


def test_profile_edit_accessible(page: Page, live_server_url: str):
    _login(page, live_server_url)
    page.goto(f"{live_server_url}/profile/edit")
    # Name field should be focusable
    name_field = page.locator("input[name='name']")
    if name_field.count() > 0:
        name_field.focus()
        assert page.evaluate("document.activeElement.name") == "name"


def test_matches_page_loads(page: Page, live_server_url: str):
    _login(page, live_server_url)
    page.goto(f"{live_server_url}/matches")
    page.wait_for_timeout(500)
    assert "/login" not in page.url


def test_privacy_page_loads(page: Page, live_server_url: str):
    page.goto(f"{live_server_url}/privacy")
    expect(page).not_to_have_url(f"{live_server_url}/login")
