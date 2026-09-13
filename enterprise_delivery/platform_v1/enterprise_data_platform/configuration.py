"""Optional INI configuration. Explicit environment settings take precedence."""
import configparser
import os
from pathlib import Path

from sqlalchemy.engine import URL
from .identity import MountedSecretProvider


SETTINGS = {
    'app': {'environment': 'EDP_ENVIRONMENT', 'cursor_secret_ref': 'EDP_CURSOR_SECRET_REF',
            'secret_dir': 'EDP_SECRET_DIR', 'resource_limits': 'EDP_RESOURCE_LIMITS'},
    'database': {'dsn_ref': 'EDP_CONTROL_DSN_REF', 'pool_size': 'EDP_CONTROL_POOL_SIZE'},
    'oidc': {'issuer': 'EDP_OIDC_ISSUER', 'audience': 'EDP_OIDC_AUDIENCE', 'jwks_url': 'EDP_OIDC_JWKS_URL'},
    'search': {'endpoint': 'EDP_SEARCH_URL', 'token_ref': 'EDP_SEARCH_TOKEN_REF'},
    'embedding': {'endpoint': 'EDP_EMBEDDING_URL', 'token_ref': 'EDP_EMBEDDING_TOKEN_REF', 'profiles': 'EDP_EMBEDDING_PROFILES'},
    'reranking': {'endpoint': 'EDP_RERANK_URL', 'token_ref': 'EDP_RERANK_TOKEN_REF'},
    'export': {'bucket': 'EDP_EXPORT_BUCKET', 'kms_key_id': 'EDP_EXPORT_KMS_KEY_ID', 'retention_seconds': 'EDP_EXPORT_RETENTION_SECONDS'},
    'processing': {'queue_capacity': 'EDP_QUEUE_CAPACITY'},
}


class IniSecretProvider:
    def __init__(self, config, base):
        self.config = config
        root = Path(os.getenv('EDP_SECRET_DIR', '/run/secrets/edp'))
        self.files = MountedSecretProvider(root if root.is_absolute() else base / root)

    def resolve(self, reference):
        if reference == 'ini://database/dsn':
            db = self.config['database']
            value = URL.create('postgresql+psycopg', username=db['user'], password=db['password'],
                host=db['host'], port=db.getint('port', 5432), database=db['name'],
                query={'sslrootcert': db['sslrootcert']} if db.get('sslrootcert') else {}).render_as_string(hide_password=False)
        elif reference.startswith('ini://secrets/'):
            value = self.config.get('secrets', reference[len('ini://secrets/'):], fallback='')
        else:
            return self.files.resolve(reference)
        if not value or len(value) > 65536:
            raise ValueError('configured secret is missing or exceeds size limit')
        return value


def configured_secrets():
    """No implicit cwd discovery: select the private file with EDP_CONFIG_FILE."""
    selected = os.getenv('EDP_CONFIG_FILE')
    if not selected:
        return MountedSecretProvider(os.getenv('EDP_SECRET_DIR', '/run/secrets/edp'))
    path = Path(selected).expanduser().resolve()
    # Disable interpolation so passwords containing % and Windows paths stay literal.
    config = configparser.ConfigParser(interpolation=None)
    try:
        with path.open(encoding='utf-8-sig') as stream:
            config.read_file(stream)
    except (OSError, configparser.Error):
        raise ValueError('Cannot load EDP_CONFIG_FILE; check its path and INI syntax') from None
    if config.defaults():
        raise ValueError('INI DEFAULT inheritance is not supported')
    for section, keys in SETTINGS.items():
        for key, env in keys.items():
            value = config.get(section, key, fallback='')
            if value:
                os.environ.setdefault(env, value)
    if config.has_section('database') and not os.getenv('EDP_CONTROL_DSN_REF'):
        os.environ['EDP_CONTROL_DSN_REF'] = 'ini://database/dsn'
    return IniSecretProvider(config, path.parent)
