"""
CSRF protection using the double-submit cookie pattern.

The security_middleware in main.py sets a `csrftoken` cookie (httponly=False
so JS can read it). On state-changing requests the backend checks that the
token in the form field or request header matches the cookie value.
"""
import secrets

from fastapi import Form, HTTPException, Request

CSRF_COOKIE = "csrftoken"


def validate_csrf_form(
    request: Request,
    csrftoken: str = Form(None),
):
    """Dependency for HTML form POST endpoints (multipart or urlencoded)."""
    cookie_token = request.cookies.get(CSRF_COOKIE)
    if (
        not cookie_token
        or not csrftoken
        or not secrets.compare_digest(cookie_token, csrftoken)
    ):
        raise HTTPException(status_code=403, detail="CSRF validation failed")


def validate_csrf_header(request: Request):
    """Dependency for fetch()/AJAX POST endpoints that send x-csrf-token header."""
    cookie_token = request.cookies.get(CSRF_COOKIE)
    header_token = request.headers.get("x-csrf-token", "")
    if (
        not cookie_token
        or not header_token
        or not secrets.compare_digest(cookie_token, header_token)
    ):
        raise HTTPException(status_code=403, detail="CSRF validation failed")
