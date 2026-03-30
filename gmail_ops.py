"""Gmail API helper functions for fetching stats and performing cleanup."""

import time
from googleapiclient.discovery import build


def get_storage_quota(creds):
    """Get Google account storage quota via Drive API.

    Returns dict with keys: used_gb, total_gb, free_gb, used_pct.
    """
    drive = build("drive", "v3", credentials=creds)
    about = drive.about().get(fields="storageQuota").execute()
    quota = about.get("storageQuota", {})

    used = int(quota.get("usage", 0))
    limit = int(quota.get("limit", 0))

    used_gb = used / (1024 ** 3)
    total_gb = limit / (1024 ** 3) if limit else 0
    free_gb = max(0, total_gb - used_gb)
    used_pct = (used / limit * 100) if limit else 0

    return {
        "used_gb": used_gb,
        "total_gb": total_gb,
        "free_gb": free_gb,
        "used_pct": used_pct,
    }


def get_label_message_count(service, label_id):
    """Get total and unread message count for a label."""
    result = service.users().labels().get(userId="me", id=label_id).execute()
    return {
        "total": result.get("messagesTotal", 0),
        "unread": result.get("messagesUnread", 0),
    }


def get_email_stats(service):
    """Gather email statistics across key categories."""
    stats = {}

    # Core labels to check
    labels = {
        "INBOX": "Inbox",
        "SENT": "Sent",
        "SPAM": "Spam",
        "TRASH": "Trash",
        "UNREAD": "Unread",
        "CATEGORY_PROMOTIONS": "Promotions",
        "CATEGORY_SOCIAL": "Social",
        "CATEGORY_UPDATES": "Updates",
        "CATEGORY_FORUMS": "Forums",
    }

    for label_id, label_name in labels.items():
        try:
            counts = get_label_message_count(service, label_id)
            stats[label_name] = counts
        except Exception:
            stats[label_name] = {"total": 0, "unread": 0}

    # Total emails across all mail
    try:
        profile = service.users().getProfile(userId="me").execute()
        stats["All Mail"] = {
            "total": profile.get("messagesTotal", 0),
            "unread": 0,
        }
    except Exception:
        stats["All Mail"] = {"total": 0, "unread": 0}

    return stats


def count_messages_by_query(service, query):
    """Count total messages matching a Gmail search query.

    Paginates through message IDs (minimal fields) for accuracy.
    Retries on transient SSL/network errors.
    """
    count = 0
    page_token = None
    while True:
        for attempt in range(3):
            try:
                result = (
                    service.users()
                    .messages()
                    .list(
                        userId="me",
                        q=query,
                        pageToken=page_token,
                        maxResults=500,
                        fields="messages(id),nextPageToken,resultSizeEstimate",
                    )
                    .execute()
                )
                break
            except Exception:
                if attempt < 2:
                    time.sleep(1)
                else:
                    return count
        messages = result.get("messages", [])
        if not messages:
            if count == 0:
                return result.get("resultSizeEstimate", 0)
            break
        count += len(messages)
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return count


