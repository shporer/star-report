import pytest


def pytest_collection_modifyitems(items):
    for item in items:
        if item.get_closest_marker("live"):
            item.add_marker(pytest.mark.enable_socket)
