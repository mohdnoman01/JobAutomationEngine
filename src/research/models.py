from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class Company(BaseModel):
    name: str
    website: str | None = None
    careers_url: str | None = None
    industry: str | None = None
    location: str | None = None


class Job(BaseModel):
    title: str
    company: str
    url: str
    location: str | None = None
    description: str | None = None
    source: str | None = None
    employment_type: str | None = None


class UserProfile(BaseModel):
    target_roles: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    employment_types: list[str] = Field(default_factory=list)


class ContactQualification(StrEnum):
    eligible = "eligible"
    uncertain = "uncertain"
    rejected = "rejected"


class Contact(BaseModel):
    name: str | None = None
    email: str
    role: str | None = None
    company: str
    source: str | None = None
    source_url: str | None = None
    discovery_type: str = "public_page"
    qualification: ContactQualification | None = None
    qualification_reason: str | None = None

    @model_validator(mode="after")
    def populate_source_url_from_legacy_source(self) -> "Contact":
        if self.source_url is None and self.source and self.source.startswith(("http://", "https://")):
            self.source_url = self.source

        return self


class ResearchResult(BaseModel):
    company_name: str
    url: str
    text: str
    contacts: list[Contact] = Field(default_factory=list)
