"""Test Langfuse tracing integration."""

import pytest
from unittest.mock import Mock, MagicMock, patch
from uuid import uuid4

from app.core.tracing import (
    SafeLangfuseInstrumentationTracer,
    NullInstrumentationTracer,
    configure_langfuse,
    LangfuseState,
    _mask_sensitive_data,
)
from app.core.config import Settings


def test_mask_sensitive_data_dict():
    """Test that sensitive fields are masked in dictionaries."""
    data = {
        "user_message": "Hello",
        "api_key": "sk-secret-12345",
        "password": "pass123",
        "model": "gpt-4",
    }
    
    masked = _mask_sensitive_data(data)
    
    assert masked["user_message"] == "Hello"
    assert masked["api_key"] == "***MASKED***"
    assert masked["password"] == "***MASKED***"
    assert masked["model"] == "gpt-4"


def test_mask_sensitive_data_nested():
    """Test masking in nested structures."""
    data = {
        "config": {
            "llm_api_key": "secret",
            "model": "gpt-4",
        },
        "messages": ["Hello", "World"],
    }
    
    masked = _mask_sensitive_data(data)
    
    assert masked["config"]["llm_api_key"] == "***MASKED***"
    assert masked["config"]["model"] == "gpt-4"
    assert masked["messages"] == ["Hello", "World"]


def test_mask_sensitive_data_long_string():
    """Test that very long strings are truncated."""
    long_string = "a" * 15000
    
    masked = _mask_sensitive_data(long_string)
    
    assert len(masked) == 10000 + len("... (truncated)")
    assert masked.endswith("... (truncated)")


def test_null_tracer():
    """Test NullInstrumentationTracer doesn't break on operations."""
    tracer = NullInstrumentationTracer()
    
    with tracer.observe(
        "test_operation",
        as_type="span",
        input={"test": "data"},
        metadata={"key": "value"},
        tags=["test"],
    ) as obs:
        # Should not raise any errors
        obs.update(output={"result": "success"})
        obs.update(metadata={"updated": True})
        obs.update(usage={"input": 100, "output": 50})


def test_safe_langfuse_tracer_with_mock_client():
    """Test SafeLangfuseInstrumentationTracer with mock Langfuse client."""
    mock_client = Mock()
    mock_observation = Mock()
    mock_context_manager = MagicMock()
    mock_context_manager.__enter__ = Mock(return_value=mock_observation)
    mock_context_manager.__exit__ = Mock(return_value=None)
    mock_client.start_as_current_observation = Mock(return_value=mock_context_manager)
    
    tracer = SafeLangfuseInstrumentationTracer(
        mock_client,
        "test-trace-id",
        feature_name="test_feature",
        tags=["test"],
    )
    
    with tracer.observe(
        "test_generation",
        as_type="generation",
        input={"prompt": "test"},
        model="gpt-4",
        tags=["generation"],
    ) as obs:
        obs.update(
            output={"response": "test"},
            usage={"input": 10, "output": 20},
        )
    
    # Verify client was called with correct arguments
    mock_client.start_as_current_observation.assert_called_once()
    call_kwargs = mock_client.start_as_current_observation.call_args[1]
    
    assert call_kwargs["name"] == "test_generation"
    assert call_kwargs["as_type"] == "generation"
    assert call_kwargs["model"] == "gpt-4"
    assert "feature:test_feature" in call_kwargs["tags"]
    assert "test" in call_kwargs["tags"]
    assert "generation" in call_kwargs["tags"]
    assert call_kwargs["trace_context"] == {"trace_id": "test-trace-id"}


def test_safe_langfuse_tracer_nested_observations():
    """Test that nested observations don't pass trace_context."""
    mock_client = Mock()
    mock_observation = Mock()
    mock_context_manager = MagicMock()
    mock_context_manager.__enter__ = Mock(return_value=mock_observation)
    mock_context_manager.__exit__ = Mock(return_value=None)
    mock_client.start_as_current_observation = Mock(return_value=mock_context_manager)
    
    tracer = SafeLangfuseInstrumentationTracer(
        mock_client,
        "test-trace-id",
        feature_name="test",
    )
    
    with tracer.observe("parent", as_type="span"):
        with tracer.observe("child", as_type="span"):
            pass
    
    # First call should have trace_context
    first_call_kwargs = mock_client.start_as_current_observation.call_args_list[0][1]
    assert "trace_context" in first_call_kwargs
    
    # Second call should NOT have trace_context (nested)
    second_call_kwargs = mock_client.start_as_current_observation.call_args_list[1][1]
    assert "trace_context" not in second_call_kwargs


