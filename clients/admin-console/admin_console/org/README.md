# Organisation admin pages (Members, Circles, Announcements)

Routes `/members`, `/circles`, `/announce`; registered by one `register_org_pages(app)` call in `admin_console.py`.
An admin adds a person, puts them in circles and sends announcements here, never via the database.
Data comes from `/admin/*` (`contracts/chat/admin_org.py`) with `CONSOLE_SOURCE=gateway`; the default is in-memory sample data.
Same `CONSOLE_GATEWAY_URL` / `_TOKEN` / `_HEADERS` as the review queue; the token must be a SIGNED one (`python -m app.tokens <admin id>`) of a site admin.
No sign-in code is shown: the gateway's only one today is a 30-day session token, so the page drops it and waits for the single-use claim.
Removing and sending ask twice; a failed send keeps its request id, so a retry posts once.
Test: `PYTHONPATH=../.. ./.venv/Scripts/python.exe -m pytest tests/ -q` (one suite runs over the sample data and the chat mock).
Gap: signing a new person in on their phone comes with the claim (a QR of `<app>/#claim=<code>`, optional `segno`).
