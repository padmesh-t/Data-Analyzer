"""Unit tests for Multi-Provider LLM Infrastructure (OpenRouter, Grok, Local Proxy, Local LLM, Fallback)."""

import pytest
from unittest.mock import patch, MagicMock

from app.config import settings
from app.services.llm_service import LLMService, LLMProviderConfig


class TestLLMProviderResolution:
    def test_default_fallback_to_local_when_no_keys(self):
        """When no external keys are present, LLMService resolves to local LLM."""
        with patch.object(settings, "LLM_PROVIDER", "auto"), \
             patch.object(settings, "LLM_USE_MOCK", False), \
             patch.object(settings, "OPENROUTER_API_KEY", ""), \
             patch.object(settings, "GROK_API_KEY", ""), \
             patch.object(settings, "XAI_API_KEY", ""), \
             patch.object(settings, "LOCAL_PROXY_URL", ""), \
             patch.object(settings, "OPENAI_API_KEY", ""), \
             patch.object(settings, "VLLM_API_URL", "http://localhost:11434/v1"), \
             patch.object(settings, "LLM_MODEL", "qwen2.5-coder:3b"):
            
            service = LLMService()
            config = service.resolve_provider()
            
            assert config.provider == "local"
            assert config.base_url in ("http://localhost:11434/v1", "http://ollama:11434/v1")
            assert config.model == "qwen2.5-coder:3b"

    def test_openrouter_selected_when_key_present(self):
        """When OPENROUTER_API_KEY is present and provider is auto, OpenRouter is selected."""
        with patch.object(settings, "LLM_PROVIDER", "auto"), \
             patch.object(settings, "LLM_USE_MOCK", False), \
             patch.object(settings, "OPENROUTER_API_KEY", "sk-or-v1-test-key-12345"), \
             patch.object(settings, "OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct"), \
             patch.object(settings, "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"):
            
            service = LLMService()
            config = service.resolve_provider()
            
            assert config.provider == "openrouter"
            assert config.base_url == "https://openrouter.ai/api/v1"
            assert config.api_key == "sk-or-v1-test-key-12345"
            assert config.model == "meta-llama/llama-3.3-70b-instruct"
            assert "HTTP-Referer" in config.headers

    def test_grok_selected_when_key_present(self):
        """When GROK_API_KEY is present (and no OpenRouter key), Grok is selected."""
        with patch.object(settings, "LLM_PROVIDER", "auto"), \
             patch.object(settings, "LLM_USE_MOCK", False), \
             patch.object(settings, "OPENROUTER_API_KEY", ""), \
             patch.object(settings, "GROK_API_KEY", "xai-test-key-67890"), \
             patch.object(settings, "GROK_MODEL", "grok-2-latest"), \
             patch.object(settings, "GROK_BASE_URL", "https://api.x.ai/v1"):
            
            service = LLMService()
            config = service.resolve_provider()
            
            assert config.provider == "grok"
            assert config.base_url == "https://api.x.ai/v1"
            assert config.api_key == "xai-test-key-67890"
            assert config.model == "grok-2-latest"

    def test_local_proxy_selected_when_url_present(self):
        """When LOCAL_PROXY_URL is present (and no cloud keys), Local Proxy is selected."""
        with patch.object(settings, "LLM_PROVIDER", "auto"), \
             patch.object(settings, "LLM_USE_MOCK", False), \
             patch.object(settings, "OPENROUTER_API_KEY", ""), \
             patch.object(settings, "GROK_API_KEY", ""), \
             patch.object(settings, "XAI_API_KEY", ""), \
             patch.object(settings, "LOCAL_PROXY_URL", "http://localhost:4000/v1"), \
             patch.object(settings, "LOCAL_PROXY_MODEL", "custom-model"):
            
            service = LLMService()
            config = service.resolve_provider()
            
            assert config.provider == "local_proxy"
            assert config.base_url in ("http://localhost:4000/v1", "http://host.docker.internal:4000/v1")
            assert config.model == "custom-model"

    def test_explicit_provider_override(self):
        """When LLM_PROVIDER is explicitly set, it overrides auto-selection."""
        with patch.object(settings, "LLM_PROVIDER", "grok"), \
             patch.object(settings, "LLM_USE_MOCK", False), \
             patch.object(settings, "OPENROUTER_API_KEY", "sk-or-key"), \
             patch.object(settings, "GROK_API_KEY", "xai-key"), \
             patch.object(settings, "GROK_MODEL", "grok-2-latest"):
            
            service = LLMService()
            config = service.resolve_provider()
            
            assert config.provider == "grok"
            assert config.model == "grok-2-latest"

    def test_get_provider_info_metadata(self):
        """get_provider_info returns comprehensive diagnostic details."""
        with patch.object(settings, "LLM_PROVIDER", "openrouter"), \
             patch.object(settings, "LLM_USE_MOCK", False), \
             patch.object(settings, "OPENROUTER_API_KEY", "sk-or-key"), \
             patch.object(settings, "OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct"):
            
            service = LLMService()
            info = service.get_provider_info()
            
            assert info["active_provider"] == "openrouter"
            assert info["model"] == "meta-llama/llama-3.3-70b-instruct"
            assert info["has_api_key"] is True
            assert info["fallback_provider"] == "local"


    def test_get_provider_chain_ordered_sequence(self):
        """get_provider_chain returns providers in priority order: OpenRouter -> Groq -> Local Proxy -> OpenAI -> Local."""
        with patch.object(settings, "LLM_PROVIDER", "auto"), \
             patch.object(settings, "LLM_USE_MOCK", False), \
             patch.object(settings, "OPENROUTER_API_KEY", "sk-or-test"), \
             patch.object(settings, "GROQ_API_KEY", "gsk-test"), \
             patch.object(settings, "LOCAL_PROXY_URL", "http://localhost:4000/v1"), \
             patch.object(settings, "OPENAI_API_KEY", "sk-proj-test"), \
             patch.object(settings, "VLLM_API_URL", "http://localhost:11434/v1"):
            
            service = LLMService()
            chain = service.get_provider_chain()
            provider_names = [c.provider for c in chain]
            
            assert provider_names == ["openrouter", "groq", "local_proxy", "openai", "local"]

    def test_groq_key_autodetection_from_gsk_prefix(self):
        """When GROK_API_KEY starts with gsk_, it is auto-detected as GroqCloud provider."""
        with patch.object(settings, "LLM_PROVIDER", "auto"), \
             patch.object(settings, "LLM_USE_MOCK", False), \
             patch.object(settings, "OPENROUTER_API_KEY", ""), \
             patch.object(settings, "GROK_API_KEY", "gsk_1234567890abcdef"), \
             patch.object(settings, "GROQ_API_KEY", ""):
            
            service = LLMService()
            config = service.resolve_provider()
            
            assert config.provider == "groq"
            assert "groq.com" in config.base_url
            assert config.api_key == "gsk_1234567890abcdef"


