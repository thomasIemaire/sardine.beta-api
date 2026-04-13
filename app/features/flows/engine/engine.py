"""
FlowEngine — orchestrateur principal qui parcourt le graphe des nœuds.
Porté depuis sardine.api, adapté à Beanie + ws_manager.
"""

import asyncio
import json
import re
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from beanie import PydanticObjectId

from .context import ExecutionContext, NodeResult
from .nodes import NODE_REGISTRY


MAX_DATA_SIZE = 50_000  # 50 Ko
MAX_STEPS = 500


# ─── Sanitization de données pour persistance ────────────────────


def _looks_like_base64(s: str) -> bool:
    if s.startswith("data:"):
        return True
    clean = s[:100].replace("\n", "").replace("\r", "")
    return bool(re.match(r"^[A-Za-z0-9+/=]{100}", clean))


def _strip_binary(obj: Any) -> None:
    """Remplace récursivement les strings base64/binaires par des placeholders."""
    if isinstance(obj, dict):
        for key, val in obj.items():
            if isinstance(val, str) and len(val) > 1000 and _looks_like_base64(val):
                obj[key] = f"<base64, {len(val)} chars>"
            elif isinstance(val, (dict, list)):
                _strip_binary(val)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            if isinstance(item, str) and len(item) > 1000 and _looks_like_base64(item):
                obj[i] = f"<base64, {len(item)} chars>"
            elif isinstance(item, (dict, list)):
                _strip_binary(item)


def _sanitize_data(data: Any) -> dict | None:
    """Sanitize les données pour stockage : strip base64, troncature."""
    if not data or not isinstance(data, dict):
        return data
    sanitized = deepcopy(data)
    _strip_binary(sanitized)
    try:
        serialized = json.dumps(sanitized, default=str)
    except Exception:
        return {"_error": "non-serializable data"}
    if len(serialized) > MAX_DATA_SIZE:
        sanitized["_truncated"] = True
        for key in list(sanitized.keys()):
            if key.startswith("_"):
                continue
            val = sanitized[key]
            try:
                val_size = len(json.dumps(val, default=str))
            except Exception:
                sanitized[key] = "<non-serializable>"
                continue
            if isinstance(val, dict) and val_size > 5000:
                sanitized[key] = {"_summary": f"<object, {len(val)} keys>"}
            elif isinstance(val, list) and len(val) > 10:
                sanitized[key] = val[:10] + [{"_truncated": True, "_total": len(val)}]
    return sanitized


def _node_name(node: dict) -> str:
    """Retourne le label/name d'un noeud (supporte les 2 conventions)."""
    return node.get("label") or node.get("name") or node["id"]


def _deep_merge(base: dict, overlay: dict) -> None:
    """Deep merge overlay → base.
    - dict + dict : récursion
    - list + list : concaténation (pour agentResults multi-branches)
    - sinon : overlay gagne
    """
    for key, val in overlay.items():
        if key in base and isinstance(base[key], dict) and isinstance(val, dict):
            _deep_merge(base[key], val)
        elif key in base and isinstance(base[key], list) and isinstance(val, list):
            base[key] = base[key] + val
        else:
            base[key] = val


# ─── Engine ──────────────────────────────────────────────────────


