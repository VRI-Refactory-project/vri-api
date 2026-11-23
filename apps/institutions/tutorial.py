"""The first-sign-in tutorial for institution staff (V2-065).

The content lives here, on the server, so it can change without a new app release. Each step's
`target` names the part of the staff screen the web app highlights for that step (its
`data-tour` attribute); None means the step is shown on its own, in the middle of the screen.
"""

STAFF_TUTORIAL = (
    {
        "id": "welcome",
        "title": "Welcome to SignConnect",
        "body": (
            "You're signed in. This short tour shows where everything is, so you can connect a "
            "Deaf client with an interpreter on your own. It takes about a minute."
        ),
        "target": None,
    },
    {
        "id": "sign-in",
        "title": "Signing in",
        "body": (
            "Sign in with the email and password from your institution. You chose your own "
            "password the first time; change it any time in Settings."
        ),
        "target": "settings",
    },
    {
        "id": "start-session",
        "title": "Start a session",
        "body": (
            "When a Deaf client is with you, choose Start Interpreter Session and say what it is "
            "about. An interpreter usually joins in about two minutes. Use Emergency Request "
            "when it cannot wait."
        ),
        "target": "start-session",
    },
    {
        "id": "booked",
        "title": "Book ahead",
        "body": (
            "Need an interpreter at a set time? Choose Book a session. Sessions you book show "
            "here, with a button to join when it is time."
        ),
        "target": "booked-sessions",
    },
    {
        "id": "sessions",
        "title": "Your sessions",
        "body": (
            "Sessions lists every session you have had: when, with which interpreter and for how "
            "long. Only you can see your sessions."
        ),
        "target": "sessions",
    },
    {
        "id": "help",
        "title": "Help is always here",
        "body": (
            "Help & Support answers common questions. You can take this tour again from there "
            "whenever you like."
        ),
        "target": "help",
    },
)

LAST_STEP = len(STAFF_TUTORIAL) - 1