class TestLLMExecutionAndFallback:
    def test_primary_provider_success(self):
        """When the primary cloud provider succeeds, return the result."""
        service = LLMService()
        mock_config = LLMProviderConfig(
            provider="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key="test-key",
            model="test-model",
        )

        with patch.object(settings, "LLM_USE_MOCK", False), \
             patch.object(service, "get_provider_chain", return_value=[mock_config]), \
             patch.object(service, "_execute_chat_completion", return_value="SELECT * FROM users;"):
            
            result = service._call_llm([{"role": "user", "content": "list users"}])
            assert result == "SELECT * FROM users;"

    def test_multi_hop_sequential_fallback(self):
        """When OpenRouter fails and Groq fails, fallback continues down chain to Local Proxy."""
        service = LLMService()
        openrouter_config = LLMProviderConfig(
            provider="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key="sk-or-key",
            model="llama-3.3-70b",
        )
        groq_config = LLMProviderConfig(
            provider="groq",
            base_url="https://api.groq.com/openai/v1",
            api_key="gsk-key",
            model="llama-3.3-70b-versatile",
        )
        proxy_config = LLMProviderConfig(
            provider="local_proxy",
            base_url="http://localhost:4000/v1",
            api_key="",
            model="qwen2.5-coder:3b",
        )
        local_config = LLMProviderConfig(
            provider="local",
            base_url="http://localhost:11434/v1",
            api_key="",
            model="qwen2.5-coder:3b",
        )

        chain = [openrouter_config, groq_config, proxy_config, local_config]
        calls = []

        def mock_execute(cfg, msgs, temperature, max_tokens):
            calls.append(cfg.provider)
            if cfg.provider == "openrouter":
                return None  # Simulate OpenRouter failure
            if cfg.provider == "groq":
                return None  # Simulate Groq rate-limit / timeout
            if cfg.provider == "local_proxy":
                return "SELECT * FROM proxy_result;"
            return "SELECT * FROM local_result;"

        with patch.object(settings, "LLM_USE_MOCK", False), \
             patch.object(service, "get_provider_chain", return_value=chain), \
             patch.object(service, "_execute_chat_completion", side_effect=mock_execute):
            
            result = service._call_llm([{"role": "user", "content": "list users"}])
            assert result == "SELECT * FROM proxy_result;"
            assert calls == ["openrouter", "groq", "local_proxy"]

    def test_full_chain_exhaustion_falls_back_to_local_llm(self):
        """When all cloud and proxy providers fail, fallback reaches the local LLM."""
        service = LLMService()
        cloud_config = LLMProviderConfig(
            provider="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key="test-key",
            model="test-model",
        )
        local_config = LLMProviderConfig(
            provider="local",
            base_url="http://localhost:11434/v1",
            api_key="",
            model="qwen2.5-coder:3b",
        )

        chain = [cloud_config, local_config]
        calls = []

        def mock_execute(cfg, msgs, temperature, max_tokens):
            calls.append(cfg.provider)
            if cfg.provider == "openrouter":
                return None
            if cfg.provider == "local":
                return "SELECT * FROM users_from_local;"
            return None

        with patch.object(settings, "LLM_USE_MOCK", False), \
             patch.object(service, "get_provider_chain", return_value=chain), \
             patch.object(service, "_execute_chat_completion", side_effect=mock_execute):
            
            result = service._call_llm([{"role": "user", "content": "list users"}])
            assert result == "SELECT * FROM users_from_local;"
            assert calls == ["openrouter", "local"]

    def test_all_providers_fail_returns_none(self):
        """When every provider in the chain fails (including local), returns None gracefully."""
        service = LLMService()
        local_config = LLMProviderConfig(
            provider="local",
            base_url="http://localhost:11434/v1",
            api_key="",
            model="qwen2.5-coder:3b",
        )
        with patch.object(settings, "LLM_USE_MOCK", False), \
             patch.object(service, "get_provider_chain", return_value=[local_config]), \
             patch.object(service, "_execute_chat_completion", return_value=None):
            
            result = service._call_llm([{"role": "user", "content": "list users"}])
            assert result is None

    def test_mock_mode_returns_none(self):
        """When mock mode is enabled, _call_llm returns None without making HTTP calls."""
        service = LLMService()
        with patch.object(settings, "LLM_USE_MOCK", True):
            result = service._call_llm([{"role": "user", "content": "hello"}])
            assert result is None
