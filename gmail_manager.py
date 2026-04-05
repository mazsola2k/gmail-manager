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
from drive_ops import (
    DriveTree,
    get_root_folders_with_sizes,
    trash_drive_folder,
    archive_drive_folder,
    format_size,
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


# ── Action menus ──────────────────────────────────────────────────────────
EMAIL_ACTIONS = [
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
]

DRIVE_ACTIONS = [
    ("🔄", "Refresh Drive",                  "drive_refresh"),
    ("─",  "─────────────────────────────",  "_sep1"),
    ("📂", "Open folder",                    "drive_open"),
    ("⬆ ", "Go up",                          "drive_up"),
    ("─",  "─────────────────────────────",  "_sep2"),
    ("🗑 ", "Trash folder",                   "drive_trash"),
    ("📦", "Archive folder",                 "drive_archive"),
]

# Keep for _update_action_labels compatibility
ACTIONS = EMAIL_ACTIONS


class ActionMenu(Static):
    """A reusable action menu using ListView for keyboard navigation."""

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

    def __init__(self, actions: list, list_id: str = "action-list", **kwargs) -> None:
        super().__init__(**kwargs)
        self._actions = actions
        self._list_id = list_id

    def compose(self) -> ComposeResult:
        items = []
        for icon, label, action in self._actions:
            if action.startswith("_sep"):
                item = ListItem(Label(f"  {icon} {label}"), classes="separator", disabled=True)
            else:
                item = ListItem(Label(f"  {icon}  {label}"), name=action)
            items.append(item)
        yield ListView(*items, id=self._list_id)


# ── Drive panel (inline on main screen) ───────────────────────────────────
class DrivePanel(Static):
    """Inline Google Drive folder browser panel."""

    DEFAULT_CSS = """
    DrivePanel {
        height: auto;
        margin: 0 1;
    }
    DrivePanel DataTable {
        height: auto;
        max-height: 24;
    }
    DrivePanel #drive-inline-breadcrumb {
        height: 1;
        color: $text-muted;
        padding: 0 1;
    }
    DrivePanel #drive-inline-status {
        height: 1;
        color: $text-muted;
        text-align: center;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.drive_tree: DriveTree | None = None
        self.folders: list[dict] = []
        self.nav_stack: list[tuple[str, str]] = []

    def compose(self) -> ComposeResult:
        yield Label("\U0001f4c2 Root", id="drive-inline-breadcrumb")
        yield DataTable(id="drive-inline-table")
        yield Label("Loading Drive folders...", id="drive-inline-status")

    def on_mount(self) -> None:
        table = self.query_one("#drive-inline-table", DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = True
        table.add_column("Google Drive", key="col")

    def set_status(self, msg: str) -> None:
        self.query_one("#drive-inline-status").update(msg)

    def update_breadcrumb(self) -> None:
        parts = ["\U0001f4c2 Root"]
        for _, name in self.nav_stack:
            parts.append(name)
        self.query_one("#drive-inline-breadcrumb").update(" \u203a ".join(parts))

    def display_folders(self) -> None:
        table = self.query_one("#drive-inline-table", DataTable)
        table.clear()
        self.update_breadcrumb()

        if self.nav_stack:
            table.add_row("\u2b06  .. (go up)", key="__go_up__")

        if not self.folders:
            self.set_status("No subfolders found")
            return

        total_size = sum(f["size"] for f in self.folders) or 1
        max_size = max((f["size"] for f in self.folders), default=1) or 1
        NAME_W = 22
        for f in self.folders:
            bar_len = int((f["size"] / max_size) * 10) if max_size > 0 else 0
            bar = "\u2588" * bar_len + "\u2591" * (10 - bar_len)
            pct = f["size"] / total_size * 100
            if f.get("is_file"):
                icon = "\U0001f4c4"
            elif f.get("has_subfolders"):
                icon = "\U0001f4c2"
            else:
                icon = "\U0001f4c1"
            short = f['name'][:NAME_W].ljust(NAME_W)
            label = f"{icon} {short} {f['size_formatted']:>9s} {bar} {pct:4.1f}%"
            table.add_row(label, key=f["id"])

        folder_count = sum(1 for f in self.folders if not f.get("is_file"))
        file_count = sum(1 for f in self.folders if f.get("is_file"))
        parts = []
        if folder_count:
            parts.append(f"{folder_count} folders")
        if file_count:
            parts.append(f"{file_count} files")
        self.set_status(f"{', '.join(parts)} \u2014 Total: {format_size(total_size)}")

    def get_selected_item(self) -> tuple:
        table = self.query_one("#drive-inline-table", DataTable)
        if table.row_count == 0:
            return None, None, False
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            item_id = str(row_key.value)
            if item_id == "__go_up__":
                return "__go_up__", None, False
            for f in self.folders:
                if f["id"] == item_id:
                    return item_id, f["name"], f.get("is_file", False)
        except Exception:
            pass
        return None, None, False

    # Keep old name for compatibility
    def get_selected_folder(self) -> tuple:
        item_id, name, is_file = self.get_selected_item()
        return item_id, name

    def navigate_to(self, folder_id: str, folder_name: str) -> None:
        if not self.drive_tree:
            return
        self.nav_stack.append((folder_id, folder_name))
        self.folders = self.drive_tree.get_children(folder_id)
        self.display_folders()

    def navigate_up(self) -> None:
        if not self.drive_tree or not self.nav_stack:
            return
        self.nav_stack.pop()
        parent_id = self.nav_stack[-1][0] if self.nav_stack else None
        self.folders = self.drive_tree.get_children(parent_id)
        self.display_folders()


# ── Drive folder browser (modal, kept for full-screen) ────────────────────
class DriveScreen(ModalScreen):
    """Full-screen modal for browsing Google Drive folders by size with drill-down."""

    BINDINGS = [
        Binding("escape", "go_back", "Back/Close"),
        Binding("enter", "open_folder", "Open"),
        Binding("t", "trash_selected", "Trash"),
        Binding("a", "archive_selected", "Archive"),
        Binding("r", "refresh_drive", "Refresh"),
    ]

    DEFAULT_CSS = """
    DriveScreen {
        align: center middle;
    }
    #drive-container {
        width: 90%;
        height: 90%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #drive-title {
        text-align: center;
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }
    #drive-breadcrumb {
        height: 1;
        color: $text-muted;
        padding: 0 1;
        margin-bottom: 1;
    }
    #drive-table {
        height: 1fr;
    }
    #drive-actions {
        height: auto;
        align: center middle;
        margin-top: 1;
    }
    #drive-actions Button {
        margin: 0 1;
    }
    #drive-status {
        height: 1;
        color: $text-muted;
        text-align: center;
        margin-top: 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.drive_tree: DriveTree | None = None
        self.folders: list[dict] = []
        # Navigation stack: list of (folder_id, folder_name) tuples
        # None = root level
        self.nav_stack: list[tuple[str, str]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="drive-container"):
            yield Label("📁 Google Drive — Folders by Size", id="drive-title")
            yield Label("📂 Root", id="drive-breadcrumb")
            yield DataTable(id="drive-table")
            yield Label("Loading folders...", id="drive-status")
            with Horizontal(id="drive-actions"):
                yield Button("📂 Open [Enter]", variant="success", id="btn-drive-open")
                yield Button("🗑  Trash [T]", variant="error", id="btn-drive-trash")
                yield Button("📦 Archive [A]", variant="warning", id="btn-drive-archive")
                yield Button("🔄 Refresh [R]", variant="default", id="btn-drive-refresh")
                yield Button("← Back [Esc]", variant="primary", id="btn-drive-back")

    def on_mount(self) -> None:
        table = self.query_one("#drive-table", DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = True
        table.add_column("Google Drive", key="col")
        self.load_tree()

    @work(thread=True)
    def load_tree(self) -> None:
        self.app.call_from_thread(self._set_status, "Loading Drive file tree (this may take a moment)...")
        try:
            creds = get_credentials()
            self.drive_tree = DriveTree(creds)
            self.nav_stack = []
            self.folders = self.drive_tree.get_children()
        except Exception as e:
            self.app.call_from_thread(self._set_status, f"❌ Error: {e}")
            return
        self.app.call_from_thread(self._display_folders)

    @property
    def current_folder_id(self) -> str | None:
        """The folder ID we're currently viewing, or None for root."""
        return self.nav_stack[-1][0] if self.nav_stack else None

    def _set_status(self, msg: str) -> None:
        self.query_one("#drive-status").update(msg)

    def _update_breadcrumb(self) -> None:
        parts = ["📂 Root"]
        for _, name in self.nav_stack:
            parts.append(name)
        crumb = " › ".join(parts)
        self.query_one("#drive-breadcrumb").update(crumb)

    def _display_folders(self) -> None:
        table = self.query_one("#drive-table", DataTable)
        table.clear()
        self._update_breadcrumb()

        # Add ".. (go up)" row if we're inside a subfolder
        if self.nav_stack:
            table.add_row("⬆  .. (go up)", key="__go_up__")

        if not self.folders:
            self._set_status("No subfolders found")
            return

        total_size = sum(f["size"] for f in self.folders) or 1
        max_size = max((f["size"] for f in self.folders), default=1) or 1
        NAME_W = 30
        for f in self.folders:
            bar_len = int((f["size"] / max_size) * 15) if max_size > 0 else 0
            bar = "█" * bar_len + "░" * (15 - bar_len)
            pct = f["size"] / total_size * 100
            if f.get("is_file"):
                icon = "📄"
            elif f.get("has_subfolders"):
                icon = "📂"
            else:
                icon = "📁"
            short = f['name'][:NAME_W].ljust(NAME_W)
            label = f"{icon} {short} {f['size_formatted']:>9s} {bar} {pct:4.1f}%"
            table.add_row(label, key=f["id"])

        folder_count = sum(1 for f in self.folders if not f.get("is_file"))
        file_count = sum(1 for f in self.folders if f.get("is_file"))
        parts = []
        if folder_count:
            parts.append(f"{folder_count} folders")
        if file_count:
            parts.append(f"{file_count} files")
        self._set_status(
            f"{', '.join(parts)} — Total: {format_size(total_size)}"
        )

    def _get_selected_folder(self) -> tuple:
        table = self.query_one("#drive-table", DataTable)
        if table.row_count == 0:
            return None, None
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            folder_id = str(row_key.value)
            if folder_id == "__go_up__":
                return "__go_up__", None
            for f in self.folders:
                if f["id"] == folder_id:
                    return folder_id, f["name"]
        except Exception:
            pass
        return None, None

    def _navigate_to(self, folder_id: str, folder_name: str) -> None:
        """Drill down into a subfolder."""
        if not self.drive_tree:
            return
        self.nav_stack.append((folder_id, folder_name))
        self.folders = self.drive_tree.get_children(folder_id)
        self._display_folders()

    def _navigate_up(self) -> None:
        """Go up one level."""
        if not self.drive_tree or not self.nav_stack:
            return
        self.nav_stack.pop()
        parent_id = self.nav_stack[-1][0] if self.nav_stack else None
        self.folders = self.drive_tree.get_children(parent_id)
        self._display_folders()

    # ── Actions ───────────────────────────────────────────────────
    def action_go_back(self) -> None:
        if self.nav_stack:
            self._navigate_up()
        else:
            self.dismiss(None)

    def action_open_folder(self) -> None:
        folder_id, name = self._get_selected_folder()
        if folder_id == "__go_up__":
            self._navigate_up()
        elif folder_id and name:
            item = next((f for f in self.folders if f["id"] == folder_id), None)
            if item and item.get("is_file"):
                self._set_status(f"📄 '{name}' is a file — use Trash/Archive actions")
            else:
                self._navigate_to(folder_id, name)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "drive-table":
            return
        row_key = str(event.row_key.value)
        if row_key == "__go_up__":
            self._navigate_up()
            return
        for f in self.folders:
            if f["id"] == row_key:
                if f.get("is_file"):
                    self._set_status(f"📄 '{f['name']}' is a file — use Trash/Archive actions")
                else:
                    self._navigate_to(row_key, f["name"])
                return

    def action_trash_selected(self) -> None:
        folder_id, name = self._get_selected_folder()
        if not folder_id or folder_id == "__go_up__":
            self._set_status("Select an item first")
            return
        item = next((f for f in self.folders if f["id"] == folder_id), None)
        kind = "file" if item and item.get("is_file") else "folder"
        size = item["size_formatted"] if item else ""
        self.app.push_screen(
            ConfirmDialog(f"🗑  Move {kind} '{name}' ({size}) to trash?"),
            lambda result, fid=folder_id, fn=name: self._do_trash(fid, fn) if result else None,
        )

    def action_archive_selected(self) -> None:
        folder_id, name = self._get_selected_folder()
        if not folder_id or folder_id == "__go_up__":
            self._set_status("Select an item first")
            return
        item = next((f for f in self.folders if f["id"] == folder_id), None)
        kind = "file" if item and item.get("is_file") else "folder"
        size = item["size_formatted"] if item else ""
        self.app.push_screen(
            ConfirmDialog(f"📦 Archive {kind} '{name}' ({size})? Moves to Archive folder."),
            lambda result, fid=folder_id, fn=name: self._do_archive(fid, fn) if result else None,
        )

    def action_refresh_drive(self) -> None:
        self.load_tree()

    @work(thread=True)
    def _do_trash(self, folder_id: str, name: str) -> None:
        self.app.call_from_thread(self._set_status, f"Trashing '{name}'...")
        try:
            creds = get_credentials()
            trash_drive_folder(creds, folder_id)
            self.folders = [f for f in self.folders if f["id"] != folder_id]
            self.app.call_from_thread(self._display_folders)
            self.app.call_from_thread(self._set_status, f"✅ '{name}' moved to trash")
        except Exception as e:
            self.app.call_from_thread(self._set_status, f"❌ Failed: {e}")

    @work(thread=True)
    def _do_archive(self, folder_id: str, name: str) -> None:
        self.app.call_from_thread(self._set_status, f"Archiving '{name}'...")
        try:
            creds = get_credentials()
            archive_drive_folder(creds, folder_id)
            self.folders = [f for f in self.folders if f["id"] != folder_id]
            self.app.call_from_thread(self._display_folders)
            self.app.call_from_thread(self._set_status, f"✅ '{name}' archived")
        except Exception as e:
            self.app.call_from_thread(self._set_status, f"❌ Failed: {e}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-drive-open":
            self.action_open_folder()
        elif event.button.id == "btn-drive-trash":
            self.action_trash_selected()
        elif event.button.id == "btn-drive-archive":
            self.action_archive_selected()
        elif event.button.id == "btn-drive-refresh":
            self.action_refresh_drive()
        elif event.button.id == "btn-drive-back":
            self.action_go_back()


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
        width: 1fr;
        min-width: 50;
        padding: 1;
    }

    #right-pane {
        width: 1fr;
        min-width: 50;
        padding: 1;
    }

    #stats-title, #email-actions-title, #yearly-title, #drive-title-main, #drive-actions-title {
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
        Binding("d", "drive", "Drive", show=True),
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
                yield Label("⚡ Email Actions", id="email-actions-title")
                yield ActionMenu(EMAIL_ACTIONS, "email-action-list", id="email-action-menu")
            with VerticalScroll(id="right-pane"):
                yield Label("💿 Google Drive Folders", id="drive-title-main")
                yield DrivePanel(id="drive-panel")
                yield Label("⚡ Drive Actions", id="drive-actions-title")
                yield ActionMenu(DRIVE_ACTIONS, "drive-action-list", id="drive-action-menu")
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
        # Auto-load Drive folders
        self.call_from_thread(self.status_msg, "Loading Google Drive folders...")
        self._load_drive_inline()

    def _load_drive_inline(self) -> None:
        """Load Drive data into the inline panel. Called from worker thread."""
        panel = self.query_one("#drive-panel", DrivePanel)
        try:
            creds = get_credentials()
            panel.drive_tree = DriveTree(creds)
            panel.nav_stack = []
            panel.folders = panel.drive_tree.get_children()
        except Exception as e:
            self.call_from_thread(panel.set_status, f"❌ Drive error: {e}")
            return
        self.call_from_thread(panel.display_folders)
        self.call_from_thread(self.status_msg, "Ready — select an action from the menu →")

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
        lv = self.query_one("#email-action-list", ListView)
        for item in lv.children:
            action = getattr(item, "name", None)
            if not action or action.startswith("_sep") or action in ("refresh",):
                continue
            for icon, base_label, act in EMAIL_ACTIONS:
                if act == action:
                    item.query_one(Label).update(f"  {icon}  {base_label}{suffix}")
                    break

    # ── Drive inline panel interactions ─────────────────────────────
    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Update drive action labels when cursor moves to a file vs folder."""
        if event.data_table.id != "drive-inline-table":
            return
        panel = self.query_one("#drive-panel", DrivePanel)
        _, _, is_file = panel.get_selected_item()
        kind = "file" if is_file else "folder"
        try:
            menu = self.query_one("#drive-action-menu", ActionMenu)
            lv = menu.query_one("#drive-action-list", ListView)
            for item in lv.children:
                act = getattr(item, "name", None)
                if act == "drive_open":
                    item.query_one(Label).update(f"  📂  Open {kind}")
                elif act == "drive_trash":
                    item.query_one(Label).update(f"  🗑   Trash {kind}")
                elif act == "drive_archive":
                    item.query_one(Label).update(f"  📦  Archive {kind}")
        except Exception:
            pass

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle row selection on yearly table or drive inline table."""
        if event.data_table.id == "drive-inline-table":
            self._handle_drive_row_selected(event)
            return
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

        # Drive actions
        if action == "drive_refresh":
            self._do_drive_refresh()
            return
        elif action == "drive_open":
            self._drive_panel_open()
            return
        elif action == "drive_up":
            self._drive_panel_up()
            return
        elif action == "drive_trash":
            self._drive_panel_trash()
            return
        elif action == "drive_archive":
            self._drive_panel_archive()
            return

        # Email actions
        yr = self.selected_year
        yr_label = f" from {yr}" if yr else ""

        if action == "refresh":
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

    def action_drive(self) -> None:
        self._do_drive_refresh()

    def _handle_drive_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle double-click / enter on drive inline table."""
        panel = self.query_one("#drive-panel", DrivePanel)
        row_key = str(event.row_key.value)
        if row_key == "__go_up__":
            panel.navigate_up()
            return
        for f in panel.folders:
            if f["id"] == row_key:
                if f.get("is_file"):
                    panel.set_status(f"📄 '{f['name']}' is a file — use Trash/Archive actions")
                else:
                    panel.navigate_to(row_key, f["name"])
                return

    def _drive_panel_open(self) -> None:
        panel = self.query_one("#drive-panel", DrivePanel)
        item_id, name, is_file = panel.get_selected_item()
        if item_id == "__go_up__":
            panel.navigate_up()
        elif is_file:
            panel.set_status(f"📄 '{name}' is a file — use Trash/Archive actions")
        elif item_id and name:
            panel.navigate_to(item_id, name)

    def _drive_panel_up(self) -> None:
        panel = self.query_one("#drive-panel", DrivePanel)
        if panel.nav_stack:
            panel.navigate_up()

    def _drive_panel_trash(self) -> None:
        panel = self.query_one("#drive-panel", DrivePanel)
        item_id, name, is_file = panel.get_selected_item()
        if item_id == "__go_up__":
            panel.set_status("⬆ Navigate up — nothing to trash here")
            return
        if not item_id:
            panel.set_status("Select an item first")
            return
        kind = "file" if is_file else "folder"
        size = next((f["size_formatted"] for f in panel.folders if f["id"] == item_id), "")
        self.push_screen(
            ConfirmDialog(f"🗑  Move {kind} '{name}' ({size}) to trash?"),
            lambda result, fid=item_id, fn=name: self._do_drive_trash(fid, fn) if result else None,
        )

    def _drive_panel_archive(self) -> None:
        panel = self.query_one("#drive-panel", DrivePanel)
        item_id, name, is_file = panel.get_selected_item()
        if item_id == "__go_up__":
            panel.set_status("⬆ Navigate up — nothing to archive here")
            return
        if not item_id:
            panel.set_status("Select an item first")
            return
        kind = "file" if is_file else "folder"
        size = next((f["size_formatted"] for f in panel.folders if f["id"] == item_id), "")
        self.push_screen(
            ConfirmDialog(f"📦 Archive {kind} '{name}' ({size})? Moves to Archive folder."),
            lambda result, fid=item_id, fn=name: self._do_drive_archive(fid, fn) if result else None,
        )

    @work(thread=True)
    def _do_drive_trash(self, folder_id: str, name: str) -> None:
        panel = self.query_one("#drive-panel", DrivePanel)
        self.call_from_thread(panel.set_status, f"Trashing '{name}'...")
        try:
            creds = get_credentials()
            trash_drive_folder(creds, folder_id)
            panel.folders = [f for f in panel.folders if f["id"] != folder_id]
            self.call_from_thread(panel.display_folders)
            self.call_from_thread(panel.set_status, f"✅ '{name}' trashed")
        except Exception as e:
            self.call_from_thread(panel.set_status, f"❌ Failed: {e}")

    @work(thread=True)
    def _do_drive_archive(self, folder_id: str, name: str) -> None:
        panel = self.query_one("#drive-panel", DrivePanel)
        self.call_from_thread(panel.set_status, f"Archiving '{name}'...")
        try:
            creds = get_credentials()
            archive_drive_folder(creds, folder_id)
            panel.folders = [f for f in panel.folders if f["id"] != folder_id]
            self.call_from_thread(panel.display_folders)
            self.call_from_thread(panel.set_status, f"✅ '{name}' archived")
        except Exception as e:
            self.call_from_thread(panel.set_status, f"❌ Failed: {e}")

    @work(thread=True)
    def _do_drive_refresh(self) -> None:
        panel = self.query_one("#drive-panel", DrivePanel)
        self.call_from_thread(panel.set_status, "Refreshing Drive...")
        self._load_drive_inline()

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
