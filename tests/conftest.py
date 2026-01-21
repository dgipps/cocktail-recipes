import pytest


@pytest.fixture
def client():
    """A Django test client instance."""
    from django.test import Client

    return Client()
