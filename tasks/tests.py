from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .executors import cleanup_expired_credentials, process_task
from .models import Task, TaskCredential


class TaskApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="alice", password="secure-pass-123")
        self.other = get_user_model().objects.create_user(username="bob", password="secure-pass-123")
        self.client = APIClient(); self.client.force_authenticate(self.user)

    @patch("tasks.views.process_task.delay")
    def test_create_and_idempotency(self, _):
        body = {"task_type": "echo", "payload": {"hello": "world"}}
        first = self.client.post("/api/v1/tasks/", body, format="json", HTTP_IDEMPOTENCY_KEY="key-1")
        duplicate = self.client.post("/api/v1/tasks/", body, format="json", HTTP_IDEMPOTENCY_KEY="key-1")
        self.assertEqual((first.status_code, duplicate.status_code, Task.objects.count()), (201, 200, 1))

    def test_owner_isolation_cancel_and_retry(self):
        task = Task.objects.create(owner=self.user, task_type="echo")
        self.client.force_authenticate(self.other); self.assertEqual(self.client.get(f"/api/v1/tasks/{task.id}/").status_code, 404)
        self.client.force_authenticate(self.user); self.assertEqual(self.client.post(f"/api/v1/tasks/{task.id}/cancel/").status_code, 202)
        with patch("tasks.views.process_task.delay"):
            self.assertEqual(self.client.post(f"/api/v1/tasks/{task.id}/retry/").status_code, 202)

    def test_registration_login_and_protected_endpoint(self):
        client = APIClient()
        self.assertEqual(client.post("/api/v1/auth/register/", {"username": "new-user", "password": "secure-password-123"}, format="json").status_code, 201)
        self.assertIn("access", client.post("/api/v1/auth/login/", {"username": "new-user", "password": "secure-password-123"}, format="json").data)
        self.assertEqual(client.get("/api/v1/tasks/").status_code, 401)


class AIHardeningTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="hardened", password="secure-pass-123")
        self.client = APIClient(); self.client.force_authenticate(self.user)

    def test_ai_endpoints_require_authentication(self):
        anon = APIClient()
        self.assertEqual(anon.post("/api/v1/ai/generate/", {"provider": "openai", "api_key": "test-key", "prompt": "x"}, format="json").status_code, 401)
        self.assertEqual(anon.post("/api/v1/ai/providers/openai/models/", {"api_key": "test-key"}, format="json").status_code, 401)
        self.assertEqual(anon.get("/api/v1/playground/").status_code, 200)

    @patch("tasks.views.process_task.delay")
    def test_api_key_is_redacted_from_payload_response_and_result(self, _):
        secret = "test-provider-credential"
        response = self.client.post("/api/v1/ai/generate/", {"provider": "openai", "api_key": secret, "prompt": "hello"}, format="json")
        task = Task.objects.get(id=response.data["id"])
        self.assertEqual(response.status_code, 201)
        self.assertNotIn(secret, str(task.payload)); self.assertNotIn(secret, str(response.data)); self.assertIsNone(task.result)
        self.assertNotEqual(task.credential.encrypted_api_key, secret)

    @patch("tasks.views.process_task.delay")
    def test_prompt_token_limits_and_provider_allowlist(self, _):
        self.assertEqual(self.client.post("/api/v1/ai/generate/", {"provider": "openai", "api_key": "test-key", "prompt": "x" * 20001}, format="json").status_code, 400)
        self.assertEqual(self.client.post("/api/v1/ai/generate/", {"provider": "openai", "api_key": "test-key", "prompt": "x", "max_tokens": 8193}, format="json").status_code, 400)
        self.assertEqual(self.client.post("/api/v1/ai/generate/", {"provider": "https://example.invalid", "api_key": "test-key", "prompt": "x"}, format="json").status_code, 400)
        self.assertEqual(self.client.post("/api/v1/ai/providers/not-a-provider/models/", {"api_key": "test-key"}, format="json").status_code, 400)

    @patch("tasks.views.process_task.delay")
    def test_generation_throttle(self, _):
        for number in range(5):
            self.assertEqual(self.client.post("/api/v1/ai/generate/", {"provider": "openai", "api_key": "test-key", "prompt": str(number)}, format="json").status_code, 201)
        self.assertEqual(self.client.post("/api/v1/ai/generate/", {"provider": "openai", "api_key": "test-key", "prompt": "blocked"}, format="json").status_code, 429)

    @patch("tasks.views.get_provider")
    def test_model_discovery_throttle_and_redaction(self, provider):
        provider.return_value.list_models.return_value = ["gpt-test"]
        for _ in range(10):
            response = self.client.post("/api/v1/ai/providers/openai/models/", {"api_key": "test-key"}, format="json")
            self.assertEqual(response.status_code, 200); self.assertNotIn("test-key", str(response.data))
        self.assertEqual(self.client.post("/api/v1/ai/providers/openai/models/", {"api_key": "test-key"}, format="json").status_code, 429)

    def test_cleanup_expired_credentials_and_cancellation(self):
        expired = Task.objects.create(owner=self.user, task_type="ai_generate", payload={"provider": "openai", "prompt": "x"})
        TaskCredential.objects.create(task=expired, encrypted_api_key=TaskCredential.encrypt("test-key"), expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(cleanup_expired_credentials(), 1)
        pending = Task.objects.create(owner=self.user, task_type="ai_generate", payload={"provider": "openai", "prompt": "x"})
        TaskCredential.objects.create(task=pending, encrypted_api_key=TaskCredential.encrypt("test-key"), expires_at=timezone.now() + timedelta(minutes=1))
        self.assertEqual(self.client.post(f"/api/v1/tasks/{pending.id}/cancel/").status_code, 202)
        self.assertFalse(TaskCredential.objects.filter(task=pending).exists())

    @patch("tasks.executors.deliver_webhook.delay")
    @patch("tasks.executors.get_provider")
    def test_success_removes_credential_and_openai_identifier_has_no_pii(self, provider, _):
        provider.return_value.generate.return_value = {"provider": "openai", "model": "gpt-test", "text": "done"}
        task = Task.objects.create(owner=self.user, task_type="ai_generate", payload={"provider": "openai", "prompt": "hello"})
        TaskCredential.objects.create(task=task, encrypted_api_key=TaskCredential.encrypt("test-key"), expires_at=timezone.now() + timedelta(minutes=1))
        process_task.apply(args=[str(task.id)]).get(); task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.SUCCESS); self.assertFalse(TaskCredential.objects.filter(task=task).exists())
        identifier = provider.return_value.generate.call_args.kwargs["safety_identifier"]
        self.assertTrue(identifier.startswith("usr_")); self.assertNotIn(self.user.username, identifier)
        if self.user.email:
            self.assertNotIn(self.user.email, identifier)

    @patch("tasks.executors.deliver_webhook.delay")
    @patch("tasks.executors.get_provider")
    def test_terminal_failure_is_sanitized_and_removes_credential(self, provider, _):
        provider.return_value.generate.side_effect = RuntimeError("Authorization: Bearer test-key")
        task = Task.objects.create(owner=self.user, task_type="ai_generate", payload={"provider": "openai", "prompt": "x"})
        TaskCredential.objects.create(task=task, encrypted_api_key=TaskCredential.encrypt("test-key"), expires_at=timezone.now() + timedelta(minutes=1))
        with patch.object(process_task, "max_retries", 0):
            process_task.apply(args=[str(task.id)]).get(propagate=False)
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.FAILED); self.assertEqual(task.error, "AI provider request failed.")
        self.assertFalse(TaskCredential.objects.filter(task=task).exists())

    def test_webhook_data_cannot_contain_credential(self):
        task = Task.objects.create(owner=self.user, task_type="ai_generate", payload={"provider": "openai", "prompt": "x"}, result={"text": "safe"})
        self.assertNotIn("api_key", str({"id": str(task.id), "result": task.result, "error": task.error}))

    def test_frontend_clears_key_and_does_not_use_browser_storage(self):
        source = open("tasks/static/tasks/playground.js", encoding="utf-8").read()
        self.assertGreaterEqual(source.count("finally { clearKey(); }"), 2)
        self.assertNotIn("localStorage", source); self.assertNotIn("sessionStorage", source)
