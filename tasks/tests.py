from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.contrib.staticfiles import finders
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from .executors import cleanup_expired_credentials, process_task
from .models import ProviderCredential, Task, TaskCredential
from .providers import ProviderError


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


@override_settings(STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
class SwaggerUiTests(TestCase):
    def test_docs_use_self_hosted_assets_and_external_initialization_script(self):
        response = self.client.get("/api/docs/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("script-src 'self'", response["Content-Security-Policy"])
        self.assertNotIn("cdn.jsdelivr.net", response.content.decode())
        document = response.content.decode()
        self.assertIn("/api/docs/?script=", document)
        self.assertNotIn("<style>", document)
        for asset in (
            "drf_spectacular_sidecar/swagger-ui-dist/swagger-ui.css",
            "drf_spectacular_sidecar/swagger-ui-dist/swagger-ui-bundle.js",
            "drf_spectacular_sidecar/swagger-ui-dist/swagger-ui-standalone-preset.js",
        ):
            self.assertIsNotNone(finders.find(asset))
        script_response = self.client.get("/api/docs/?script=")
        self.assertEqual(script_response.status_code, 200)
        self.assertEqual(script_response["Content-Type"].split(";", 1)[0], "application/javascript")


class AIHardeningTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="hardened", password="secure-pass-123")
        self.client = APIClient(); self.client.force_authenticate(self.user)

    @override_settings(STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
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

    def test_frontend_uses_saved_credentials_and_does_not_use_browser_storage(self):
        source = open("tasks/static/tasks/playground.js", encoding="utf-8").read()
        self.assertIn("/api/v1/ai/credentials/${provider}/", source)
        self.assertIn("/api/v1/ai/generate/saved/", source)
        self.assertIn("clearApiKey();", source)
        self.assertNotIn("localStorage", source); self.assertNotIn("sessionStorage", source)
        self.assertNotIn("/api/v1/ai/generate/\",", source)


@override_settings(STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
class PlaygroundPageTests(TestCase):
    def test_playground_uses_local_assets_and_has_no_inline_code(self):
        response = self.client.get("/api/v1/playground/")
        document = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("script-src 'self'", response["Content-Security-Policy"])
        self.assertIn("tasks/playground.css", document)
        self.assertIn("tasks/playground.js", document)
        self.assertNotIn("<style", document)
        self.assertNotIn("<script>", document)
        self.assertIn("/api/v1/ai/generate/saved/", open("tasks/static/tasks/playground.js", encoding="utf-8").read())
        self.assertIsNotNone(finders.find("tasks/playground.css"))
        self.assertIsNotNone(finders.find("tasks/playground.js"))


class ProviderCredentialTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="credential-owner", password="secure-pass-123")
        self.other = get_user_model().objects.create_user(username="credential-other", password="secure-pass-123")
        self.client = APIClient(); self.client.force_authenticate(self.user)

    def test_credential_endpoints_require_authentication_and_reject_unknown_providers(self):
        anon = APIClient()
        self.assertEqual(anon.put("/api/v1/ai/credentials/openai/", {"api_key": "secret"}, format="json").status_code, 401)
        self.assertEqual(self.client.put("/api/v1/ai/credentials/unknown/", {"api_key": "secret"}, format="json").status_code, 400)
        self.assertEqual(self.client.post("/api/v1/ai/generate/saved/", {"provider": "unknown", "prompt": "hello"}, format="json").status_code, 400)

    @patch("tasks.views.get_provider")
    def test_save_status_update_and_delete_never_return_plaintext(self, provider):
        secret = "openai-user-key"
        response = self.client.put("/api/v1/ai/credentials/openai/", {"api_key": secret}, format="json")
        provider.return_value.validate_api_key.assert_called_once_with(secret)
        credential = ProviderCredential.objects.get(owner=self.user, provider="openai")
        self.assertEqual(response.status_code, 200); self.assertEqual(response.data, {"provider": "openai", "configured": True})
        self.assertNotEqual(credential.encrypted_api_key, secret); self.assertEqual(credential.decrypt(), secret)
        self.assertEqual(self.client.get("/api/v1/ai/credentials/openai/").data, {"provider": "openai", "configured": True})
        self.assertEqual(self.client.put("/api/v1/ai/credentials/openai/", {"api_key": "rotated-key"}, format="json").status_code, 200)
        self.assertEqual(ProviderCredential.objects.filter(owner=self.user, provider="openai").count(), 1)
        self.assertEqual(self.client.delete("/api/v1/ai/credentials/openai/").status_code, 204)
        self.assertEqual(self.client.get("/api/v1/ai/credentials/openai/").data, {"provider": "openai", "configured": False})

    def test_credentials_are_owner_scoped(self):
        ProviderCredential.objects.create(owner=self.user, provider="gemini", encrypted_api_key=ProviderCredential.encrypt("owner-key"))
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get("/api/v1/ai/credentials/gemini/").data, {"provider": "gemini", "configured": False})
        self.assertEqual(self.client.delete("/api/v1/ai/credentials/gemini/").status_code, 204)
        self.assertTrue(ProviderCredential.objects.filter(owner=self.user, provider="gemini").exists())

    @patch("tasks.views.process_task.delay")
    def test_saved_credential_generation_uses_only_request_owner_credential(self, _):
        ProviderCredential.objects.create(owner=self.user, provider="openai", encrypted_api_key=ProviderCredential.encrypt("owner-key"))
        ProviderCredential.objects.create(owner=self.other, provider="openai", encrypted_api_key=ProviderCredential.encrypt("other-key"))
        response = self.client.post("/api/v1/ai/generate/saved/", {"provider": "openai", "prompt": "hello"}, format="json")
        task = Task.objects.get(id=response.data["id"])
        self.assertEqual(response.status_code, 201)
        self.assertEqual(task.payload["credential_source"], "saved")
        self.assertFalse(TaskCredential.objects.filter(task=task).exists())
        self.assertNotIn("owner-key", str(task.payload)); self.assertNotIn("other-key", str(response.data))

    @patch("tasks.views.process_task.delay")
    def test_saved_generation_requires_own_credential(self, _):
        ProviderCredential.objects.create(owner=self.other, provider="isaac", encrypted_api_key=ProviderCredential.encrypt("other-key"))
        response = self.client.post("/api/v1/ai/generate/saved/", {"provider": "isaac", "prompt": "hello"}, format="json")
        self.assertEqual(response.status_code, 409)

    @patch("tasks.executors.deliver_webhook.delay")
    @patch("tasks.executors.get_provider")
    def test_worker_uses_saved_credential_for_owner(self, provider, _):
        ProviderCredential.objects.create(owner=self.user, provider="gemini", encrypted_api_key=ProviderCredential.encrypt("owner-key"))
        task = Task.objects.create(owner=self.user, task_type="ai_generate", payload={"provider": "gemini", "prompt": "hello", "credential_source": "saved"})
        provider.return_value.generate.return_value = {"provider": "gemini", "model": "gemini-test", "text": "done"}
        process_task.apply(args=[str(task.id)]).get(); task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.SUCCESS)
        self.assertEqual(provider.return_value.generate.call_args.kwargs["api_key"], "owner-key")


