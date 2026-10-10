from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Profile


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_profile(sender, instance, created, **kwargs):
    """Every user gets a Profile (default role: customer)."""
    if created:
        role = Profile.Role.STAFF if instance.is_staff else Profile.Role.CUSTOMER
        Profile.objects.get_or_create(user=instance, defaults={'role': role})
