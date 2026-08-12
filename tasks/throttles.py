from rest_framework.throttling import UserRateThrottle


class AIGenerateThrottle(UserRateThrottle):
    scope = "ai_generate"


class AIModelDiscoveryThrottle(UserRateThrottle):
    scope = "ai_model_discovery"
