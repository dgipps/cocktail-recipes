import django


def test_django_setup():
    """Verify Django is properly configured."""
    assert django.VERSION >= (6, 0)