def _connect_imap(creds):
    """Connect to Gmail IMAP with XOAUTH2 and select All Mail. Returns imap connection."""
    import imaplib
    import json
    import httplib2

    access_token = creds.token

    h = httplib2.Http()
    resp, content = h.request(
        "https://gmail.googleapis.com/gmail/v1/users/me/profile",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    profile = json.loads(content)
    user_email = profile.get("emailAddress", "")
    if not user_email:
        raise RuntimeError("Could not get user email from profile")

    auth_string = f"user={user_email}\x01auth=Bearer {access_token}\x01\x01"

    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.authenticate("XOAUTH2", lambda x: auth_string.encode())

    # Try known "All Mail" folder names (varies by locale)
    all_mail_folders = ['"[Gmail]/All Mail"', '"[Gmail]/Todos"', '"[Gmail]/Alle Nachrichten"', "INBOX"]
    for folder in all_mail_folders:
        try:
            typ, data = imap.select(folder, readonly=True)
            if typ == "OK":
                return imap
        except Exception:
            continue

    # Fallback: scan LIST for a folder containing "All"
    try:
        typ, folders = imap.list()
        if typ == "OK":
            for f in folders:
                decoded = f.decode() if isinstance(f, bytes) else f
                if "All" in decoded or "all" in decoded:
                    parts = decoded.split(' "/" ')
                    if len(parts) == 2:
                        folder_name = parts[1].strip()
                        typ2, _ = imap.select(folder_name, readonly=True)
                        if typ2 == "OK":
                            return imap
    except Exception:
        pass

    imap.logout()
    raise RuntimeError("Could not select All Mail folder")


def get_yearly_breakdown_imap(creds):
    """Get exact email counts by year using IMAP SEARCH."""
    import datetime

    current_year = datetime.datetime.now().year
    yearly = {}

    imap = _connect_imap(creds)

    for year in range(current_year, current_year - 20, -1):
        try:
            since = f"01-Jan-{year}"
            before = f"01-Jan-{year + 1}"
            status, data = imap.search(None, f"SINCE {since} BEFORE {before}")
            if status == "OK" and data[0]:
                ids = data[0].split()
                if len(ids) > 0:
                    yearly[year] = len(ids)
        except Exception:
            pass

    imap.logout()
    return yearly


def get_year_category_stats_imap(creds, year):
    """Get per-category email counts for a specific year using IMAP X-GM-RAW.

    Returns a dict compatible with StatsPanel.update_stats().
    """
    imap = _connect_imap(creds)
    stats = {}

    # Queries that work within All Mail folder
    queries = {
        "All Mail": f"after:{year}/1/1 before:{year + 1}/1/1",
        "Inbox": f"in:inbox after:{year}/1/1 before:{year + 1}/1/1",
        "Sent": f"in:sent after:{year}/1/1 before:{year + 1}/1/1",
        "Unread": f"is:unread after:{year}/1/1 before:{year + 1}/1/1",
        "Promotions": f"category:promotions after:{year}/1/1 before:{year + 1}/1/1",
        "Social": f"category:social after:{year}/1/1 before:{year + 1}/1/1",
        "Updates": f"category:updates after:{year}/1/1 before:{year + 1}/1/1",
        "Forums": f"category:forums after:{year}/1/1 before:{year + 1}/1/1",
    }

    for name, query in queries.items():
        try:
            status, data = imap.search(None, "X-GM-RAW", f'"{query}"')
            count = len(data[0].split()) if status == "OK" and data[0] else 0
            unread = count if name == "Unread" else 0
            stats[name] = {"total": count, "unread": unread}
        except Exception:
            stats[name] = {"total": 0, "unread": 0}

    # Trash and Spam must be searched in their own folders
    # (All Mail excludes Trash and Spam)
    since = f"01-Jan-{year}"
    before = f"01-Jan-{year + 1}"

    # Discover actual Trash/Spam folder names from IMAP LIST (locale-dependent)
    folder_map = {"Trash": None, "Spam": None}
    try:
        typ, folders = imap.list()
        if typ == "OK":
            for f in folders:
                decoded = f.decode() if isinstance(f, bytes) else f
                # Gmail marks special folders with attributes like \Trash, \Junk
                low = decoded.lower()
                if "\\trash" in low:
                    parts = decoded.split(' "/" ')
                    if len(parts) == 2:
                        folder_map["Trash"] = parts[1].strip()
                elif "\\junk" in low:
                    parts = decoded.split(' "/" ')
                    if len(parts) == 2:
                        folder_map["Spam"] = parts[1].strip()
    except Exception:
        pass

    for label, folder_name in folder_map.items():
        if not folder_name:
            stats[label] = {"total": 0, "unread": 0}
            continue
        try:
            typ, _ = imap.select(folder_name, readonly=True)
            if typ == "OK":
                status, data = imap.search(None, f"SINCE {since} BEFORE {before}")
                count = len(data[0].split()) if status == "OK" and data[0] else 0
                stats[label] = {"total": count, "unread": 0}
            else:
                stats[label] = {"total": 0, "unread": 0}
        except Exception:
            stats[label] = {"total": 0, "unread": 0}

    imap.logout()
    return stats


def _batch_trash_messages(service, query, progress_callback=None):
    """Move messages matching a query to trash in batches. Returns count trashed."""
    total_trashed = 0

    while True:
        result = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=500)
            .execute()
        )
        messages = result.get("messages", [])
        if not messages:
            break

        msg_ids = [m["id"] for m in messages]

        # batchModify supports up to 1000 IDs per call
        for i in range(0, len(msg_ids), 1000):
            batch = msg_ids[i:i + 1000]
            service.users().messages().batchModify(
                userId="me",
                body={"ids": batch, "addLabelIds": ["TRASH"], "removeLabelIds": ["INBOX"]},
            ).execute()

        total_trashed += len(msg_ids)
        if progress_callback:
            progress_callback(total_trashed)

        time.sleep(0.2)

    return total_trashed


