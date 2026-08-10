"""PlanningPing's CustomTkinter application shell."""

from __future__ import annotations

import customtkinter as ctk
from tkinterdnd2 import TkinterDnD

from planning_ping.contracts import AppServices

from .components import FocusButton, RadarMark
from .models import NAVIGATION_ROUTES
from .screens import HomeScreen, IssuesScreen, SearchNewScreen, SearchSavedScreen, SendApplicationsScreen
from .theme import COLORS, SPACING, TYPE, configure_theme


class PlanningPingApp(ctk.CTk):
    """Resizable Windows-first shell backed by injected application services."""

    TITLES = {
        "home": "Home",
        "search_new": "Search New Applications",
        "search_saved": "Search Saved Applications",
        "send_applications": "Send Applications",
        "view_issues": "View Issues",
    }

    def __init__(self, services: AppServices):
        configure_theme()
        super().__init__(fg_color=COLORS["background"])
        TkinterDnD.require(self)
        self.title("PlanningPing")
        self.geometry("1280x800")
        self.minsize(1024, 680)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._destroying = False
        sidebar = ctk.CTkFrame(self, width=245, corner_radius=0, fg_color=COLORS["surface"])
        sidebar.grid(row=0, column=0, rowspan=2, sticky="nsew")
        sidebar.grid_propagate(False)
        brand = ctk.CTkFrame(sidebar, fg_color="transparent")
        brand.pack(fill="x", padx=SPACING["md"], pady=SPACING["lg"])
        RadarMark(brand, size=48).pack(side="left")
        ctk.CTkLabel(brand, text="PlanningPing", font=TYPE["heading"]).pack(side="left", padx=SPACING["sm"])
        self.navigation_buttons: dict[str, FocusButton] = {}
        for route in NAVIGATION_ROUTES:
            button = FocusButton(
                sidebar,
                text=self.TITLES[route],
                anchor="w",
                height=42,
                command=lambda target=route: self.navigate(target),
            )
            button.pack(fill="x", padx=SPACING["md"], pady=SPACING["xs"])
            self.navigation_buttons[route] = button
        topbar = ctk.CTkFrame(self, height=58, corner_radius=0, fg_color=COLORS["surface"])
        topbar.grid(row=0, column=1, sticky="ew")
        topbar.grid_propagate(False)
        self.top_title_var = ctk.StringVar(value="Home")
        ctk.CTkLabel(topbar, textvariable=self.top_title_var, font=TYPE["heading"], anchor="w").pack(
            fill="both", expand=True, padx=SPACING["lg"]
        )
        self.workspace = ctk.CTkFrame(self, fg_color=COLORS["background"], corner_radius=0)
        self.workspace.grid(row=1, column=1, sticky="nsew")
        self.workspace.grid_columnconfigure(0, weight=1)
        self.workspace.grid_rowconfigure(0, weight=1)
        self.screens = {
            "home": HomeScreen(self.workspace, self.navigate),
            "search_new": SearchNewScreen(self.workspace, services.search, self),
            "search_saved": SearchSavedScreen(self.workspace, services.applications),
            "send_applications": SendApplicationsScreen(self.workspace),
            "view_issues": IssuesScreen(self.workspace, services.issues),
        }
        for screen in self.screens.values():
            screen.grid(row=0, column=0, sticky="nsew")
            screen.grid_remove()
        self.active_route = ""
        self.navigate("home")
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def navigate(self, route: str) -> None:
        if route not in self.screens:
            raise ValueError(f"Unknown route: {route}")
        if self.active_route:
            self.screens[self.active_route].on_hide()
            self.screens[self.active_route].grid_remove()
            self.navigation_buttons[self.active_route].configure(fg_color=COLORS["surface_raised"])
        self.active_route = route
        screen = self.screens[route]
        screen.grid()
        screen.on_show()
        self.navigation_buttons[route].configure(fg_color="#3A3A3A")
        self.top_title_var.set(self.TITLES[route])

    def destroy(self) -> None:
        if self._destroying:
            return
        self._destroying = True
        for screen in self.screens.values():
            screen.shutdown()
        super().destroy()


def run_app(services: AppServices) -> None:
    app = PlanningPingApp(services)
    app.mainloop()
