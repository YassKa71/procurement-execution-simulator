from django.conf import settings
from django.core.mail import send_mail


def send_validation_email(validation):
    """Replaceable email boundary; Django uses a console backend in local development."""
    agreement = validation.requirement.exchange_agreement
    deadline = validation.requirement.deadline
    body = (
        f"Hello {validation.person},\n\n"
        f"Please review the following request for {agreement.reference}:\n\n"
        f"{validation.question}\n\n"
        f"Context: {validation.requirement.note}\n"
        f"Response deadline: {deadline or 'Not specified'}\n\n"
        "Please reply to the requesting team. Your response will be recorded in the case."
    )
    sent = send_mail(
        subject=f"Validation requested: {agreement.reference}", message=body,
        from_email=settings.DEFAULT_FROM_EMAIL, recipient_list=[validation.email], fail_silently=False,
    )
    if sent != 1:
        raise RuntimeError("The email backend did not accept the validation request.")
