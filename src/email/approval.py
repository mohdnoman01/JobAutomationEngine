from src.email.models import EmailStatus, OutreachEmail


def approve_email(email: OutreachEmail) -> OutreachEmail:
    if email.status != EmailStatus.draft:
        raise ValueError(
            f"Only draft emails can be approved. "
            f"Current status: {email.status}"
        )

    email.status = EmailStatus.approved
    return email


def reject_email(email: OutreachEmail) -> OutreachEmail:
    if email.status != EmailStatus.draft:
        raise ValueError(
            f"Only draft emails can be rejected. "
            f"Current status: {email.status}"
        )

    email.status = EmailStatus.rejected
    return email