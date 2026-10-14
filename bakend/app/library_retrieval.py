"""Independent Method/Pipeline retrieval with auditable, bounded hybrid recall.

Semantic query rewrites come from task understanding; sparse retrieval, canonical
command matching and the output-type graph run independently. The tree never
excludes an entry. A failed shortlist can trigger a paginated semantic scan of
compact descriptions, not thousands of full parameter schemas.
"""
from __future__ import annotations

from collections import Counter
import math
import re

from .capability_retriever import _search_tokens


def _pipelines(index):
    result = {}
    for method in index.registry.all_methods():
        if method.entry_kind == 'pipeline' and method.execution_recipe:
            key = method.execution_recipe.recipe_id
            result[key] = method
    return result


def compact_catalog(index):
    index.refresh()
    rows = [dict(id=m.id, kind='method', label=m.label, description=m.description[:280],
                 inputs=m.input_types, outputs=m.output_types) for m in index.methods.values()]
    rows += [dict(id=identity, kind='pipeline', label=m.label, description=m.description[:280],
                  inputs=m.input_types, outputs=m.output_types) for identity, m in _pipelines(index).items()]
    return sorted(rows, key=lambda row: (row['kind'], row['id']))


def retrieve(index, contract, capability_ids, *, message='', limit=32, max_depth=4, semantic_ids=()):
    graph, trace = index.retrieve(contract, capability_ids, limit=max(128, limit*3), max_depth=max_depth)
    rows = compact_catalog(index)
    pipelines = _pipelines(index)
    # Rewrites add bilingual scientific synonyms. They are retrieval hints and
    # cannot grant permission for an operation or an existing input.
    queries = [message, contract.goal, *contract.retrieval_queries]
    tokens = _search_tokens(' '.join(queries))
    documents = {r['id']: _search_tokens(' '.join([r['id'], r['label'], r['description'],
                  *r['inputs'], *r['outputs']])) for r in rows}
    frequencies = Counter(t for terms in documents.values() for t in terms)
    count = len(rows)
    lexical, exact, scores = {}, set(), {}
    for row in rows:
        identity = row['id']
        aliases = [identity]
        if row['kind'] == 'method':
            command = index.methods[identity].execution.get('contract', {}).get('command')
            if command:
                aliases.append(command)
        # Exact commands are token bounded: 'bet' must not match 'better'.
        anchored = any(re.search(r'(?<![\w.-])'+re.escape(a)+r'(?![\w.-])', message, re.I) for a in aliases)
        if anchored:
            exact.add(identity)
        overlap = tokens & documents[identity]
        score = sum(math.log(1+(count+1)/(frequencies[t]+1)) for t in overlap)
        if score:
            lexical[identity] = score
        scores[identity] = (1000 if anchored else 0) + min(score, 45)
        if identity in semantic_ids:
            scores[identity] += 120
        if row['kind'] == 'method':
            if identity in graph:
                scores[identity] += 35 + 10/(1+graph.index(identity))
            if any(ref.capability in capability_ids for ref in index.references.get(identity, [])):
                scores[identity] += 4
        elif any(output in contract.goal_artifact_types for output in row['outputs']):
            scores[identity] += 30
    ranked = sorted((key for key in scores if scores[key] > 0), key=lambda key: (-scores[key], key))
    selected_pipelines = [key for key in ranked if key in pipelines][:6]
    # Retrieve the real atomic members of a reusable Pipeline. The graph planner
    # can reuse its wiring, but must still validate it against the current scope.
    for key in selected_pipelines:
        for step in pipelines[key].execution_recipe.steps:
            member = step.method_id
            if member in scores:
                scores[member] += 25 if key not in exact and key not in semantic_ids else 120
    forbidden = {mid for mid, refs in index.references.items()
                 if any(r.capability in contract.forbidden_capability_ids for r in refs)}
    ordered = sorted((mid for mid in index.methods if scores.get(mid, 0) > 0 and mid not in forbidden),
                     key=lambda mid: (-scores[mid], mid))
    selected = ordered[:limit]
    templates = []
    for key in selected_pipelines:
        method = pipelines[key]
        recipe = method.execution_recipe
        members = [s.method_id for s in recipe.steps]
        if not set(members) <= set(selected):
            continue
        templates.append(dict(id=key, label=method.label, description=method.description[:350],
            parameters=[{'id': p.id, 'default': p.default} for p in recipe.parameters],
            steps=[s.model_dump(exclude_defaults=True) for s in recipe.steps],
            output_bindings={k: v.model_dump() for k, v in recipe.output_bindings.items()}))
    trace.update(strategy='hybrid_method_pipeline', indexed_pipelines=len(pipelines),
        semantic_queries=contract.retrieval_queries, exact_matches=sorted(exact),
        semantic_matches=list(semantic_ids), lexical_matches=[key for key in ranked if key in lexical][:20],
        graph_candidates=len(graph), returned_candidates=len(selected), candidate_budget=limit,
        tree_is_filter=False, pipeline_templates=templates,
        truncated=trace['truncated'] or len(ordered)>limit,
        returned_methods=selected,
        note='A bounded shortlist is not proof that no method exists.')
    return selected, trace
