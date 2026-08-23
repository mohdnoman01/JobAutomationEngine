import csv
from pathlib import Path

from src.research.models import Company


def load_companies(file_path: str) -> list[Company]:
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"Company file not found: {file_path}")

    with path.open("r", newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)

        return [
            Company(**row)
            for row in reader
        ]