def trash_by_year(service, year, progress_callback=None):
    """Move all emails from a specific year to trash."""
    query = f"after:{year}/1/1 before:{year + 1}/1/1"
    return _batch_trash_messages(service, query, progress_callback)


def trash_promotions(service, year=None, progress_callback=None):
    """Move all promotional emails to trash."""
    query = "category:promotions"
    if year:
        query += f" after:{year}/1/1 before:{year + 1}/1/1"
    return _batch_trash_messages(service, query, progress_callback)


def trash_spam(service, year=None, progress_callback=None):
    """Move all spam emails to trash (and purge)."""
    if year:
        query = f"in:spam after:{year}/1/1 before:{year + 1}/1/1"
        return _batch_trash_messages(service, query, progress_callback)

    total = 0
    while True:
        result = (
            service.users()
            .messages()
            .list(userId="me", labelIds=["SPAM"], maxResults=500)
            .execute()
        )
        messages = result.get("messages", [])
        if not messages:
            break

        for msg in messages:
            try:
                service.users().messages().delete(userId="me", id=msg["id"]).execute()
                total += 1
            except Exception:
                pass

        if progress_callback:
            progress_callback(total)

        time.sleep(0.2)

    return total


def trash_unread(service, year=None, progress_callback=None):
    """Move all unread emails to trash."""
    query = "is:unread"
    if year:
        query += f" after:{year}/1/1 before:{year + 1}/1/1"
    return _batch_trash_messages(service, query, progress_callback)


def trash_social(service, year=None, progress_callback=None):
    """Move all social emails to trash."""
    query = "category:social"
    if year:
        query += f" after:{year}/1/1 before:{year + 1}/1/1"
    return _batch_trash_messages(service, query, progress_callback)


def empty_trash(service):
    """Permanently delete all messages in trash."""
    service.users().messages().trash  # noqa
    # Gmail API doesn't have a direct "empty trash" but we can use:
    try:
        # This permanently deletes all trash
        service.users().messages().list(userId="me", labelIds=["TRASH"], maxResults=1).execute()
        # Use the dedicated endpoint if available
        import googleapiclient.http
        service._http.request(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages?labelIds=TRASH",
            method="DELETE",
        )
    except Exception:
        pass


def trash_older_than(service, days, year=None, progress_callback=None):
    """Move emails older than N days to trash."""
    query = f"older_than:{days}d"
    if year:
        query += f" after:{year}/1/1 before:{year + 1}/1/1"
    return _batch_trash_messages(service, query, progress_callback)


def trash_large_emails(service, size_mb=10, year=None, progress_callback=None):
    """Move emails larger than size_mb to trash."""
    query = f"larger:{size_mb}M"
    if year:
        query += f" after:{year}/1/1 before:{year + 1}/1/1"
    return _batch_trash_messages(service, query, progress_callback)


def trash_inbox(service, year=None, progress_callback=None):
    """Move all inbox emails (optionally for a year) to trash."""
    query = "in:inbox"
    if year:
        query += f" after:{year}/1/1 before:{year + 1}/1/1"
    return _batch_trash_messages(service, query, progress_callback)


def trash_sent(service, year=None, progress_callback=None):
    """Move all sent emails (optionally for a year) to trash."""
    query = "in:sent"
    if year:
        query += f" after:{year}/1/1 before:{year + 1}/1/1"
    return _batch_trash_messages(service, query, progress_callback)


def permanently_delete_trash(service, year=None, progress_callback=None):
    """Permanently delete trashed emails. If year is given, only delete trash from that year."""
    total = 0
    if year:
        query = f"in:trash after:{year}/1/1 before:{year + 1}/1/1"
        while True:
            result = (
                service.users()
                .messages()
                .list(userId="me", q=query, maxResults=500)
                .execute()
            )
            messages = result.get("messages", [])
            if not messages:
                break
            for msg in messages:
                try:
                    service.users().messages().delete(userId="me", id=msg["id"]).execute()
                    total += 1
                except Exception:
                    pass
            if progress_callback:
                progress_callback(total)
            time.sleep(0.2)
    else:
        while True:
            result = (
                service.users()
                .messages()
                .list(userId="me", labelIds=["TRASH"], maxResults=500)
                .execute()
            )
            messages = result.get("messages", [])
            if not messages:
                break
            for msg in messages:
                try:
                    service.users().messages().delete(userId="me", id=msg["id"]).execute()
                    total += 1
                except Exception:
                    pass
            if progress_callback:
                progress_callback(total)
            time.sleep(0.2)
    return total
