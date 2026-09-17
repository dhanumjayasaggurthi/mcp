# SmartHub SSO and source setup

This integration was checked against the supplied SmartHub DEV archive
`e0bf632a202`: MSAL initialization, token acquisition, local logout, and Snowflake
PEM-to-DER RSA key loading. Control Hub remains a separately deployed application.
The archive has not been copied into the repository.

## 1. Entra configuration (identity administrator)

Use the existing approved SmartHub tenant. Expose a delegated scope on the Control
Hub API registration (example `api://<API-CLIENT-ID>/access_as_user`) and grant it
to the frontend registration. Configure API access-token version 2. Register the
exact frontend origin as its SPA redirect URI: `http://localhost:5173` for Vite,
and the real HTTPS origin for Linux deployment. This MSAL flow returns to the
origin, not `/auth/callback`. Preserve `/logout` as an unprotected signed-out page.

Configure a dedicated API app role such as `ControlHub.Admin` and assign approved
users/groups, or use approved group object IDs in token claims. Set the backend
admin scope and role/group mapping explicitly. This does not import the portal's
SQLite role overrides; that requires a defined server-to-server RBAC contract.
Graph access tokens are not accepted as Control Hub API credentials.

Frontend environment (public identifiers only):

```dotenv
VITE_MSAL_CLIENT_ID=<FRONTEND-CLIENT-ID>
VITE_MSAL_AUTHORITY=https://login.microsoftonline.com/<TENANT-ID>
VITE_CONTROL_HUB_API_SCOPES=api://<API-CLIENT-ID>/access_as_user
```

Backend INI:

```ini
[oidc]
issuer = https://login.microsoftonline.com/<TENANT-ID>/v2.0
audience = <API-CLIENT-ID>
jwks_url = https://login.microsoftonline.com/<TENANT-ID>/discovery/v2.0/keys
required_scope = access_as_user
allowed_client_ids = <FRONTEND-CLIENT-ID>

[authorization]
admin_scope = access_as_user
admin_group_ids =
admin_app_roles = ControlHub.Admin
```

The audience must match the actual API registration/token configuration, not the
frontend client ID or Graph audience. MSAL owns its localStorage cache, matching
the existing portal. This replaces the previous blanket in-memory-token claim
for MSAL deployments. OIDC fallback retains its in-memory store. Local logout
clears the configured MSAL application cache and does not terminate Entra's SSO
session. Separate origins do not share browser storage; do not assume logout
synchronization across portal and Control Hub origins. An embedded deployment
can instead supply `window.edpAuth`, acquiring the API scope through its existing
MSAL instance. Multiple cached accounts require explicit user selection.

The `required_scope` setting is for delegated user calls. A separate approved
application-permission policy and signed agent identity mapping are still needed
before enabling app-only MCP clients with this Entra setup. Do not invent an
`agent_id` claim or treat a user token as a machine identity.

## 2. Private config.ini

Copy `config.example.ini`, fill the actual values, and select it with
`EDP_CONFIG_FILE`. Linux and PowerShell commands are in
`PRODUCTION_CONTROL_HUB.md`. Section names now accept uppercase; duplicate names
ignoring case are rejected. `[database]` is the control database. `[POSTGRES]` is
an RDH source and never becomes the migration target implicitly. PostgreSQL uses
TLS verify-full; install the correct CA and use the certificate's hostname.

Register sources through the authenticated Sources screen:

| Source kind | Secret reference | INI section |
| --- | --- | --- |
| postgres | `ini://postgres/dsn` | `[POSTGRES]` |
| snowflake | `ini://snowflake/dsn` | `[SNOWFLAKE]` |

Register the frontend client ID in Consumers with only approved dataset grants.
Grant source service accounts read access to the required objects. Merely
configuring a source does not grant consumer access. Snowflake account names,
PrivateLink DNS/routing and RSA public-key registration must be provisioned by
your platform team. Private key paths can be absolute or relative to config.ini.
Do not place keys in the frontend directory. Use the optional Snowflake lock:

```sh
python -m pip install --require-hashes -r requirements.snowflake.lock
```

## 3. Existing embeddings

Fill `[AZURE_OPENAI_EMBEDDING]`: `api_base`, `api_version`, `deployment`,
`profile_id`, and `api_key` (or `api_key_ref`). Set `enabled=true` only when these
are known. Set `embedding_dim` and `embedding_batch_size` under `[EMBEDDING]`.
Leave the generic embedding endpoint empty. The adapter uses:

`POST <api_base>/openai/deployments/<deployment>/embeddings?api-version=<version>`

It sends the `api-key` header and input array, matching the supplied Azure SDK
usage. It does not send the dimensions parameter to the legacy gateway version;
returned vector dimensions must exactly match the configured dataset profile.
The profile ID and model/deployment must match those used for the stored vectors.
Do not enable retrieval with a different model just because dimensions match.
The adapter retains request deadlines, bounded responses and scoped caching.
Corporate proxy or custom-CA transport configuration needs separate verification;
the existing transport does not automatically trust HTTP proxy environment vars.

Ask the DBA for the installed vector extension version, actual vector dimensions
and full index definitions. The example's 3072 is not evidence of the stored
shape. The current native adapter builds `vector` indexes, not halfvec indexes;
3072-dimensional HNSW may require a separately reviewed halfvec implementation
or another strategy. No index/table migration is performed by loading this INI.
OCR, chunking, S3 source ingestion, chat, GCP and pipeline retry sections from the
legacy ingestion application are not executed by this configuration loader.

## 4. Verification before deployment

Run the API against development services. Check login, token refresh, denied
access for unassigned users, local logout and account selection. Confirm API
audience/issuer rejection. Register the two sources, inspect a table and activate
one reviewed dataset. Verify actual embedding model/dimension and retrieval
results. Then test Linux TLS, proxy, PrivateLink access, key permissions and
service restart. Live Entra, PostgreSQL, Snowflake and gateway calls cannot be
verified from the archive alone.
