"""Create a tenant + user for local testing, and print a ready-to-use JWT.

    python manage.py create_tenant --name "Acme" --username acme --password pw

Prints the tenant id and an access token you can paste into an Authorization
header: `Authorization: Bearer <token>`.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from core.tokens import TenantTokenObtainPairSerializer
from tenants.models import Membership, Tenant


class Command(BaseCommand):
    help = "Create a tenant + user and print a JWT access token."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--name", required=True, help="Tenant display name")
        parser.add_argument("--username", required=True)
        parser.add_argument("--password", required=True)

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        User = get_user_model()
        name = options["name"]
        username = options["username"]
        password = options["password"]

        tenant = Tenant.objects.create(name=name)
        user = User.objects.create_user(username=username, password=password)
        Membership.objects.create(
            user=user, tenant=tenant, role=Membership.Role.OWNER
        )

        token = TenantTokenObtainPairSerializer.get_token(user)

        self.stdout.write(self.style.SUCCESS("Tenant created."))
        self.stdout.write(f"  tenant_id: {tenant.id}")
        self.stdout.write(f"  username : {username}")
        self.stdout.write("")
        self.stdout.write("Access token (Authorization: Bearer <token>):")
        self.stdout.write(str(token.access_token))
