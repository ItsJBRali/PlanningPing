"""The five PlanningPing application screens."""

from __future__ import annotations

import webbrowser
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from tkinter import filedialog

import customtkinter as ctk
from tkinterdnd2 import COPY, DND_FILES

from planning_ping.contracts import ApplicationQueryService, IssueQueryService, SearchService

from .components import BaseScreen, FocusButton, PageHeader, ScrollableTable
from .controllers import BackgroundTaskController, SearchController, SearchViewState
from .models import (
    IssueResultsModel,
    SavedApplicationsModel,
    build_search_request,
    is_openable_url,
    open_url_if_safe,
    parse_drop_paths,
    validate_geojson_selection,
)
from .theme import COLORS, SPACING, TYPE


class HomeScreen(BaseScreen):
    ACTIONS = (
        ("search_new", "Search New Applications", "Choose a boundary and start a new council search."),
        ("search_saved", "Search Saved Applications", "Filter, sort and inspect saved planning applications."),
        ("send_applications", "Send Applications", "Future customer review and sending workflow."),
        ("view_issues", "View Issues", "Review warnings and bounded-completeness failures."),
    )

    def __init__(self, master, navigate: Callable[[str], None]):
        super().__init__(master)
        self.grid_columnconfigure((0, 1), weight=1, uniform="home")
        PageHeader(self, "Home", "Search planning portals, inspect saved results, and review issues.").grid(
            row=0, column=0, columnspan=2, sticky="ew", padx=SPACING["xl"], pady=(SPACING["xl"], SPACING["lg"])
        )
        self.action_buttons: dict[str, FocusButton] = {}
        for index, (route, title, description) in enumerate(self.ACTIONS):
            card = ctk.CTkFrame(self, fg_color=COLORS["surface"], border_color=COLORS["line"], border_width=1)
            card.grid(row=1 + index // 2, column=index % 2, sticky="nsew", padx=SPACING["md"], pady=SPACING["md"])
            card.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(card, text=title, font=TYPE["heading"], anchor="w").grid(
                row=0, column=0, sticky="ew", padx=SPACING["lg"], pady=(SPACING["lg"], SPACING["sm"])
            )
            ctk.CTkLabel(card, text=description, text_color=COLORS["muted"], anchor="w", justify="left", wraplength=360).grid(
                row=1, column=0, sticky="ew", padx=SPACING["lg"]
            )
            button = FocusButton(card, text="Open", command=lambda target=route: navigate(target), height=40)
            button.grid(row=2, column=0, sticky="ew", padx=SPACING["lg"], pady=SPACING["lg"])
            self.action_buttons[route] = button


class SearchNewScreen(BaseScreen):
    def __init__(self, master, service: SearchService, root):
        super().__init__(master)
        self._app_root = root
        self._poll_job: str | None = None
        self._controller = SearchController(service, self._render_state)
        self.grid_columnconfigure(0, weight=1)
        PageHeader(
            self,
            "Search New Applications",
            "Select one GeoJSON boundary. Dates are inclusive and exclusion phrases are matched one per line.",
        ).grid(row=0, column=0, sticky="ew", padx=SPACING["xl"], pady=(SPACING["xl"], SPACING["md"]))
        form = ctk.CTkFrame(self, fg_color=COLORS["surface"])
        form.grid(row=1, column=0, sticky="nsew", padx=SPACING["xl"], pady=SPACING["sm"])
        form.grid_columnconfigure(1, weight=1)
        self.path_var = ctk.StringVar()
        self.start_var = ctk.StringVar()
        self.end_var = ctk.StringVar()
        self.status_var = ctk.StringVar(value="Ready")
        self.progress_var = ctk.StringVar(value="0 of 0 councils · 0 saved")
        self.error_var = ctk.StringVar()
        self.boundary_error_var = ctk.StringVar()
        self.date_error_var = ctk.StringVar()
        ctk.CTkLabel(form, text="Boundary GeoJSON", anchor="w").grid(row=0, column=0, sticky="w", padx=SPACING["md"], pady=SPACING["sm"])
        self.drop_target = ctk.CTkEntry(form, textvariable=self.path_var, placeholder_text="Drop one .geojson here or browse")
        self.drop_target.grid(row=0, column=1, sticky="ew", padx=SPACING["sm"], pady=SPACING["sm"])
        self.drop_target.drop_target_register(DND_FILES)
        self.drop_target.dnd_bind("<<Drop>>", self._on_drop)
        FocusButton(form, text="Browse", width=100, command=self._browse).grid(row=0, column=2, padx=SPACING["md"])
        ctk.CTkLabel(form, textvariable=self.boundary_error_var, text_color=COLORS["error"], font=TYPE["body_strong"], anchor="w").grid(
            row=1, column=1, columnspan=2, sticky="ew", padx=SPACING["sm"]
        )
        self.date_entries = []
        for row, (label, variable) in enumerate((("Search From", self.start_var), ("Search To", self.end_var)), start=2):
            ctk.CTkLabel(form, text=f"{label} (YYYY-MM-DD)", anchor="w").grid(row=row, column=0, sticky="w", padx=SPACING["md"], pady=SPACING["sm"])
            entry = ctk.CTkEntry(form, textvariable=variable)
            entry.grid(row=row, column=1, sticky="ew", padx=SPACING["sm"], pady=SPACING["sm"])
            self.date_entries.append(entry)
        self.start_entry, self.end_entry = self.date_entries
        self.date_error_label = ctk.CTkLabel(form, textvariable=self.date_error_var, text_color=COLORS["error"], font=TYPE["body_strong"], anchor="w")
        self.date_error_label.grid(row=4, column=1, columnspan=2, sticky="ew", padx=SPACING["sm"])
        ctk.CTkLabel(form, text="Exclusion phrases", anchor="nw").grid(row=5, column=0, sticky="nw", padx=SPACING["md"], pady=SPACING["sm"])
        self.phrases = ctk.CTkTextbox(form, height=100)
        self.phrases.grid(row=5, column=1, columnspan=2, sticky="ew", padx=SPACING["sm"], pady=SPACING["sm"])
        self.error_label = ctk.CTkLabel(form, textvariable=self.error_var, text_color=COLORS["error"], font=TYPE["body_strong"], anchor="w")
        self.error_label.grid(row=6, column=0, columnspan=3, sticky="ew", padx=SPACING["md"])
        actions = ctk.CTkFrame(form, fg_color="transparent")
        actions.grid(row=7, column=0, columnspan=3, sticky="ew", padx=SPACING["md"], pady=SPACING["md"])
        self.search_button = FocusButton(actions, text="Search", command=self._start_search)
        self.search_button.pack(side="left", padx=(0, SPACING["sm"]))
        self.cancel_button = FocusButton(actions, text="Cancel", command=self._controller.cancel, state="disabled")
        self.cancel_button.pack(side="left")
        ctk.CTkLabel(self, textvariable=self.status_var, anchor="w", font=TYPE["heading"]).grid(
            row=2, column=0, sticky="ew", padx=SPACING["xl"], pady=(SPACING["md"], SPACING["xs"])
        )
        ctk.CTkLabel(self, textvariable=self.progress_var, anchor="w", text_color=COLORS["muted"]).grid(
            row=3, column=0, sticky="ew", padx=SPACING["xl"]
        )
        self.warning_box = ctk.CTkTextbox(self, height=90, state="disabled")
        self.warning_box.grid(row=4, column=0, sticky="ew", padx=SPACING["xl"], pady=SPACING["md"])

    def _on_drop(self, event):
        try:
            selected = validate_geojson_selection(parse_drop_paths(event.data, self._app_root.tk.splitlist))
        except ValueError as error:
            self.boundary_error_var.set(str(error))
            self.status_var.set("Boundary selection invalid")
            self.drop_target.focus_set()
        else:
            self.path_var.set(str(selected))
            self.boundary_error_var.set("")
            self.status_var.set("Boundary selected")
        return COPY

    def _browse(self) -> None:
        chosen = filedialog.askopenfilename(filetypes=(("GeoJSON", "*.geojson"),))
        if chosen:
            try:
                selected = validate_geojson_selection((chosen,))
            except ValueError as error:
                self.boundary_error_var.set(str(error))
            else:
                self.path_var.set(str(selected))
                self.boundary_error_var.set("")

    def _start_search(self) -> None:
        self.boundary_error_var.set("")
        self.date_error_var.set("")
        self.error_var.set("")
        try:
            validate_geojson_selection((self.path_var.get(),))
        except ValueError as error:
            self.boundary_error_var.set(str(error))
            self.status_var.set("Please select one valid GeoJSON boundary")
            self.drop_target.focus_set()
            return
        try:
            request = build_search_request(
                self.path_var.get(), self.start_var.get(), self.end_var.get(), self.phrases.get("1.0", "end")
            )
        except ValueError as error:
            self.date_error_var.set(str(error))
            self.status_var.set("Please correct the highlighted search details")
            if "Search To" in str(error):
                self.end_entry.focus_set()
            else:
                self.start_entry.focus_set()
            return
        self._controller.start(request)
        self._schedule_poll()

    def _schedule_poll(self) -> None:
        if self._poll_job is None:
            self._poll_job = self.after(50, self._poll)

    def _poll(self) -> None:
        self._poll_job = None
        self._controller.poll()
        if self._controller.state.running:
            self._schedule_poll()

    def _render_state(self, state: SearchViewState) -> None:
        self.search_button.configure(state="normal" if state.search_enabled else "disabled")
        self.cancel_button.configure(state="normal" if state.cancel_enabled else "disabled")
        self.status_var.set(state.status_message)
        self.error_var.set(state.error_message)
        self.progress_var.set(
            f"{state.completed_councils} of {state.total_councils} councils · {state.saved_count} saved"
        )
        self.warning_box.configure(state="normal")
        self.warning_box.delete("1.0", "end")
        self.warning_box.insert("1.0", "\n".join(state.warnings) or "No warnings")
        self.warning_box.configure(state="disabled")

    def shutdown(self, timeout_seconds: float = 0.0) -> bool:
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass
            self._poll_job = None
        return self._controller.close(timeout_seconds)


class SearchSavedScreen(BaseScreen):
    COLUMNS = (
        ("reference", "Reference"), ("council", "Council"), ("application_date", "Application Date"),
        ("address", "Address"), ("postcode", "Postcode"), ("description", "Description"),
        ("status", "Status"), ("application_url", "Application Link"), ("council_url", "Council Link"),
    )
    COLUMN_WIDTHS = (140, 160, 140, 220, 110, 280, 110, 120, 120)

    def __init__(self, master, service: ApplicationQueryService):
        super().__init__(master)
        self.model = SavedApplicationsModel(service)
        self.filter_vars = {name: ctk.StringVar() for name in self.model.FILTER_NAMES}
        self.status_var = ctk.StringVar(value="Set filters and select Search")
        self.page_var = ctk.StringVar(value="Page 1 of 1 · 0 results")
        self.link_buttons: list[FocusButton] = []
        self._query_job: str | None = None
        self._query_controller = BackgroundTaskController(self._finish_query)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        PageHeader(self, "Search Saved Applications", "Filter and sort locally saved applications.").grid(
            row=0, column=0, sticky="ew", padx=SPACING["xl"], pady=(SPACING["xl"], SPACING["md"])
        )
        filters = ctk.CTkFrame(self, fg_color=COLORS["surface"])
        filters.grid(row=1, column=0, sticky="ew", padx=SPACING["xl"])
        for column in range(3):
            filters.grid_columnconfigure(column, weight=1)
        labels = {
            "reference": "Application Reference", "address": "Address", "postcode": "Postcode",
            "application_date": "Application Date (YYYY-MM-DD)", "keywords": "Keywords", "council": "Council",
        }
        for index, name in enumerate(self.model.FILTER_NAMES):
            field = ctk.CTkFrame(filters, fg_color="transparent")
            field.grid(row=index // 3, column=index % 3, sticky="ew", padx=SPACING["sm"], pady=SPACING["sm"])
            ctk.CTkLabel(field, text=labels[name], anchor="w").pack(fill="x")
            ctk.CTkEntry(field, textvariable=self.filter_vars[name]).pack(fill="x")
        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=2, column=0, sticky="ew", padx=SPACING["xl"], pady=SPACING["sm"])
        self.clear_button = FocusButton(buttons, text="Clear Filters", command=self._clear)
        self.clear_button.pack(side="left")
        self.search_button = FocusButton(buttons, text="Search", command=self._search)
        self.search_button.pack(side="left", padx=SPACING["sm"])
        ctk.CTkLabel(buttons, textvariable=self.status_var, anchor="w").pack(side="left", padx=SPACING["md"])
        self.table = ScrollableTable(self, min_content_width=sum(self.COLUMN_WIDTHS))
        self.table.grid(row=3, column=0, sticky="nsew", padx=SPACING["xl"], pady=SPACING["sm"])
        pager = ctk.CTkFrame(self, fg_color="transparent")
        pager.grid(row=4, column=0, sticky="ew", padx=SPACING["xl"], pady=(SPACING["sm"], SPACING["lg"]))
        self.previous_button = FocusButton(pager, text="Previous", command=self._previous)
        self.previous_button.pack(side="left")
        ctk.CTkLabel(pager, textvariable=self.page_var).pack(side="left", padx=SPACING["md"])
        self.next_button = FocusButton(pager, text="Next", command=self._next)
        self.next_button.pack(side="left")
        self._render_table()

    def _sync_filters(self) -> None:
        self.model.set_filters(**{name: variable.get() for name, variable in self.filter_vars.items()})

    def _search(self) -> None:
        try:
            self._sync_filters()
        except ValueError as error:
            self.status_var.set(str(error))
            return
        self._begin_query(self.model.search)

    def _clear(self) -> None:
        if self._query_controller.running:
            return
        for variable in self.filter_vars.values():
            variable.set("")
        self.model.clear_filters()
        self._search()

    def _sort(self, column: str) -> None:
        self._begin_query(lambda: self.model.sort(column))

    def _previous(self) -> None:
        self._begin_query(self.model.previous_page)

    def _next(self) -> None:
        self._begin_query(self.model.next_page)

    def _begin_query(self, action: Callable[[], object]) -> None:
        if self._query_controller.running:
            return
        self.status_var.set("Loading…")
        self.search_button.configure(state="disabled")
        self.clear_button.configure(state="disabled")
        self._query_controller.start(action)
        self._query_job = self.after(30, self._poll_query)

    def _poll_query(self) -> None:
        self._query_job = None
        self._query_controller.poll()
        if self._query_controller.running:
            self._query_job = self.after(30, self._poll_query)

    def _finish_query(self) -> None:
        self.search_button.configure(state="normal")
        self.clear_button.configure(state="normal")
        if self._query_controller.last_error is not None:
            self.status_var.set(str(self._query_controller.last_error))
            return
        self._render_table()

    def shutdown(self, timeout_seconds: float = 0.0) -> bool:
        if self._query_job is not None:
            try:
                self.after_cancel(self._query_job)
            except Exception:
                pass
            self._query_job = None
        return self._query_controller.close(timeout_seconds)

    def _render_table(self) -> None:
        content = self.table.content
        for child in content.winfo_children():
            child.destroy()
        self.link_buttons = []
        for column_index, (field, label) in enumerate(self.COLUMNS):
            content.grid_columnconfigure(column_index, minsize=self.COLUMN_WIDTHS[column_index])
            if field in {"reference", "council", "application_date", "address", "postcode", "description", "status"}:
                heading = FocusButton(content, text=label, command=lambda value=field: self._sort(value), width=120)
            else:
                heading = ctk.CTkLabel(content, text=label, font=TYPE["heading"])
            heading.grid(row=0, column=column_index, sticky="ew", padx=2, pady=2)
            self.table.register_mousewheel_target(heading)
        if self.model.error_message:
            self.status_var.set(f"Error: {self.model.error_message}")
        elif not self.model.rows:
            self.status_var.set("No saved applications found")
        else:
            self.status_var.set(f"Loaded {len(self.model.rows)} application(s)")
        for row_index, row in enumerate(self.model.rows, start=1):
            values = (
                row.reference, row.council, row.application_date.isoformat() if row.application_date else "",
                row.address, row.postcode, row.description, row.status or "", row.application_url, row.council_url,
            )
            for column_index, value in enumerate(values):
                if column_index in {7, 8}:
                    valid = is_openable_url(value)
                    button = FocusButton(
                        content,
                        text="Open" if valid else "Unavailable",
                        state="normal" if valid else "disabled",
                        command=lambda url=value: open_url_if_safe(url, webbrowser.open),
                        width=90,
                    )
                    button.grid(row=row_index, column=column_index, padx=2, pady=2)
                    self.link_buttons.append(button)
                    self.table.register_mousewheel_target(button)
                else:
                    cell = ctk.CTkLabel(content, text=value, anchor="w", justify="left", wraplength=260)
                    cell.grid(
                        row=row_index, column=column_index, sticky="nw", padx=4, pady=3
                    )
                    self.table.register_mousewheel_target(cell)
        self.page_var.set(f"Page {self.model.page} of {self.model.total_pages} · {self.model.total_items} results")
        self.previous_button.configure(state="normal" if self.model.page > 1 else "disabled")
        self.next_button.configure(state="normal" if self.model.page < self.model.total_pages else "disabled")


@dataclass(frozen=True, slots=True)
class CustomerRow:
    customer_name: str
    plan_type: str
    send_history: str


class SendApplicationsScreen(BaseScreen):
    COLUMNS = ("Customer Name", "Plan Type", "Send History", "Find and Review", "Find and Send")

    def __init__(self, master, rows: Sequence[CustomerRow] = ()):
        super().__init__(master)
        self.rows = tuple(rows)
        self.status_var = ctk.StringVar(value="No customer records are configured")
        self.unfinished_buttons: list[FocusButton] = []
        self.grid_columnconfigure(0, weight=1)
        PageHeader(self, "Send Applications", "Customer delivery is intentionally unavailable in version 1.").grid(
            row=0, column=0, sticky="ew", padx=SPACING["xl"], pady=(SPACING["xl"], SPACING["md"])
        )
        table = ctk.CTkFrame(self, fg_color=COLORS["surface"])
        table.grid(row=1, column=0, sticky="ew", padx=SPACING["xl"])
        for index, heading in enumerate(self.COLUMNS):
            ctk.CTkLabel(table, text=heading, font=TYPE["heading"]).grid(row=0, column=index, padx=SPACING["sm"], pady=SPACING["md"])
        if not self.rows:
            ctk.CTkLabel(
                table,
                text="There are no customer records. Customer storage and sending will arrive in a future version.",
                text_color=COLORS["muted"],
                wraplength=720,
            ).grid(row=1, column=0, columnspan=5, padx=SPACING["lg"], pady=SPACING["lg"])
            for column, label in ((3, "Find and Review"), (4, "Find and Send")):
                button = FocusButton(table, text=label, command=self._not_available)
                button.grid(row=2, column=column, padx=SPACING["sm"], pady=SPACING["md"])
                self.unfinished_buttons.append(button)
        else:
            for row_index, row in enumerate(self.rows, start=1):
                for column_index, value in enumerate((row.customer_name, row.plan_type, row.send_history)):
                    ctk.CTkLabel(table, text=value).grid(row=row_index, column=column_index, padx=SPACING["sm"], pady=SPACING["sm"])
                for column_index, label in ((3, "Find and Review"), (4, "Find and Send")):
                    button = FocusButton(table, text=label, command=self._not_available)
                    button.grid(row=row_index, column=column_index, padx=SPACING["sm"], pady=SPACING["sm"])
                    self.unfinished_buttons.append(button)
        ctk.CTkLabel(self, textvariable=self.status_var, anchor="w").grid(
            row=2, column=0, sticky="ew", padx=SPACING["xl"], pady=SPACING["md"]
        )

    def _not_available(self) -> None:
        self.status_var.set("Not available yet")


class IssuesScreen(BaseScreen):
    COLUMNS = ("Timestamp", "Run", "Council", "Portal Family", "Outcome", "Error / Exception")
    COLUMN_WIDTHS = (180, 80, 180, 150, 130, 400)

    def __init__(self, master, service: IssueQueryService):
        super().__init__(master)
        self.model = IssueResultsModel(service)
        self.run_var = ctk.StringVar()
        self.outcome_var = ctk.StringVar(value="All outcomes")
        self.status_var = ctk.StringVar(value="Select Load Issues")
        self._query_job: str | None = None
        self._query_controller = BackgroundTaskController(self._finish_query)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        PageHeader(self, "View Issues", "Filter warnings and errors captured during bounded-completeness searches.").grid(
            row=0, column=0, sticky="ew", padx=SPACING["xl"], pady=(SPACING["xl"], SPACING["md"])
        )
        filters = ctk.CTkFrame(self, fg_color=COLORS["surface"])
        filters.grid(row=1, column=0, sticky="ew", padx=SPACING["xl"])
        ctk.CTkLabel(filters, text="Run").pack(side="left", padx=(SPACING["md"], SPACING["sm"]), pady=SPACING["md"])
        ctk.CTkEntry(filters, textvariable=self.run_var, width=140).pack(side="left")
        ctk.CTkLabel(filters, text="Outcome").pack(side="left", padx=(SPACING["lg"], SPACING["sm"]))
        ctk.CTkEntry(filters, textvariable=self.outcome_var, width=180).pack(side="left")
        self.load_button = FocusButton(filters, text="Load Issues", command=self._load)
        self.load_button.pack(side="left", padx=SPACING["lg"])
        ctk.CTkLabel(self, textvariable=self.status_var, anchor="w").grid(
            row=2, column=0, sticky="ew", padx=SPACING["xl"], pady=SPACING["sm"]
        )
        self.table = ScrollableTable(self, min_content_width=sum(self.COLUMN_WIDTHS))
        self.table.grid(row=3, column=0, sticky="nsew", padx=SPACING["xl"], pady=(0, SPACING["lg"])); self._render()

    def _load(self) -> None:
        if self._query_controller.running:
            return
        self.status_var.set("Loading…")
        outcome = self.outcome_var.get()
        if outcome == "All outcomes":
            outcome = ""
        run_id_text = self.run_var.get()
        self.load_button.configure(state="disabled")
        self._query_controller.start(lambda: self.model.load(run_id_text, outcome))
        self._query_job = self.after(30, self._poll_query)

    def _poll_query(self) -> None:
        self._query_job = None
        self._query_controller.poll()
        if self._query_controller.running:
            self._query_job = self.after(30, self._poll_query)

    def _finish_query(self) -> None:
        self.load_button.configure(state="normal")
        if self._query_controller.last_error is not None:
            error_message = str(self._query_controller.last_error)
            self._render()
            self.status_var.set(error_message)
            return
        self._render()

    def shutdown(self, timeout_seconds: float = 0.0) -> bool:
        if self._query_job is not None:
            try:
                self.after_cancel(self._query_job)
            except Exception:
                pass
            self._query_job = None
        return self._query_controller.close(timeout_seconds)

    def _render(self) -> None:
        content = self.table.content
        for child in content.winfo_children():
            child.destroy()
        for index, heading in enumerate(self.COLUMNS):
            content.grid_columnconfigure(index, minsize=self.COLUMN_WIDTHS[index])
            heading_label = ctk.CTkLabel(content, text=heading, font=TYPE["heading"])
            heading_label.grid(row=0, column=index, padx=SPACING["sm"], pady=SPACING["sm"])
            self.table.register_mousewheel_target(heading_label)
        if self.model.error_message:
            self.status_var.set(f"Error: {self.model.error_message}")
        elif not self.model.rows:
            self.status_var.set("No issues found")
        else:
            self.status_var.set(f"Loaded {len(self.model.rows)} issue(s)")
        for row_index, row in enumerate(self.model.rows, start=1):
            error = " — ".join(part for part in (row.error_type, row.message) if part)
            run_id = "" if row.run_id is None else str(row.run_id)
            values = (row.timestamp.isoformat(sep=" ", timespec="seconds"), run_id, row.council, row.portal_family, row.outcome, error)
            for column_index, value in enumerate(values):
                cell = ctk.CTkLabel(content, text=value, anchor="w", justify="left", wraplength=300)
                cell.grid(
                    row=row_index, column=column_index, sticky="nw", padx=SPACING["sm"], pady=SPACING["xs"]
                )
                self.table.register_mousewheel_target(cell)
