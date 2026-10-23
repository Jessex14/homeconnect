import os

from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.models import User
from apps.organizations.models import Organization, OrganizationMembership


class Command(BaseCommand):
    help = "Create the initial HomeConnect owner from secure environment variables."

    def handle(self, *args, **options):
        email = os.environ.pop("HC_OWNER_EMAIL", "").strip().lower()
        password = os.environ.pop("HC_OWNER_PASSWORD", "")
        if not email or not password:
            raise CommandError("HC_OWNER_EMAIL and HC_OWNER_PASSWORD are required.")
        if User.objects.filter(email=email).exists():
            raise CommandError("That account already exists; no changes were made.")
        try:
            validate_password(password)
        except ValidationError as exc:
            raise CommandError("Password rejected: " + "; ".join(exc.messages)) from exc

        with transaction.atomic():
            user = User.objects.create_user(email=email, password=password)
            organization = Organization.objects.create(name="HomeConnect")
            OrganizationMembership.objects.create(
                organization=organization,
                user=user,
                role=OrganizationMembership.Role.OWNER,
            )
        password = None
        self.stdout.write(self.style.SUCCESS(f"HomeConnect owner account created for {email}"))
