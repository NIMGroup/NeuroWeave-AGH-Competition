"""Resumable AND/OR planning over declared ports, not model catalog exploration.

The durable frontier contains method identities and links, never copies of manuals.
Existing input classifications are interpretations tied to scoped data evidence;
file existence, per-sample coverage and execution remain authoritative workflow checks.
"""
from __future__ import annotations

import copy
import heapq
import itertools
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from .artifact_semantics import compatible, accepts_format, canonical_input
from .data_selection import DataSelection
from .fact_memory import digest
from .method_planning import MethodContractIndex, MethodPlan, validate_plan
from .operation_context import checkpoint
from .input_evidence import observed_inputs, observed_values
from .parameter_rules import effective_parameters, input_required, matches
from .task_contract import ArtifactConstraint, TaskContract, OutputReference, StageOutputReference, declared_input_scope, input_declaration_signature
from .goal_grounding import ground_contract, save_grounding, output_port

GENERIC = {"generated_file", "imaging_input", "analysis_input", "execution_log", "*"}


class KnownInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_type: str
    path: str = Field(default="", max_length=4096, description="已有观测的相对路径，或用户明确声明的路径；声明未给文件名可留空，定位与核验留给dry-run。")
    _declared: bool = PrivateAttr(default=False)
    fact_id: str = Field(default='', description="可选的data事实ID；省略时后端按路径关联当前范围已保存的证据，不能引用未查询路径。")
    slot_id: str = Field(default="", description="同类型有不同角色时必须明确映射槽，如wm_response/gm_response/csf_response；这是输入解释，不用features.tissue代替。类型唯一时留空，端口别名不限制复用。")
    per_sample: bool = True
    features: dict[str, Any] = Field(default_factory=dict, description="仅填写原始事实明确观测的字段值，未知不猜测。")


class GoalRouteContract(TaskContract):
    # This entry plans a goal backwards; legacy requested_steps belongs to the
    # explicit-graph API. It must not disable required intermediate producers.
    plan_scope: Literal['goal_from_inputs'] = 'goal_from_inputs'


class RouteQuery(BaseModel):
    _input_notes: list[str] = PrivateAttr(default_factory=list)
    _suggestions: dict = PrivateAttr(default_factory=dict)
    model_config = ConfigDict(extra="forbid")
    input_selection: Literal['evidence', 'user'] = 'evidence'
    declared_input_roles: list[str] = Field(default_factory=list, max_length=40)
    declared_input_types: list[str] = Field(default_factory=list)
    task_contract: GoalRouteContract
    existing_inputs: list[KnownInput] = Field(default_factory=list, max_length=80)
    targets: list[OutputReference] = Field(default_factory=list, max_length=12,
        description='从真实候选 output_refs 选择最终输出；不填写产物类型。相同目标已有路线时省略。')
    stage_outputs: list[StageOutputReference] = Field(default_factory=list, max_length=12,
        description='逐项实现主控 required_stages 的真实输出；最终目标仍在targets中。')
    preferred_methods: list[str] = Field(default_factory=list, max_length=20)
    parameters: dict[str, dict[str, Any]] = Field(default_factory=dict)
    expansion_budget: int = Field(default=256, ge=1, le=2048)
    max_steps: int = Field(default=12, ge=1, le=20)
    max_routes: int = Field(default=1, ge=1, le=3)

    @model_validator(mode='after')
    def goal_scope(self):
        inputs = {}
        for item in self.existing_inputs:
            item.artifact_type, item.slot_id = canonical_input(item.artifact_type, item.path, item.slot_id)
            key = (item.artifact_type, item.path, item.slot_id, item.per_sample)
            previous = inputs.get(key)
            if previous:
                if any(k in previous.features and previous.features[k] != v for k,v in item.features.items()):
                    raise ValueError('同一路径的输入条件互相冲突；修正映射，不需重新查询文件结构')
                previous.features.update(item.features)
                previous.fact_id = min(filter(None, (previous.fact_id,item.fact_id)), default='')
            else:
                inputs[key] = item
        self.existing_inputs = [inputs[k] for k in sorted(inputs)]
        if 'plan_scope' not in self.task_contract.model_fields_set:
            self.task_contract.plan_scope='goal_from_inputs'
        if self.task_contract.input_policy=='only_listed' and not self.task_contract.allowed_existing_inputs:
            # The explicitly supplied, sourced inputs are already the starting
            # set. Avoid requiring the model to duplicate it in another field.
            # Forbidden inputs still win, and no additional path is introduced.
            self.task_contract.allowed_existing_inputs=[ArtifactConstraint(artifact_type=t)
                for t in sorted({i.artifact_type for i in self.existing_inputs})]
        return self


