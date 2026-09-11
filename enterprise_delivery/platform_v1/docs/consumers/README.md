# SmartHub MCP & Agentic Gateway consumer API documentation

This package separates the integration contract by consumer role. All IDs, text, scores and tokens in the examples are synthetic.

| Consumer | Use this guide | Primary APIs |
|---|---|---|
| RegAssist orchestration | [RegAssist integration](REGASSIST_INTEGRATION.md) | Exact document lookup, Q/HAQ mapping lookup, keyword and vector routing |
| Quill and other RAG services | [RAG and Quill integration](RAG_QUILL_INTEGRATION.md) | Separate sparse/dense retrieval, downstream fusion, RAG-ready retrieval |
| Reporting and application services | [Structured data integration](STRUCTURED_DATA_INTEGRATION.md) | Typed queries, lookup, aggregation and exports |
| Every consumer | [Common API contract](COMMON_API_CONTRACT.md) | Identity, discovery, filters, errors, retries and versioning |

The RDH onboarding and source-owner process is documented separately in [RDH onboarding](../../RDH_ONBOARDING.md). Consumer teams do not need source credentials or control-plane administrator access.

Before handoff, RDH supplies the environment base URL, OAuth issuer/audience, registered client ID, dataset IDs, granted scopes, field and filter contracts, operational contact, approved timeout/retry policy, and environment SLO. These values are environment configuration and are not embedded in this repository.
