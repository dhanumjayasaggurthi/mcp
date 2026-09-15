import os
import pytest
from sqlalchemy.engine import make_url
from enterprise_data_platform.configuration import configured_secrets, SETTINGS


@pytest.fixture
def configure(tmp_path, monkeypatch):
    for key in [v for section in SETTINGS.values() for v in section.values()]:
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / 'config.ini'
    monkeypatch.setenv('EDP_CONFIG_FILE', str(path))
    return path


def test_ini_literal_password_and_secret_resolution(configure):
    configure.write_text('[database]\nhost=localhost\nname=control\nuser=runtime\npassword=p%:@#word\n[app]\ncursor_secret_ref=ini://secrets/cursor_key\n[secrets]\ncursor_key=abc%123\n')
    secrets = configured_secrets()
    url = make_url(secrets.resolve(os.environ['EDP_CONTROL_DSN_REF']))
    assert url.password == 'p%:@#word'
    assert url.database == 'control'
    assert secrets.resolve(os.environ['EDP_CURSOR_SECRET_REF']) == 'abc%123'
    with pytest.raises(ValueError):
        secrets.resolve('ini://secrets/missing')


def test_environment_overrides_ini_and_relative_file_refs(configure, monkeypatch):
    configure.write_text('[app]\nenvironment=dev\nsecret_dir=secrets\n[database]\ndsn_ref=file://dsn\n')
    directory = configure.parent / 'secrets'
    directory.mkdir()
    (directory / 'dsn').write_text('private-value')
    monkeypatch.setenv('EDP_ENVIRONMENT', 'prod')
    secrets = configured_secrets()
    assert os.environ['EDP_ENVIRONMENT'] == 'prod'
    assert secrets.resolve('file://dsn') == 'private-value'
    with pytest.raises(ValueError):
        secrets.resolve('file://../dsn')


def test_bad_ini_does_not_expose_secret(configure):
    configure.write_text('[secrets]\nx=secret-value\nx=another-secret\n')
    with pytest.raises(ValueError, match='INI syntax') as error:
        configured_secrets()
    assert 'secret-value' not in str(error.value)
