from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.test import TestCase, Client
from .models import User
from .services import require_permission

class FoundationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_roles", verbosity=0)
        cls.manager = User.objects.create_user("manager", password="test-password")
        cls.bookkeeper = User.objects.create_user("bookkeeper", password="test-password")
        cls.manager.groups.add(Group.objects.get(name="Manager"))
        cls.bookkeeper.groups.add(Group.objects.get(name="Bookkeeper"))

    def test_role_boundaries(self):
        for user in (self.manager, self.bookkeeper):
            for action in ("access_workspace", "create_purchase_order", "operate_exchange_agreement", "manage_digital_invoice", "mark_invoice_ready", "execute_payment"):
                require_permission(user, "accounts." + action)
        for action in ("create_contract", "create_exchange_agreement"):
            require_permission(self.manager, "accounts." + action)
            with self.assertRaises(PermissionDenied):
                require_permission(self.bookkeeper, "accounts." + action)

    def test_anonymous_inactive_and_unassigned_denied(self):
        inactive = User.objects.create_user("inactive", is_active=False)
        inactive.groups.add(Group.objects.get(name="Manager"))
        unassigned = User.objects.create_user("unassigned")
        for actor in (AnonymousUser(), inactive, unassigned):
            with self.assertRaises(PermissionDenied):
                require_permission(actor, "accounts.access_workspace")

    def test_custom_group_can_grant_permission(self):
        group = Group.objects.create(name="Future role")
        group.permissions.add(Permission.objects.get(codename="access_workspace"))
        user = User.objects.create_user("future")
        user.groups.add(group)
        require_permission(user, "accounts.access_workspace")

    def test_setup_roles_is_repeatable(self):
        call_command("setup_roles", verbosity=0)
        self.assertEqual(Group.objects.filter(name__in=["Manager", "Bookkeeper"]).count(), 2)
        self.assertEqual(Group.objects.get(name="Bookkeeper").permissions.count(), 6)

    def test_login_dashboard_logout(self):
        self.assertRedirects(self.client.get("/"), "/accounts/login/?next=/")
        response = self.client.post("/accounts/login/", {"username": "bookkeeper", "password": "test-password"})
        self.assertRedirects(response, "/")
        self.assertContains(self.client.get("/"), "Workspace foundation")
        self.assertEqual(self.client.get("/accounts/logout/").status_code, 405)
        self.assertRedirects(self.client.post("/accounts/logout/"), "/accounts/login/")
        self.assertRedirects(self.client.get("/"), "/accounts/login/?next=/")

    def test_workspace_denies_user_without_role(self):
        self.client.force_login(User.objects.create_user("no-role"))
        self.assertEqual(self.client.get("/").status_code, 403)

    def test_login_form_has_csrf_protection(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post("/accounts/login/", {"username": "manager", "password": "test-password"}).status_code, 403)

    def test_business_manager_has_no_admin_access(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get("/admin/").status_code, 302)
