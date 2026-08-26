from pathlib import Path

import pytest

from src.email.gmail_auth import get_gmail_service


def test_missing_credentials_file_raises_error(tmp_path):
    credentials_path = tmp_path / "credentials.json"
    token_path = tmp_path / "token.json"

    with pytest.raises(FileNotFoundError, match="Google credentials file not found"):
        get_gmail_service(
            credentials_path=credentials_path,
            token_path=token_path,
        )


def test_missing_credentials_does_not_create_token(tmp_path):
    credentials_path = tmp_path / "credentials.json"
    token_path = tmp_path / "token.json"

    with pytest.raises(FileNotFoundError):
        get_gmail_service(
            credentials_path=credentials_path,
            token_path=token_path,
        )

    assert not token_path.exists()