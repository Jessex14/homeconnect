import os
import subprocess
import sys

from django.test import TestCase


class HealthTests(TestCase):
    def test_live_and_ready(self):
        self.assertEqual(self.client.get("/health/live").status_code, 200)
        self.assertEqual(self.client.get("/health/ready").status_code, 200)

    def test_production_configuration_fails_without_secret(self):
        backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        environment = os.environ.copy()
        environment.pop("DJANGO_SECRET_KEY", None)
        environment["DJANGO_DEBUG"] = "false"
        environment.pop("DJANGO_ALLOWED_HOSTS", None)
        result = subprocess.run(
            [sys.executable, "-c", "import config.settings"],
            cwd=backend_dir,
            env=environment,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DJANGO_SECRET_KEY must be set", result.stderr)
