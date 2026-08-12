import requests

from .base import BaseProvider, ProviderError


class OpenAIProvider(BaseProvider):
    slug = "openai"
    label = "OpenAI"
    default_model = "gpt-5.6"
    base_url = "https://api.openai.com/v1"

    def _headers(self, api_key):
        return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def list_models(self, api_key):
        try:
            response = requests.get(f"{self.base_url}/models", headers=self._headers(api_key), timeout=15)
            response.raise_for_status()
            return sorted(item["id"] for item in response.json().get("data", []) if item.get("id"))
        except (requests.RequestException, ValueError, KeyError) as exc:
            raise ProviderError("Unable to list OpenAI models.") from exc

    def generate(self, api_key, prompt, model=None, **options):
        safety_identifier = options.pop("safety_identifier", None)
        body = {"model": model or self.default_model, "input": prompt, "store": False}
        if safety_identifier:
            body["safety_identifier"] = safety_identifier
        body.update(self.normalize_options(options))
        try:
            response = requests.post(f"{self.base_url}/responses", headers=self._headers(api_key), json=body, timeout=120)
            response.raise_for_status()
            data = response.json()
            text = data.get("output_text") or ""
            if not text:
                for output in data.get("output", []):
                    for content in output.get("content", []):
                        if content.get("type") == "output_text":
                            text += content.get("text", "")
            if not text:
                raise ProviderError("OpenAI returned no text.")
            result = {"provider": self.slug, "model": data.get("model", body["model"]), "text": text}
            if data.get("usage"):
                result["usage"] = data["usage"]
            return result
        except ProviderError:
            raise
        except (requests.RequestException, ValueError, KeyError) as exc:
            raise ProviderError("OpenAI generation failed.") from exc
