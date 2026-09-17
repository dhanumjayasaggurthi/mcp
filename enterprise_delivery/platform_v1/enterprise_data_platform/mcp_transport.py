"""Authenticated, stateless MCP transport over the governed service."""
from contextvars import ContextVar
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.concurrency import run_in_threadpool
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import Tool, CallToolResult, TextContent
from .mcp_facade import MCPFacade
from .services import AccessDenied
from .control_state import ResourceNotFound

identity = ContextVar('mcp_identity')


def register_mcp(app, runtime):
    facade = MCPFacade(service=runtime.service, catalog=runtime.catalog, control=runtime.control)
    server = Server('SmartHub MCP Agentic Gateway')
    requirements = {'query_dataset': {'query'}, 'search_dataset': {'keyword', 'hybrid'},
                    'retrieve_context': {'retrieve'}, 'describe_dataset': {'discover', 'query', 'keyword', 'vector', 'hybrid', 'retrieve'}}

    @server.list_tools()
    async def list_tools():
        principal = identity.get()
        agent = await run_in_threadpool(runtime.control.agents.get, principal.agent_id)
        caps = {c.value for c in agent.allowed_capabilities}
        return [Tool(**definition) for definition in facade.tool_definitions()
                if caps & requirements[definition['name']]]

    @server.call_tool()
    async def call_tool(name, arguments):
        principal = identity.get()
        try:
            return await run_in_threadpool(facade.invoke, agent_id=principal.agent_id,
                                          principal=principal, tool=name, arguments=arguments)
        except Exception as exc:
            # Never send raw database, credential or provider exception text to agents.
            await run_in_threadpool(runtime.store.audit, 'mcp.failed', name, {'error_type': type(exc).__name__})
            message = 'Operation is not authorized' if isinstance(exc, (AccessDenied, ResourceNotFound)) else 'Tool execution failed; use the request trace for investigation'
            return CallToolResult(isError=True, content=[TextContent(type='text', text=message)])

    manager = StreamableHTTPSessionManager(server, stateless=True, json_response=True,
                                          max_request_body_size=1_000_000)

    class Transport:
        async def __call__(self, scope, receive, send):
            try:
                principal = await app.state.principal_dependency(Request(scope, receive))
                if not principal.agent_id:
                    raise HTTPException(403, 'registered agent identity is required')
                agent = await run_in_threadpool(runtime.control.agents.get, principal.agent_id)
                client = await run_in_threadpool(runtime.control.clients.get, principal.client_id)
                if (not agent.mcp_enabled or agent.status.value != 'active' or client.status.value != 'active'
                        or agent.service_principal != principal.subject):
                    raise HTTPException(403, 'MCP is not enabled for this identity')
            except (HTTPException, ResourceNotFound) as exc:
                status = exc.status_code if isinstance(exc, HTTPException) else 403
                return await JSONResponse({'detail': 'MCP authentication or registration failed'}, status,
                    headers={'WWW-Authenticate': 'Bearer'} if status == 401 else {})(scope, receive, send)
            token = identity.set(principal)
            try:
                await manager.handle_request(scope, receive, send)
            finally:
                identity.reset(token)

    app.router.routes.append(Route('/mcp', Transport(), methods=['GET', 'POST', 'DELETE']))
    app.state.mcp_manager = manager
    return manager
