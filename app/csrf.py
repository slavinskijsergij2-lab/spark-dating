"""
CSRF protection using the double-submit cookie pattern.

The security_middleware in main.py sets a `csrftoken` cookie (httponly=False
so JS can read it). On state-changing requests the backend checks that the
token in the form field or request header matches the cookie value.
"""
import logging
import secrets

from fastapi import Form, HTTPException, Request

CSRF_COOKIE = "csrftoken"


def _cookie_tokens(request: Request) -> list[str]:
    """All csrftoken values sent by the browser.

    A browser can hold several cookies with the same name (different path/domain,
    e.g. left over from an older version of the site). JS reads the first one while
    request.cookies keeps only one of them, so comparing against a single value
    rejected every request for such users.
    """
    values = []
    for part in request.headers.get("cookie", "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == CSRF_COOKIE and value:
            values.append(value.strip('"'))
    return values


def _check(request: Request, submitted: str | None) -> None:
    tokens = _cookie_tokens(request)
    if submitted and any(secrets.compare_digest(t, submitted) for t in tokens):
        return
    logging.warning(
        "CSRF rejected %s %s: cookies=%d submitted=%s",
        request.method, request.url.path, len(tokens), "yes" if submitted else "no",
    )
    raise HTTPException(status_code=403, detail="CSRF validation failed")


def validate_csrf_form(
    request: Request,
    csrftoken: str = Form(None),
) -> None:
    """Dependency for HTML form POST endpoints (multipart or urlencoded)."""
    _check(request, csrftoken)


def validate_csrf_header(request: Request) -> None:
    """Dependency for fetch()/AJAX POST endpoints that send x-csrf-token header."""
    _check(request, request.headers.get("x-csrf-token", ""))
