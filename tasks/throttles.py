from rest_framework.throttling import UserRateThrottle


class AIGenerateThrottle(UserRateThrottle):
    scope = "ai_generate"


class AIModelDiscoveryThrottle(UserRateThrottle):
    scope = "ai_model_discovery"


class AICredentialThrottle(UserRateThrottle):
    """Limit key validation attempts to reduce provider-key probing and cost."""

    scope = "ai_credential"
