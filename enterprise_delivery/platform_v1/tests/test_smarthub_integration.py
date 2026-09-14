import json
import time
from types import SimpleNamespace
import configparser
import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from fastapi import HTTPException
from enterprise_data_platform.identity import JWTPrincipalResolver, control_admin_authorizer
from enterprise_data_platform.configuration import IniSecretProvider, configured_secrets
from enterprise_data_platform.azure_embedding import AzureEmbeddingTransport


def test_entra_validates_api_audience_scope_and_client(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    resolver = JWTPrincipalResolver(issuer='https://issuer.example/tenant/v2.0', audience='hub-api',
        jwks_url='https://issuer.example/keys', required_scope='access_as_user', allowed_client_ids=['portal'],
        jwks_client=SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=key.public_key())))
    claims = dict(sub='user', exp=int(time.time())+300, iat=int(time.time()), iss=resolver.issuer,
        aud='hub-api', azp='portal', tid='tenant', scp='access_as_user', roles=['ControlHub.Admin'])
    def authenticate(c):
        return resolver(SimpleNamespace(headers={'authorization':'Bearer '+jwt.encode(c,key,algorithm='RS256')}))
    p = authenticate(claims)
    assert p.tenant == 'tenant'
    monkeypatch.setenv('EDP_ADMIN_SCOPE','access_as_user')
    monkeypatch.setenv('EDP_ADMIN_GROUP_IDS','')
    monkeypatch.setenv('EDP_ADMIN_APP_ROLES','ControlHub.Admin')
    assert control_admin_authorizer()(p)
    for change in [{'aud':'https://graph.microsoft.com'}, {'scp':''}, {'azp':'other'}, {'exp':int(time.time())-60}]:
        with pytest.raises(HTTPException):authenticate({**claims,**change})
    assert not control_admin_authorizer()(authenticate({**claims,'roles':[]}))


def test_snowflake_keypair_and_relative_path(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    (tmp_path/'key.p8').write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,serialization.BestAvailableEncryption(b'p%word')))
    config=configparser.ConfigParser(interpolation=None)
    config['snowflake']={'private_key_path':'key.p8','private_key_password':'p%word'}
    secrets=IniSecretProvider(config,tmp_path)
    decoded=serialization.load_der_private_key(secrets.connection_args('ini://snowflake/dsn')['private_key'],None)
    assert decoded.public_key().public_numbers()==key.public_key().public_numbers()
    config['snowflake']['private_key_password']='wrong'
    with pytest.raises(ValueError,match='Cannot load Snowflake'):secrets.connection_args('ini://snowflake/dsn')


def test_uppercase_postgres_does_not_become_control_database(tmp_path,monkeypatch):
    p=tmp_path/'config.ini'
    p.write_text('[POSTGRES]\nhost=localhost\nuser=reader\npassword=p%word\ndatabase=regulatory\n')
    monkeypatch.setenv('EDP_CONFIG_FILE',str(p));monkeypatch.delenv('EDP_CONTROL_DSN_REF',raising=False)
    secrets=configured_secrets()
    assert 'regulatory' in secrets.resolve('ini://postgres/dsn')
    import os
    assert 'EDP_CONTROL_DSN_REF' not in os.environ


def test_azure_gateway_path_key_and_no_dimensions_parameter():
    captured=[]
    def send(request):
        captured.append(request)
        return httpx.Response(200,json={'data':[{'index':0,'embedding':[0.1,0.2,0.3]}]})
    client=httpx.Client(base_url='https://gateway.example/openai-embeddings/',transport=httpx.MockTransport(send))
    transport=AzureEmbeddingTransport('https://gateway.example/openai-embeddings','test-key','2022-12-01','deployment',3,client=client)
    result=transport.request('POST','/embeddings',body={'model':'deployment','input':['hello'],'dimensions':3})
    assert result['data'][0]['index']==0
    request=captured[0]
    assert request.url.path=='/openai-embeddings/openai/deployments/deployment/embeddings'
    assert request.url.params['api-version']=='2022-12-01'
    assert request.headers['api-key']=='test-key'
    assert 'authorization' not in request.headers
    assert json.loads(request.content)=={'input':['hello']}
    with pytest.raises(ValueError):transport.request('POST','/embeddings',body={'model':'deployment','input':['hello'],'dimensions':4})
    transport.close()


def test_snowflake_url_contains_no_key_material(tmp_path):
    pytest.importorskip('snowflake.sqlalchemy')
    from sqlalchemy.engine import make_url
    config=configparser.ConfigParser(interpolation=None)
    config['snowflake']={'account':'account-test','user':'reader','database':'DB','schema':'SCHEMA','warehouse':'WH','role':'READ_ROLE'}
    value=IniSecretProvider(config,tmp_path).resolve('ini://snowflake/dsn')
    assert make_url(value).get_backend_name()=='snowflake'
    assert 'private_key' not in value
