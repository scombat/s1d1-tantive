def test_version_is_the_release():
    from tantive import __version__

    assert __version__ == "0.6.0"
