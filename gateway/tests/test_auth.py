import pytest
from auth import AuthError, issue_token, verify_token


def test_issue_then_verify_round_trips():
    token, user_id = issue_token("Bob Smith!")
    assert user_id == "bob_smith"
    assert verify_token(token) == user_id


def test_display_name_sanitization():
    # Same convention matrix_circle_store.py's _localpart uses -- a
    # token's user_id must always be safe to hand straight to any
    # backbone without a second sanitization pass.
    _, user_id = issue_token("  ÜNïcode Ãccent!! ")
    assert user_id  # non-empty
    assert user_id.replace("_", "").isalnum() or user_id.isalnum()


def test_empty_display_name_after_sanitizing_is_rejected():
    with pytest.raises(AuthError):
        issue_token("!!!###")


def test_tampered_signature_is_rejected():
    token, _ = issue_token("carol")
    tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
    with pytest.raises(AuthError, match="signature"):
        verify_token(tampered)


def test_expired_token_is_rejected():
    token, _ = issue_token("dave", ttl_seconds=-1)
    with pytest.raises(AuthError, match="expired"):
        verify_token(token)


def test_malformed_token_is_rejected():
    with pytest.raises(AuthError):
        verify_token("not-a-real-token")
    with pytest.raises(AuthError):
        verify_token("")


def test_two_different_users_get_different_tokens():
    token_a, uid_a = issue_token("alice")
    token_b, uid_b = issue_token("bob")
    assert token_a != token_b
    assert uid_a != uid_b
    # A token can't be replayed as a different identity.
    assert verify_token(token_a) == uid_a
    assert verify_token(token_a) != uid_b
