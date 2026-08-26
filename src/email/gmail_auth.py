from __future__ import annotations

from pathlib import Path
from typing import Any

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def get_gmail_service(
    credentials_path: str | Path = "credentials.json",
    token_path: str | Path = "token.json",
) -> Any:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    credentials_file = Path(credentials_path)
    token_file = Path(token_path)

    credentials = None

    if token_file.exists():
        credentials = Credentials.from_authorized_user_file(
            token_file,
            SCOPES,
        )

    if credentials is None or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            if not credentials_file.exists():
                raise FileNotFoundError(
                    f"Google credentials file not found: {credentials_file}"
                )

            flow = InstalledAppFlow.from_client_secrets_file(
                credentials_file,
                SCOPES,
            )

            credentials = flow.run_local_server(port=0)

        token_file.write_text(
            credentials.to_json(),
            encoding="utf-8",
        )

    return build(
        "gmail",
        "v1",
        credentials=credentials,
    )