#!/usr/bin/env python3
"""Gmail Manager — Full-screen TUI built with Textual."""

from __future__ import annotations

import sys
from threading import Thread

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Label,
    ListItem,
    ListView,
    Static,
    DataTable,
    LoadingIndicator,
)
from textual.widget import Widget

from auth import authenticate, get_credentials
from gmail_ops import (
    get_email_stats,
    get_yearly_breakdown_imap,
    get_year_category_stats_imap,
    get_storage_quota,
    trash_by_year,
    trash_promotions,
    trash_spam,
    trash_unread,
    trash_social,
    trash_older_than,
    trash_large_emails,
    trash_inbox,
    trash_sent,
    permanently_delete_trash,
    count_messages_by_query,
)


# ── Confirm dialog ────────────────────────────────────────────────────────
class ConfirmDialog(ModalScreen[bool]):
    """A modal yes/no confirmation dialog."""

    BINDINGS = [
        Binding("y", "confirm", "Yes"),
        Binding("n", "cancel", "No"),
        Binding("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    ConfirmDialog {
        align: center middle;
    }
    #confirm-box {
        width: 60;
        height: auto;
        max-height: 14;
        border: thick $warning;
        background: $surface;
        padding: 1 2;
    }
    #confirm-box .title {
        text-align: center;
        text-style: bold;
        color: $warning;
        margin-bottom: 1;
    }
    #confirm-box .message {
        text-align: center;
        margin-bottom: 1;
    }
    #confirm-box .hint {
        text-align: center;
        color: $text-muted;
    }
    #confirm-buttons {
        height: auto;
        align: center middle;
        margin-top: 1;
    }
    #confirm-buttons Button {
        margin: 0 1;
        min-width: 12;
    }
    """

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label("⚠️  Confirm Action", classes="title")
            yield Label(self.message, classes="message")
            with Horizontal(id="confirm-buttons"):
                yield Button("Yes [Y]", variant="success", id="btn-yes")
                yield Button("No [N]", variant="error", id="btn-no")
            yield Label("[Esc] Cancel", classes="hint")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-yes":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


# ── Input dialog ──────────────────────────────────────────────────────────
class InputDialog(ModalScreen[str]):
    """A modal input dialog for entering a value."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    InputDialog {
        align: center middle;
    }
    #input-box {
        width: 60;
        height: auto;
        max-height: 14;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #input-box .title {
        text-align: center;
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }
    #input-box .hint {
        text-align: center;
        color: $text-muted;
        margin-top: 1;
    }
    """

    def __init__(self, prompt: str, default: str = "") -> None:
        super().__init__()
        self.prompt = prompt
        self.default = default

    def compose(self) -> ComposeResult:
        from textual.widgets import Input
        with Vertical(id="input-box"):
            yield Label(self.prompt, classes="title")
            yield Input(value=self.default, placeholder="Enter value...")
            yield Label("[Enter] Submit   [Esc] Cancel", classes="hint")

    def on_input_submitted(self, event) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss("")


# ── Stats panel ───────────────────────────────────────────────────────────
class StatsPanel(Static):
    """Mailbox statistics table."""

    DEFAULT_CSS = """
    StatsPanel {
        height: auto;
        margin: 0 1;
    }
    StatsPanel DataTable {
        height: auto;
        max-height: 26;
    }
    """

    def compose(self) -> ComposeResult:
        yield DataTable(id="stats-table")

    def on_mount(self) -> None:
        table = self.query_one("#stats-table", DataTable)
        table.cursor_type = "none"
        table.zebra_stripes = True
        table.add_columns("", "Category", "Total", "Unread")

    def update_stats(self, stats: dict) -> None:
        table = self.query_one("#stats-table", DataTable)
        table.clear()

        rows = [
            ("📁", "All Mail",   stats.get("All Mail", {})),
            ("📥", "Inbox",      stats.get("Inbox", {})),
            ("📤", "Sent",       stats.get("Sent", {})),
            ("📬", "Unread",     stats.get("Unread", {})),
            ("📢", "Promotions", stats.get("Promotions", {})),
            ("👥", "Social",     stats.get("Social", {})),
            ("🔔", "Updates",    stats.get("Updates", {})),
            ("💬", "Forums",     stats.get("Forums", {})),
            ("⛔", "Spam",       stats.get("Spam", {})),
            ("🗑 ", "Trash",      stats.get("Trash", {})),
        ]

        for icon, name, data in rows:
            total = f"{data.get('total', 0):,}"
            unread = data.get("unread", 0)
            unread_str = f"{unread:,}" if unread > 0 else "—"
            table.add_row(icon, name, total, unread_str)


# ── Suggestions panel ─────────────────────────────────────────────────────
class SuggestionsPanel(Static):
    """Cleanup suggestions."""

    DEFAULT_CSS = """
    SuggestionsPanel {
        height: auto;
        margin: 0 1;
        padding: 1;
        border: round $success;
    }
    """

    def update_suggestions(self, stats: dict, yearly: dict) -> None:
        items = []

        sp = stats.get("Spam", {}).get("total", 0)
        if sp > 0:
            items.append(f"⛔ {sp:,} spam — delete permanently")
        pr = stats.get("Promotions", {}).get("total", 0)
        if pr > 100:
            items.append(f"📢 {pr:,} promotions — trash them")
        so = stats.get("Social", {}).get("total", 0)
        if so > 100:
            items.append(f"👥 {so:,} social — trash them")
        un = stats.get("Unread", {}).get("total", 0)
        if un > 500:
            items.append(f"📬 {un:,} unread — consider cleanup")
        if yearly:
            import datetime
            cy = datetime.datetime.now().year
            old = {y: c for y, c in yearly.items() if y < cy - 2 and c > 100}
            if old:
                t = sum(old.values())
                ys = ", ".join(str(y) for y in sorted(old))
                items.append(f"📅 {t:,} old emails ({ys})")

        if items:
            self.update("💡 [bold]Suggestions:[/bold]\n" + "\n".join(f"  {i}" for i in items))
        else:
            self.update("✅ Your mailbox looks clean!")


# ── Yearly panel ──────────────────────────────────────────────────────────
class YearlyPanel(Static):
    """Yearly breakdown chart — select a year to trash its emails."""

    DEFAULT_CSS = """
    YearlyPanel {
        height: auto;
        margin: 0 1;
    }
    YearlyPanel DataTable {
        height: auto;
        max-height: 20;
    }
    YearlyPanel .loading-hint {
        text-align: center;
        color: $text-muted;
        padding: 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label("Loading yearly data...", classes="loading-hint", id="yearly-loading")
        yield DataTable(id="yearly-table")

    def on_mount(self) -> None:
        table = self.query_one("#yearly-table", DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = True
        table.add_columns("Year", "Count", "")

    def update_yearly(self, yearly: dict, selected_year: int | None = None) -> None:
        table = self.query_one("#yearly-table", DataTable)
        table.clear()
        self.query_one("#yearly-loading").display = False

        if not yearly:
            return

        # "All years" row to clear selection
        table.add_row("✦ All", "—", "← show global stats", key="all")

        mx = max(yearly.values()) if yearly else 1
        for y in sorted(yearly, reverse=True):
            c = yearly[y]
            bl = int((c / mx) * 30) if mx > 0 else 0
            bar = "█" * bl + "░" * (30 - bl)
            marker = " ◄" if y == selected_year else ""
            table.add_row(str(y), f"{c:,}", bar + marker, key=str(y))

    def get_selected_year(self, row_key) -> int | str | None:
        """Return the year integer, 'all', or None from a row key."""
        try:
            val = str(row_key.value)
            if val == "all":
                return "all"
            return int(val)
        except (ValueError, AttributeError):
            return None


# ── Status bar ────────────────────────────────────────────────────────────
class StatusBar(Static):
    """Bottom status line for messages."""

    DEFAULT_CSS = """
    StatusBar {
        dock: bottom;
        height: 1;
        background: $primary-background;
        color: $text-muted;
        padding: 0 2;
    }
    """

    def set_message(self, msg: str) -> None:
        self.update(msg)


# ── Action menu ───────────────────────────────────────────────────────────
ACTIONS = [
    ("📊", "Refresh statistics",             "refresh"),
    ("─",  "─────────────────────────────",  "_sep1"),
    ("🗑 ", "Delete spam (permanent)",        "spam"),
    ("📢", "Trash promotions",               "promotions"),
    ("👥", "Trash social emails",            "social"),
    ("📬", "Trash unread emails",            "unread"),
    ("📥", "Trash inbox emails",             "inbox"),
    ("📤", "Trash sent emails",              "sent"),
    ("─",  "─────────────────────────────",  "_sep2"),
    ("⏰", "Trash emails older than N days", "older"),
    ("📦", "Trash large emails (>10 MB)",    "large"),
    ("─",  "─────────────────────────────",  "_sep3"),
    ("🔥", "Empty trash (permanent)",        "empty_trash"),
    ("─",  "─────────────────────────────",  "_sep4"),
    ("🚪", "Exit",                           "exit"),
]


class ActionMenu(Static):
    """The action menu using ListView for keyboard navigation."""

    DEFAULT_CSS = """
    ActionMenu {
        height: auto;
        margin: 0 1;
        border: round $warning;
        padding: 0;
    }
    ActionMenu ListView {
        height: auto;
        max-height: 20;
        background: $surface;
    }
    ActionMenu ListView > ListItem {
        padding: 0 2;
        height: 1;
    }
    ActionMenu ListView > ListItem.separator {
        color: $text-disabled;
        height: 1;
    }
    ActionMenu ListView:focus > ListItem.--highlight {
        background: $accent 30%;
    }
    """

    def compose(self) -> ComposeResult:
        items = []
        for icon, label, action in ACTIONS:
            if action.startswith("_sep"):
                item = ListItem(Label(f"  {icon} {label}"), classes="separator", disabled=True)
            else:
                item = ListItem(Label(f"  {icon}  {label}"), name=action)
            items.append(item)
        yield ListView(*items, id="action-list")


# ── Main App ──────────────────────────────────────────────────────────────
class GmailManagerApp(App):
    """Gmail Manager TUI Application."""

    TITLE = "📧 Gmail Manager"
    SUB_TITLE = "View stats · Clean up your mailbox"

    CSS = """
    Screen {
        background: $surface;
    }

    #main-container {
        width: 1fr;
    }

    #left-pane {
        width: 2fr;
        min-width: 50;
        padding: 1;
    }

    #right-pane {
        width: 1fr;
        min-width: 32;
        padding: 1;
    }

    #stats-title, #actions-title, #yearly-title {
        text-align: center;
        text-style: bold;
        color: $accent;
        margin-bottom: 0;
    }

    #loading {
        height: 3;
        margin: 1;
    }

    #quota-bar {
        height: 1;
        dock: top;
        background: $primary-background;
        color: $text;
        padding: 0 2;
        text-style: bold;
    }
    """

    BINDINGS = [
        Binding("q", "quit_app", "Quit", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("y", "yearly", "Yearly", show=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.service = None
        self.stats: dict = {}
        self.yearly: dict = {}
        self.selected_year: int | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("💾 Storage: loading...", id="quota-bar")
        with Horizontal(id="main-container"):
            with VerticalScroll(id="left-pane"):
                yield Label("📊 Mailbox Overview", id="stats-title")
                yield StatsPanel(id="stats-panel")
                yield Label("📅 Emails by Year  [dim](select a year for details)[/dim]", id="yearly-title")
                yield YearlyPanel(id="yearly-panel")
                yield SuggestionsPanel(id="suggestions")
            with VerticalScroll(id="right-pane"):
                yield Label("⚡ Actions", id="actions-title")
                yield ActionMenu(id="action-menu")
        yield StatusBar(id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.status_msg("Authenticating with Gmail...")
        self.do_authenticate()

    def status_msg(self, msg: str) -> None:
        self.query_one("#status", StatusBar).set_message(msg)

    @work(thread=True)
    def do_authenticate(self) -> None:
        try:
            self.service = authenticate()
        except FileNotFoundError as e:
            self.call_from_thread(self.status_msg, f"❌ {e}")
            return
        except Exception as e:
            self.call_from_thread(self.status_msg, f"❌ Auth failed: {e}")
            return

        self.call_from_thread(self.status_msg, "✅ Authenticated — loading stats...")
        stats = get_email_stats(self.service)
        self.stats = stats
        self.call_from_thread(self._update_display)
        # Load storage quota
        try:
            creds = get_credentials()
            quota = get_storage_quota(creds)
            self.call_from_thread(self._update_quota, quota)
        except Exception as e:
            self.call_from_thread(self._update_quota_error, str(e))
        # Auto-load yearly breakdown via IMAP
        self.call_from_thread(self.status_msg, "Loading yearly breakdown (IMAP)...")
        try:
            creds = get_credentials()
            self.yearly = get_yearly_breakdown_imap(creds)
        except Exception as e:
            self.yearly = {}
            self.call_from_thread(self.status_msg, f"⚠️ Yearly load failed: {e}")
        self.call_from_thread(self._update_yearly_display)

    def _update_display(self) -> None:
        self.query_one("#stats-panel", StatsPanel).update_stats(self.stats)
        self.query_one("#suggestions", SuggestionsPanel).update_suggestions(self.stats, self.yearly)
        self.status_msg("Ready — select an action from the menu →")

    def _update_quota(self, quota: dict) -> None:
        used = quota["used_gb"]
        total = quota["total_gb"]
        free = quota["free_gb"]
        pct = quota["used_pct"]
        bar_len = 30
        filled = int(pct / 100 * bar_len)
        bar = "█" * filled + "░" * (bar_len - filled)
        color = "green" if pct < 70 else ("yellow" if pct < 90 else "red")
        self.query_one("#quota-bar", Static).update(
            f"💾 Storage: [{color}]{bar}[/{color}] "
            f"{used:.1f} GB / {total:.1f} GB used ({pct:.1f}%) — "
            f"[bold green]{free:.1f} GB free[/bold green]"
        )

    def _update_quota_error(self, error: str) -> None:
        if "accessNotConfigured" in error or "has not been used" in error:
            self.query_one("#quota-bar", Static).update(
                "💾 Storage: [bold yellow]Enable Drive API in Google Cloud Console to see quota[/bold yellow]"
            )
        else:
            self.query_one("#quota-bar", Static).update(
                f"💾 Storage: [dim]unavailable ({error[:60]})[/dim]"
            )

    def _update_yearly_display(self) -> None:
        panel = self.query_one("#yearly-panel", YearlyPanel)
        panel.update_yearly(self.yearly, self.selected_year)
        self.query_one("#suggestions", SuggestionsPanel).update_suggestions(self.stats, self.yearly)
        if self.selected_year:
            self.query_one("#yearly-title").update(
                f"📅 Emails by Year  [bold cyan](◄ {self.selected_year} selected)[/bold cyan]"
            )
        else:
            self.query_one("#yearly-title").update(
                "📅 Emails by Year  [dim](select a year for details)[/dim]"
            )
        self.status_msg("Ready — select a year for details, or pick an action")

    def _update_action_labels(self) -> None:
        """Update action menu labels to reflect selected year."""
        year = self.selected_year
        suffix = f" ({year})" if year else ""
        lv = self.query_one("#action-list", ListView)
        for item in lv.children:
            action = getattr(item, "name", None)
            if not action or action.startswith("_sep") or action in ("exit", "refresh"):
                continue
            for icon, base_label, act in ACTIONS:
                if act == action:
                    item.query_one(Label).update(f"  {icon}  {base_label}{suffix}")
                    break

    # ── Yearly table row selection ──────────────────────────────────
    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle clicking a year row to load per-year details."""
        if event.data_table.id != "yearly-table":
            return
        panel = self.query_one("#yearly-panel", YearlyPanel)
        result = panel.get_selected_year(event.row_key)
        if result is None:
            return
        if result == "all":
            self._clear_year_selection()
            return
        self.selected_year = result
        self._update_action_labels()
        self._do_load_year_details(result)

    def _clear_year_selection(self) -> None:
        """Return to global mailbox overview."""
        self.selected_year = None
        self.query_one("#stats-title").update("📊 Mailbox Overview")
        self._update_action_labels()
        self.status_msg("Refreshing global stats...")
        self._do_refresh()

    @work(thread=True)
    def _do_load_year_details(self, year: int) -> None:
        """Load per-category stats for a specific year via IMAP."""
        self.call_from_thread(self.status_msg, f"📅 Loading {year} breakdown...")
        try:
            creds = get_credentials()
            year_stats = get_year_category_stats_imap(creds, year)
            self.stats = year_stats
        except Exception as e:
            self.call_from_thread(self.status_msg, f"❌ Failed to load {year}: {e}")
            return
        self.call_from_thread(self._show_year_stats, year)

    def _show_year_stats(self, year: int) -> None:
        """Update display after loading year-specific stats."""
        self.query_one("#stats-title").update(f"📊 Year {year} Overview")
        self.query_one("#stats-panel", StatsPanel).update_stats(self.stats)
        self.query_one("#yearly-title").update(
            f"📅 Emails by Year  [bold cyan](◄ {year} selected)[/bold cyan]"
        )
        self.status_msg(f"Showing {year} — pick an action or select another year")

    # ── Action dispatch ───────────────────────────────────────────────
    def on_list_view_selected(self, event: ListView.Selected) -> None:
        action = event.item.name
        if action is None:
            return
        yr = self.selected_year
        yr_label = f" from {yr}" if yr else ""

        if action == "exit":
            self.exit()
        elif action == "refresh":
            self.action_refresh()
        elif action == "spam":
            count = self.stats.get("Spam", {}).get("total", 0)
            self._confirm_and_run(
                f"Delete {count:,} spam emails{yr_label}?",
                self._do_spam,
            )
        elif action == "promotions":
            count = self.stats.get("Promotions", {}).get("total", 0)
            self._confirm_and_run(
                f"Trash {count:,} promotions{yr_label}?",
                self._do_promotions,
            )
        elif action == "social":
            count = self.stats.get("Social", {}).get("total", 0)
            self._confirm_and_run(
                f"Trash {count:,} social emails{yr_label}?",
                self._do_social,
            )
        elif action == "unread":
            count = self.stats.get("Unread", {}).get("total", 0)
            self._confirm_and_run(
                f"Trash {count:,} unread emails{yr_label}?",
                self._do_unread,
            )
        elif action == "inbox":
            count = self.stats.get("Inbox", {}).get("total", 0)
            self._confirm_and_run(
                f"Trash {count:,} inbox emails{yr_label}?",
                self._do_inbox,
            )
        elif action == "sent":
            count = self.stats.get("Sent", {}).get("total", 0)
            self._confirm_and_run(
                f"Trash {count:,} sent emails{yr_label}?",
                self._do_sent,
            )
        elif action == "older":
            self._input_and_run("Trash emails older than N days:", "365", self._do_older)
        elif action == "large":
            self._input_and_run("Trash emails larger than N MB:", "10", self._do_large)
        elif action == "empty_trash":
            count = self.stats.get("Trash", {}).get("total", 0)
            self._confirm_and_run(
                f"⚠️ PERMANENTLY delete {count:,} trash emails{yr_label}? This cannot be undone!",
                self._do_empty_trash,
            )

    def _confirm_and_run(self, msg: str, callback) -> None:
        def on_result(result: bool) -> None:
            if result:
                callback()
        self.push_screen(ConfirmDialog(msg), on_result)

    def _input_and_run(self, prompt: str, default: str, callback) -> None:
        def on_result(value: str) -> None:
            if value:
                callback(value)
        self.push_screen(InputDialog(prompt, default), on_result)

    # ── Key bindings ──────────────────────────────────────────────────
    def action_quit_app(self) -> None:
        self.exit()

    def action_refresh(self) -> None:
        if self.selected_year:
            self.status_msg(f"Refreshing {self.selected_year} stats...")
            self._do_load_year_details(self.selected_year)
        else:
            self.status_msg("Refreshing statistics...")
            self._do_refresh()

    def action_yearly(self) -> None:
        self.status_msg("Refreshing yearly data...")
        self._do_yearly()

    # ── Helper: refresh after an action ───────────────────────────────
    def _post_action_refresh(self, done_msg: str) -> None:
        """Refresh stats (year-scoped or global) after a trash action. Runs in worker thread."""
        creds = get_credentials()
        self.yearly = get_yearly_breakdown_imap(creds)
        if self.selected_year:
            self.stats = get_year_category_stats_imap(creds, self.selected_year)
            self.call_from_thread(self._show_year_stats, self.selected_year)
        else:
            self.stats = get_email_stats(self.service)
            self.call_from_thread(self._update_display)
        self.call_from_thread(self._update_yearly_display)
        try:
            quota = get_storage_quota(creds)
            self.call_from_thread(self._update_quota, quota)
        except Exception:
            pass
        self.call_from_thread(self.status_msg, done_msg)

    # ── Background workers ────────────────────────────────────────────
    @work(thread=True)
    def _do_refresh(self) -> None:
        self.stats = get_email_stats(self.service)
        self.call_from_thread(self._update_display)
        creds = get_credentials()
        self.yearly = get_yearly_breakdown_imap(creds)
        self.call_from_thread(self._update_yearly_display)

    @work(thread=True)
    def _do_yearly(self) -> None:
        creds = get_credentials()
        self.yearly = get_yearly_breakdown_imap(creds)
        self.call_from_thread(self._update_yearly_display)

    @work(thread=True)
    def _do_spam(self) -> None:
        yr = self.selected_year
        label = f" from {yr}" if yr else ""
        self.call_from_thread(self.status_msg, f"🗑  Deleting spam{label}...")
        count = trash_spam(self.service, year=yr)
        self._post_action_refresh(f"✅ Deleted {count:,} spam emails{label}")

    @work(thread=True)
    def _do_promotions(self) -> None:
        yr = self.selected_year
        label = f" from {yr}" if yr else ""
        self.call_from_thread(self.status_msg, f"📢 Trashing promotions{label}...")
        count = trash_promotions(self.service, year=yr)
        self._post_action_refresh(f"✅ Trashed {count:,} promotions{label}")

    @work(thread=True)
    def _do_social(self) -> None:
        yr = self.selected_year
        label = f" from {yr}" if yr else ""
        self.call_from_thread(self.status_msg, f"👥 Trashing social{label}...")
        count = trash_social(self.service, year=yr)
        self._post_action_refresh(f"✅ Trashed {count:,} social emails{label}")

    @work(thread=True)
    def _do_unread(self) -> None:
        yr = self.selected_year
        label = f" from {yr}" if yr else ""
        self.call_from_thread(self.status_msg, f"📬 Trashing unread{label}...")
        count = trash_unread(self.service, year=yr)
        self._post_action_refresh(f"✅ Trashed {count:,} unread emails{label}")

    @work(thread=True)
    def _do_year(self, year_str: str) -> None:
        try:
            year = int(year_str)
        except ValueError:
            self.call_from_thread(self.status_msg, "❌ Invalid year")
            return
        self.call_from_thread(self.status_msg, f"📅 Trashing emails from {year}...")
        count = trash_by_year(self.service, year)
        self._post_action_refresh(f"✅ Trashed {count:,} emails from {year}")

    @work(thread=True)
    def _do_older(self, days_str: str) -> None:
        try:
            days = int(days_str)
        except ValueError:
            self.call_from_thread(self.status_msg, "❌ Invalid number")
            return
        yr = self.selected_year
        label = f" from {yr}" if yr else ""
        self.call_from_thread(self.status_msg, f"⏰ Trashing emails older than {days} days{label}...")
        count = trash_older_than(self.service, days, year=yr)
        self._post_action_refresh(f"✅ Trashed {count:,} old emails{label}")

    @work(thread=True)
    def _do_inbox(self) -> None:
        yr = self.selected_year
        label = f" from {yr}" if yr else ""
        self.call_from_thread(self.status_msg, f"📥 Trashing inbox{label}...")
        count = trash_inbox(self.service, year=yr)
        self._post_action_refresh(f"✅ Trashed {count:,} inbox emails{label}")

    @work(thread=True)
    def _do_sent(self) -> None:
        yr = self.selected_year
        label = f" from {yr}" if yr else ""
        self.call_from_thread(self.status_msg, f"📤 Trashing sent{label}...")
        count = trash_sent(self.service, year=yr)
        self._post_action_refresh(f"✅ Trashed {count:,} sent emails{label}")

    @work(thread=True)
    def _do_large(self, size_str: str) -> None:
        try:
            size = int(size_str)
        except ValueError:
            self.call_from_thread(self.status_msg, "❌ Invalid number")
            return
        yr = self.selected_year
        label = f" from {yr}" if yr else ""
        self.call_from_thread(self.status_msg, f"📦 Trashing emails > {size}MB{label}...")
        count = trash_large_emails(self.service, size, year=yr)
        self._post_action_refresh(f"✅ Trashed {count:,} large emails{label}")

    @work(thread=True)
    def _do_empty_trash(self) -> None:
        yr = self.selected_year
        label = f" from {yr}" if yr else ""
        self.call_from_thread(self.status_msg, f"🔥 Permanently deleting trash{label}...")
        count = permanently_delete_trash(self.service, year=yr)
        self._post_action_refresh(f"✅ Permanently deleted {count:,} trash emails{label}")


# ── Entry point ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = GmailManagerApp()
    app.run()
