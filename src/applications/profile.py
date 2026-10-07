from __future__ import annotations

from pathlib import Path
import re

from pydantic import BaseModel, Field, field_validator

EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class EducationEntry(BaseModel):
    degree: str | None = None
    institution: str | None = None
    graduation_date: str | None = None


class ExperienceEntry(BaseModel):
    company: str
    role: str
    start_date: str | None = None
    end_date: str | None = None
    responsibilities: list[str] = Field(default_factory=list)


class ProjectEntry(BaseModel):
    name: str
    description: str | None = None
    technologies: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)


class CertificationEntry(BaseModel):
    name: str
    issuer: str | None = None
    date: str | None = None


class ApplicationLinks(BaseModel):
    linkedin: str | None = None
    github: str | None = None
    portfolio: str | None = None
    other: dict[str, str] = Field(default_factory=dict)


class ApplicationProfile(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    education: list[EducationEntry] = Field(default_factory=list)
    experience: list[ExperienceEntry] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    projects: list[ProjectEntry] = Field(default_factory=list)
    certifications: list[CertificationEntry] = Field(default_factory=list)
    links: ApplicationLinks = Field(default_factory=ApplicationLinks)
    resume_path: Path | None = None
    documents: list[Path] = Field(default_factory=list)
    work_authorization: str | None = None
    requires_sponsorship: bool | None = None
    relocation_preference: str | None = None
    notice_period: str | None = None
    salary_expectation: str | None = None
    employment_type: str | None = None
    configured_answers: dict[str, str] = Field(default_factory=dict)

    @field_validator("name", "email", "phone", "address", "city", "state", "country")
    @classmethod
    def normalize_optional_identity(cls, value: str | None) -> str | None:
        return value.strip() if value else value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value is not None and not EMAIL_PATTERN.fullmatch(value):
            raise ValueError("email must be a valid email address")
        return value.lower() if value else value

    @field_validator("resume_path")
    @classmethod
    def validate_resume_path(cls, value: Path | None) -> Path | None:
        if value is not None and not str(value).strip():
            raise ValueError("resume_path must not be empty")
        return value

    def validate_resume_file(self) -> Path:
        if self.resume_path is None:
            raise ValueError("A resume path is not configured")

        path = self.resume_path.expanduser()
        if not path.is_file():
            raise ValueError("Configured resume path is not a file")

        return path