ROUTE_TOOL = {"type": "function", "function": {"name": "read_method_routes",
    "description": ('按目标及真实方法契约生成流程草案，外部输入由用户在任务卡选择。'
    '自动继承主控保存的完整目标契约，不重复填写目标；目标或范围改变须由主控更新。'
    '用户已声明的已有／缺失输入自动复用，必要的缺失输入在流程内生成。'
    '不需要先识别文件类型、提供路径或核验样本；未知输入由用户选择。'
    'targets选择候选output_refs，stage_outputs对应用户必需中间阶段；后端从真实端口确定类型。'
    '已保存路线直接复用，search_incomplete按原参数续查。参数偏好使用真实方法ID，不查询类型总表。'),
    "parameters": RouteQuery.model_json_schema()}}
# Assistant queries choose preferences; persisted/internal queries retain the
# full goal contract and sourced inputs for compatibility and graph validation.
for _internal_field in ('task_contract','existing_inputs','input_selection','declared_input_roles','declared_input_types'):
    ROUTE_TOOL['function']['parameters']['properties'].pop(_internal_field)
ROUTE_TOOL['function']['parameters']['required'].remove('task_contract')
_route_definitions = ROUTE_TOOL['function']['parameters'].pop('$defs', {})
for _field in ('targets','stage_outputs'):
    _ref = ROUTE_TOOL['function']['parameters']['properties'][_field]['items']['$ref'].split('/')[-1]
    ROUTE_TOOL['function']['parameters']['properties'][_field]['items'] = _route_definitions[_ref]


def accepts_output(output, need):
    if not compatible(output.type, need["type"]):
        return False
    if not accepts_format(output.format, need.get('formats', [])):
        return False
    return all(output.provides_features.get(k) == v for k, v in need.get("features", {}).items())


def accepts_input(item, need, contract):
    if not compatible(item.artifact_type, need["type"]):
        # Precise gradient subtypes are valid at their declared FSL port.
        if not (need["type"] == "gradient_table" and item.artifact_type in {"b_values", "b_vectors"}
                and any(item.path.endswith(ext) for ext in need.get("formats", []))):
            return False
    if need['type'] == 'gradient_table' and item.artifact_type in {'b_values', 'b_vectors'}:
        target, _ = canonical_input('gradient_table', '', need.get('slot') or '')
        if target != 'gradient_table' and target != item.artifact_type:
            return False
    # Port names are local to a consumer. They constrain reuse only when the
    # contract has several compatible inputs with different semantic roles.
    if need.get('distinct_roles') and item.artifact_type not in {'b_values', 'b_vectors'} and item.slot_id and item.slot_id != need.get("slot"):
        return False
    if item.path and not accepts_format(item.path, need.get('formats', [])):
        return False
    from .workflow_models import InputSlotSpec
    slot = InputSlotSpec(id=need.get("slot") or "input", type=need["type"], label="input", formats=need.get("formats", []))
    return contract.allows_slot(slot, item.path)