def test_safe_langfuse_tracer_error_handling():
    """Test that tracer handles errors gracefully."""
    mock_client = Mock()
    mock_observation = Mock()
    mock_context_manager = MagicMock()
    mock_context_manager.__enter__ = Mock(return_value=mock_observation)
    mock_context_manager.__exit__ = Mock(return_value=None)
    mock_client.start_as_current_observation = Mock(return_value=mock_context_manager)
    
    tracer = SafeLangfuseInstrumentationTracer(mock_client, "test-trace-id")
    
    # Test that errors are caught and observation updated
    with pytest.raises(ValueError):
        with tracer.observe("test", as_type="span") as obs:
            raise ValueError("Test error")
    
    # Observation should have been updated with error status
    mock_observation.update.assert_called()
    update_kwargs = mock_observation.update.call_args[1]
    assert update_kwargs["level"] == "ERROR"
    assert "ValueError" in update_kwargs["status_message"]


def test_safe_langfuse_tracer_with_none_client():
    """Test that tracer with None client uses null behavior."""
    tracer = SafeLangfuseInstrumentationTracer(None, "test-trace-id")
    
    # Should not raise errors
    with tracer.observe("test", as_type="span", input={"test": "data"}) as obs:
        obs.update(output={"result": "success"})


def test_configure_langfuse_disabled():
    """Test Langfuse configuration when disabled."""
    settings = Settings(langfuse_enabled=False)
    
    state = configure_langfuse(settings)
    
    assert state.status == "disabled"
    assert state.client is None


def test_configure_langfuse_missing_keys():
    """Test Langfuse configuration with missing keys."""
    settings = Settings(
        langfuse_enabled=True,
        langfuse_public_key=None,
        langfuse_secret_key=None,
    )
    
    # Should be invalid due to validator
    assert not settings.langfuse_configured


@patch("app.core.tracing.Langfuse")
def test_configure_langfuse_with_credentials(mock_langfuse_class):
    """Test Langfuse configuration with valid credentials."""
    mock_client = Mock()
    mock_langfuse_class.return_value = mock_client
    
    settings = Settings(
        langfuse_enabled=True,
        langfuse_public_key="pk-lf-test",
        langfuse_secret_key="sk-lf-test",
        langfuse_base_url="https://cloud.langfuse.com",
    )
    
    state = configure_langfuse(settings)
    
    assert state.status == "configured"
    assert state.client == mock_client
    mock_langfuse_class.assert_called_once_with(
        public_key="pk-lf-test",
        secret_key="sk-lf-test",
        base_url="https://cloud.langfuse.com",
    )


@patch("app.core.tracing.Langfuse")
def test_configure_langfuse_initialization_error(mock_langfuse_class):
    """Test Langfuse configuration handles initialization errors."""
    mock_langfuse_class.side_effect = Exception("Connection failed")
    
    settings = Settings(
        langfuse_enabled=True,
        langfuse_public_key="pk-lf-test",
        langfuse_secret_key="sk-lf-test",
    )
    
    state = configure_langfuse(settings)
    
    # Should degrade gracefully
    assert state.status == "degraded"
    assert state.client is None


def test_observation_update_with_token_usage():
    """Test that observation update correctly maps token usage."""
    mock_observation = Mock()
    
    from app.core.tracing import _SafeObservation
    
    safe_obs = _SafeObservation(mock_observation, "test")
    
    safe_obs.update(
        usage={
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
        }
    )
    
    # Should map to Langfuse format
    mock_observation.update.assert_called_once()
    call_kwargs = mock_observation.update.call_args[1]
    assert call_kwargs["usage"] == {
        "input": 100,
        "output": 50,
        "total": 150,
    }


def test_observation_update_handles_errors():
    """Test that observation update handles errors gracefully."""
    mock_observation = Mock()
    mock_observation.update.side_effect = Exception("Update failed")
    
    from app.core.tracing import _SafeObservation
    
    safe_obs = _SafeObservation(mock_observation, "test")
    
    # Should not raise exception
    safe_obs.update(output={"test": "data"})
    mock_observation.update.assert_called_once()
