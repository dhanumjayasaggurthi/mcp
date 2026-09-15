"""Azure gateway adapter matching SmartHub's deployment-based embedding calls."""
from urllib.parse import quote
from .search import OpenSearchTransport, HTTPEmbeddingProvider


class AzureEmbeddingTransport(OpenSearchTransport):
    def __init__(self, endpoint, api_key, api_version, deployment, dimensions, *, client=None):
        if not api_key or not api_version or not deployment or dimensions < 1:
            raise ValueError('Azure embedding credentials, version, deployment and dimensions are required')
        super().__init__(endpoint, client=client)
        self.client.headers['api-key'] = api_key
        self.api_version, self.deployment, self.dimensions = api_version, deployment, dimensions

    def request(self, method, path, *, body=None, **kwargs):
        if method != 'POST' or path != '/embeddings' or body['model'] != self.deployment or body['dimensions'] != self.dimensions:
            raise ValueError('Azure embedding profile does not match the configured deployment')
        # Legacy gateway API versions do not accept a dimensions parameter.
        # The shared provider validates the returned vector dimension exactly.
        payload = {'input': body['input']}
        path = f'/openai/deployments/{quote(self.deployment, safe="")}/embeddings?api-version={quote(self.api_version, safe="")}'
        return super().request(method, path, body=payload, **kwargs)


def configured_azure_embedding(secrets):
    config = getattr(secrets, 'config', None)
    if config is None or not config.has_section('azure_openai_embedding'):
        return None, None
    section = config['azure_openai_embedding']
    if not section.getboolean('enabled', fallback=False):
        return None, None
    dimensions = config.getint('embedding', 'embedding_dim')
    deployment = section['deployment']
    profile = section['profile_id']
    if not profile.strip():
        raise ValueError('Azure embedding profile_id is required')
    token = secrets.resolve(section['api_key_ref']) if section.get('api_key_ref') else section['api_key']
    transport = AzureEmbeddingTransport(section['api_base'], token, section['api_version'], deployment, dimensions)
    batch_size = config.getint('embedding', 'embedding_batch_size', fallback=16)
    if not 1 <= batch_size <= 128:
        transport.close()
        raise ValueError('embedding_batch_size must be between 1 and 128')
    return transport, HTTPEmbeddingProvider(transport, {profile: deployment}, batch_size=batch_size)