class GoalPlanner:
    def __init__(self, registry):
        self.registry = registry
        self.index = MethodContractIndex(registry)
        self.index.refresh()

    def fingerprint(self, query, versions):
        # Budgets change progress, not query identity. Description edits invalidate
        # the method graph while data facts retain their own content revisions.
        value = query.model_dump(exclude={"expansion_budget", "max_routes"})
        # Rephrased goals and lexical hints do not invalidate an identical
        # artifact/constraint search. They remain stored as user-facing intent.
        value['task_contract'].pop('goal', None)
        value['task_contract'].pop('retrieval_queries', None)
        return digest(["goal-routes-v3", value, versions,
            [(mid, method.revision()) for mid, method in sorted(self.index.methods.items())]])

    def search(self, query, versions, previous=None):
        query.task_contract = GoalRouteContract.model_validate(ground_contract(
            self.registry, query.task_contract, query.targets, query.stage_outputs).model_dump())
        query.preferred_methods = list(dict.fromkeys([*query.preferred_methods,
            *(r.method_id for r in query.task_contract.goal_output_refs),
            *(r.method_id for r in query.task_contract.required_output_refs)]))
        if any(t in GENERIC for t in query.task_contract.goal_artifact_types) and not query.preferred_methods:
            state={'key':self.fingerprint(query,versions),'frontier':[],'seen':[], 'routes':[],
                'gaps':[{'status':'unknown','reason':'通用文件类型不能确定具体操作；需明确产物语义或真实生产方法，无需重新查询数据结构'}],
                'excluded':{},'expansions':0,'counter':0}
            state['summary']=self.summary(state)
            return state,state['summary']
        if not query.existing_inputs and query.input_selection != 'user':
            state={'key':self.fingerprint(query,versions),'frontier':[],'seen':[], 'routes':[],
                'gaps':[{'status':'unknown','reason':'需要允许的已查询输入路径及其data事实引用；无需再浏览方法目录'}],
                'excluded':{},'expansions':0,'counter':0}
            state['summary']=self.summary(state)
            return state,state['summary']
        self.query = query
        self.costs = self.lower_bounds(query)
        key = self.fingerprint(query, versions)
        reused = previous and previous.get("key") == key
        state = copy.deepcopy(previous) if reused else {"key": key, "frontier": [], "seen": [],
            "routes": [], "gaps": [], "excluded": {}, "expansions": 0, "counter": 0}
        state['input_notes']=query._input_notes
        if not reused:
            needs = ([{'type':output_port(self.registry,r).type,
                'producer':r.method_id,'consumer':None,'slot':r.output_slot_id,'trail':[]}
                for r in query.task_contract.goal_output_refs] if query.task_contract.goal_output_refs else
                [{"type": t, "consumer": None, "slot": None, "trail": []} for t in query.task_contract.goal_artifact_types])
            if not needs:
                raise ValueError("需要明确已登记目标产物类型")
            self.push(state, {"steps": [], "needs": needs, "bindings": []})
        seen = set(state["seen"])
        start = state["expansions"]
        while state["frontier"] and len(state["routes"]) < query.max_routes and state["expansions"] - start < query.expansion_budget:
            checkpoint("搜索方法依赖")
            _, _, node = heapq.heappop(state["frontier"])
            identity = digest(node)
            if identity in seen:
                continue
            seen.add(identity)
            state["expansions"] += 1
            if not node["needs"]:
                self.complete(state, node, query)
                continue
            need, *rest = node["needs"]
            next_node = {**node, "needs": rest}
            # A requested new output is always produced by a method. Input facts
            # can close dependencies, never silently convert a request into reuse.
            inputs = [v for v in query.existing_inputs if need["consumer"] and need['type'] not in query.task_contract.required_output_types
                      and accepts_input(v, need, query.task_contract)]
            if inputs:
                ambiguous = [v for v in inputs if need.get('distinct_roles') and v.artifact_type not in {'b_values', 'b_vectors'} and not v.slot_id]
                if ambiguous:
                    self.gap(state, need, 'ambiguous', '已保存同类型输入，需要标明语义角色 '+str(need.get('distinct_roles'))+'；修正existing_inputs.slot_id即可，无需重复查文件或方法目录')
                    inputs = [v for v in inputs if v not in ambiguous]
                for item in inputs:
                    unknown = [k for k, v in need.get("features", {}).items() if k not in item.features]
                    mismatched = [k for k, v in need.get("features", {}).items() if k in item.features and item.features[k] != v]
                    if mismatched:
                        self.gap(state, need, "input_incompatible", "输入条件不满足：" + ",".join(mismatched))
                        continue
                    if unknown:
                        # A structural plan can carry pending input conditions.
                        # Scientific contents are checked once by dry-run.
                        query._input_notes.append(item.path+'：待dry-run核验条件 '+','.join(unknown))
                    bound = {**item.model_dump(), "step_id": need["consumer"], "slot_id": need["slot"],
                             "user_declared": item._declared}
                    self.push(state, {**copy.deepcopy(next_node), "bindings": [*node["bindings"], bound]})
                # Supplied inputs define the user's available starting point;
                # a mismatch must not be hidden by generating unrelated inputs.
                continue
            if node['steps']:
                reused_outputs = []
                for step in node["steps"]:
                    if step["method_id"] in need["trail"]:
                        continue
                    method = self.index.methods[step["method_id"]]
                    values = effective_parameters(method, step["parameters"])
                    for output in method.output_slots:
                        if accepts_output(output, need) and matches(output.generated_when, values) and (
                                not need.get('producer') or step['method_id']==need['producer']):
                            reused_outputs.append((step, output))
                reused_outputs = self.precise_ports(reused_outputs, need)
                if reused_outputs:
                    for step, output in reused_outputs:
                        branch = copy.deepcopy(next_node)
                        self.link(branch, need, step["id"], output.id)
                        self.push(state, branch)
                    continue
            if need['consumer'] and query.input_selection == 'user':
                consumer = next(s for s in node['steps'] if s['id'] == need['consumer'])
                slot = next(s for s in self.index.methods[consumer['method_id']].input_slots if s.id == need['slot'])
                requested_producers = (self.index.producers.get(need['type'], set()) & set(query.preferred_methods + query.task_contract.required_method_ids)) - set(need['trail'])
                # A missing binding is a user choice, not evidence that the data
                # is absent. Generate only explicitly requested dependencies or
                # dependencies required by the user's external-input restrictions.
                if (need['type'] not in query.task_contract.required_output_types and query.task_contract.allows_slot(slot) and
                        {'existing_path', 'dataset'} & set(slot.source_options) and
                        (not requested_producers or slot.id in query.declared_input_roles or
                         slot.type in query.declared_input_types)):
                    binding = {'step_id':need['consumer'], 'slot_id':slot.id, 'artifact_type':slot.type,
                               'path':'', 'fact_id':'', 'per_sample':True, 'user_selection':True}
                    suggestion = query._suggestions.get(slot.id)
                    if suggestion:
                        binding.update(path=suggestion['path'], fact_id=suggestion['fact_id'], user_declared=True)
                    self.push(state, {**copy.deepcopy(next_node), 'bindings':[*node['bindings'],binding]})
                    continue
            # Generic containers carry no semantics. Only specifically preferred
            # producer identities may cross them; never enumerate all image/file writers.
            mids = self.index.producers.get(need["type"], set())
            if need.get('producer'):
                mids = set(mids) & {need['producer']}
            required_ports = [r for r in query.task_contract.required_output_refs
                              if output_port(self.registry,r).type == need['type']]
            if need['consumer'] and required_ports:
                mids = set(mids) & {r.method_id for r in required_ports}
            if need["type"] in GENERIC and not need.get('producer'):
                mids = set(mids) & set(query.preferred_methods)
            choices = []
            for mid in sorted(mids, key=lambda m: (m not in query.preferred_methods, m)):
                method = self.index.methods[mid]
                if mid in need["trail"]:
                    state["excluded"][mid] = "循环依赖"
                    continue
                if any(r.capability in query.task_contract.forbidden_capability_ids for r in self.index.references.get(mid, [])):
                    state["excluded"][mid] = "用户排除该能力"
                    continue
                if need["consumer"] and (not query.task_contract.allow_generated_intermediates or query.task_contract.plan_scope == "requested_steps"):
                    state["excluded"][mid] = "本次范围不允许补充生成步骤"
                    continue
                for output in method.output_slots:
                    if not accepts_output(output, need) or (need.get('producer') and output.id != need['slot']):
                        continue
                    variants = self.variants(method, output, query, limit=17)
                    if len(variants)>16:
                        self.gap(state, need, 'search_incomplete', '此方法条件参数组合较多，请明确必要参数以缩小范围：'+mid)
                    for variant in variants[:16]:
                        values = effective_parameters(method, variant)
                        generic_inputs = [s for s in method.input_slots if input_required(s, values)
                            and s.type in GENERIC and not s.requires_features]
                        if generic_inputs and output.type not in GENERIC and mid not in query.preferred_methods:
                            # A generic image port does not prove that a brain
                            # mask or DWI can become a 5TT/FOD/etc. Candidate
                            # contracts need semantics or an explicit method choice.
                            state['excluded'][mid] = '通用输入端口尚未声明生成此语义产物的必要条件；需核实具体方法，不能仅凭文件类型自动选用'
                            continue
                        self_inputs = [s for s in method.input_slots if input_required(s, values) and s.type==output.type]
                        format_conversion = (need.get('formats') and all(s.formats and
                            not accepts_format(output.format, s.formats) for s in self_inputs))
                        if (self_inputs and not format_conversion and mid not in query.preferred_methods and mid not in query.parameters
                                and not any(i.artifact_type==output.type for i in query.existing_inputs)):
                            state['excluded'][mid] = '同类型后处理不是当前生成目标的必要起点；明确方法偏好可启用'
                            continue
                        choices.append((method, output, variant))
            choices = self.precise_ports(choices, need)
            if not choices:
                self.gap(state, need, "unknown" if need["consumer"] else "unsupported",
                    ("已有输入范围不允许直接提供此必需输入，且当前方法与参数约束下没有可连接的生成步骤；需调整输入范围或生成方法，而非重复查询文件"
                     if query.input_selection == 'user' else "缺少已查询的对应输入；定向补充路径或条件证据")
                    if need["consumer"] else "登记契约中没有目标产物的生成方法")
            for method, output, parameters in choices:
                if len(node["steps"]) >= query.max_steps:
                    self.gap(state, need, "search_incomplete", "达到路线深度限制，不能据此判断不可完成")
                    continue
                branch = copy.deepcopy(next_node)
                sid = "n" + str(len(node["steps"]) + 1)
                self.link(branch, need, sid, output.id)
                branch["steps"].append({"id": sid, "method_id": method.id, "parameters": parameters, "inputs": {}})
                values = effective_parameters(method, parameters)
                if query.input_selection == 'user':
                    relevant = set(query.declared_input_roles) & {s.id for s in method.input_slots}
                    used_roles = {s.id for s in method.input_slots if input_required(s, values)}
                    branch['ignored_roles'] = node.get('ignored_roles', 0) + len(relevant - used_roles)
                branch["needs"] = [{"type": slot.type, "formats": slot.formats,
                    "features": slot.requires_features, "consumer": sid, "slot": slot.id,
                    "distinct_roles": [other.id for other in method.input_slots if other.type == slot.type]
                        if sum(other.type == slot.type for other in method.input_slots) > 1 else [],
                    "trail": [*need["trail"], method.id]} for slot in method.input_slots if input_required(slot, values)] + rest
                self.push(state, branch)
        state["seen"] = sorted(seen)
        if len(state["frontier"]) > 10000 or len(state["seen"]) > 10000:
            # Do not persist an unbounded graph. Report a narrowing requirement,
            # never destroy the frontier and silently restart the same search.
            raise ValueError("依赖分支超过安全范围，请指定方法偏好或更精确的目标/输入语义")
        result = self.summary(state)
        if reused and previous.get("summary") == result:
            result = previous["summary"]
        state["summary"] = result
        return state, {**result, "summary_reused": bool(reused and previous.get("summary") == result)}

    @staticmethod
    def precise_ports(choices, need):
        # Equal artifact types do not make WM/GM/CSF ports interchangeable.
        grouped = {}
        for c in choices:
            method = c[0]
            mid = method["id"] if isinstance(method, dict) else method.id
            grouped.setdefault(mid, []).append(c)
        selected = []
        for group in grouped.values():
            exact = [c for c in group if c[1].id == need.get('slot')]
            selected.extend(exact or (group if len({c[1].id for c in group}) == 1 else []))
        return selected

    @staticmethod
    def link(node, need, sid, output):
        if need["consumer"]:
            target = next(s for s in node["steps"] if s["id"] == need["consumer"])
            target["inputs"][need["slot"]] = {"step_id": sid, "output_slot_id": output}

    def push(self, state, node):
        state["counter"] += 1
        priority = (len(node["steps"]) * 10 + sum(self.costs.get(n['type'], 100) * 10 for n in node['needs'])
                    + len(node['needs']) + node.get('ignored_roles', 0) * 100)
        heapq.heappush(state["frontier"], [priority, state["counter"], node])

    @staticmethod
    def variants(method, output, query, limit=16):
        """Bounded declared input-control choices; explicit user values are fixed.

        No parameter is invented, and unrelated algorithm/format knobs remain at
        their defaults. Every candidate passes the ordinary compiler afterward.
        """
        fixed = dict(query.parameters.get(method.id, {}))
        for key, allowed in output.generated_when.items():
            if key in fixed and not matches({key: allowed}, fixed):
                return []
            fixed.setdefault(key, allowed[0])
        controls = {key for s in method.input_slots if not s.required
            for condition in [s.required_when, *s.required_when_any] for key in condition}
        fields, choices = [], []
        for spec in method.parameters:
            if spec.id not in controls or spec.id in fixed:
                continue
            allowed = [spec.default]
            if spec.type == 'boolean':
                allowed += [False, True]
            else:
                allowed += [o.value for o in spec.options]
            values = []
            for value in allowed:
                if value is not None and not any(type(value) is type(v) and value==v for v in values):
                    values.append(value)
            if len(values) > 1:
                fields.append(spec.id)
                choices.append(values)
        return [{**fixed, **dict(zip(fields, values))} for values in itertools.islice(itertools.product(*choices), limit)]

    def lower_bounds(self, query):
        # Rank dependency-completing paths before repeated transformations of
        # the requested output. This is a ranking estimate, not absence evidence.
        costs = {kind: 0 for kind in self.index.producers if any(compatible(i.artifact_type, kind) for i in query.existing_inputs)}
        costs.update({i.artifact_type: 0 for i in query.existing_inputs})
        if query.input_selection == 'user':
            costs.update({s.type:0 for m in self.index.methods.values() for s in m.input_slots
                          if query.task_contract.allows_slot(s) and {'existing_path','dataset'} & set(s.source_options)})
        for _ in range(query.max_steps):
            changed = False
            for method in self.index.methods.values():
                for output in method.output_slots:
                    for variant in self.variants(method, output, query):
                        values = effective_parameters(method, variant)
                        required = [s.type for s in method.input_slots if input_required(s, values)]
                        if all(k in costs for k in required):
                            value = 1 + sum(costs[k] for k in required)
                            if value < costs.get(output.type, float('inf')):
                                costs[output.type] = value
                                changed = True
            if not changed:
                break
        return costs

    @staticmethod
    def gap(state, need, status, reason):
        gap = {"artifact_type": need["type"], "slot_id": need.get("slot"), "status": status, "reason": reason}
        if gap not in state["gaps"] and len(state["gaps"]) < 40:
            state["gaps"].append(gap)

    def complete(self, state, node, query):
        try:
            plan = MethodPlan(status="matched", steps=node["steps"])
            validate_plan(plan, query.task_contract, self.registry)
        except (ValueError, KeyError) as exc:
            self.gap(state, {"type": ",".join(query.task_contract.goal_artifact_types)}, "contract_mismatch", str(exc)[:400])
            return
        payload = {"steps": [s.model_dump(exclude_defaults=True) for s in plan.steps], "bindings": node["bindings"]}
        rid = "route:" + digest(payload)[:24]
        if any(r["id"] == rid for r in state["routes"]):
            return
        state["routes"].append({"id": rid, **payload})

    @staticmethod
    def summary(state):
        routes = [{"route_id": r["id"], "method_ids": [s["method_id"] for s in r["steps"]],
            "step_count": len(r["steps"]), "existing_input_count": sum(not b.get('user_selection') for b in r['bindings']),
            "user_input_count": sum(bool(b.get('user_selection')) for b in r['bindings']),
            "input_selection": 'user' if any(b.get('user_selection') for b in r['bindings']) else 'evidence',
            "fact_ids": sorted({b["fact_id"] for b in r["bindings"] if b['fact_id']}),
            "input_interpretations": list({(b['artifact_type'],b['path'],b['slot_id']):
                {'artifact_type':b['artifact_type'],'path':b['path'],'fact_id':b['fact_id'], 'slot_id':b['slot_id']}
                for b in r['bindings'] if not b.get('user_selection')}.values()),
            "inputs_to_select": [{'step_id':b['step_id'],'slot_id':b['slot_id'],'artifact_type':b['artifact_type']}
                                 for b in r['bindings'] if b.get('user_selection')],
            "parameter_choices": [{'method_id':s['method_id'],'parameters':s['parameters']}
                for s in r['steps'] if s.get('parameters')],
            "validation": "ports_parameters_scope_checked", "sample_coverage": "pending_dry_run"} for r in state["routes"]]
        incomplete = bool(state['frontier']) or any(g['status']=='search_incomplete' for g in state['gaps'])
        unsupported = bool(state['gaps']) and all(g['status']=='unsupported' for g in state['gaps'])
        return {"status": "matched" if routes else "search_incomplete" if incomplete else 'unsupported' if unsupported else "needs_facts",
            "routes": routes, "gaps": [] if routes else state["gaps"][:12],
            "excluded": [{"method_id": mid, "reason": reason} for mid, reason in list(state["excluded"].items())[:12]],
            "search": {"expanded": state["expansions"], "remaining": len(state["frontier"]), "resumable": bool(state["frontier"])},
            "input_notes":state.get('input_notes',[]),
            "notice": "流程草案按真实方法契约生成；用户在任务卡选择外部输入，覆盖和内容兼容性由dry-run检查，尚未执行。"}


