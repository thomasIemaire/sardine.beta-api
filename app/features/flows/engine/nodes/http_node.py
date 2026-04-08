"""Nœud http — appel HTTP avec retry et timeout."""

import asyncio

import httpx

from ..context import ExecutionContext, NodeResult
from ..expressions import get_value, resolve_template


async def execute_http(node: dict, context: ExecutionContext, engine) -> NodeResult:
    config = node.get("config", {})
    method = config.get("method", "GET").upper()
    url = resolve_template(config.get("url", ""), context)
    raw_headers = config.get("headers", [])
    body_raw = config.get("body")
    body_type = config.get("bodyType", "none")
    timeout = config.get("timeout", 30000) / 1000  # ms → s
    retries = config.get("retries", 0)
    output_path = config.get("outputPath")

    if not url:
        return NodeResult(error="HTTP: 'url' requis")

    # Templates dans les headers
    headers = {}
    for h in raw_headers:
        key = resolve_template(h.get("key", ""), context)
        value = resolve_template(h.get("value", ""), context)
        if key:
            headers[key] = value

    # Body
    body = None
    if body_raw and body_type != "none":
        body = resolve_template(str(body_raw), context)

    if body_type == "json" and "content-type" not in {k.lower() for k in headers}:
        headers["Content-Type"] = "application/json"
    elif body_type == "form" and "content-type" not in {k.lower() for k in headers}:
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    # Exécution avec retry
    last_error = None
    for attempt in range(retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    content=body if body_type != "none" else None,
                )

            try:
                resp_body = response.json()
            except Exception:
                resp_body = response.text

            result_data = {
                "status": response.status_code,
                "headers": dict(response.headers),
                "body": resp_body,
            }

            if output_path and isinstance(resp_body, dict):
                result_data["body"] = get_value(resp_body, output_path)

            context.data["httpResponse"] = result_data
            return NodeResult(
                output_port=0,
                metadata={"method": method, "url": url, "status_code": response.status_code},
            )

        except Exception as e:
            last_error = str(e)
            if attempt < retries:
                await asyncio.sleep(2 ** attempt)
            continue

    return NodeResult(error=f"HTTP: échec après {retries + 1} tentatives — {last_error}")
