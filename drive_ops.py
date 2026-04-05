"""Google Drive helper functions for listing folders and performing actions."""

from googleapiclient.discovery import build


def format_size(size_bytes):
    """Format bytes into human-readable size."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / (1024 ** 2):.1f} MB"
    else:
        return f"{size_bytes / (1024 ** 3):.2f} GB"


class DriveTree:
    """Cached in-memory tree of all Drive files for fast navigation."""

    def __init__(self, creds):
        drive = build("drive", "v3", credentials=creds)

        # Resolve real root folder ID
        root_meta = drive.files().get(fileId="root", fields="id").execute()
        self.root_id = root_meta["id"]

        # Fetch all non-trashed files
        self.all_files = []
        page_token = None
        while True:
            resp = drive.files().list(
                q="trashed=false",
                fields="nextPageToken, files(id, name, mimeType, parents, size, quotaBytesUsed)",
                pageSize=1000,
                pageToken=page_token,
            ).execute()
            self.all_files.extend(resp.get("files", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        # Build parent -> children mapping
        self.children_map = {}
        for f in self.all_files:
            for p in f.get("parents", []):
                self.children_map.setdefault(p, []).append(f)

        # Cache computed sizes
        self._size_cache = {}

    def compute_size(self, folder_id):
        """Recursively compute total size of a folder."""
        if folder_id in self._size_cache:
            return self._size_cache[folder_id]
        total = 0
        for child in self.children_map.get(folder_id, []):
            total += int(child.get("size") or child.get("quotaBytesUsed") or 0)
            if child.get("mimeType") == "application/vnd.google-apps.folder":
                total += self.compute_size(child["id"])
        self._size_cache[folder_id] = total
        return total

    def get_children(self, folder_id=None):
        """Get child folders of a folder with sizes, sorted descending.

        If folder_id is None, returns root-level folders.
        Returns list of dicts: [{"id", "name", "size", "size_formatted", "has_subfolders"}, ...]
        """
        parent_id = folder_id or self.root_id
        children = self.children_map.get(parent_id, [])

        # Separate folders and loose files
        child_folders = [
            f for f in children
            if f.get("mimeType") == "application/vnd.google-apps.folder"
        ]
        loose_files = [
            f for f in children
            if f.get("mimeType") != "application/vnd.google-apps.folder"
        ]

        results = []
        for folder in child_folders:
            size = self.compute_size(folder["id"])
            has_subs = any(
                c.get("mimeType") == "application/vnd.google-apps.folder"
                for c in self.children_map.get(folder["id"], [])
            )
            results.append({
                "id": folder["id"],
                "name": folder["name"],
                "size": size,
                "size_formatted": format_size(size),
                "has_subfolders": has_subs,
            })

        # Add a summary entry for loose files in this folder
        for lf in loose_files:
            file_size = int(lf.get("size") or lf.get("quotaBytesUsed") or 0)
            results.append({
                "id": lf["id"],
                "name": lf["name"],
                "size": file_size,
                "size_formatted": format_size(file_size),
                "has_subfolders": False,
                "is_file": True,
            })

        results.sort(key=lambda x: x["size"], reverse=True)
        return results


def get_root_folders_with_sizes(creds):
    """List root-level Drive folders with their total sizes (descending).

    Returns list of dicts: [{"id", "name", "size", "size_formatted"}, ...]
    """
    tree = DriveTree(creds)
    return tree.get_children()


def trash_drive_item(creds, file_id):
    """Move a Drive file or folder to trash."""
    drive = build("drive", "v3", credentials=creds)
    drive.files().update(fileId=file_id, body={"trashed": True}).execute()


# Keep old name as alias for compatibility
trash_drive_folder = trash_drive_item


def archive_drive_item(creds, file_id):
    """Move a Drive file or folder into an 'Archive' folder in root.

    Creates the Archive folder if it doesn't exist.
    """
    drive = build("drive", "v3", credentials=creds)

    # Find or create Archive folder
    resp = drive.files().list(
        q="name='Archive' and mimeType='application/vnd.google-apps.folder' "
          "and 'root' in parents and trashed=false",
        fields="files(id)",
    ).execute()

    archive_folders = resp.get("files", [])
    if archive_folders:
        archive_id = archive_folders[0]["id"]
    else:
        meta = {
            "name": "Archive",
            "mimeType": "application/vnd.google-apps.folder",
            "parents": ["root"],
        }
        archive_folder = drive.files().create(body=meta, fields="id").execute()
        archive_id = archive_folder["id"]

    # Move the item into Archive
    drive.files().update(
        fileId=file_id,
        addParents=archive_id,
        removeParents="root",
        fields="id, parents",
    ).execute()


# Keep old name as alias for compatibility
archive_drive_folder = archive_drive_item
