# Organisation admin pages (Members, Circles, Announcements)

Routes `/members`, `/circles`, `/announce`; registered by one `register_org_pages(app)` call in `admin_console.py`.
An admin adds a person, puts them in circles and sends announcements here, never via the database.
Data comes from `/admin/*` (`contracts/chat/admin_org.py`) with `CONSOLE_SOURCE=gateway`; the default is in-memory sample data.
Same `CONSOLE_GATEWAY_URL` / `_TOKEN` / `_HEADERS` as the review queue; the token must belong to a site admin (403 otherwise).
A new person's sign-in code is shown once as a QR (optional `segno`) plus text, and is never stored.
Removing and sending ask twice; a failed send keeps its request id, so a retry posts once.
Test: `PYTHONPATH=../.. ./.venv/Scripts/python.exe -m pytest tests/ -q` (one suite runs over the sample data and the chat mock).
Gap: the elder app cannot yet accept a server-issued token, so the code cannot be used on a phone.
