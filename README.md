# JuveTube YouTube transcript worker

Deploy this folder as a Render **Web Service**. It exposes `/transcript` and
uses a bearer token plus an optional base64-encoded Netscape cookie file.

Do not commit cookies or tokens. Put them only in Render Environment Variables.

Build command:

```text
pip install -r requirements.txt
```

Start command:

```text
gunicorn --bind 0.0.0.0:$PORT app:app
```

Render variables:

- `WORKER_TOKEN`: a random long secret.
- `YOUTUBE_COOKIES_B64`: base64 of a freshly exported cookie file (optional;
  never reuse the cookie pasted into chat).

Test `/health`, then call:

```text
GET /transcript?video_id=VIDEO_ID&lang=it
Authorization: Bearer WORKER_TOKEN
```
