from src.email.models import OutreachEmail
from src.research.models import Company, Contact, Job


DEFAULT_SUBJECT = "Application for {job_title} at {company}"


DEFAULT_BODY = """Hi {contact_name},

I came across the {job_title} opportunity at {company} and I'm very interested in the role.

I would love to connect and learn more about the position and the team.

Best regards,
Job Candidate
"""


def create_outreach_email(
    job: Job,
    company: Company,
    contact: Contact,
    *,
    subject_template: str = DEFAULT_SUBJECT,
    body_template: str = DEFAULT_BODY,
) -> OutreachEmail:
    contact_name = contact.name or "Hiring Team"

    values = {
        "contact_name": contact_name,
        "company": company.name,
        "job_title": job.title,
    }

    return OutreachEmail(
        recipient=contact.email,
        subject=subject_template.format(**values),
        body=body_template.format(**values),
        company=company.name,
        job_url=job.url,
        contact_name=contact.name,
    )