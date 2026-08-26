"""One-off script: seed two real `users` rows for manual Web Push
verification. There's no signup/registration route in the app yet (out of
scope this week) — this is the only way to get real, addressable user ids
to authenticate as (see app/auth.py's widened stub: a bearer/`?token=`
value that parses as a UUID is taken as that user's own id).

Prints each row's id — paste the recipient's into manual_verification's
index.html as the token, and the sender's as the token for whichever
connection you use to trigger the message. Not part of the app itself; see
README.md in this directory.

Run from services/gateway/ (needs the real DATABASE_URL from .env, same as
the app itself — no PYTHONPATH needed, this doesn't touch contracts/):

    ./.venv/Scripts/python.exe manual_verification/seed_test_users.py
"""

from app.db.base import SessionLocal
from app.db.models import User


def main() -> None:
    session = SessionLocal()
    try:
        recipient = User(name="Manual Verify Recipient", preferred_language="en")
        sender = User(name="Manual Verify Sender", preferred_language="en")
        session.add_all([recipient, sender])
        session.commit()
        print(f"recipient_id={recipient.id}")
        print(f"sender_id={sender.id}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
