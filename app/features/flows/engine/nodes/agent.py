"""
Nœud agent (container) — extrait des données structurées depuis le texte
OCR du document via le serveur GPU (POST /extract).

Config attendu :
  {
    "agents": [
      { "agentId": "<id_agent>", "agentName": "Vendeur", "version": "v1" }
    ]
  }

Prérequis :
  - context.data["determinationResult"]["pages"][i]["detections"][j]["text"]
    doit exister (le nœud determination doit avoir tourné avant).

Le schéma de chaque agent (depuis Agent.active_version.schema_data) est une
structure imbriquée. Trois types de nœud :

  1. Feuille scalaire — dict avec _key / _type / _description / _requirements :
       { "_key": "SELLER_NAME", "_description": "Nom du vendeur",
         "_requirements": [{"type": "required"}] }

  2. Branche — dict sans marqueur _, on récurse dessus :
       { "seller": { "name": <feuille> } }

  3. Liste d'objets répétés — dict avec _list: true. Tous les enfants non
     préfixés '_' décrivent la forme d'un item :
       { "lines": {
           "_list": true,
           "description": <feuille>,
           "quantity":    <feuille>
         } }

Résultat dans context.data["agentExtractions"] (merge de tous les agents).
"""

import re

from beanie import PydanticObjectId

from ..context import ExecutionContext, NodeResult
from ..expressions import set_value
from . import gpu_client


# ─── Helpers schéma imbriqué ─────────────────────────────────────


def _normalize_requirements(raw) -> list[dict]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict) and r.get("type")]
    if isinstance(raw, str) and raw.strip():
        return [{"type": "required"}]
    return []


def _set_nested(d: dict, path: tuple[str, ...], value) -> None:
    for key in path[:-1]:
        if key not in d or not isinstance(d[key], dict):
            d[key] = {}
        d = d[key]
    d[path[-1]] = value


def _get_nested(d: dict, path: tuple[str, ...]):
    for key in path:
        if not isinstance(d, dict):
            return None
        d = d.get(key)
    return d


def _count_leaves(node) -> tuple[int, int]:
    """Compte (filled, total) récursivement sur dicts, listes et scalaires."""
    if isinstance(node, dict):
        filled = total = 0
        for v in node.values():
            f, t = _count_leaves(v)
            filled += f
            total += t
        return filled, total
    if isinstance(node, list):
        filled = total = 0
        for item in node:
            f, t = _count_leaves(item)
            filled += f
            total += t
        return filled, total
    return (1 if node is not None else 0), 1


