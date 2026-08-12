import requests

from .base import BaseProvider, ProviderError


class GeminiProvider(BaseProvider):
    slug = "gemini"
    label = "Gemini"
    default_model = "gemini-3.6-flash"
    base_url = "https://generativelanguage.googleapis.com/v1beta"

    def _headers(self, api_key):
        return {"x-goog-api-key": api_key, "Content-Type": "application/json"}

    def list_models(self, api_key):
        try:
            response = requests.get(f"{self.base_url}/models", headers=self._headers(api_key), timeout=15)
            response.raise_for_status()
            return sorted(item["name"].removeprefix("models/") for item in response.json().get("models", []) if item.get("name") and "generateContent" in item.get("supportedGenerationMethods", ["generateContent"]))
        except (requests.RequestException, ValueError, KeyError) as exc:
            raise ProviderError("Unable to list Gemini models.") from exc

    def generate(self, api_key, prompt, model=None, **options):
        generation_config = self.normalize_options(options)
        body = {"contents": [{"parts": [{"text": prompt}]}]}
        if generation_config:
            body["generationConfig"] = generation_config
        selected_model = model or self.default_model
        try:
            response = requests.post(f"{self.base_url}/models/{selected_model}:generateContent", headers=self._headers(api_key), json=body, timeout=120)
            response.raise_for_status()
            data = response.json()
            parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            text = "".join(part.get("text", "") for part in parts)
            if not text:
                raise ProviderError("Gemini returned no text.")
            result = {"provider": self.slug, "model": selected_model, "text": text}
            if data.get("usageMetadata"):
                result["usage"] = data["usageMetadata"]
            return result
        except ProviderError:
            raise
        except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
            raise ProviderError("Gemini generation failed.") from exc
