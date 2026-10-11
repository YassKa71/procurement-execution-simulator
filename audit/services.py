from .models import BusinessEvent


def record_event(*, actor, event_type, related_object, summary, agreement=None, metadata=None):
    return BusinessEvent.objects.create(
        exchange_agreement=agreement, event_type=event_type,
        actor_type=BusinessEvent.ActorType.USER, actor_user=actor,
        actor_label=actor.get_full_name() or actor.get_username(),
        related_object_type=related_object._meta.label,
        related_object_id=related_object.pk, summary=summary, metadata=metadata or {},
    )
