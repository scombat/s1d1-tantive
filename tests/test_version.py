from importlib.metadata import version


def test_version_is_the_installed_metadata():
    from tantive import __version__

    assert __version__ == version("tantive")
