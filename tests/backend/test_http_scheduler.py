from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from planning_ping.backend.http import CouncilFetchError, CouncilHttpClient, monitor_council_requests
from planning_ping.backend.scheduler import PlatformAwareScheduler, ScheduledTask


class FakeHeaders:
    def get_content_charset(self) -> str:
        return "utf-8"


class FakeResponse:
    headers = FakeHeaders()
    status = 200

    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def geturl(self) -> str:
        return "https://planning.example.test/search"

    def read(self) -> bytes:
        return self.body


class HttpBoundaryTests(unittest.TestCase):
    def test_retries_empty_responses_with_a_bound_and_rejects_waf_challenges(self) -> None:
        class Opener:
            def __init__(self) -> None:
                self.calls = 0

            def open(self, request: object, timeout: float) -> FakeResponse:
                self.calls += 1
                return FakeResponse(b"" if self.calls == 1 else b"<html>ready</html>")

        class Client(CouncilHttpClient):
            def __init__(self) -> None:
                super().__init__(min_delay_seconds=0, retries=1)
                self.opener = Opener()

            def _opener(self) -> Opener:
                return self.opener

            def _pause_before_retry(self, *args: object, **kwargs: object) -> None:
                return None

        client = Client()
        self.assertEqual("<html>ready</html>", client.get("https://planning.example.test").text)
        self.assertEqual(2, client.opener.calls)

        class WafClient(Client):
            def _opener(self):
                class WafOpener:
                    def open(self, request: object, timeout: float) -> FakeResponse:
                        return FakeResponse(b'<script src="/_Incapsula_Resource?SWJIYLWA=abc"></script>')

                return WafOpener()

        with self.assertRaisesRegex(CouncilFetchError, "firewall"):
            WafClient().get("https://planning.example.test")

    def test_cancellation_is_checked_before_a_network_request(self) -> None:
        class Client(CouncilHttpClient):
            def _opener(self):
                raise AssertionError("cancelled request reached the network")

        with monitor_council_requests(lambda: None, should_cancel=lambda: True):
            with self.assertRaisesRegex(CouncilFetchError, "cancelled"):
                Client(min_delay_seconds=0).get("https://planning.example.test")


class SchedulerBoundaryTests(unittest.TestCase):
    def test_allows_one_active_request_per_host_and_adapts_after_rate_limiting(self) -> None:
        scheduler = PlatformAwareScheduler[str](platform_limits={"idox": 2}, default_platform_limit=1, host_limit=1, rate_limit_cooldown_seconds=0)
        scheduler.load_phase(
            (
                ScheduledTask("A", "idox", "shared.test"),
                ScheduledTask("B", "idox", "shared.test"),
                ScheduledTask("C", "idox", "other.test"),
            )
        )

        first = scheduler.acquire()
        second = scheduler.acquire()
        self.assertEqual(("A", "C"), (first.item, second.item))
        adjustment = scheduler.release(first, signal="rate_limited")
        self.assertEqual(1, adjustment.limit)
        scheduler.release(second)
        third = scheduler.acquire()
        self.assertEqual("B", third.item)
        scheduler.release(third)


if __name__ == "__main__":
    unittest.main()
