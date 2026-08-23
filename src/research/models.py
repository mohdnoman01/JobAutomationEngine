from pydantic import BaseModel, Field
from typing import Optional


class Company(BaseModel):
    name: str
    website: Optional[str] = None
    careers_url: Optional[str] = None
    industry: Optional[str] = None
    location: Optional[str] = None


class Job(BaseModel):
    title: str
    company: str
    url: str
    location: Optional[str] = None
    description: Optional[str] = None
    source: Optional[str] = None
    employment_type: Optional[str] = None

class ResearchResult(BaseModel):
    company_name: str
    url: str
    text: str
