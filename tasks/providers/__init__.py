from .base import ProviderError
from .gemini import GeminiProvider
from .isaac import IsaacProvider
from .openai import OpenAIProvider

PROVIDERS = {
    "openai": OpenAIProvider(),
    "gemini": GeminiProvider(),
    "isaac": IsaacProvider(),
}


def get_provider(slug):
    try:
        return PROVIDERS[slug]
    except KeyError as exc:
        raise ProviderError("Unsupported AI provider.") from exc


def provider_metadata():
    return [
        {"id": provider.slug, "label": provider.label, "default_model": provider.default_model}
        for provider in PROVIDERS.values()
    ]