def _clean_value(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = (
        value.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    )
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned if cleaned else None


# ─── Validation des requirements ─────────────────────────────────


def _check_requirements(value, reqs: list[dict], field_name: str) -> list[str]:
    """Applique une liste de requirements à une valeur unique."""
    errors = []
    for req in reqs:
        req_type = req.get("type")
        req_value = req.get("value")
        if req_type == "required" and (value is None or value == ""):
            errors.append(f"Le champ '{field_name}' est requis")
        elif req_type == "regex" and req_value and value:
            try:
                if not re.match(str(req_value), str(value)):
                    errors.append(f"Le champ '{field_name}' ne correspond pas au format")
            except re.error:
                pass
        elif req_type == "gte" and req_value is not None and value is not None and value != "":
            try:
                if float(value) < float(req_value):
                    errors.append(f"Le champ '{field_name}' doit être >= {req_value}")
            except (ValueError, TypeError):
                pass
        elif req_type == "lte" and req_value is not None and value is not None and value != "":
            try:
                if float(value) > float(req_value):
                    errors.append(f"Le champ '{field_name}' doit être <= {req_value}")
            except (ValueError, TypeError):
                pass
    return errors


def _collect_validation_errors(
    merged: dict,
    key_to_path: dict[str, tuple[str, ...]],
    key_to_reqs: dict[str, list[dict]],
) -> list[str]:
    """Valide les feuilles scalaires hors listes."""
    errors = []
    for key, reqs in key_to_reqs.items():
        if not reqs:
            continue
        path = key_to_path[key]
        value = _get_nested(merged, path)
        field_name = ".".join(path)
        errors.extend(_check_requirements(value, reqs, field_name))
    return errors


def _collect_list_validation_errors(
    container: dict,
    list_specs: dict[str, dict],
    parent_label: str = "",
) -> list[str]:
    """Valide récursivement les items des listes (et listes imbriquées)."""
    errors = []
    for list_dot_key, spec in list_specs.items():
        items = _get_nested(container, spec["path"])
        if not isinstance(items, list):
            continue
        list_label = f"{parent_label}.{list_dot_key}" if parent_label else list_dot_key
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            item_label = f"{list_label}[{idx}]"
            for item_dot_key, reqs in spec["item_key_to_reqs"].items():
                if not reqs:
                    continue
                item_path = spec["item_key_to_path"][item_dot_key]
                value = _get_nested(item, item_path)
                field_name = f"{item_label}." + ".".join(item_path)
                errors.extend(_check_requirements(value, reqs, field_name))
            nested = spec.get("item_list_specs") or {}
            if nested:
                errors.extend(
                    _collect_list_validation_errors(item, nested, item_label),
                )
    return errors


# ─── Construction du schéma LLM ──────────────────────────────────


def _build_llm_schema(
    mapper: dict,
    out_schema: dict,
    key_to_path: dict,
    key_to_reqs: dict,
    field_descriptions: list,
    list_specs: dict,
    path: tuple[str, ...] = (),
) -> None:
    """
    Parcourt récursivement le schéma de l'agent et construit :
    - out_schema : structure imbriquée à envoyer au LLM (feuilles = null,
      listes = [item_template])
    - key_to_path / key_to_reqs : mappings pour la validation des feuilles scalaires
    - list_specs : dot_key → { path, item_key_to_path, item_key_to_reqs,
      item_list_specs } pour la validation des items des listes
    - field_descriptions : liste 'chemin: description' à passer au LLM
    """
    for name, value in mapper.items():
        if not isinstance(value, dict):
            continue
        current = path + (name,)
        dot_key = ".".join(current)

        # Liste de sous-objets : _list: true + enfants non-_ = forme d'un item
        if value.get("_list") is True:
            item_mapper = {k: v for k, v in value.items() if not k.startswith("_")}
            item_template: dict = {}
            item_key_to_path: dict = {}
            item_key_to_reqs: dict = {}
            item_field_desc: list = []
            item_list_specs: dict = {}
            _build_llm_schema(
                item_mapper, item_template,
                item_key_to_path, item_key_to_reqs,
                item_field_desc, item_list_specs,
            )
            if not item_template:
                continue
            _set_nested(out_schema, current, [item_template])
            list_specs[dot_key] = {
                "path": current,
                "item_key_to_path": item_key_to_path,
                "item_key_to_reqs": item_key_to_reqs,
                "item_list_specs": item_list_specs,
            }
            list_desc = (value.get("_description") or "").strip()
            base_msg = f"- {dot_key}: tableau — produire une entrée par occurrence trouvée"
            if list_desc:
                base_msg += f" ({list_desc})"
            field_descriptions.append(base_msg)
            for d in item_field_desc:
                stripped = d.lstrip()
                if stripped.startswith("- "):
                    stripped = stripped[2:]
                field_descriptions.append(f"  - {dot_key}[].{stripped}")
            continue

        # Feuille scalaire : présence de _key, _description ou _type
        if "_key" in value or "_description" in value or "_type" in value:
            desc = value.get("_description", "")
            reqs = _normalize_requirements(value.get("_requirements"))
            key_to_path[dot_key] = current
            key_to_reqs[dot_key] = reqs
            _set_nested(out_schema, current, None)
            if desc:
                field_descriptions.append(f"- {dot_key}: {desc}")
        else:
            _build_llm_schema(
                value, out_schema, key_to_path, key_to_reqs,
                field_descriptions, list_specs, current,
            )


# ─── Handler ─────────────────────────────────────────────────────


async def execute_agent(
    node: dict, context: ExecutionContext, engine,
) -> NodeResult:
    from app.features.agents.models import Agent, AgentVersion

    config = node.get("config", {})

    # 1. Récupérer la liste des agents configurés
    # Support des 2 formats :
    #   - { "agents": [{ "agentId": "..." }, ...] }
    #   - { "agentId": "...", "agentName": "...", "version": "..." }
    agents_config = config.get("agents")
    if not agents_config:
        single_id = config.get("agentId") or config.get("agent_id")
        if single_id:
            agents_config = [{
                "agentId": single_id,
                "agentName": config.get("agentName", ""),
                "version": config.get("version", ""),
            }]
        else:
            agents_config = []

    agent_ids = [
        a.get("agentId") or a.get("agent_id")
        for a in agents_config
        if a.get("agentId") or a.get("agent_id")
    ]
    if not agent_ids:
        return NodeResult(
            error="Agent: aucun agent configuré dans le nœud",
            metadata={"config": config},
        )

    org_id = context.metadata.get("org_id")
    if not org_id:
        return NodeResult(
            error="Agent: org_id manquant dans le contexte",
            metadata={"config": config, "context_metadata": dict(context.metadata)},
        )

    # 2. Charger les agents et leur schema_data depuis la version active
    agents_data = []
    for aid in agent_ids:
        try:
            agent = await Agent.get(PydanticObjectId(aid))
        except Exception as exc:
            return NodeResult(
                error=f"Agent: id invalide '{aid}' — {type(exc).__name__}: {exc}",
                metadata={"agent_ids": agent_ids, "failing_id": aid},
            )
        if not agent:
            return NodeResult(
                error=f"Agent: agent '{aid}' introuvable",
                metadata={"agent_ids": agent_ids, "failing_id": aid, "org_id": org_id},
            )
        if str(agent.organization_id) != org_id:
            return NodeResult(
                error=(
                    f"Agent: agent '{aid}' n'appartient pas à cette organisation "
                    f"(agent.org={agent.organization_id}, ctx.org={org_id})"
                ),
                metadata={
                    "agent_ids": agent_ids, "failing_id": aid,
                    "agent_org_id": str(agent.organization_id), "ctx_org_id": org_id,
                },
            )
        if not agent.active_version_id:
            return NodeResult(
                error=f"Agent: agent '{agent.name}' n'a pas de version active",
                metadata={"agent_id": aid, "agent_name": agent.name},
            )
        version = await AgentVersion.get(agent.active_version_id)
        if not version or not version.schema_data:
            return NodeResult(
                error=f"Agent: schema vide pour '{agent.name}'",
                metadata={
                    "agent_id": aid, "agent_name": agent.name,
                    "active_version_id": str(agent.active_version_id),
                },
            )
        agents_data.append({
            "id": aid,
            "name": agent.name,
            "schema": version.schema_data,
        })

    # 3. Lire le determinationResult pour récupérer le texte OCR
    det_result = context.data.get("determinationResult")
    if not det_result:
        return NodeResult(
            error="Agent: 'determinationResult' manquant — le nœud Determination doit s'exécuter avant",
            metadata={"context_data_keys": list(context.data.keys())},
        )

    pages = det_result.get("pages", [])
    if not pages:
        return NodeResult(
            error="Agent: aucune page dans determinationResult",
            metadata={"determination_result_keys": list(det_result.keys())},
        )

    all_texts = []
    for page in pages:
        for det in page.get("detections", []):
            t = det.get("text", "")
            if isinstance(t, str) and t.strip():
                all_texts.append(t.strip())
    full_text = "\n".join(all_texts)

    if not full_text:
        return NodeResult(
            error="Agent: aucun texte OCR disponible dans determinationResult",
            metadata={
                "pages_count": len(pages),
                "detections_count": sum(len(p.get("detections", [])) for p in pages),
            },
        )

    # 4. Construire le schéma LLM combiné (tous agents fusionnés pour l'appel GPU)
    llm_schema: dict = {}
    key_to_path: dict[str, tuple[str, ...]] = {}
    key_to_reqs: dict[str, list[dict]] = {}
    field_descriptions: list[str] = []
    list_specs: dict[str, dict] = {}
    # Mapping dot_key → agent_id pour la provenance
    agent_field_keys: dict[str, list[str]] = {}  # agent_id → [dot_keys scalaires]
    agent_list_keys: dict[str, list[str]] = {}   # agent_id → [dot_keys de listes]

    for a in agents_data:
        agent_llm_schema: dict = {}
        agent_key_to_path: dict = {}
        agent_key_to_reqs: dict = {}
        agent_field_desc: list[str] = []
        agent_list_specs: dict = {}
        _build_llm_schema(
            a["schema"], agent_llm_schema, agent_key_to_path, agent_key_to_reqs,
            agent_field_desc, agent_list_specs,
        )
        agent_field_keys[a["id"]] = list(agent_key_to_path.keys())
        agent_list_keys[a["id"]] = list(agent_list_specs.keys())
        # Fusionner dans le schéma global
        _build_llm_schema(
            a["schema"], llm_schema, key_to_path, key_to_reqs,
            field_descriptions, list_specs,
        )

    if not llm_schema:
        return NodeResult(
            error="Agent: les schémas des agents sont vides ou mal formés",
            metadata={
                "agent_ids": [a["id"] for a in agents_data],
                "agent_names": [a["name"] for a in agents_data],
                "raw_schemas": [a["schema"] for a in agents_data],
            },
        )

    # 5. Appel /extract
    # Plus le schéma contient de listes, plus la réponse peut être longue.
    # On part de 2048 et on ajoute 1024 par liste détectée (cap à 8192).
    extract_max_tokens = min(2048 + 1024 * len(list_specs), 8192)

    base_meta = {
        "agent_ids": [a["id"] for a in agents_data],
        "agent_names": [a["name"] for a in agents_data],
        "ocr_text_chars": len(full_text),
        "schema_paths_count": len(key_to_path),
        "list_paths_count": len(list_specs),
        "list_paths": list(list_specs.keys()),
        "max_tokens": extract_max_tokens,
    }

    try:
        llm_result = await gpu_client.extract_structured(
            text=full_text,
            schema=llm_schema,
            field_descriptions=field_descriptions,
            max_tokens=extract_max_tokens,
        )
    except gpu_client.ExtractError as exc:
        return NodeResult(
            error=f"Agent: erreur /extract [{exc.kind}] — {exc}",
            metadata={
                **base_meta,
                "error_kind": exc.kind,
                "raw_response": exc.raw_response,
                "request_meta": exc.request_meta,
                "llm_schema_sent": llm_schema,
                "field_descriptions": field_descriptions,
            },
        )
    except Exception as exc:
        return NodeResult(
            error=f"Agent: erreur serveur GPU /extract — {type(exc).__name__}: {exc}",
            metadata={**base_meta, "exception_type": type(exc).__name__},
        )

    # 6. Nettoyage des valeurs (newlines, espaces multiples) — gère dicts et listes
    def _clean_node(value):
        if isinstance(value, dict):
            return {k: _clean_node(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_clean_node(v) for v in value]
        if value is None:
            return None
        return _clean_value(str(value))

    merged: dict = _clean_node(llm_result) if isinstance(llm_result, dict) else {}

    # 7. Validation des requirements (feuilles scalaires + items de listes)
    validation_errors = _collect_validation_errors(merged, key_to_path, key_to_reqs)
    validation_errors.extend(_collect_list_validation_errors(merged, list_specs))
    filled, total = _count_leaves(merged)

    if validation_errors:
        import json as _json
        try:
            extracted_dump = _json.dumps(merged, ensure_ascii=False, indent=2)
        except Exception:
            extracted_dump = repr(merged)
        return NodeResult(
            error=(
                "Validation du schéma : " + ", ".join(validation_errors)
                + f"\n--- Données extraites ({filled}/{total} feuilles remplies) ---\n"
                + extracted_dump
            ),
            metadata={
                **base_meta,
                "error_kind": "validation",
                "total_fields": filled,
                "total_schema_fields": total,
                "validation_errors": validation_errors,
                "extracted_data": merged,
            },
        )

    # 8. Structurer agentResults par agent : [{ agentId, agentName, fields }]
    def _extract_fields_for_agent(
        scalar_keys: list[str], list_keys: list[str], merged: dict,
    ) -> dict:
        """Extrait les feuilles scalaires + listes appartenant à cet agent."""
        result: dict = {}
        for dot_key in scalar_keys:
            path = key_to_path.get(dot_key)
            if not path:
                continue
            value = _get_nested(merged, path)
            _set_nested(result, path, value)
        for dot_key in list_keys:
            spec = list_specs.get(dot_key)
            if not spec:
                continue
            path = spec["path"]
            value = _get_nested(merged, path)
            _set_nested(result, path, value if isinstance(value, list) else [])
        return result

    agent_results = [
        {
            "agentId": a["id"],
            "agentName": a["name"],
            "fields": _extract_fields_for_agent(
                agent_field_keys.get(a["id"], []),
                agent_list_keys.get(a["id"], []),
                merged,
            ),
        }
        for a in agents_data
    ]

    # Accumule dans agentExtractions (plusieurs nœuds agents peuvent s'enchaîner)
    existing = context.data.get("agentExtractions")
    if isinstance(existing, list):
        existing.extend(agent_results)
        set_value(context.data, "agentExtractions", existing)
    else:
        set_value(context.data, "agentExtractions", agent_results)

    return NodeResult(
        output_port=0,
        metadata={
            "agents_count": len(agents_data),
            "total_fields": filled,
            "total_schema_fields": total,
        },
    )