def validate_evidence(owner, ledger, request, query):
    """Reject invented/out-of-scope paths; semantic classifications stay explicit."""
    versions = {}
    if query.input_selection == 'user':
        # Classification strings and observed paths cannot block a structural
        # draft or silently become binding authority.
        return {'input_declarations':declaration_version(ledger.facts.declarations(request))}
    facts = ledger.facts
    rows = {r["id"]: r for r in facts.rows(request, "data")}
    raw_cache={}
    def source_result(row):
        if row['id'] not in raw_cache:
            raw_cache[row['id']]=facts.raw_result(request,row)[0]
        return raw_cache[row['id']]
    views = {}
    def view(row):
        if row['id'] not in views:
            views[row['id']] = observed_inputs(row, source_result(row))
        return views[row['id']]
    def available_paths(row):
        return view(row)[0]
    def dataset_evidence(row):
        raw=source_result(row)
        scope=row['arguments'].get('source_scope') or raw.get('source_scope')
        return scope!='output' and not any(p in row['tool'] for p in ('_output_','_workspace_'))
    for item in query.existing_inputs:
        row = rows.get(item.fact_id)
        if row and row['tool'] == 'user_declaration':
            declaration = source_result(row)
            if (declaration.get('dataset_id') != request.dataset_id or not declaration.get('available')
                    or declaration['artifact_type'] != item.artifact_type
                    or declaration.get('slot_id','') != item.slot_id
                    or declaration.get('path','') != item.path):
                raise ValueError('输入声明不属于当前数据集、已撤回或角色/路径不一致')
            item._declared = True
            item.features = declaration.get('features', {})
            versions[item.fact_id] = row['content_hash']
            continue
        if not item.path:
            raise ValueError('未给路径的输入必须引用用户声明')
        item.path = DataSelection(path=item.path).path
        row = rows.get(item.fact_id)
        # An omitted or imprecisely selected data reference can be linked to
        # already stored evidence for this exact path. Do not query the source
        # again, accept invented references or substitute an output-scope fact.
        if not item.fact_id or (row and dataset_evidence(row) and item.path not in available_paths(row)):
            for candidate in reversed(list(rows.values())):
                if not dataset_evidence(candidate):
                    continue
                if item.path in available_paths(candidate):
                    row=candidate
                    item.fact_id=row['id']
                    break
        if not row:
            raise ValueError("输入需要当前授权范围内仍有效的数据事实引用：" + item.fact_id)
        raw = source_result(row)
        if not dataset_evidence(row):
            raise ValueError('科学流程输入需来自当前所选数据集，输出目录事实不能替代数据集输入')
        candidates, records = view(row)
        if item.path not in candidates:
            raise ValueError("事实尚未包含此路径，定向查询该路径即可：" + item.path)
        observed = observed_values(records.get(item.path, []))
        verified={}
        for field,value in item.features.items():
            if any(digest(value)==digest(v) for v in observed.get(field,[])):
                verified[field]=value
            else:
                query._input_notes.append(item.path+' / '+field+'：该路径的观测尚未证明，按未知处理；仅方法必需此条件时补查。')
        item.features=verified
        source_scope = row['arguments'].get('source_scope') or raw.get('source_scope') or (
            'output' if any(part in row['tool'] for part in ('_output_','_workspace_')) else 'dataset')
        if source_scope != 'dataset':
            raise ValueError("科学流程输入需来自当前所选数据集，输出目录事实不能替代数据集输入")
        source, _ = owner.data_operations._roots({'conversation_id':request.conversation_id,
            'dataset_id':request.dataset_id,'workspace_id':request.workspace_id,'source_scope':'dataset','mode':'inplace'})
        if raw.get('root_path'):
            from pathlib import Path
            if Path(raw['root_path']).resolve()!=Path(source).resolve():
                raise ValueError('输入事实来自其他目录，需复用当前所选数据集的证据')
        # Planning uses stored facts. Current path authorization, existence and
        # content checks belong to dry-run, not a second scientific inspection.
        versions[item.fact_id] = {'content': row['content_hash'], 'source_version': row.get('source_version')}
    return versions