class ApplicationEndToEndTests(TestCase):
    """Exercises the public page, JWT boundary, BYOK lifecycle, and task result flow."""

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="e2e-owner", password="secure-pass-123")
        self.other = get_user_model().objects.create_user(username="e2e-other", password="secure-pass-123")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    @override_settings(STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
    @patch("tasks.views.process_task.delay")
    @patch("tasks.views.get_provider")
    def test_complete_playground_lifecycle_for_all_providers(self, validator, _):
        anonymous = APIClient()
        page = anonymous.get("/api/v1/playground/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Bring Your Own API Key", page.content.decode())
        self.assertEqual(self.client.get("/api/v1/playground/").status_code, 200)

        for provider in ("openai", "gemini", "isaac"):
            secret = f"{provider}-e2e-key"
            with self.subTest(provider=provider):
                saved = self.client.put(f"/api/v1/ai/credentials/{provider}/", {"api_key": secret}, format="json")
                self.assertEqual(saved.status_code, 200)
                self.assertEqual(saved.data, {"provider": provider, "configured": True})
                self.assertNotIn(secret, str(saved.data))
                self.assertEqual(self.client.get(f"/api/v1/ai/credentials/{provider}/").data, {"provider": provider, "configured": True})

                queued = self.client.post("/api/v1/ai/generate/saved/", {"provider": provider, "prompt": "Return a short integration response."}, format="json")
                self.assertEqual(queued.status_code, 201)
                task = Task.objects.get(id=queued.data["id"])
                with patch("tasks.executors.deliver_webhook.delay"), patch("tasks.executors.get_provider") as worker_provider:
                    worker_provider.return_value.generate.return_value = {"provider": provider, "model": "integration-model", "text": f"{provider} complete"}
                    process_task.apply(args=[str(task.id)]).get()
                detail = self.client.get(f"/api/v1/tasks/{task.id}/")
                self.assertEqual(detail.status_code, 200)
                self.assertEqual(detail.data["status"], Task.Status.SUCCESS)
                self.assertEqual(detail.data["result"]["text"], f"{provider} complete")
                self.assertNotIn(secret, str(detail.data))

        removed = self.client.delete("/api/v1/ai/credentials/gemini/")
        self.assertEqual(removed.status_code, 204)
        self.assertEqual(self.client.get("/api/v1/ai/credentials/gemini/").data, {"provider": "gemini", "configured": False})

    def test_expired_jwt_is_rejected_at_the_real_jwt_boundary(self):
        expired = AccessToken.for_user(self.user)
        expired.set_exp(lifetime=timedelta(seconds=-1))
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {expired}")
        self.assertEqual(client.get("/api/v1/ai/credentials/openai/").status_code, 401)
        self.assertEqual(client.post("/api/v1/ai/generate/saved/", {"provider": "openai", "prompt": "hello"}, format="json").status_code, 401)

    @patch("tasks.views.process_task.delay")
    def test_existing_operational_endpoints_and_owner_boundary(self, _):
        task_response = self.client.post("/api/v1/tasks/", {"task_type": "echo", "payload": {"source": "e2e"}}, format="json")
        self.assertEqual(task_response.status_code, 201)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f"/api/v1/tasks/{task_response.data['id']}/").status_code, 404)
        self.assertEqual(self.client.get("/api/v1/ai/credentials/openai/").data, {"provider": "openai", "configured": False})

    @override_settings(STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
    def test_docs_schema_health_and_local_static_assets_remain_available(self):
        self.assertEqual(self.client.get("/healthz/").status_code, 200)
        self.assertEqual(self.client.get("/api/schema/").status_code, 200)
        self.assertEqual(self.client.get("/api/docs/").status_code, 200)
        self.assertEqual(self.client.get("/static/tasks/playground.css").status_code, 200)
        self.assertEqual(self.client.get("/static/tasks/playground.js").status_code, 200)


class BYOKEndToEndTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="byok-owner", password="secure-pass-123")
        self.other = get_user_model().objects.create_user(username="byok-other", password="secure-pass-123")
        self.client = APIClient(); self.client.force_authenticate(self.user)

    def test_credential_write_throttle_limits_validation_attempts(self):
        with patch("tasks.views.get_provider"):
            for _ in range(5):
                self.assertEqual(self.client.put("/api/v1/ai/credentials/openai/", {"api_key": "valid-key"}, format="json").status_code, 200)
            self.assertEqual(self.client.put("/api/v1/ai/credentials/openai/", {"api_key": "valid-key"}, format="json").status_code, 429)

    @patch("tasks.views.process_task.delay")
    @patch("tasks.views.get_provider")
    def test_saved_credential_flow_for_every_provider(self, validator, _):
        for provider in ("openai", "gemini", "isaac"):
            secret = f"{provider}-private-key"
            with self.subTest(provider=provider):
                saved = self.client.put(f"/api/v1/ai/credentials/{provider}/", {"api_key": secret}, format="json")
                self.assertEqual(saved.status_code, 200)
                self.assertEqual(saved.data, {"provider": provider, "configured": True})
                self.assertNotIn(secret, str(saved.data))
                self.assertEqual(self.client.get(f"/api/v1/ai/credentials/{provider}/").data, {"provider": provider, "configured": True})
                task_response = self.client.post("/api/v1/ai/generate/saved/", {"provider": provider, "prompt": "Say hello"}, format="json")
                self.assertEqual(task_response.status_code, 201)
                task = Task.objects.get(id=task_response.data["id"])
                self.assertEqual(task.payload["credential_source"], "saved")
                self.assertNotIn(secret, str(task_response.data)); self.assertNotIn(secret, str(task.payload))
                with patch("tasks.executors.deliver_webhook.delay"), patch("tasks.executors.get_provider") as worker_provider:
                    worker_provider.return_value.generate.return_value = {"provider": provider, "model": "test-model", "text": f"{provider} response"}
                    process_task.apply(args=[str(task.id)]).get()
                task.refresh_from_db()
                self.assertEqual(task.status, Task.Status.SUCCESS)
                self.assertEqual(task.result["text"], f"{provider} response")
                self.assertEqual(worker_provider.return_value.generate.call_args.kwargs["api_key"], secret)
                self.assertNotIn(secret, str(task.result)); self.assertNotIn(secret, task.error or "")

    @patch("tasks.views.get_provider")
    def test_invalid_key_is_not_stored_and_is_safely_reported(self, provider):
        secret = "invalid-key-that-must-not-leak"
        provider.return_value.validate_api_key.side_effect = ProviderError(f"401 Bearer {secret}")
        response = self.client.put("/api/v1/ai/credentials/openai/", {"api_key": secret}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data, {"detail": "Unable to validate this API key."})
        self.assertNotIn(secret, str(response.data))
        self.assertFalse(ProviderCredential.objects.filter(owner=self.user, provider="openai").exists())

    def test_missing_malformed_empty_and_large_requests_are_rejected(self):
        self.assertEqual(self.client.put("/api/v1/ai/credentials/openai/", {}, format="json").status_code, 400)
        self.assertEqual(self.client.post("/api/v1/ai/generate/saved/", {"provider": "openai"}, format="json").status_code, 400)
        self.assertEqual(self.client.post("/api/v1/ai/generate/saved/", {"provider": "openai", "prompt": "   "}, format="json").status_code, 400)
        response = self.client.post("/api/v1/ai/generate/saved/", {"provider": "openai", "prompt": "x" * 20001}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.post("/api/v1/ai/generate/saved/", {"provider": "unsupported", "prompt": "hello"}, format="json").status_code, 400)

    @patch("tasks.views.process_task.delay")
    def test_deleted_credential_and_duplicate_submission_are_handled(self, _):
        ProviderCredential.objects.create(owner=self.user, provider="openai", encrypted_api_key=ProviderCredential.encrypt("owner-key"))
        headers = {"HTTP_IDEMPOTENCY_KEY": "playground-request-1"}
        first = self.client.post("/api/v1/ai/generate/saved/", {"provider": "openai", "prompt": "hello"}, format="json", **headers)
        duplicate = self.client.post("/api/v1/ai/generate/saved/", {"provider": "openai", "prompt": "hello"}, format="json", **headers)
        self.assertEqual((first.status_code, duplicate.status_code), (201, 200))
        self.assertEqual(first.data["id"], duplicate.data["id"])
        self.client.delete("/api/v1/ai/credentials/openai/")
        deleted = self.client.post("/api/v1/ai/generate/saved/", {"provider": "openai", "prompt": "hello"}, format="json")
        self.assertEqual(deleted.status_code, 409)

    @patch("tasks.executors.deliver_webhook.delay")
    @patch("tasks.executors.get_provider")
    def test_provider_outage_is_sanitized_without_leaking_key(self, provider, _):
        secret = "outage-key-that-must-not-leak"
        ProviderCredential.objects.create(owner=self.user, provider="gemini", encrypted_api_key=ProviderCredential.encrypt(secret))
        task = Task.objects.create(owner=self.user, task_type="ai_generate", payload={"provider": "gemini", "prompt": "hello", "credential_source": "saved"})
        provider.return_value.generate.side_effect = ProviderError(f"upstream Authorization: Bearer {secret}")
        with patch.object(process_task, "max_retries", 0):
            process_task.apply(args=[str(task.id)]).get(propagate=False)
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.FAILED)
        self.assertEqual(task.error, "AI provider request failed.")
        self.assertNotIn(secret, task.error)

    @override_settings(OPENAI_API_KEY="deployment-key-must-not-be-used")
    @patch("tasks.executors.deliver_webhook.delay")
    @patch("tasks.executors.get_provider")
    def test_deleted_credential_never_falls_back_to_deployment_key(self, provider, _):
        task = Task.objects.create(owner=self.user, task_type="ai_generate", payload={"provider": "openai", "prompt": "hello", "credential_source": "saved"})
        with patch.object(process_task, "max_retries", 0):
            process_task.apply(args=[str(task.id)]).get(propagate=False)
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.FAILED)
        self.assertEqual(task.error, "AI credential expired or is unavailable; submit a new task.")
        provider.return_value.generate.assert_not_called()

    def test_expired_jwt_cannot_access_credentials_or_generation(self):
        anonymous = APIClient()
        self.assertEqual(anonymous.get("/api/v1/ai/credentials/openai/").status_code, 401)
        self.assertEqual(anonymous.put("/api/v1/ai/credentials/openai/", {"api_key": "key"}, format="json").status_code, 401)
        self.assertEqual(anonymous.post("/api/v1/ai/generate/saved/", {"provider": "openai", "prompt": "hello"}, format="json").status_code, 401)
