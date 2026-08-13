from abc import ABC, abstractmethod


class ProviderError(Exception):
    """A safe, user-facing provider failure."""


class BaseProvider(ABC):
    slug = ""
    label = ""
    default_model = ""

    @abstractmethod
    def generate(self, api_key, prompt, model=None, **options):
        """Return provider, model, text and optional usage without secrets."""

    @abstractmethod
    def list_models(self, api_key):
        """Return a list of safe model identifiers."""

    def validate_api_key(self, api_key):
        """Validate credentials with the provider without retaining plaintext."""
        self.list_models(api_key)

    def normalize_options(self, options):
        return {key: value for key, value in options.items() if value is not None}
