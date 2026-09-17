import pytest
from sqlalchemy import create_engine, inspect

from enterprise_data_platform.durable import _control_schema, migrate
from enterprise_data_platform.configuration import ControlDatabaseConfig


CONTROL_ENV = {
    'EDP_CONTROL_SCHEMA': 'tenant_control', 'EDP_CONTROL_POOL_SIZE': '17',
    'EDP_CONTROL_MAX_OVERFLOW': '3', 'EDP_CONTROL_CONNECT_TIMEOUT_SECONDS': '11',
    'EDP_CONTROL_POOL_TIMEOUT_SECONDS': '13', 'EDP_CONTROL_POOL_RECYCLE_SECONDS': '601',
    'EDP_CONTROL_POOL_PRE_PING': 'false',
    'EDP_CONTROL_STATEMENT_TIMEOUT_MS': '7001', 'EDP_CONTROL_LOCK_TIMEOUT_MS': '3001',
    'EDP_CONTROL_IDLE_TRANSACTION_TIMEOUT_MS': '41001', 'EDP_CONTROL_PARTITION_COUNT': '19',
    'EDP_CONTROL_MIGRATION_LOCK_ID': '123456', 'EDP_CONTROL_SSL_MODE': 'verify-ca',
}


def test_control_schema_is_required_and_rejects_unsafe_identifiers(monkeypatch):
    monkeypatch.delenv('EDP_CONTROL_SCHEMA', raising=False)
    with pytest.raises(ValueError, match='required'):
        _control_schema()
    for value in ['control-hub', 'public;DROP SCHEMA public', '1control', 'x' * 64]:
        monkeypatch.setenv('EDP_CONTROL_SCHEMA', value)
        with pytest.raises(ValueError, match='valid unquoted PostgreSQL identifier'):
            _control_schema()


def test_contract_database_migration_translates_namespace_for_sqlite():
    engine = create_engine('sqlite:///:memory:')
    migrate(engine)
    try:
        tables = set(inspect(engine).get_table_names())
        assert {'edp_schema_versions', 'edp_objects', 'edp_audit', 'edp_jobs',
                'edp_chunks', 'edp_record_versions'} <= tables
        assert engine.get_execution_options()['schema_translate_map']['edp_control_namespace'] is None
    finally:
        engine.dispose()


def test_every_operational_database_value_comes_from_configuration(monkeypatch):
    for name, value in CONTROL_ENV.items():
        monkeypatch.setenv(name, value)
    configured = ControlDatabaseConfig.from_environment()
    assert configured.schema == 'tenant_control'
    assert configured.pool_size == 17
    assert configured.max_overflow == 3
    assert configured.connect_timeout_seconds == 11
    assert configured.pool_timeout_seconds == 13
    assert configured.pool_recycle_seconds == 601
    assert configured.pool_pre_ping is False
    assert configured.statement_timeout_ms == 7001
    assert configured.lock_timeout_ms == 3001
    assert configured.idle_transaction_timeout_ms == 41001
    assert configured.partition_count == 19
    assert configured.migration_lock_id == 123456
    assert configured.ssl_mode == 'verify-ca'


def test_database_configuration_has_no_implicit_operational_defaults(monkeypatch):
    for name in CONTROL_ENV:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match='EDP_CONTROL_SCHEMA is required'):
        ControlDatabaseConfig.from_environment()