def apply_input_declarations(query, declarations, vocabulary):
    query.task_contract = declared_input_scope(query.task_contract, declarations, vocabulary)
    query.declared_input_roles = sorted({r['summary'].get('slot_id', '') for r in declarations
        if r['summary'].get('available') and r['summary'].get('slot_id')})
    query.declared_input_types = sorted({r['summary']['artifact_type'] for r in declarations
        if r['summary'].get('available') and not r['summary'].get('slot_id')})
    query._suggestions = {r['summary']['slot_id']:{'path':r['summary']['path'], 'fact_id':r['id']}
        for r in declarations if r['summary'].get('available') and r['summary'].get('slot_id') and r['summary'].get('path')}


def declaration_version(declarations):
    # Rephrasing unchanged assertions keeps the route and its summary reusable.
    return digest(input_declaration_signature(declarations))


def read_routes(owner, ledger, request, arguments):
    from .task_management import TaskActionError, assert_task_open
    assert_task_open(request.assistant_task)
    manual = request.assistant_task.get('deliverable') == 'workflow'
    inputs = {k:v for k,v in arguments.items() if k != 'existing_inputs'} if manual else arguments
    current_contract = request.assistant_task.get('task_contract')
    if manual and current_contract:
        # Compatibility clients may still echo a contract. It is never a second
        # authority: normalize from the current goal before validating arguments.
        inputs = {**inputs, 'task_contract':{**current_contract,'plan_scope':'goal_from_inputs'}}
        # Older internal callers supplied known type keys with their query.
        # Only fill an unresolved final output, never replace canonical scope,
        # required stages or an already selected port. Model-facing tools do not
        # expose this field; new requests select targets instead.
        legacy_goals = arguments.get('task_contract',{}).get('goal_artifact_types',[])
        if not current_contract.get('goal_artifact_types') and not arguments.get('targets') and legacy_goals:
            vocabulary = {a.id for a in owner.workflow_service.registry.artifact_types()}
            if not set(legacy_goals)-vocabulary:
                inputs['task_contract']['goal_artifact_types'] = legacy_goals
                legacy = TaskContract.model_validate(arguments['task_contract'])
                # Preserve restrictive scope carried by old internal callers,
                # only when the main goal has no scope/stage restrictions yet.
                unconstrained = (current_contract.get('input_policy','unspecified') in {'unspecified','user_selected'}
                    and not any(current_contract.get(k) for k in ('allowed_existing_inputs','forbidden_existing_inputs',
                        'forbidden_capability_ids','required_stages','required_output_types','required_method_ids'))
                    and current_contract.get('allow_generated_intermediates',True))
                if unconstrained:
                    inputs['task_contract'] = {**legacy.model_dump(),
                        'goal':current_contract.get('goal',''),'plan_scope':'goal_from_inputs'}
    if not inputs.get('task_contract'):
        raise TaskActionError('planning_goal_missing', 'coordinator',
            '尚未保存完整任务契约；主控先用 update_task_state 保存最终产物与必需阶段，再按原偏好选路。')
    query = RouteQuery.model_validate({**inputs, 'input_selection':'user'} if manual else inputs)
    grounded = ground_contract(owner.workflow_service.registry, query.task_contract, query.targets, query.stage_outputs)
    query.task_contract = GoalRouteContract.model_validate(grounded.model_dump())
    if manual:
        save_grounding(ledger, request, grounded)
        current_contract = grounded.model_dump()
    # The refs are now part of the canonical contract; echoed selection fields
    # must not alter the query fingerprint on an identical follow-up.
    query.targets = []
    query.stage_outputs = []
    snapshot = request._active_plan_snapshot
    if snapshot and request.assistant_task.get('active_plan',{}).get('id') == snapshot.workflow_id:
        for step in snapshot.steps:
            if step.selected_method_id:
                query.parameters[step.selected_method_id] = {**step.selected_parameters,
                    **query.parameters.get(step.selected_method_id,{})}
    declarations = ledger.facts.declarations(request)
    registry = owner.workflow_service.registry
    vocabulary = {a.id for a in registry.artifact_types()}
    if manual:
        apply_input_declarations(query, declarations, vocabulary)
    for row in ([] if manual else declarations):
        declaration = row['summary']
        if not declaration.get('available'):
            query.existing_inputs = [i for i in query.existing_inputs
                if (i.artifact_type,i.slot_id) != (declaration['artifact_type'],declaration.get('slot_id',''))]
            continue
        if not any(i.artifact_type==declaration['artifact_type'] and i.slot_id==declaration.get('slot_id','')
                   for i in query.existing_inputs):
            query.existing_inputs.append(KnownInput(artifact_type=declaration['artifact_type'],
                slot_id=declaration.get('slot_id',''), path=declaration.get('path',''), fact_id=row['id'],
                per_sample=declaration.get('per_sample',True), features=declaration.get('features',{})))
    if not manual and query.task_contract.input_policy=='only_listed' and not arguments.get('task_contract',{}).get('allowed_existing_inputs'):
        query.task_contract.allowed_existing_inputs = [ArtifactConstraint(artifact_type=t) for t in
            sorted({i.artifact_type for i in query.existing_inputs})]
    # Revoked assertions are not external inputs, even if a model repeats a
    # previously stored interpretation. A new observed fact may later replace it.
    used = set(query.task_contract.goal_artifact_types + query.task_contract.required_output_types) | {i.artifact_type for i in query.existing_inputs}
    used |= {i.artifact_type for i in [*query.task_contract.allowed_existing_inputs,*query.task_contract.forbidden_existing_inputs]}
    if used - vocabulary:
        raise TaskActionError('planning_scope_unresolved', 'coordinator',
            '任务输入范围或必需阶段仍包含未登记引用：'+','.join(sorted(used-vocabulary))+
            '；只修正对应范围或用真实输出引用表示阶段，不重复查询文件和方法目录。')
    planner = GoalPlanner(registry)
    if set(query.preferred_methods) - planner.index.methods.keys():
        raise ValueError("方法偏好包含未知方法ID")
    if set(query.parameters) - planner.index.methods.keys():
        raise ValueError("参数配置包含未知方法ID")
    versions = validate_evidence(owner, ledger, request, query)
    saved = request.assistant_task.get("method_planning", {})
    state, result = planner.search(query, versions, saved.get("state"))
    ledger.save(request, {**request.assistant_task, "method_planning": {
        "task_id": request.assistant_task.get('task_id'),
        'task_revision':request.assistant_task.get('task_revision',1),
        "query": query.model_dump(), "versions": versions, "state": state}})
    return result


