from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from .executors import process_task
from .models import Task


class TaskApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="alice", password="secure-pass-123")
        self.other = get_user_model().objects.create_user(username="bob", password="secure-pass-123")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    @patch("tasks.views.process_task.delay")
    def test_create_and_idempotency(self, delay):
        payload = {"task_type": "echo", "payload": {"hello": "world"}}
        first = self.client.post("/api/v1/tasks/", payload, format="json", HTTP_IDEMPOTENCY_KEY="key-1")
        duplicate = self.client.post("/api/v1/tasks/", payload, format="json", HTTP_IDEMPOTENCY_KEY="key-1")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(Task.objects.count(), 1)
        self.assertEqual(first.data["id"], duplicate.data["id"])
        # Transaction callbacks run after the enclosing test transaction commits.
        # The response and database uniqueness are the behavior under test here.

    @patch("tasks.views.process_task.delay")
    def test_same_key_for_different_users_creates_two_tasks(self, _):
        self.client.post("/api/v1/tasks/", {"task_type": "echo"}, format="json", HTTP_IDEMPOTENCY_KEY="same")
        self.client.force_authenticate(self.other)
        response = self.client.post("/api/v1/tasks/", {"task_type": "echo"}, format="json", HTTP_IDEMPOTENCY_KEY="same")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Task.objects.count(), 2)

    def test_owner_isolation_cancel_and_retry(self):
        task = Task.objects.create(owner=self.user, task_type="echo")
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f"/api/v1/tasks/{task.id}/").status_code, 404)
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.post(f"/api/v1/tasks/{task.id}/cancel/").status_code, 202)
        with patch("tasks.views.process_task.delay"):
            self.assertEqual(self.client.post(f"/api/v1/tasks/{task.id}/retry/").status_code, 202)

    @patch("config.urls.redis.Redis.from_url")
    def test_health_and_readiness(self, redis_from_url):
        redis_from_url.return_value.ping.return_value = True
        client = APIClient()
        self.assertEqual(client.get("/health/").status_code, 200)
        self.assertEqual(client.get("/ready/").status_code, 200)

    def test_unknown_task_type_is_rejected(self):
        response = self.client.post("/api/v1/tasks/", {"task_type": "unknown"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_registration_login_and_protected_endpoint(self):
        client = APIClient()
        registration = client.post("/api/v1/auth/register/", {"username": "new-user", "password": "secure-password-123"}, format="json")
        login = client.post("/api/v1/auth/login/", {"username": "new-user", "password": "secure-password-123"}, format="json")
        self.assertEqual(registration.status_code, 201)
        self.assertEqual(login.status_code, 200)
        self.assertIn("access", login.data)
        self.assertEqual(client.get("/api/v1/tasks/").status_code, 401)


class TaskExecutionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="worker", password="secure-pass-123")

    @patch("tasks.executors.deliver_webhook.delay")
    def test_echo_execution_succeeds(self, _):
        task = Task.objects.create(owner=self.user, task_type="echo", payload={"x": 1})
        process_task.apply(args=[str(task.id)]).get()
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.SUCCESS)
        self.assertEqual(task.result, {"echoed": {"x": 1}})


class PlaygroundApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="ai-user", password="secure-pass-123")
        self.client = APIClient(); self.client.force_authenticate(self.user)

    @patch("tasks.views.process_task.delay")
    def test_generate_redacts_key_and_is_idempotent(self, _):
        body = {"provider": "openai", "api_key": "super-secret-key", "model": "gpt-test", "prompt": "hello"}
        first = self.client.post("/api/v1/ai/generate/", body, format="json", HTTP_IDEMPOTENCY_KEY="ai-one")
        second = self.client.post("/api/v1/ai/generate/", body, format="json", HTTP_IDEMPOTENCY_KEY="ai-one")
        task = Task.objects.get(id=first.data["id"])
        self.assertEqual((first.status_code, second.status_code), (201, 200))
        self.assertNotIn("api_key", task.payload); self.assertNotIn("super-secret-key", str(first.data))
        self.assertNotEqual(task.credential.encrypted_api_key, "super-secret-key")

    def test_invalid_ai_request_and_auth(self):
        self.assertEqual(self.client.post("/api/v1/ai/generate/", {"provider": "openai", "prompt": "x"}, format="json").status_code, 400)
        self.assertEqual(self.client.post("/api/v1/ai/generate/", {"provider": "bad", "api_key": "x", "prompt": "x"}, format="json").status_code, 400)
        anon = APIClient(); self.assertEqual(anon.get("/api/v1/ai/providers/").status_code, 401)
        self.assertEqual(anon.get("/api/v1/playground/").status_code, 200)

    @patch("tasks.views.get_provider")
    def test_model_discovery_does_not_return_key(self, provider):
        provider.return_value.list_models.return_value = ["gpt-test"]
        response = self.client.post("/api/v1/ai/providers/openai/models/", {"api_key": "super-secret-key"}, format="json")
        self.assertEqual(response.status_code, 200); self.assertEqual(response.data["models"], ["gpt-test"])
        self.assertNotIn("super-secret-key", str(response.data))


class AIExecutionTests(TestCase):
    def setUp(self): self.user = get_user_model().objects.create_user(username="worker-ai", password="secure-pass-123")

    @patch("tasks.executors.deliver_webhook.delay")
    @patch("tasks.executors.get_provider")
    def test_ai_execution_uses_key_and_removes_credential(self, provider, _):
        from django.utils import timezone
        from datetime import timedelta
        provider.return_value.generate.return_value = {"provider": "openai", "model": "gpt-test", "text": "done"}
        task = Task.objects.create(owner=self.user, task_type="ai_generate", payload={"provider": "openai", "model": "gpt-test", "prompt": "hello"})
        from .models import TaskCredential
        TaskCredential.objects.create(task=task, encrypted_api_key=TaskCredential.encrypt("test-key"), expires_at=timezone.now() + timedelta(minutes=5))
        process_task.apply(args=[str(task.id)]).get(); task.refresh_from_db()
        provider.return_value.generate.assert_called_once(); self.assertEqual(task.result["text"], "done")
        self.assertFalse(TaskCredential.objects.filter(task=task).exists())
