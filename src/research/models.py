from pydantic import BaseModel, Field


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


class Contact(BaseModel):
    name: str | None = None
    email: str
    role: str | None = None
    company: str
    source: str | None = None


class ResearchResult(BaseModel):
    company_name: str
    url: str
    text: str
    contacts: list[Contact] = Field(default_factory=list)