def saved_route(owner, ledger, request, route_id, contract=None):
    saved = request.assistant_task.get("method_planning", {})
    if saved.get('task_revision',1) != request.assistant_task.get('task_revision',1):
        raise ValueError('当前任务目标已修改，旧路线不能直接复用；按完整目标更新路线，复用未变化的事实')
    query = RouteQuery.model_validate(saved.get("query", {}))
    declarations = ledger.facts.declarations(request)
    if query.input_selection == 'user':
        base = contract or TaskContract.model_validate(request.assistant_task.get('task_contract', query.task_contract.model_dump()))
        contract = declared_input_scope(base, declarations,
            {a.id for a in owner.workflow_service.registry.artifact_types()})
    if contract is not None and query.task_contract.model_dump(exclude={'goal','retrieval_queries'}) != contract.model_dump(exclude={'goal','retrieval_queries'}):
        raise ValueError("目标或范围已变，需要按新契约更新路线")
    versions = validate_evidence(owner, ledger, request, query)
    if GoalPlanner(owner.workflow_service.registry).fingerprint(query, versions) != saved["state"]["key"]:
        raise ValueError("输入事实或方法契约已变，需要定向更新路线")
    route = next((r for r in saved["state"]["routes"] if r["id"] == route_id), None)
    if route is None:
        raise ValueError("路线ID不在当前任务和授权范围内")
    return {**route, 'task_contract':query.task_contract.model_dump()}
