import requests

from .base import BaseProvider, ProviderError


class IsaacProvider(BaseProvider):
    slug = "isaac"
    label = "Poke Isaac"
    default_model = "pokee-isaac"
    base_url = "https://api.pokee.ai/v1"

    def _headers(self, api_key):
        return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def list_models(self, api_key):
        try:
            response = requests.get(f"{self.base_url}/models", headers=self._headers(api_key), timeout=15)
            response.raise_for_status()
            return sorted(item["id"] for item in response.json().get("data", []) if item.get("id"))
        except (requests.RequestException, ValueError, KeyError) as exc:
            raise ProviderError("Unable to list Poke Isaac models.") from exc

    def generate(self, api_key, prompt, model=None, **options):
        body = {"model": model or self.default_model, "messages": [{"role": "user", "content": prompt}], "stream": False}
        body.update(self.normalize_options(options))
        try:
            response = requests.post(f"{self.base_url}/chat/completions", headers=self._headers(api_key), json=body, timeout=120)
            response.raise_for_status()
            data = response.json()
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not text:
                raise ProviderError("Poke Isaac returned no text.")
            result = {"provider": self.slug, "model": data.get("model", body["model"]), "text": text}
            if data.get("usage"):
                result["usage"] = data["usage"]
            return result
        except ProviderError:
            raise
        except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
            raise ProviderError("Poke Isaac generation failed.") from exc
