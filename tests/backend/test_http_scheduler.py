from __future__ import annotations

import sys
import ssl
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import planning_ping.backend.http as planning_http
from planning_ping.backend.http import (
    CouncilBrowserClient,
    CouncilFetchError,
    CouncilHttpClient,
    browser_fallback_recommended,
    monitor_council_requests,
)
from planning_ping.backend.adapters.arcus import ArcusCouncilConfig, ArcusPlanningScraper
from planning_ping.backend.adapters.wiltshire import WiltshireCouncilConfig, WiltshirePlanningScraper
from planning_ping.backend.scheduler import PlatformAwareScheduler, ScheduledTask


class FakeHeaders:
    def get_content_charset(self) -> str:
        return "utf-8"

    def get(self, name: str, default: object = None) -> object:
        return default


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

    def test_certificate_errors_remain_visible_and_never_retry_without_verification(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be disabled"):
            CouncilHttpClient(verify_tls=False)

        class CertificateOpener:
            def __init__(self) -> None:
                self.calls = 0

            def open(self, request: object, timeout: float) -> FakeResponse:
                self.calls += 1
                if self.calls == 1:
                    raise URLError(ssl.SSLCertVerificationError("certificate verify failed"))
                return FakeResponse(b"<html>must not be reached</html>")

        class Client(CouncilHttpClient):
            def __init__(self) -> None:
                super().__init__(min_delay_seconds=0, retries=1)
                self.opener = CertificateOpener()

            def _opener(self) -> CertificateOpener:
                return self.opener

            def _pause_before_retry(self, *args: object, **kwargs: object) -> None:
                return None

        client = Client()
        with self.assertRaisesRegex(CouncilFetchError, "certificate|Network error"):
            client.get("https://planning.example.test")
        self.assertEqual(1, client.opener.calls)
        self.assertTrue(client.verify_tls)

    def test_production_salesforce_adapters_use_verified_tls(self) -> None:
        arcus = ArcusPlanningScraper(ArcusCouncilConfig("Alpha", "https://alpha.test"))
        wiltshire = WiltshirePlanningScraper(WiltshireCouncilConfig("Wiltshire", "https://wiltshire.test"))
        try:
            self.assertTrue(arcus.http.verify_tls)
            self.assertTrue(wiltshire.http.verify_tls)
        finally:
            arcus.close()
            wiltshire.close()

    def test_default_and_explicit_ports_share_one_hostname_gate(self) -> None:
        first_entered = threading.Event()
        second_entered = threading.Event()
        release = threading.Event()
        errors: list[Exception] = []

        class BlockingResponse(FakeResponse):
            def __init__(self, entered: threading.Event) -> None:
                super().__init__(b"<html>ready</html>")
                self.entered = entered

            def read(self) -> bytes:
                self.entered.set()
                release.wait(2)
                return self.body

        class Opener:
            def __init__(self, entered: threading.Event) -> None:
                self.entered = entered

            def open(self, request: object, timeout: float) -> BlockingResponse:
                return BlockingResponse(self.entered)

        class Client(CouncilHttpClient):
            def __init__(self, key: str, entered: threading.Event) -> None:
                super().__init__(min_delay_seconds=0, concurrency_key=key, concurrency_limit=2)
                self.opener = Opener(entered)

            def _opener(self) -> Opener:
                return self.opener

        def fetch(client: Client, url: str) -> None:
            try:
                client.get(url)
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        first = threading.Thread(
            target=fetch,
            args=(Client("idox", first_entered), "https://shared-host.test/one"),
        )
        second = threading.Thread(
            target=fetch,
            args=(Client("northgate", second_entered), "https://shared-host.test:443/two"),
        )
        try:
            first.start()
            self.assertTrue(first_entered.wait(1))
            second.start()
            time.sleep(0.1)
            self.assertFalse(second_entered.is_set())
        finally:
            release.set()
            first.join(2)
            second.join(2)
        self.assertFalse(errors)

    def test_cross_host_redirect_releases_source_and_obeys_target_hostname_gate(self) -> None:
        target_busy = threading.Event()
        release_target = threading.Event()
        redirect_returned = threading.Event()
        redirect_target_entered = threading.Event()
        source_probe_entered = threading.Event()
        results: list[str] = []
        errors: list[Exception] = []

        class Response(FakeResponse):
            def __init__(
                self,
                url: str,
                body: bytes,
                *,
                status: int = 200,
                location: str | None = None,
                entered: threading.Event | None = None,
                release: threading.Event | None = None,
            ) -> None:
                super().__init__(body)
                self.url = url
                self.status = status
                self.entered = entered
                self.release = release
                self.headers = FakeHeaders()
                self.location = location

            def geturl(self) -> str:
                return self.url

            def read(self) -> bytes:
                if self.entered:
                    self.entered.set()
                if self.release:
                    self.release.wait(2)
                return self.body

        class RedirectHeaders(FakeHeaders):
            def __init__(self, location: str) -> None:
                self.location = location

            def get(self, name: str, default: object = None) -> object:
                return self.location if name.casefold() == "location" else default

        class TargetOpener:
            def open(self, request: object, timeout: float) -> Response:
                return Response(
                    request.full_url,
                    b"target holder",
                    entered=target_busy,
                    release=release_target,
                )

        class RedirectOpener:
            def open(self, request: object, timeout: float) -> Response:
                if request.full_url == "https://source-host.test/start":
                    redirect_returned.set()
                    response = Response(
                        request.full_url,
                        b"redirect",
                        status=302,
                        location="https://target-host.test/final",
                    )
                    response.headers = RedirectHeaders("https://target-host.test/final")
                    return response
                return Response(
                    request.full_url,
                    b"redirect target",
                    entered=redirect_target_entered,
                )

        class SourceProbeOpener:
            def open(self, request: object, timeout: float) -> Response:
                return Response(request.full_url, b"source probe", entered=source_probe_entered)

        class Client(CouncilHttpClient):
            def __init__(self, opener: object) -> None:
                super().__init__(min_delay_seconds=0, retries=0)
                self.opener = opener

            def _opener(self) -> object:
                return self.opener

        def fetch(client: Client, url: str) -> None:
            try:
                results.append(client.get(url).text)
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        holder = threading.Thread(
            target=fetch,
            args=(Client(TargetOpener()), "https://target-host.test/busy"),
        )
        redirected = threading.Thread(
            target=fetch,
            args=(Client(RedirectOpener()), "https://source-host.test/start"),
        )
        source_probe = threading.Thread(
            target=fetch,
            args=(Client(SourceProbeOpener()), "https://source-host.test/probe"),
        )
        try:
            holder.start()
            self.assertTrue(target_busy.wait(1))
            redirected.start()
            self.assertTrue(redirect_returned.wait(1))
            time.sleep(0.05)
            self.assertFalse(redirect_target_entered.is_set())
            source_probe.start()
            self.assertTrue(source_probe_entered.wait(1), "redirect retained the source-host gate")
        finally:
            release_target.set()
            holder.join(2)
            redirected.join(2)
            source_probe.join(2)
        self.assertTrue(redirect_target_entered.is_set())
        self.assertIn("redirect target", results)
        self.assertFalse(errors)

    def test_platform_capacity_reduces_on_rate_limit_and_recovers_after_successes(self) -> None:
        platform = f"adaptive-{id(self)}"

        class BlockingOpener:
            def __init__(self, entered: threading.Event, release: threading.Event) -> None:
                self.entered = entered
                self.release = release

            def open(self, request: object, timeout: float) -> FakeResponse:
                class Response(FakeResponse):
                    def read(inner_self) -> bytes:
                        self.entered.set()
                        self.release.wait(2)
                        return inner_self.body

                return Response(b"<html>ready</html>")

        class RateLimitedOpener:
            def open(self, request: object, timeout: float) -> FakeResponse:
                raise HTTPError(request.full_url, 429, "Too Many Requests", FakeHeaders(), None)

        class Client(CouncilHttpClient):
            def __init__(self, opener: object) -> None:
                super().__init__(
                    min_delay_seconds=0,
                    retries=0,
                    concurrency_key=platform,
                    concurrency_limit=2,
                )
                self.opener = opener

            def _opener(self) -> object:
                return self.opener

        errors: list[Exception] = []

        def fetch(client: Client, url: str) -> None:
            try:
                client.get(url)
            except Exception as exc:
                errors.append(exc)

        with self.assertRaisesRegex(CouncilFetchError, "HTTP 429"):
            Client(RateLimitedOpener()).get("https://rate-limit.test/search")

        first_entered, second_entered = threading.Event(), threading.Event()
        first_release, second_release = threading.Event(), threading.Event()
        first = threading.Thread(
            target=fetch,
            args=(Client(BlockingOpener(first_entered, first_release)), "https://adaptive-one.test"),
        )
        second = threading.Thread(
            target=fetch,
            args=(Client(BlockingOpener(second_entered, second_release)), "https://adaptive-two.test"),
        )
        try:
            first.start()
            self.assertTrue(first_entered.wait(1))
            second.start()
            time.sleep(0.05)
            self.assertFalse(second_entered.is_set(), "rate limiting did not reduce platform capacity")
            first_release.set()
            self.assertTrue(second_entered.wait(1))
            second_release.set()
            first.join(2)
            second.join(2)

            recovered_one, recovered_two = threading.Event(), threading.Event()
            recovered_release = threading.Event()
            third = threading.Thread(
                target=fetch,
                args=(Client(BlockingOpener(recovered_one, recovered_release)), "https://adaptive-three.test"),
            )
            fourth = threading.Thread(
                target=fetch,
                args=(Client(BlockingOpener(recovered_two, recovered_release)), "https://adaptive-four.test"),
            )
            third.start()
            fourth.start()
            self.assertTrue(recovered_one.wait(1))
            self.assertTrue(recovered_two.wait(1), "successful requests did not restore platform capacity")
            recovered_release.set()
            third.join(2)
            fourth.join(2)
        finally:
            first_release.set()
            second_release.set()
        self.assertFalse(errors)

    def test_browser_keeps_webdriver_visible_and_rejects_challenges_without_waiting(self) -> None:
        class FakeOptions:
            def __init__(self) -> None:
                self.arguments: list[str] = []
                self.experimental: list[tuple[str, object]] = []

            def add_argument(self, value: str) -> None:
                self.arguments.append(value)

            def add_experimental_option(self, name: str, value: object) -> None:
                self.experimental.append((name, value))

        class FakeDriver:
            page_source = '<script src="https://captcha-sdk.awswaf.com/captcha.js"></script>'
            title = "Checking your browser"
            current_url = "https://planning.example.test/search"

            def __init__(self, options: FakeOptions | None = None) -> None:
                self.options = options
                self.cdp_commands: list[tuple[str, object]] = []

            def execute_cdp_cmd(self, command: str, payload: object) -> None:
                self.cdp_commands.append((command, payload))

        with (
            mock.patch.object(planning_http, "ChromeOptions", FakeOptions),
            mock.patch.object(planning_http, "ChromeWebDriver", FakeDriver),
        ):
            driver = CouncilBrowserClient()._create_driver()

        self.assertFalse(driver.cdp_commands)
        self.assertNotIn("--no-sandbox", driver.options.arguments)
        self.assertFalse(any("AutomationControlled" in item for item in driver.options.arguments))
        self.assertNotIn(("excludeSwitches", ["enable-automation"]), driver.options.experimental)

        browser = CouncilBrowserClient()
        browser._driver = FakeDriver()
        with self.assertRaisesRegex(CouncilFetchError, "CAPTCHA|firewall"):
            browser._raise_for_error_page()
        with mock.patch.object(
            planning_http,
            "WebDriverWait",
            side_effect=AssertionError("challenge pages must fail before a browser wait"),
        ):
            with self.assertRaisesRegex(CouncilFetchError, "CAPTCHA|firewall"):
                browser._wait_for_usable_page()

    def test_browser_fallback_is_never_recommended_for_access_controls(self) -> None:
        for message in (
            "Blocked by web application firewall",
            "CAPTCHA challenge detected",
            "HTTP 403 while fetching portal",
            "HTTP 503 while fetching portal",
            "SSL certificate verify failed",
        ):
            with self.subTest(message=message):
                self.assertFalse(browser_fallback_recommended(CouncilFetchError(message)))


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
