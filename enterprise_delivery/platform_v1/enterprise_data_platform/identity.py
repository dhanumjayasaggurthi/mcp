"""Verified OIDC/workload JWT identities and runtime-only secret resolution."""
from pathlib import Path
import os
import re
import ssl
from urllib.parse import urlsplit

from fastapi import HTTPException
import jwt

from .models import Principal


class MountedSecretProvider:
    def __init__(self, root='/run/secrets/edp'):
        self.root = Path(root).resolve()

    def resolve(self, reference):
        if not reference.startswith('file://'):
            raise ValueError('mounted secret provider requires a file reference')
        name = reference[7:]
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', name):
            raise ValueError('invalid mounted secret name')
        path = (self.root / name).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('secret path escapes mounted directory')
        value = path.read_text(encoding='utf-8').strip()
        if not value or len(value) > 65536:
            raise ValueError('secret value is empty or exceeds size limit')
        return value


class AWSSecretProvider:
    def __init__(self, client):
        self.client = client

    def resolve(self, reference):
        if not reference.startswith('aws-sm://'):
            raise ValueError('expected Secrets Manager reference')
        return self.client.get_secret_value(SecretId=reference[9:])['SecretString']


class JWTPrincipalResolver:
    def __init__(self, *, issuer, audience, jwks_url, algorithms=('RS256',), jwks_client=None):
        for url in [issuer, jwks_url]:
            parsed = urlsplit(url)
            if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.fragment:
                raise ValueError('OIDC endpoints must use credential-free HTTPS')
        if not audience or not algorithms or set(algorithms) - {'RS256', 'RS384', 'RS512', 'ES256', 'ES384'}:
            raise ValueError('OIDC audience and asymmetric algorithms must be configured')
        self.issuer, self.audience, self.algorithms = issuer, audience, list(algorithms)
        self.jwks = jwks_client or jwt.PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=300, timeout=5,
            ssl_context=ssl.create_default_context())

    def __call__(self, request):
        authorization = request.headers.get('authorization', '')
        scheme, _, token = authorization.partition(' ')
        if scheme.lower() != 'bearer' or not token or len(token) > 32768:
            raise HTTPException(401, 'valid Bearer authentication is required', headers={'WWW-Authenticate': 'Bearer'})
        try:
            key = self.jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=self.algorithms, audience=self.audience, issuer=self.issuer,
                options={'require': ['sub', 'exp', 'iat', 'iss', 'aud']}, leeway=15)
            client_id = claims.get('client_id') or claims.get('azp')
            if not isinstance(client_id, str) or not client_id:
                raise ValueError('client identity is required')
            groups = claims.get('groups', [])
            if not isinstance(groups, list) or len(groups) > 256 or any(not isinstance(g, str) for g in groups):
                raise ValueError('invalid group claim')
            return Principal(subject=claims['sub'], client_id=client_id, tenant=claims.get('tenant') or claims.get('tid'),
                groups=set(groups), agent_id=claims.get('agent_id'), attributes={'identity_expires': str(claims['exp']),
                    'oauth_scope': str(claims.get('scope', ''))})
        except (jwt.PyJWTError, ValueError, TypeError, KeyError) as exc:
            raise HTTPException(401, 'Bearer token is invalid', headers={'WWW-Authenticate': 'Bearer'}) from exc
