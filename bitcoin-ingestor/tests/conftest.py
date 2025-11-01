"""Pytest configuration and shared fixtures."""

import pytest


@pytest.fixture(autouse=True)
def reset_environment():
    """Reset environment variables between tests."""
    import os

    original_env = os.environ.copy()
    yield
    os.environ.clear()
    os.environ.update(original_env)
