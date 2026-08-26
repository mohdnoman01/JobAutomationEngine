from __future__ import annotations

import json
from pathlib import Path

from src.email.models import OutreachEmail


def save_drafts(
    drafts: list[OutreachEmail],
    path: str | Path = "data/output/email_drafts.json",
) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    data = [draft.model_dump(mode="json") for draft in drafts]

    file_path.write_text(
        json.dumps(data, indent=2),
        encoding="utf-8",
    )


def load_drafts(
    path: str | Path = "data/output/email_drafts.json",
) -> list[OutreachEmail]:
    file_path = Path(path)

    if not file_path.exists():
        return []

    data = json.loads(file_path.read_text(encoding="utf-8"))

    return [OutreachEmail.model_validate(item) for item in data]