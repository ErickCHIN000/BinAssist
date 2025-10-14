#!/usr/bin/env python3

"""
Base LLM Provider - Abstract interface for all LLM providers
Defines the contract that all providers must implement
"""

import asyncio
import random
from abc import ABC, abstractmethod
from typing import AsyncGenerator, List, Dict, Any, Optional, Callable
from ..models.llm_models import (
    ChatRequest, ChatResponse, EmbeddingRequest, EmbeddingResponse,
    ProviderCapabilities, ToolCall, ToolResult
)
from ..models.provider_types import ProviderType

# Binary Ninja logging
try:
    import binaryninja
    log = binaryninja.log.Logger(0, "BinAssist")
except ImportError:
    # Fallback for testing outside Binary Ninja
    class MockLog:
        @staticmethod
        def log_info(msg): print(f"[BinAssist] INFO: {msg}")
        @staticmethod
        def log_error(msg): print(f"[BinAssist] ERROR: {msg}")
        @staticmethod
        def log_warn(msg): print(f"[BinAssist] WARN: {msg}")
        @staticmethod
        def log_debug(msg): print(f"[BinAssist] DEBUG: {msg}")
    log = MockLog()


class BaseLLMProvider(ABC):
    """
    Abstract base class for all LLM providers.
    Defines the interface that all providers must implement.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize provider with configuration from settings service.

        Args:
            config: Provider configuration dictionary containing:
                - name: Provider name
                - model: Model identifier
                - url: API endpoint URL
                - api_key: API key (if required)
                - max_tokens: Maximum tokens per request
                - disable_tls: Whether to disable TLS verification
                - provider_type: Type of provider (from ProviderType enum)
        """
        self.config = config
        self.name = config.get('name', 'Unknown')
        self.model = config.get('model', '')
        self.url = config.get('url', '')
        self.api_key = config.get('api_key', '')
        self.max_tokens = config.get('max_tokens', 4096)
        self.disable_tls = config.get('disable_tls', False)
        self.provider_type = config.get('provider_type', 'openai')

        # Rate limit retry configuration
        self.rate_limit_max_retries = config.get('rate_limit_max_retries', 50)
        self.rate_limit_min_delay = config.get('rate_limit_min_delay', 10.0)  # seconds
        self.rate_limit_max_delay = config.get('rate_limit_max_delay', 30.0)  # seconds

    # ===================================================================
    # Rate Limit Retry Mechanism
    # ===================================================================

    async def _with_rate_limit_retry(self, operation_callable, *args, **kwargs):
        """
        Wrapper that retries an async operation when rate limit errors occur.

        This method implements exponential backoff with random jitter for rate limit errors.
        It will retry up to rate_limit_max_retries times, waiting between
        rate_limit_min_delay and rate_limit_max_delay seconds between attempts.

        Args:
            operation_callable: Async callable to execute (e.g., self._chat_completion_impl)
            *args: Positional arguments to pass to operation_callable
            **kwargs: Keyword arguments to pass to operation_callable

        Returns:
            Result from operation_callable

        Raises:
            RateLimitError: If max retries exceeded
            Other exceptions: Propagated as-is from operation_callable
        """
        attempt = 0

        while attempt < self.rate_limit_max_retries:
            try:
                # Execute the operation
                result = await operation_callable(*args, **kwargs)
                return result

            except RateLimitError as e:
                attempt += 1

                if attempt >= self.rate_limit_max_retries:
                    log.log_error(
                        f"Rate limit retry exhausted after {attempt} attempts for {self.name}. "
                        f"Giving up on this request."
                    )
                    raise RateLimitError(
                        f"Rate limit exceeded after {attempt} retries: {e}"
                    )

                # Calculate random delay between min and max
                delay = random.uniform(self.rate_limit_min_delay, self.rate_limit_max_delay)

                log.log_warn(
                    f"Rate limit error on attempt {attempt}/{self.rate_limit_max_retries} "
                    f"for {self.name}. Retrying in {delay:.1f} seconds... Error: {e}"
                )

                # Wait before retrying
                await asyncio.sleep(delay)

            except Exception as e:
                # Non-rate-limit errors are propagated immediately
                raise

        # This should never be reached due to the raise in the loop, but just in case
        raise RateLimitError(f"Rate limit retry mechanism failed unexpectedly")

    async def _with_rate_limit_retry_stream(self, operation_callable, *args, **kwargs) -> AsyncGenerator:
        """
        Wrapper that retries a streaming async operation when rate limit errors occur.

        This method implements exponential backoff with random jitter for rate limit errors
        in streaming contexts. It will retry up to rate_limit_max_retries times.

        Args:
            operation_callable: Async generator callable to execute (e.g., self._chat_completion_stream_impl)
            *args: Positional arguments to pass to operation_callable
            **kwargs: Keyword arguments to pass to operation_callable

        Yields:
            Items from operation_callable async generator

        Raises:
            RateLimitError: If max retries exceeded
            Other exceptions: Propagated as-is from operation_callable
        """
        attempt = 0

        while attempt < self.rate_limit_max_retries:
            try:
                # Execute the streaming operation
                async for item in operation_callable(*args, **kwargs):
                    yield item

                # If we successfully complete the stream, we're done
                return

            except RateLimitError as e:
                attempt += 1

                if attempt >= self.rate_limit_max_retries:
                    log.log_error(
                        f"Rate limit retry exhausted after {attempt} attempts for {self.name}. "
                        f"Giving up on this streaming request."
                    )
                    raise RateLimitError(
                        f"Rate limit exceeded after {attempt} retries: {e}"
                    )

                # Calculate random delay between min and max
                delay = random.uniform(self.rate_limit_min_delay, self.rate_limit_max_delay)

                log.log_warn(
                    f"Rate limit error on streaming attempt {attempt}/{self.rate_limit_max_retries} "
                    f"for {self.name}. Retrying in {delay:.1f} seconds... Error: {e}"
                )

                # Wait before retrying
                await asyncio.sleep(delay)

            except Exception as e:
                # Non-rate-limit errors are propagated immediately
                raise

        # This should never be reached due to the raise in the loop, but just in case
        raise RateLimitError(f"Rate limit retry mechanism failed unexpectedly for streaming")

    @abstractmethod
    async def chat_completion(self, request: ChatRequest, 
                            native_message_callback: Optional[Callable[[Dict[str, Any], ProviderType], None]] = None) -> ChatResponse:
        """
        Generate a chat completion (non-streaming).
        
        Args:
            request: Chat completion request
            native_message_callback: Optional callback for provider-native message updates
                                   Signature: callback(native_message: Dict[str, Any], provider_type: ProviderType)
            
        Returns:
            Chat completion response
            
        Raises:
            APIProviderError: On API errors
            AuthenticationError: On authentication failures
            RateLimitError: On rate limit exceeded
            NetworkError: On network issues
        """
        pass
    
    @abstractmethod
    async def chat_completion_stream(self, request: ChatRequest, 
                                   native_message_callback: Optional[Callable[[Dict[str, Any], ProviderType], None]] = None) -> AsyncGenerator[ChatResponse, None]:
        """
        Generate a streaming chat completion.
        
        Args:
            request: Chat completion request (with stream=True)
            native_message_callback: Optional callback for provider-native message updates
                                   Signature: callback(native_message: Dict[str, Any], provider_type: ProviderType)
            
        Yields:
            Partial chat completion responses
            
        Raises:
            APIProviderError: On API errors
            AuthenticationError: On authentication failures
            RateLimitError: On rate limit exceeded
            NetworkError: On network issues
        """
        pass
    
    @abstractmethod
    async def generate_embeddings(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """
        Generate text embeddings.
        
        Args:
            request: Embedding generation request
            
        Returns:
            Embedding response with vectors
            
        Raises:
            APIProviderError: On API errors
            AuthenticationError: On authentication failures
            RateLimitError: On rate limit exceeded
            NetworkError: On network issues
            NotImplementedError: If provider doesn't support embeddings
        """
        pass
    
    @abstractmethod
    async def test_connection(self) -> bool:
        """
        Test connectivity to the provider.
        
        Returns:
            True if connection successful, False otherwise
        """
        pass
    
    @abstractmethod
    def get_capabilities(self) -> ProviderCapabilities:
        """
        Get provider capabilities and supported features.
        
        Returns:
            Provider capabilities object
        """
        pass
    
    @abstractmethod
    def get_provider_type(self) -> ProviderType:
        """
        Get the provider type for this provider.
        
        Returns:
            ProviderType enum value
        """
        pass
    
    # ===================================================================
    # Tool Support Methods
    # ===================================================================
    
    def supports_tools(self) -> bool:
        """
        Check if this provider supports tool calling.
        
        Returns:
            True if provider supports tools, False otherwise
        """
        capabilities = self.get_capabilities()
        return capabilities.supports_tools
    
    def prepare_tool_enabled_request(self, request: ChatRequest, tools: List[Dict[str, Any]]) -> ChatRequest:
        """
        Prepare a chat request with tool definitions enabled.
        
        Args:
            request: Original chat request
            tools: List of tool definitions in OpenAI format
            
        Returns:
            Modified chat request with tools enabled
            
        Note:
            Default implementation adds tools to request. Providers can override
            for provider-specific tool formatting.
        """
        if not self.supports_tools():
            return request
        
        # Create a copy of the request with tools added
        tool_request = ChatRequest(
            messages=request.messages,
            model=request.model,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            top_p=request.top_p,
            stream=request.stream,
            tools=tools,
            tool_choice="auto",  # Let LLM decide when to use tools
            stop=request.stop,
            presence_penalty=request.presence_penalty,
            frequency_penalty=request.frequency_penalty,
            user=request.user
        )
        
        return tool_request
    
    def parse_tool_calls(self, response: ChatResponse) -> List[ToolCall]:
        """
        Parse tool calls from a chat response.
        
        Args:
            response: Chat response that may contain tool calls
            
        Returns:
            List of parsed tool calls
            
        Note:
            Default implementation returns response.tool_calls if present.
            Providers can override for provider-specific parsing.
        """
        if response.tool_calls:
            return response.tool_calls
        return []
    
    def get_stop_reason(self, response: ChatResponse) -> str:
        """
        Get the stop reason from a chat response.
        
        Args:
            response: Chat response
            
        Returns:
            Stop reason string ("stop", "tool_calls", "length", etc.)
        """
        return response.finish_reason
    
    def format_tool_results_for_continuation(self, tool_calls: List[ToolCall], tool_results: List[str]) -> List[Dict[str, Any]]:
        """
        Format tool execution results for LLM conversation continuation.
        
        Args:
            tool_calls: Original tool calls from LLM
            tool_results: Results from tool execution
            
        Returns:
            List of message dictionaries for LLM continuation
            
        Note:
            Default implementation creates tool messages in OpenAI format.
            Providers can override for provider-specific formatting.
        """
        messages = []
        
        for tool_call, result in zip(tool_calls, tool_results):
            messages.append({
                "role": "tool",
                "content": result,
                "tool_call_id": tool_call.id,
                "name": tool_call.name
            })
        
        return messages
    
    def has_tool_capability_changed(self, tools: List[Dict[str, Any]]) -> bool:
        """
        Check if tool capability requirements have changed.
        
        Args:
            tools: List of tool definitions
            
        Returns:
            True if provider needs to update tool capabilities
        """
        # Default implementation: no capability changes needed
        return False
    
    def validate_config(self) -> bool:
        """
        Validate provider configuration.
        
        Returns:
            True if configuration is valid
            
        Raises:
            ValueError: If configuration is invalid
        """
        if not self.name:
            raise ValueError("Provider name is required")
        if not self.model:
            raise ValueError("Model is required")
        if not self.url:
            raise ValueError("URL is required")
        
        return True
    
    def __str__(self) -> str:
        """String representation of provider"""
        return f"{self.__class__.__name__}(name='{self.name}', model='{self.model}')"
    
    def __repr__(self) -> str:
        """Detailed string representation"""
        return (f"{self.__class__.__name__}("
                f"name='{self.name}', "
                f"model='{self.model}', "
                f"url='{self.url}', "
                f"provider_type='{self.provider_type}')")


class LLMProviderError(Exception):
    """Base exception for LLM provider errors"""
    pass


class APIProviderError(LLMProviderError):
    """General API provider error"""
    pass


class AuthenticationError(LLMProviderError):
    """Authentication/authorization error"""
    pass


class RateLimitError(LLMProviderError):
    """Rate limit exceeded error"""
    pass


class NetworkError(LLMProviderError):
    """Network connectivity error"""
    pass