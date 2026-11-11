from django.test import SimpleTestCase, override_settings

from apps.realtime.ice import ice_servers


class IceServersTests(SimpleTestCase):
    @override_settings(
        LIVEKIT_STUN_URLS=[],
        LIVEKIT_TURN_URLS=[],
        LIVEKIT_TURN_USERNAME="",
        LIVEKIT_TURN_CREDENTIAL="",
    )
    def test_nothing_configured_gives_an_empty_list(self):
        self.assertEqual(ice_servers(), [])

    @override_settings(
        LIVEKIT_STUN_URLS=["stun:stun.example.com:3478"],
        LIVEKIT_TURN_URLS=[],
        LIVEKIT_TURN_USERNAME="",
        LIVEKIT_TURN_CREDENTIAL="",
    )
    def test_stun_only(self):
        self.assertEqual(
            ice_servers(),
            [{"urls": ["stun:stun.example.com:3478"]}],
        )

    @override_settings(
        LIVEKIT_STUN_URLS=["stun:stun.example.com:3478"],
        LIVEKIT_TURN_URLS=["turn:turn.example.com:3478", "turns:turn.example.com:5349"],
        LIVEKIT_TURN_USERNAME="relay-user",
        LIVEKIT_TURN_CREDENTIAL="relay-secret",
    )
    def test_stun_and_turn_with_credentials(self):
        self.assertEqual(
            ice_servers(),
            [
                {"urls": ["stun:stun.example.com:3478"]},
                {
                    "urls": ["turn:turn.example.com:3478", "turns:turn.example.com:5349"],
                    "username": "relay-user",
                    "credential": "relay-secret",
                },
            ],
        )

    @override_settings(
        LIVEKIT_STUN_URLS=[],
        LIVEKIT_TURN_URLS=["turn:turn.example.com:3478"],
        LIVEKIT_TURN_USERNAME="relay-user",
        LIVEKIT_TURN_CREDENTIAL="relay-secret",
    )
    def test_turn_without_stun(self):
        self.assertEqual(
            ice_servers(),
            [
                {
                    "urls": ["turn:turn.example.com:3478"],
                    "username": "relay-user",
                    "credential": "relay-secret",
                }
            ],
        )
