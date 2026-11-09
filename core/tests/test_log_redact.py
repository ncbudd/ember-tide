"""A sign-in token in a logged path (the phone's /api/live?token=…) never reaches the logs."""

import logging

from embertide.__main__ import RedactTokens


def test_token_is_redacted():
    r = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1, '%s - "WebSocket %s" [accepted]',
                          ("1.2.3.4:5", "/api/live?token=abcDEF123-_.x&x=1"), None)
    assert RedactTokens().filter(r)
    assert "abcDEF123" not in r.getMessage() and "token=***&x=1" in r.getMessage()
