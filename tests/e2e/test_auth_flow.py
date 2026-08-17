"""E2E: registration and login flows."""
import secrets

import pytest
from playwright.sync_api import Page, expect


def _unique_email() -> str:
    return f"e2e_{secrets.token_hex(5)}@test.com"


def _ignore_401(msg):
    """Console error filter — 401/403 are expected for anonymous background requests."""
    if msg.type == "error":
        text = msg.text
        return "401" not in text and "Unauthorized" not in text and "403" not in text
    return False


def test_register_page_loads(page: Page, live_server_url: str):
    page.goto(f"{live_server_url}/register")
    expect(page.locator("input[name='email']")).to_be_visible()
    expect(page.locator("input[name='password']")).to_be_visible()


def test_login_page_loads(page: Page, live_server_url: str):
    page.goto(f"{live_server_url}/login")
    expect(page.locator("input[type='email']")).to_be_visible()
    expect(page.locator("input[type='password']")).to_be_visible()


def test_register_empty_email_stays(page: Page, live_server_url: str):
    page.goto(f"{live_server_url}/register")
    page.click("button[type='submit']")
    # HTML5 validation keeps user on page
    assert "/register" in page.url or page.url == f"{live_server_url}/register"


def test_register_weak_password_rejected(page: Page, live_server_url: str):
    page.goto(f"{live_server_url}/register")
    page.fill("input[name='email']", _unique_email())
    page.fill("input[name='password']", "123")
    page.click("button[type='submit']")
    page.wait_for_timeout(500)
    # Should NOT reach swipe or profile
    assert "/swipe" not in page.url and "/profile" not in page.url


def test_full_registration_flow(page: Page, live_server_url: str):
    email = _unique_email()
    page.goto(f"{live_server_url}/register")
    page.fill("input[name='email']", email)
    page.fill("input[name='password']", "TestE2E123!")
    page.click("button[type='submit']")
    page.wait_for_timeout(2000)
    # After registration → profile/edit or swipe (not login or register)
    assert "/register" not in page.url
    assert "/login" not in page.url
    assert page.url != live_server_url + "/"


def test_login_wrong_password(page: Page, live_server_url: str):
    page.goto(f"{live_server_url}/login")
    page.fill("input[type='email']", "nonexistent@test.com")
    page.fill("input[type='password']", "WrongPass999!")
    page.click("button[type='submit']")
    page.wait_for_timeout(600)
    # Should stay on login
    assert "/login" in page.url


def test_register_then_login(page: Page, live_server_url: str):
    email = _unique_email()
    # 1. Register
    page.goto(f"{live_server_url}/register")
    page.fill("input[name='email']", email)
    page.fill("input[name='password']", "TestE2E123!")
    page.click("button[type='submit']")
    page.wait_for_timeout(1500)
    assert "/login" not in page.url

    # 2. Logout (delete access_token cookie via navigation)
    page.context.clear_cookies()

    # 3. Login
    page.goto(f"{live_server_url}/login")
    page.fill("input[type='email']", email)
    page.fill("input[type='password']", "TestE2E123!")
    page.click("button[type='submit']")
    page.wait_for_timeout(1500)
    # Should be on authenticated page now
    assert "/login" not in page.url


def test_login_page_no_js_errors(page: Page, live_server_url: str):
    errors = []
    def _on_console(msg):
        if msg.type == "error":
            text = msg.text
            if "401" not in text and "Unauthorized" not in text and "403" not in text:
                errors.append(text)
    page.on("console", _on_console)
    page.goto(f"{live_server_url}/login")
    page.wait_for_timeout(800)
    assert errors == [], f"JS errors on login: {errors}"


def test_register_page_has_csrf(page: Page, live_server_url: str):
    page.goto(f"{live_server_url}/register")
    csrf_input = page.locator("input[name='csrftoken']")
    expect(csrf_input).to_have_count(1)
    assert len(csrf_input.get_attribute("value") or "") > 0


def test_login_remember_email_from_query(page: Page, live_server_url: str):
    email = "test@example.com"
    page.goto(f"{live_server_url}/login?email={email}")
    # The email query param pre-fills via hidden input (auto-submit or value)
    # Just check the page loads without error
    expect(page.locator("input[type='password']")).to_be_visible()
