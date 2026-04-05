"""Gmail OAuth2 authentication module."""

import os
import json
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://mail.google.com/", "https://www.googleapis.com/auth/drive"]
TOKEN_PATH = os.path.join(os.path.dirname(__file__), "token.json")
CREDENTIALS_PATH = os.path.join(os.path.dirname(__file__), "credentials.json")


def authenticate():
    """Authenticate with Gmail API and return the service object."""
    creds = None

    if os.path.exists(TOKEN_PATH):
        # Check if stored token has all required scopes
        with open(TOKEN_PATH, "r") as f:
            token_data = json.load(f)
        stored_scopes = set(token_data.get("scopes", []))
        if set(SCOPES).issubset(stored_scopes):
            creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
        # else: creds stays None → forces re-authentication for new scopes

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_PATH):
                raise FileNotFoundError(
                    f"Missing {CREDENTIALS_PATH}. "
                    "Download OAuth credentials from Google Cloud Console. "
                    "See README.md for setup instructions."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_PATH, "w") as token_file:
            token_file.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def get_credentials():
    """Return the current OAuth2 credentials (refreshing if needed)."""
    creds = None
    if os.path.exists(TOKEN_PATH):
        with open(TOKEN_PATH, "r") as f:
            token_data = json.load(f)
        stored_scopes = set(token_data.get("scopes", []))
        if set(SCOPES).issubset(stored_scopes):
            creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds
