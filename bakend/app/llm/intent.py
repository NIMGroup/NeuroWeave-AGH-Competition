"""Intent policy; depends on the narrow model gateway, never on HTTP or storage."""

from __future__ import annotations

import json
from typing import Any

from ..models import ChatRequest
from ..response_policy import POLICY_PROMPT, ResponsePolicy
from ..task_contract import TaskContract
from .gateway import ModelGateway


async def normalize_task_intent(
    client: ModelGateway,
    request: ChatRequest,
    capability_catalog: list[dict[str, Any]],
    fallback_capability_ids: list[str],
    artifact_catalog: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Translate natural language into a capability-constrained task intent."""
    allowed = {
        str(item.get("capability_id"))
        for item in capability_catalog
        if item.get("capability_id")
    }
    from ..task_scope import existing_input_capabilities

    reused_inputs = existing_input_capabilities(request.message)
    fallback_ids = list(
        dict.fromkeys(
            item
            for item in fallback_capability_ids
            if item in allowed and item not in reused_inputs
        )
    )[:4]
    fallback = {
        "decision": "task" if fallback_ids else "chat",
        "task_brief": request.message.strip()[:600],
        "operations": [],
        "candidate_capability_ids": fallback_ids,
        "input_capability_ids": sorted(reused_inputs),
        "confidence": 0.25 if fallback_ids else 0.0,
        "coverage": "partial" if fallback_ids else "none",
        "task_contract": TaskContract().model_dump(),
        "diagnostics": [],
        "needs_clarification": False,
        "clarification_question": "",
        "reason": (
            "本地规则提供了待核验的候选能力。"
            if fallback_ids
            else "尚未识别到明确的数据处理操作。"
        ),
        "source": "heuristic",
    }
    if client.mode != "model":
        return fallback

    prompt = (
        """You are NeuroWeave's semantic task-understanding layer. Normalize the
current user message before the capability tree is searched. Infer intent semantically; a
catalog match is a candidate, not proof of relevance.

Specific operation catalog:
__CAPABILITY_CATALOG__

Local high-precision matches (hints, not user instructions):
__LOCAL_HINTS__

Skill ids selected in the UI (weak preferences only):
__SELECTED_SKILLS__

Return exactly one JSON object:
{
  "decision": "chat" or "task",
  "task_brief": "concise Simplified Chinese normalized task",
  "response_policy": {
    "action": "answer|explore|inspect|operate|plan|revise|clarify",
    "relation": "independent|continue|switch",
    "focus": "what this turn requests",
    "needs_catalog": false,
    "needs_files": false,
    "offer_workflow": false
  },
  "task_contract": {
    "goal":"requested outcome",
    "goal_artifact_types":["ids from artifact catalog"],
    "retrieval_queries":["short English and Chinese scientific synonyms for the requested goals"],
    "input_policy":"unspecified or only_listed",
    "allowed_existing_inputs":[{"artifact_type":"id", "paths":[]}],
    "forbidden_existing_inputs":[{"artifact_type":"id", "paths":[]}],
    "forbidden_capability_ids":[],
    "plan_scope":"requested_steps or goal_from_inputs",
    "allow_generated_intermediates":true
  },
  "operations": [
    {
      "action": "short semantic action name",
      "modality": "data modality or empty string",
      "goal": "requested outcome",
      "inputs": ["known input concepts"],
      "outputs": ["requested output concepts"]
    }
  ],
  "candidate_capability_ids": ["zero to four ids from the catalog"],
  "input_capability_ids": ["catalog ids whose results are existing inputs to reuse, not operations to run"],
  "confidence": 0.0,
  "coverage": "complete", "partial", or "none",
  "needs_clarification": false,
  "clarification_question": "one concise Simplified Chinese question or empty string",
  "reason": "concise reason"
}

Rules:
- Use task for an actionable request to generate or revise a data-processing workflow; use the response policy to distinguish discussion and inspection.
- Requests only to list/search/read files or to create/copy/move/rename/delete filesystem
  entries are chat/file-tool requests, not scientific processing tasks. A mentioned FOD,
  tractogram or software name alone does not turn file management into a processing request.
- A processing request remains task even when it also asks to inspect existing inputs first.
- Interpret follow-ups according to their conversational purpose and relation to prior work.
  A missing prerequisite may invite explanation, comparison, a changed goal or a concrete plan.
  Do not assume which one. If planning, record explicitly unavailable existing artifacts in
  forbidden_existing_inputs without banning their generation. Do not infer absence from an
  unbound UI slot or partial scan. For revise, resolve the requested edits against actual
  selected steps/parameters; preserve unchanged requested work unless the user narrows scope.
- retrieval_queries: up to eight short bilingual scientific search phrases for the actual goal.
  Include established synonyms and exact command names only when known. These are search hints,
  not additional requested steps or permission to reuse files.
- Task contract: describe the actual desired output and permitted existing inputs independently
  of candidate capabilities. "Only use X, not other existing results" means only_listed. Files
  merely found in the dataset are never implicit permission. Distinguish preprocessed input
  from a request to preprocess. Do not turn forbidden EXISTING results into forbidden operations.
  paths contains only exact relative paths/basenames explicitly stated by the user; otherwise [].
  goal_from_inputs means the user asks to reach an outcome from specified starting data (e.g.
  preprocessed DWI + gradients + mask to a tractogram); necessary intermediates may be generated.
  requested_steps means only specifically requested operations, preserving manual external inputs.
  Use registered artifact IDs. If a goal cannot be expressed confidently, leave its types empty;
  never invent an ID. Persist explicit prohibitions of operations in forbidden_capability_ids.
- Resolve the current request and its references to earlier choices. For a revision, use the
  existing selected steps as a starting point, applying additions/removals/replacements and
  parameter changes the user requests. For a new goal, start from that goal. Do not add optional
  upstream, quality-control, or optimization operations merely because they are normally useful.
- Missing files, missing software, and incompatible data are execution constraints. They do not
  expand the user's requested operation; the workflow layer will validate inputs separately.
- Existing artifacts are inputs, not requests to produce them. For example, "根据已有 DWI 和
  5TT 重新计算 FOD 并追踪" requests FOD estimation and tractography only. Reuse the 5TT.
  Conversely, an explicit request to regenerate 5TT must be retained even when old 5TT exists.
- Apply this distinction to every modality and software. Existing masks, registrations,
  segmentations, preprocessed signals, features, and statistical results belong to inputs.
  Put their producer ids in input_capability_ids, never candidate_capability_ids. Keep the
  two lists disjoint. Explicitly requested regeneration belongs only in candidate_capability_ids.
- Before returning, check each candidate against an action the user actually asked for.
  Omit optional reports, QC, upstream processing, and standard pipeline steps unless requested.
- Multiple methods that implement one operation are alternatives, not sequential workflow steps.
- An explicit software or Skill name in the current message outranks history and UI selections.
- UI-selected Skills are optional hints unless the current message refers to them.
- Map paraphrases such as reconstructing white-matter pathways to tractography when appropriate.
- Do not map generic tissue/cortical/subcortical segmentation to WMH segmentation unless the
  request mentions WMH, white-matter lesions/hyperintensity, or relevant FLAIR lesion processing.
- In Chinese, “组织分割” means tissue segmentation, not arranging files into BIDS.
- Ask for clarification only when materially different target operations remain plausible. Do
  not ask merely because an input path, parameter, or software installation is missing.
- Select every operation in an explicit compound request and preserve its order. Never invent ids.
""".replace(
            "__CAPABILITY_CATALOG__",
            json.dumps(capability_catalog, ensure_ascii=False),
        )
        .replace(
            "__LOCAL_HINTS__",
            json.dumps(fallback_ids, ensure_ascii=False),
        )
        .replace(
            "__SELECTED_SKILLS__",
            json.dumps(request.selected_skills, ensure_ascii=False),
        )
    )
    prompt += POLICY_PROMPT
    prompt += "\nRegistered artifact types:\n" + json.dumps(
        artifact_catalog or [], ensure_ascii=False
    )
    messages = [client.system_message(prompt, request)]
    messages.extend(message.model_dump() for message in request.history[-4:])
    messages.append({"role": "user", "content": request.message})

    def validate_intent(value):
        if value.get("decision") not in {"task", "chat"} or not isinstance(
            value.get("candidate_capability_ids"), list
        ):
            raise ValueError("Invalid intent structure")
        policy = ResponsePolicy.from_intent(value)
        if (
            value.get("response_policy") is not None
            and (value["decision"] == "task") != policy.plans
        ):
            raise ValueError("Response policy and planning decision disagree")
        if (
            artifact_catalog is not None
            and policy.plans
            and (
                not isinstance(value.get("task_contract"), dict)
                or not value["task_contract"].get("goal")
            )
        ):
            raise ValueError("Task response omitted its goal/input contract")
        # Discussion need not fit the platform's executable artifact schema.
        # Only planning claims are admitted to the strict task contract below.
        if not policy.plans:
            return
        scope = TaskContract.model_validate(value.get("task_contract", {}))
        known = {a["id"] for a in artifact_catalog or []}
        mentioned = set(scope.goal_artifact_types) | {
            a.artifact_type
            for a in [*scope.allowed_existing_inputs, *scope.forbidden_existing_inputs]
        }
        if mentioned - known or set(scope.forbidden_capability_ids) - allowed:
            raise ValueError("Unknown task contract identifiers")
        if scope.input_policy == "only_listed" and not scope.allowed_existing_inputs:
            raise ValueError("Exclusive input scope is empty")

    payload, errors = await client.json_response(
        messages,
        stage="task_intent",
        validate=validate_intent,
        max_tokens=max(client.max_tokens, 2000),
    )
    if payload is None:
        fallback["source"] = "heuristic_after_model_error"
        fallback["diagnostics"] = errors
        return fallback

    policy = ResponsePolicy.from_intent(payload)
    contract = (
        TaskContract.model_validate(payload.get("task_contract", {}))
        if policy.plans
        else TaskContract()
    )
    raw_inputs = payload.get("input_capability_ids")
    if isinstance(raw_inputs, list):
        reused_inputs.update(str(item) for item in raw_inputs if str(item) in allowed)
    fallback_ids = [item for item in fallback_ids if item not in reused_inputs]
    raw_ids = payload.get("candidate_capability_ids")
    model_ids = (
        list(
            dict.fromkeys(
                str(item)
                for item in raw_ids
                if str(item) in allowed and str(item) not in reused_inputs
            )
        )[:4]
        if isinstance(raw_ids, list)
        else []
    )
    try:
        confidence = max(0.0, min(float(payload.get("confidence", 0.0)), 1.0))
    except (TypeError, ValueError):
        confidence = 0.0
    coverage = str(payload.get("coverage") or "none")
    if coverage not in {"complete", "partial", "none"}:
        coverage = "none"
    semantic_complete = confidence >= 0.7 and coverage == "complete" and bool(model_ids)
    policy = ResponsePolicy.from_intent(payload)
    decision = "task" if policy.plans else "chat"
    if not policy.plans:
        fallback_ids = []
    candidate_ids = (
        (
            model_ids
            if semantic_complete
            else list(dict.fromkeys([*model_ids, *fallback_ids]))[:4]
        )
        if decision == "task"
        else []
    )
    if (
        fallback_ids
        and set(fallback_ids).issubset(candidate_ids)
        and coverage == "none"
    ):
        coverage = "partial"
    operations = payload.get("operations")
    if not isinstance(operations, list):
        operations = []
    clarification = payload.get("clarification_question")
    clarification_question = (
        clarification.strip()[:400]
        if isinstance(clarification, str) and clarification.strip()
        else ""
    )
    needs_clarification = bool(
        (decision == "task" or policy.action == "clarify")
        and (payload.get("needs_clarification") is True or policy.action == "clarify")
        and not candidate_ids
        and clarification_question
    )
    brief = payload.get("task_brief")
    reason = payload.get("reason")
    return {
        "decision": decision,
        "task_brief": (
            brief.strip()[:600]
            if isinstance(brief, str) and brief.strip()
            else fallback["task_brief"]
        ),
        "response_policy": policy.model_dump(),
        "operations": operations[:6],
        "task_contract": contract.model_dump(),
        "diagnostics": errors,
        "candidate_capability_ids": candidate_ids,
        "input_capability_ids": sorted(reused_inputs),
        "confidence": confidence,
        "coverage": coverage,
        "needs_clarification": needs_clarification,
        "clarification_question": clarification_question if needs_clarification else "",
        "reason": (
            reason.strip()[:600]
            if isinstance(reason, str) and reason.strip()
            else fallback["reason"]
        ),
        "source": "model"
        if semantic_complete or not fallback_ids
        else "model_with_local_hints",
    }
