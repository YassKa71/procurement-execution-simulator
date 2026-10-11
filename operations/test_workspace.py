from datetime import date
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from accounts.models import User
from agreements.services import create_exchange_agreement
from contracts.services import create_contract
from suppliers.models import Supplier
from .models import Requirement


class WorkspaceInteractionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_roles", verbosity=0)
        cls.user = User.objects.create_user("workspace-user")
        cls.user.groups.add(Group.objects.get(name="Manager"))
        supplier = Supplier.objects.create(reference="WS", name="Workspace Supplier")
        contract = create_contract(actor=cls.user, supplier=supplier, reference="WS-C", title="Contract", start_date=date(2026, 1, 1))
        cls.agreement = create_exchange_agreement(actor=cls.user, supplier=supplier, contract=contract)

    def setUp(self):
        self.client.force_login(self.user)
        self.action_url = reverse("case-action", args=[self.agreement.pk, "create-requirement"]) + "?panel=1"

    def test_drawer_form_uses_same_origin_frame_and_csrf(self):
        response = self.client.get(self.action_url)
        self.assertEqual(response["X-Frame-Options"], "SAMEORIGIN")
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertNotContains(response, 'class="main-nav"')

    def test_invalid_drawer_form_keeps_errors_and_does_not_write(self):
        response = self.client.post(self.action_url, {"note": ""})
        self.assertContains(response, "This field is required.")
        self.assertNotContains(response, "data-action-saved")
        self.assertFalse(Requirement.objects.exists())

    def test_successful_drawer_form_signals_parent_after_saving(self):
        response = self.client.post(self.action_url, {"note": "Wait for delivery"})
        self.assertContains(response, "data-action-saved")
        self.assertEqual(response["X-Frame-Options"], "SAMEORIGIN")
        self.assertEqual(Requirement.objects.get().note, "Wait for delivery")

    def test_server_tab_navigation_works_without_javascript(self):
        url = reverse("agreement-detail", args=[self.agreement.pk])
        for tab in ("overview", "requirements", "goods", "history"):
            response = self.client.get(url, {"tab": tab})
            self.assertEqual(response.context["active_tab"], tab)
        self.assertEqual(self.client.get(url, {"tab": "unknown"}).context["active_tab"], "overview")
