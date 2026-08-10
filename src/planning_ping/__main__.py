"""Executable composition root for PlanningPing."""

from __future__ import annotations

import os

from planning_ping.backend.factory import create_services
from planning_ping.contracts import AppServices
from planning_ping.ui.app import PlanningPingApp, run_app


def close_services(services: AppServices) -> None:
    """Close resources owned by the concrete service composition."""

    search_service = services.search
    search_service._database.close()


def _run_smoke(services: AppServices) -> None:
    app = PlanningPingApp(services)

    def stop_after_startup() -> None:
        if app._window_exists:
            app.after_idle(app.quit)
        else:
            app.after(1, stop_after_startup)

    app.after(1, stop_after_startup)
    try:
        app.mainloop()
    finally:
        for callback_id in app.tk.splitlist(app.tk.call("after", "info")):
            app.tk.call("after", "cancel", callback_id)
        app.destroy()


def main() -> int:
    """Construct the production services and run the desktop application."""

    services = create_services()
    try:
        if os.environ.get("PLANNINGPING_SMOKE_TEST") == "1":
            _run_smoke(services)
        else:
            run_app(services)
    finally:
        close_services(services)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
