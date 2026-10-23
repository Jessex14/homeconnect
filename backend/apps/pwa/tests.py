from pathlib import Path

from django.test import TestCase


class PwaShellTests(TestCase):
    def test_shell_sets_csrf_cookie_and_renders_app(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("csrftoken", response.cookies)
        self.assertContains(response, "HomeConnect — Property workspace")

    def test_tenant_invitation_link_opens_the_app_shell(self):
        response = self.client.get("/tenant-invitation?token=one-time-token")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertContains(response, "HomeConnect — Property workspace")

    def test_invitation_link_uses_browser_fragment_and_hides_token_after_load(self):
        app_js = (Path(__file__).parent / "static" / "pwa" / "app.js").read_text(encoding="utf-8")
        self.assertIn('invitationFragment.get("token")', app_js)
        self.assertIn('history.replaceState({}, document.title, window.location.pathname)', app_js)

    def test_properties_screen_has_tenancy_invitation_form(self):
        app_js = (Path(__file__).parent / "static" / "pwa" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="tenant-invite-form"', app_js)
        self.assertIn("/invitations`, { method: \"POST\"", app_js)

    def test_tenancy_screen_uses_scoped_records_and_manager_capabilities(self):
        app_js = (Path(__file__).parent / "static" / "pwa" / "app.js").read_text(encoding="utf-8")
        self.assertIn('api("/api/v1/tenancies")', app_js)
        self.assertIn("function tenanciesView()", app_js)
        self.assertIn('data-tenancy-activate=', app_js)
        self.assertIn('data-tenancy-end=', app_js)

    def test_mpesa_setup_and_tenant_checkout_are_present(self):
        app_js = (Path(__file__).parent / "static" / "pwa" / "app.js").read_text(encoding="utf-8")
        self.assertIn('function mpesaSettingsView()', app_js)
        self.assertIn('id="mpesa-settings-form"', app_js)
        self.assertIn('id="mpesa-payment-form"', app_js)
        self.assertIn('Pay with M-Pesa', app_js)

    def test_service_worker_can_control_root_and_never_caches_api(self):
        response = self.client.get("/service-worker.js")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Service-Worker-Allowed"], "/")
        self.assertContains(response, 'url.pathname.startsWith("/api/")')