class FlowEngine:
    def __init__(self) -> None:
        self.node_map: dict = {}
        self.adjacency: dict = {}
        self.children: dict = {}
        self._triggered_by: str | None = None
        self._recipients: list[str] = []  # tous les membres de l'org à notifier

    # ── Construction du graphe ──────────────────────────────────

    def _build_graph(self, nodes: list, links: list) -> None:
        self.node_map = {n["id"]: n for n in nodes}
        self.adjacency = {}
        self.children = {}

        for link in links:
            src_id, src_port, src_parent = self._parse_endpoint(link, "src", "source")
            dst_id, _dst_port, _dst_parent = self._parse_endpoint(link, "dst", "target")

            if src_id and dst_id:
                self.adjacency.setdefault((src_id, src_port), []).append(dst_id)

            parent_id = link.get("parentId") or src_parent
            if parent_id:
                self.children.setdefault(parent_id, [])
                if dst_id and dst_id not in self.children[parent_id]:
                    self.children[parent_id].append(dst_id)

        for n in nodes:
            parent = n.get("parentId")
            if parent:
                self.children.setdefault(parent, [])
                if n["id"] not in self.children[parent]:
                    self.children[parent].append(n["id"])

    @staticmethod
    def _parse_endpoint(
        link: dict, key: str, alt_key: str,
    ) -> tuple[str | None, int, str | None]:
        """
        Extrait (node_id, port_index, parent_id) d'une extrémité de lien.
        Supporte plusieurs formats :
          { "src": {"nodeId": "...", "portIndex": 0} }            # objet
          { "source": "node_id" }                                  # string
          { "source": "node_id", "sourceHandle": "0" }             # avec port
          { "from": "...", "to": "..." }                           # alias
        """
        endpoint = link.get(key) or link.get(alt_key) or link.get("from" if key == "src" else "to")
        if isinstance(endpoint, dict):
            return (
                endpoint.get("nodeId") or endpoint.get("id"),
                int(endpoint.get("portIndex", 0)),
                endpoint.get("parentId"),
            )
        if isinstance(endpoint, str):
            handle_key = "sourceHandle" if key == "src" else "targetHandle"
            handle = link.get(handle_key)
            try:
                port = int(handle) if handle is not None else 0
            except (ValueError, TypeError):
                port = 0
            return endpoint, port, None
        return None, 0, None

    def _find_start_node(self) -> dict | None:
        for node in self.node_map.values():
            if node.get("type") == "start" and not node.get("parentId"):
                return node
        return None

    def _get_next_nodes(self, node_id: str, port: int) -> list[str]:
        targets = self.adjacency.get((node_id, port), [])
        if not targets and port != 0:
            targets = self.adjacency.get((node_id, 0), [])
        return targets

    def _get_all_outgoing(self, node_id: str) -> list[str]:
        result = []
        for (src, _port), dsts in self.adjacency.items():
            if src == node_id:
                result.extend(dsts)
        return result

    def _find_join_node(self, fork_targets: list[str]) -> str | None:
        """BFS depuis chaque branche pour trouver le 1er nœud de convergence."""
        n = len(fork_targets)
        fork_set = set(fork_targets)
        reached_by: dict[str, set[int]] = {}
        queues: list[set[str]] = [{t} for t in fork_targets]
        visited: list[set[str]] = [set() for _ in range(n)]

        for _ in range(200):
            next_queues: list[set[str]] = [set() for _ in range(n)]
            for i in range(n):
                for nid in queues[i]:
                    if nid in visited[i]:
                        continue
                    visited[i].add(nid)
                    if nid not in fork_set:
                        reached_by.setdefault(nid, set()).add(i)
                        if len(reached_by[nid]) == n:
                            return nid
                    for dst in self._get_all_outgoing(nid):
                        next_queues[i].add(dst)
            queues = next_queues
            if not any(queues):
                break
        return None

    # ── Évènements ──────────────────────────────────────────────

    async def _emit(self, event_type: str, data: dict, triggered_by: str | None) -> None:
        """
        Broadcast un évènement WS à tous les membres de l'organisation.
        triggered_by est gardé en signature pour rétrocompat mais ignoré :
        l'engine utilise self._recipients (pré-chargé au démarrage de run).
        """
        if not self._recipients:
            return
        try:
            from app.features.notifications.ws_manager import ws_manager
            await ws_manager.send_to_users(
                self._recipients,
                {"event": event_type, "data": data},
            )
        except Exception:
            pass

    async def _emit_loop_event(
        self, execution_id: str | None, org_id: str | None,
        node_id: str, iteration: int, total: int, status: str,
    ) -> None:
        triggered_by = self._triggered_by
        await self._emit("execution.loop.iteration", {
            "execution_id": execution_id,
            "org_id": org_id,
            "node_id": node_id,
            "iteration": iteration,
            "total": total,
            "status": status,
        }, triggered_by)

    # ── Helpers nœud log ────────────────────────────────────────

    async def _create_node_log(
        self, execution_id: str, node: dict, input_snapshot: Any,
        parent_node_id: str | None = None,
        loop_iteration: int | None = None,
        loop_total: int | None = None,
    ):
        from app.features.flows.models import ExecutionNodeLog

        log = ExecutionNodeLog(
            execution_id=PydanticObjectId(execution_id),
            node_id=node["id"],
            node_type=node.get("type", ""),
            node_name=_node_name(node),
            status="running",
            input_data=input_snapshot,
            started_at=datetime.now(UTC),
            parent_node_id=parent_node_id,
            loop_iteration=loop_iteration,
            loop_total=loop_total,
        )
        await log.insert()
        return log

    async def _finish_node_log(
        self, log, status: str, result: NodeResult | None = None,
        error: str | None = None, output_snapshot: Any = None,
    ) -> None:
        completed_at = datetime.now(UTC)
        duration_ms = int((completed_at - log.started_at.replace(tzinfo=UTC)).total_seconds() * 1000)
        update: dict = {"status": status, "completed_at": completed_at, "duration_ms": duration_ms}
        if status == "completed" and result:
            update["output_port"] = result.output_port
            update["output_data"] = output_snapshot
            update["metadata"] = result.metadata
        elif status == "failed":
            update["error"] = error
            update["output_data"] = None
        await log.set(update)

    # ── Exécution principale ────────────────────────────────────

    async def run(
        self, flow_doc: dict, execution_id: str, org_id: str,
        input_data: dict | None, triggered_by: str | None,
        depth: int = 0,
    ) -> None:
        from app.core.membership import get_org_member_user_ids
        from app.features.flows.engine import unregister_execution
        from app.features.flows.models import FlowExecution

        self._triggered_by = triggered_by
        self._recipients = await get_org_member_user_ids(org_id)

        flow_data = flow_doc.get("flow_data", {})
        nodes = flow_data.get("nodes", [])
        # Support des deux conventions : "links" ou "edges"
        links = flow_data.get("links") or flow_data.get("edges") or []

        self._build_graph(nodes, links)

        start_node = self._find_start_node()
        if not start_node:
            await self._fail_execution(execution_id, "Aucun nœud 'start' trouvé")
            return

        context = ExecutionContext(
            data=input_data or {},
            variables={},
            metadata={
                "flow_id": str(flow_doc["_id"]),
                "execution_id": execution_id,
                "org_id": org_id,
                "triggered_by": triggered_by,
                "started_at": datetime.now(UTC).isoformat(),
                "depth": depth,
            },
        )

        # Marquer en cours
        exec_oid = PydanticObjectId(execution_id)
        execution = await FlowExecution.get(exec_oid)
        if execution:
            await execution.set({"status": "running"})

        await self._emit("execution.started", {
            "execution_id": execution_id,
            "flow_id": str(flow_doc["_id"]),
            "started_at": datetime.now(UTC).isoformat(),
        }, triggered_by)

        try:
            await self._main_loop(start_node, context, execution_id, org_id, triggered_by)
        except asyncio.CancelledError:
            return
        finally:
            unregister_execution(execution_id)

    async def _main_loop(
        self, start_node: dict, context: ExecutionContext,
        execution_id: str, org_id: str, triggered_by: str | None,
    ) -> None:
        current_node = start_node
        step = 0

        while current_node and step < MAX_STEPS:
            step += 1
            node_type = current_node.get("type", "")
            node_name = _node_name(current_node)
            handler = NODE_REGISTRY.get(node_type)

            if not handler:
                await self._fail_execution(execution_id, f"Type de nœud inconnu: '{node_type}'")
                return

            input_snapshot = _sanitize_data(context.data)
            log = await self._create_node_log(execution_id, current_node, input_snapshot)

            await self._emit("execution.node.started", {
                "execution_id": execution_id,
                "node_id": current_node["id"],
                "node_type": node_type,
                "node_name": node_name,
            }, triggered_by)

            try:
                result = await handler(current_node, context, self)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                error_msg = f"Erreur dans le nœud '{node_name}': {e}"
                await self._finish_node_log(log, "failed", error=error_msg)
                await self._emit("execution.node.failed", {
                    "execution_id": execution_id, "node_id": current_node["id"],
                    "node_type": node_type, "error": error_msg,
                }, triggered_by)
                await self._fail_execution(execution_id, error_msg)
                return

            if result.error:
                await self._finish_node_log(log, "failed", error=result.error)
                await self._emit("execution.node.failed", {
                    "execution_id": execution_id, "node_id": current_node["id"],
                    "node_type": node_type, "error": result.error,
                }, triggered_by)
                await self._fail_execution(execution_id, result.error)
                return

            if result.pause:
                await self._handle_pause(log, current_node, context, result, execution_id, triggered_by)
                return

            output_snapshot = _sanitize_data(context.data)
            await self._finish_node_log(log, "completed", result=result, output_snapshot=output_snapshot)

            await self._emit("execution.node.completed", {
                "execution_id": execution_id, "node_id": current_node["id"],
                "node_type": node_type, "node_name": node_name,
                "output_port": result.output_port,
                "metadata": result.metadata,
            }, triggered_by)

            if node_type == "end":
                await self._complete_execution(execution_id, context)
                return

            next_nodes = self._get_next_nodes(current_node["id"], result.output_port)
            if not next_nodes:
                await self._complete_execution(execution_id, context)
                return

            if len(next_nodes) > 1:
                join_node_id = await self._run_parallel_branches(
                    next_nodes, context, execution_id, org_id, triggered_by,
                )
                if not join_node_id:
                    await self._complete_execution(execution_id, context)
                    return
                current_node = self.node_map.get(join_node_id)
                if not current_node:
                    await self._fail_execution(execution_id, f"Nœud de convergence '{join_node_id}' introuvable")
                    return
            else:
                current_node = self.node_map.get(next_nodes[0])
                if not current_node:
                    await self._fail_execution(execution_id, f"Nœud suivant '{next_nodes[0]}' introuvable")
                    return

        if step >= MAX_STEPS:
            await self._fail_execution(execution_id, f"Limite de {MAX_STEPS} étapes atteinte")

    async def _handle_pause(
        self, log, current_node: dict, context: ExecutionContext,
        result: NodeResult, execution_id: str, triggered_by: str | None,
    ) -> None:
        from app.features.flows.models import FlowExecution

        await log.set({"status": "waiting", "metadata": result.metadata or None})

        await self._emit("execution.node.waiting", {
            "execution_id": execution_id,
            "node_id": current_node["id"],
            "reason": "approval",
        }, triggered_by)

        execution = await FlowExecution.get(PydanticObjectId(execution_id))
        if execution:
            await execution.set({
                "status": "waiting",
                "paused_at_node": current_node["id"],
                "paused_node_log_id": str(log.id),
                "context_snapshot": {
                    "data": context.data,
                    "variables": context.variables,
                    "metadata": context.metadata,
                },
            })

    # ── Branches parallèles ─────────────────────────────────────

    async def _run_parallel_branches(
        self, targets: list[str], context: ExecutionContext,
        execution_id: str, org_id: str, triggered_by: str | None,
    ) -> str | None:
        join_node_id = self._find_join_node(targets)
        stop_nodes = {join_node_id} if join_node_id else set()

        async def _run_branch(start_node_id: str) -> ExecutionContext:
            branch_ctx = context.clone()
            current = self.node_map.get(start_node_id)

            for _ in range(200):
                if not current:
                    break
                if current["id"] in stop_nodes or current.get("type") == "end":
                    break

                node_type = current.get("type", "")
                handler = NODE_REGISTRY.get(node_type)
                if not handler:
                    raise RuntimeError(f"Type de nœud inconnu: '{node_type}'")

                input_snapshot = _sanitize_data(branch_ctx.data)
                log = await self._create_node_log(execution_id, current, input_snapshot)

                try:
                    result = await handler(current, branch_ctx, self)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    error_msg = f"Erreur dans le nœud '{_node_name(current)}': {e}"
                    await self._finish_node_log(log, "failed", error=error_msg)
                    raise RuntimeError(error_msg)

                if result.error:
                    await self._finish_node_log(log, "failed", error=result.error)
                    raise RuntimeError(result.error)

                if result.pause:
                    raise RuntimeError("Approval non supporté dans une branche parallèle")

                output_snapshot = _sanitize_data(branch_ctx.data)
                await self._finish_node_log(log, "completed", result=result, output_snapshot=output_snapshot)

                next_nodes = self._get_next_nodes(current["id"], result.output_port)
                if not next_nodes:
                    break
                current = self.node_map.get(next_nodes[0])

            return branch_ctx

        results = await asyncio.gather(*[_run_branch(t) for t in targets], return_exceptions=True)

        errors = [r for r in results if isinstance(r, Exception)]
        if errors:
            raise errors[0]

        for branch_ctx in results:
            _deep_merge(context.data, branch_ctx.data)

        return join_node_id

    # ── Reprise après pause ─────────────────────────────────────

    async def resume(self, execution_id: str, response_value: str) -> None:
        from app.features.flows.engine import unregister_execution
        from app.features.flows.models import (
            ApprovalTask,
            ExecutionNodeLog,
            Flow,
            FlowExecution,
            FlowVersion,
        )

        exec_oid = PydanticObjectId(execution_id)
        execution = await FlowExecution.get(exec_oid)
        if not execution:
            raise ValueError("Exécution introuvable")
        if execution.status != "waiting":
            raise ValueError("L'exécution n'est pas en attente")

        paused_node_id = execution.paused_at_node
        paused_node_log_id = execution.paused_node_log_id
        snapshot = execution.context_snapshot or {}
        org_id = str(execution.organization_id)
        flow_id = str(execution.flow_id)
        triggered_by = str(execution.triggered_by) if execution.triggered_by else None
        self._triggered_by = triggered_by

        from app.core.membership import get_org_member_user_ids
        self._recipients = await get_org_member_user_ids(org_id)

        # Recharger le flow et sa version active
        flow = await Flow.get(PydanticObjectId(flow_id))
        if not flow or not flow.active_version_id:
            await self._fail_execution(execution_id, "Flow introuvable")
            return
        version = await FlowVersion.get(flow.active_version_id)
        if not version:
            await self._fail_execution(execution_id, "Version active du flow introuvable")
            return

        self._build_graph(
            version.flow_data.get("nodes", []),
            version.flow_data.get("links") or version.flow_data.get("edges") or [],
        )

        # Restaurer le contexte
        context = ExecutionContext(
            data=snapshot.get("data", {}),
            variables=snapshot.get("variables", {}),
            metadata=snapshot.get("metadata", {}),
        )
        context.data["approvalResponse"] = response_value

        # Trouver l'output_port via l'approval task
        approval = await ApprovalTask.find_one(
            ApprovalTask.execution_id == exec_oid,
            ApprovalTask.node_id == paused_node_id,
        )

        output_port = 0
        option_label = response_value
        if approval:
            for i, opt in enumerate(approval.options):
                if opt.get("value") == response_value:
                    output_port = i
                    option_label = opt.get("label", response_value)
                    break

        # Compléter le node log mis en attente
        if paused_node_log_id:
            log = await ExecutionNodeLog.get(PydanticObjectId(paused_node_log_id))
            if log:
                await log.set({
                    "status": "completed",
                    "output_port": output_port,
                    "output_data": _sanitize_data(context.data),
                    "completed_at": datetime.now(UTC),
                    "metadata": {
                        "responded_at": datetime.now(UTC).isoformat(),
                        "option_value": response_value,
                        "option_label": option_label,
                    },
                })

        # Marquer l'exécution running à nouveau
        await execution.set({
            "status": "running",
            "paused_at_node": None,
            "paused_node_log_id": None,
            "context_snapshot": None,
        })

        # Trouver le prochain nœud après l'approval
        next_nodes = self._get_next_nodes(paused_node_id, output_port)
        if not next_nodes:
            await self._complete_execution(execution_id, context)
            return

        try:
            if len(next_nodes) > 1:
                join_node_id = await self._run_parallel_branches(
                    next_nodes, context, execution_id, org_id, triggered_by,
                )
                if not join_node_id:
                    await self._complete_execution(execution_id, context)
                    return
                current_node = self.node_map.get(join_node_id)
            else:
                current_node = self.node_map.get(next_nodes[0])

            if not current_node:
                await self._fail_execution(execution_id, "Nœud suivant introuvable")
                return

            await self._main_loop(current_node, context, execution_id, org_id, triggered_by)
        except asyncio.CancelledError:
            return
        finally:
            unregister_execution(execution_id)

    # ── Exécution des enfants (boucles) ─────────────────────────

    async def _run_children(self, parent_node_id: str, context: ExecutionContext) -> None:
        execution_id = context.metadata.get("execution_id")
        loop_iteration = context.variables.get("index")
        loop_total = context.variables.get("length")

        child_ids = self.children.get(parent_node_id, [])
        if not child_ids:
            return

        # Trouver le start parmi les enfants
        start_node = None
        for cid in child_ids:
            node = self.node_map.get(cid)
            if node and node.get("type") == "start":
                start_node = node
                break
        if not start_node:
            start_node = self.node_map.get(child_ids[0])
            if not start_node:
                return

        current_node = start_node
        max_steps = 200
        step = 0

        while current_node and step < max_steps:
            step += 1
            node_type = current_node.get("type", "")
            if node_type == "end":
                break

            handler = NODE_REGISTRY.get(node_type)
            if not handler:
                break

            input_snapshot = _sanitize_data(context.data)
            log = await self._create_node_log(
                execution_id, current_node, input_snapshot,
                parent_node_id=parent_node_id,
                loop_iteration=loop_iteration,
                loop_total=loop_total,
            )

            try:
                result = await handler(current_node, context, self)
            except Exception as e:
                error_msg = f"Erreur dans le nœud '{_node_name(current_node)}': {e}"
                await self._finish_node_log(log, "failed", error=error_msg)
                raise

            if result.error:
                await self._finish_node_log(log, "failed", error=result.error)
                raise RuntimeError(result.error)

            if result.pause:
                # Approval dans une boucle : on met en pause toute l'exécution
                await self._handle_pause(log, current_node, context, result, execution_id, self._triggered_by)
                return

            output_snapshot = _sanitize_data(context.data)
            await self._finish_node_log(log, "completed", result=result, output_snapshot=output_snapshot)

            next_list = self._get_next_nodes(current_node["id"], result.output_port)
            next_id = next_list[0] if next_list else None
            if not next_id or next_id == parent_node_id:
                break
            current_node = self.node_map.get(next_id)

    # ── Helpers terminaux ───────────────────────────────────────

    async def _fail_execution(self, execution_id: str, error: str) -> None:
        from app.features.flows.engine import unregister_execution
        from app.features.flows.engine.nodes.subflow import _notify_child_completion
        from app.features.flows.models import FlowExecution

        unregister_execution(execution_id)

        execution = await FlowExecution.get(PydanticObjectId(execution_id))
        if execution:
            await execution.set({
                "status": "failed",
                "error": error,
                "completed_at": datetime.now(UTC),
            })

        await self._emit("execution.failed", {
            "execution_id": execution_id, "error": error,
        }, self._triggered_by)

        # Réveiller le parent si c'est un sous-flow
        _notify_child_completion(execution_id)

    async def _complete_execution(self, execution_id: str, context: ExecutionContext) -> None:
        from app.features.flows.engine import unregister_execution
        from app.features.flows.engine.nodes.subflow import _notify_child_completion
        from app.features.flows.models import FlowExecution

        unregister_execution(execution_id)

        execution = await FlowExecution.get(PydanticObjectId(execution_id))
        if execution:
            await execution.set({
                "status": "completed",
                "completed_at": datetime.now(UTC),
                "execution_data": context.data,
            })

        await self._emit("execution.completed", {
            "execution_id": execution_id,
        }, self._triggered_by)

        _notify_child_completion(execution_id)
