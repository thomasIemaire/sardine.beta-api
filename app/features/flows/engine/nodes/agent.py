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

Le schéma de chaque agent (depuis Agent.active_version.schema_data) doit être
une structure imbriquée où chaque feuille est un dict avec :
  { "_key": "...", "_description": "...", "_requirements": [...] }

Exemple :
  {
    "seller": {
      "name": { "_key": "SELLER_NAME", "_description": "Nom du vendeur",
                "_requirements": [{"type": "required"}] }
    }
  }

Résultat dans context.data["agentResults"] (merge de tous les agents).
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


def _count_leaves(d: dict) -> tuple[int, int]:
    filled = total = 0
    for v in d.values():
        if isinstance(v, dict):
            f, t = _count_leaves(v)
            filled += f
            total += t
        else:
            total += 1
            if v is not None:
                filled += 1
    return filled, total


def _clean_value(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = (
        value.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    )
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned if cleaned else None


# ─── Validation des requirements ─────────────────────────────────


def _collect_validation_errors(
    merged: dict,
    key_to_path: dict[str, tuple[str, ...]],
    key_to_reqs: dict[str, list[dict]],
) -> list[str]:
    errors = []
    for key, reqs in key_to_reqs.items():
        if not reqs:
            continue
        path = key_to_path[key]
        value = _get_nested(merged, path)
        field_name = ".".join(path)
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


# ─── Construction du schéma LLM ──────────────────────────────────


def _build_llm_schema(
    mapper: dict,
    out_schema: dict,
    key_to_path: dict,
    key_to_reqs: dict,
    field_descriptions: list,
    path: tuple[str, ...] = (),
) -> None:
    """
    Parcourt récursivement le schéma de l'agent et construit :
    - out_schema : la structure imbriquée à envoyer au LLM (feuilles = null)
    - key_to_path / key_to_reqs : mappings pour la validation
    - field_descriptions : liste 'chemin: description' à passer au LLM
    """
    for name, value in mapper.items():
        if not isinstance(value, dict):
            continue
        current = path + (name,)
        # Feuille = présence de _key, _description ou _type
        if "_key" in value or "_description" in value or "_type" in value:
            desc = value.get("_description", "")
            reqs = _normalize_requirements(value.get("_requirements"))
            dot_key = ".".join(current)
            key_to_path[dot_key] = current
            key_to_reqs[dot_key] = reqs
            _set_nested(out_schema, current, None)
            if desc:
                field_descriptions.append(f"- {dot_key}: {desc}")
        else:
            _build_llm_schema(value, out_schema, key_to_path, key_to_reqs, field_descriptions, current)


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
        return NodeResult(error="Agent: aucun agent configuré dans le nœud")

    org_id = context.metadata.get("org_id")
    if not org_id:
        return NodeResult(error="Agent: org_id manquant dans le contexte")

    # 2. Charger les agents et leur schema_data depuis la version active
    agents_data = []
    for aid in agent_ids:
        try:
            agent = await Agent.get(PydanticObjectId(aid))
        except Exception:
            return NodeResult(error=f"Agent: id invalide '{aid}'")
        if not agent:
            return NodeResult(error=f"Agent: agent '{aid}' introuvable")
        if str(agent.organization_id) != org_id:
            return NodeResult(error=f"Agent: agent '{aid}' n'appartient pas à cette organisation")
        if not agent.active_version_id:
            return NodeResult(error=f"Agent: agent '{agent.name}' n'a pas de version active")
        version = await AgentVersion.get(agent.active_version_id)
        if not version or not version.schema_data:
            return NodeResult(error=f"Agent: schema vide pour '{agent.name}'")
        agents_data.append({
            "id": aid,
            "name": agent.name,
            "schema": version.schema_data,
        })

    # 3. Lire le determinationResult pour récupérer le texte OCR
    det_result = context.data.get("determinationResult")
    if not det_result:
        return NodeResult(error="Agent: 'determinationResult' manquant — le nœud Determination doit s'exécuter avant")

    pages = det_result.get("pages", [])
    if not pages:
        return NodeResult(error="Agent: aucune page dans determinationResult")

    all_texts = []
    for page in pages:
        for det in page.get("detections", []):
            t = det.get("text", "")
            if isinstance(t, str) and t.strip():
                all_texts.append(t.strip())
    full_text = "\n".join(all_texts)

    if not full_text:
        return NodeResult(error="Agent: aucun texte OCR disponible dans determinationResult")

    # 4. Construire le schéma LLM combiné (tous agents fusionnés pour l'appel GPU)
    llm_schema: dict = {}
    key_to_path: dict[str, tuple[str, ...]] = {}
    key_to_reqs: dict[str, list[dict]] = {}
    field_descriptions: list[str] = []
    # Mapping dot_key → agent_id pour la provenance
    agent_field_keys: dict[str, list[str]] = {}  # agent_id → [dot_keys]

    for a in agents_data:
        agent_keys: list[str] = []
        agent_llm_schema: dict = {}
        agent_key_to_path: dict = {}
        agent_key_to_reqs: dict = {}
        agent_field_desc: list[str] = []
        _build_llm_schema(
            a["schema"], agent_llm_schema, agent_key_to_path, agent_key_to_reqs, agent_field_desc,
        )
        agent_keys = list(agent_key_to_path.keys())
        agent_field_keys[a["id"]] = agent_keys
        # Fusionner dans le schéma global
        _build_llm_schema(
            a["schema"], llm_schema, key_to_path, key_to_reqs, field_descriptions,
        )

    if not llm_schema:
        return NodeResult(error="Agent: les schémas des agents sont vides ou mal formés")

    # 5. Appel /extract
    try:
        llm_result = await gpu_client.extract_structured(
            text=full_text,
            schema=llm_schema,
            field_descriptions=field_descriptions,
            max_tokens=2048,
        )
    except Exception as exc:
        return NodeResult(error=f"Agent: erreur serveur GPU /extract — {exc}")

    if llm_result is None:
        return NodeResult(error="Agent: l'appel /extract a échoué ou retourné une réponse non-JSON")

    # 6. Nettoyage des valeurs (newlines, espaces multiples)
    def _assemble(src: dict, dst: dict) -> None:
        for key, value in src.items():
            if isinstance(value, dict):
                dst[key] = {}
                _assemble(value, dst[key])
            else:
                dst[key] = _clean_value(str(value)) if value is not None else None

    merged: dict = {}
    if isinstance(llm_result, dict):
        _assemble(llm_result, merged)

    # 7. Validation des requirements
    validation_errors = _collect_validation_errors(merged, key_to_path, key_to_reqs)
    filled, total = _count_leaves(merged)

    if validation_errors:
        return NodeResult(
            error="Validation du schéma : " + ", ".join(validation_errors),
            metadata={
                "total_fields": filled,
                "validation_errors": validation_errors,
            },
        )

    # 8. Structurer agentResults par agent : [{ agentId, agentName, fields }]
    def _extract_fields_for_agent(dot_keys: list[str], merged: dict) -> dict:
        """Extrait uniquement les champs appartenant à cet agent depuis merged."""
        result = {}
        for dot_key in dot_keys:
            path = key_to_path.get(dot_key)
            if not path:
                continue
            value = _get_nested(merged, path)
            _set_nested(result, path, value)
        return result

    agent_results = [
        {
            "agentId": a["id"],
            "agentName": a["name"],
            "fields": _extract_fields_for_agent(agent_field_keys.get(a["id"], []), merged),
        }
        for a in agents_data
    ]

    set_value(context.data, "agentResults", agent_results)

    return NodeResult(
        output_port=0,
        metadata={
            "agents_count": len(agents_data),
            "total_fields": filled,
            "total_schema_fields": total,
        },
    )
