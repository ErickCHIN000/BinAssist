#!/usr/bin/env python3

from .base_provider import (
    BaseLLMProvider, LLMProviderError, APIProviderError, 
    AuthenticationError, RateLimitError, NetworkError
)
from .provider_factory import get_provider_factory, LLMProviderFactory

# Import providers as they are implemented
try:
    from .anthropic_provider import AnthropicProvider
except ImportError:
    AnthropicProvider = None

try:
    from .openai_provider import OpenAIProvider
except ImportError:
    OpenAIProvider = None

try:
    from .ollama_provider import OllamaProvider
except ImportError:
    OllamaProvider = None

try:
    from .lmstudio_provider import LMStudioProvider
except ImportError:
    LMStudioProvider = None

__all__ = [
    'BaseLLMProvider', 'LLMProviderError', 'APIProviderError',
    'AuthenticationError', 'RateLimitError', 'NetworkError',
    'get_provider_factory', 'LLMProviderFactory'
]

# Add available providers to __all__
if AnthropicProvider:
    __all__.append('AnthropicProvider')
if OpenAIProvider:
    __all__.append('OpenAIProvider')
if OllamaProvider:
    __all__.append('OllamaProvider')
if LMStudioProvider:
    __all__.append('LMStudioProvider')
