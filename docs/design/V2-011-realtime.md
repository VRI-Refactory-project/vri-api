# V2-011: Real-time foundation (backend design)

| | |
|---|---|
| **Story** | V2-011: Call signalling. This doc covers the shared WebSocket layer that V2-011, V2-031 and V2-053 build on. |
| **Owner** | Zipporah T (backend) |
| **Sprint** | 4 |
| **Status** | **Planned** (3 Nov 2026). |

Approach signed off by the technical coach: Django Channels for real-time, LiveKit (SFU) for the call media.
Builds on the call-token endpoint (#85).

## 1. Who does what

| Part | Job |
|---|---|
| LiveKit | Carries the audio and video. Handles WebRTC setup (offer, answer, ICE) itself. |
| Django REST | Decides who may join and issues LiveKit tokens (`POST /api/v1/requests/{id}/call-token/`). |
| Django Channels | Pushes app events (status changes, call events, chat) to the people on a session. |
| LiveKit webhooks | Tell Django when people join and leave (later in V2-011). |

## 2. Connecting (web and mobile)

1. The app, already signed in, calls `POST /api/v1/realtime/ticket/` and gets `{"ticket": "..."}`.
   The ticket is signed, lasts 60 seconds and names the user. The login JWT never goes in a URL.
2. The app opens `ws(s)://<host>/ws/sessions/<id>/?ticket=<ticket>`.
3. The server checks the ticket and that the user is on the session, then sends the current status.
4. If the connection drops, the app gets a new ticket and reconnects; the snapshot brings it up to date.
   REST stays available as a fallback.

Tickets are used instead of browser cookies because the mobile app cannot rely on them.

## 3. Who may connect to `ws/sessions/<id>/`

| Person | Allowed |
|---|---|
| The requester | Yes, at any status, so they see their request move from waiting to matched. |
| The interpreter who accepted | Yes, once `ASSIGNED`. |
| An interpreter only offered the request | No. Offers have their own screen. |
| Anyone else, or no valid ticket | No. The connection is closed with code `4403` (`4401` for a missing or expired ticket). |

The hearing participant (no account) is a later step of V2-011, with a link limited to one session.

## 4. Message format

Every message from the server is JSON with the same shape:

```json
{"type": "session.status", "session": 12, "status": "ASSIGNED"}
```

| `type` | Sent when | Owner |
|---|---|---|
| `session.status` | On connect (snapshot) and whenever the status changes | Zipporah (snapshot), Catherine (V2-031 changes) |
| `call.*` | Call events: joined, left, failed | Zipporah (V2-011) |
| `chat.*` | Chat messages | V2-053 |

Statuses are sent as stored (`PENDING`, `ASSIGNED`, ...). Apps map them to labels.

## 5. Sending from backend code

```python
from apps.realtime.events import send_to_session

send_to_session(service_request.pk, "session.status", {"status": service_request.status})
```

It sends only after the database transaction commits, so nobody hears about a change that was rolled back.

## 6. Setup

| Item | Value |
|---|---|
| Packages | `channels`, `channels-redis`, `daphne` |
| Channel layer | Redis (`REDIS_URL`); in-memory in tests |
| Server | ASGI: `asgi.py` sends HTTP to Django and WebSockets to Channels |
| Docker | New `redis` service |
| Deployment | Production moves from Gunicorn to an ASGI server and needs Redis (Shadia) |

## 7. Not in this doc

CONNECTING and ACTIVE statuses, the hearing link, the LiveKit webhook, `CONNECT_TIMEOUT` to `FAILED`,
call metadata audit, and TURN. These follow in V2-011 once this foundation is merged.
