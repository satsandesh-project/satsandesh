"""Typed chat messages for a reader of another language (elder_app.py, the chat script).

The behaviour lives in JavaScript and is checked in tests/js/chat_text.test.js under Node with
jsdom; this renders the page script into a file and runs it, and is skipped when node or jsdom is
not installed (as the bhajan rooms tests are). The plain Python checks below always run.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from elder_app import elder_app as app

JS_TESTS = Path(__file__).resolve().parent / "js"


def _render_chat_js() -> str:
    return app.CHAT_CONNECT_JS_TEMPLATE % {
        "gateway_url": json.dumps("http://gw"),
        "undo_window_seconds": app.UNDO_WINDOW_SECONDS,
        "connecting": json.dumps("Connecting"),
        "no_messages": json.dumps("No messages"),
        "status_text": json.dumps(
            {
                lang: {key: app.TEXTS[lang][key] for key in app.STATUS_TEXT_KEYS}
                for lang in ("en", "te")
            }
        ),
        "status_pending": json.dumps("pending"),
        "status_sent": json.dumps("sent"),
        "status_delivered": json.dumps("delivered"),
        "status_cancelled": json.dumps("cancelled"),
        "send_error_not_member": json.dumps("x"),
        "send_error_announcement_only": json.dumps("x"),
        "recv_text": json.dumps(
            {
                lang: {key: app.TEXTS[lang][key] for key in app.RECEIVER_TEXT_KEYS}
                for lang in ("en", "te")
            }
        ),
        "uuid_v4_fallback": app._UUID_V4_JS_FALLBACK,
    }


def test_the_original_message_label_exists_in_both_languages() -> None:
    assert "recv_original_text" in app.RECEIVER_TEXT_KEYS
    assert app.TEXTS["en"]["recv_original_text"] == "Original message"
    assert app.TEXTS["te"]["recv_original_text"]


def test_the_chat_script_has_the_text_modes() -> None:
    js = _render_chat_js()
    for needle in ("__satTextMode", "t.recv_original_text", "addChoice(t.recv_show_original"):
        assert needle in js, needle


def _jsdom_dir() -> Path | None:
    for candidate in (JS_TESTS / "node_modules", Path(os.environ.get("JSDOM_DIR", "/nonexistent"))):
        if (candidate / "jsdom").is_dir():
            return candidate
    return None


def test_typed_messages_show_the_translation_and_the_original_in_jsdom(tmp_path) -> None:
    node = shutil.which("node")
    modules = _jsdom_dir()
    if not node or not modules:
        pytest.skip("node or jsdom is not installed (npm install in tests/js)")
    chat_js = tmp_path / "chat.js"
    chat_js.write_text(_render_chat_js(), encoding="utf-8")
    result = subprocess.run(
        [node, "--test", str(JS_TESTS / "chat_text.test.js")],
        env={**os.environ, "CHAT_JS": str(chat_js), "NODE_PATH": str(modules)},
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-1500:]
