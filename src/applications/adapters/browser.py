from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field


class BrowserField(BaseModel):
    key: str
    label: str
    kind: str = "text"
    required: bool = False
    accept: list[str] = Field(default_factory=list)
    max_file_size: int | None = None
    honeypot: bool = False


class BrowserControl(BaseModel):
    label: str
    selector: str
    safe_to_click: bool = False


class BrowserPageSnapshot(BaseModel):
    url: str
    title: str = ""
    form_found: bool = False
    form_action: str | None = None
    form_method: str | None = None
    form_enctype: str | None = None
    fields: list[BrowserField] = Field(default_factory=list)
    controls: list[BrowserControl] = Field(default_factory=list)


class BrowserAutomation(Protocol):
    def open_url(self, url: str) -> None: ...

    def inspect_page(self) -> BrowserPageSnapshot: ...

    def click_safe_control(self, selector: str) -> None: ...

    def locate_field(self, label: str) -> str | None: ...

    def fill_field(self, key: str, value: str) -> None: ...

    def upload_file(self, key: str, path: str) -> None: ...

    def detect_human_action(self) -> str | None: ...

    def submit_form(self) -> None: ...

    def inspect_result(self) -> BrowserPageSnapshot: ...
