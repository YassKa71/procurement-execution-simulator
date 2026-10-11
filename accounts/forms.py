def add_service_errors(form, error):
    """Map model/service validation failures onto the submitted form."""
    if hasattr(error, "message_dict"):
        for field, errors in error.message_dict.items():
            form.add_error(field if field in form.fields else None, errors)
    else:
        form.add_error(None, error)
