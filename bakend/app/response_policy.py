"""Separate conversational purpose from scientific execution capability.

This is semantic model output, not keyword routing or permission to execute.
Only plan/revise may enter graph planning; the Runner retains its own approval.
"""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class ResponsePolicy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['answer', 'explore', 'inspect', 'operate', 'plan', 'revise', 'clarify'] = 'answer'
    relation: Literal['independent', 'continue', 'switch'] = 'independent'
    focus: str = Field(default='', max_length=800)
    needs_catalog: bool = False
    needs_files: bool = False
    # An invitation to request a draft is conversational, never an execution grant.
    offer_workflow: bool = False

    @property
    def plans(self):
        return self.action in {'plan', 'revise'}

    @property
    def needs_tools(self):
        return self.needs_catalog or self.needs_files or self.action in {'inspect', 'operate'}

    @classmethod
    def from_intent(cls, intent):
        # Older/offline intent producers omit policy. Keep their declared intent,
        # never upgrade a valid conversational decision from lexical hints.
        if intent.get('response_policy') is not None:
            return cls.model_validate(intent['response_policy'])
        return cls(action='plan' if intent.get('decision') == 'task' else 'answer',
                   needs_catalog=intent.get('decision') != 'task')


POLICY_PROMPT = """
Decide the conversational purpose before deciding whether to build a workflow.
Task mode enables tools and planning; it does not require a workflow on every turn.
Use response_policy with these fields:
- action: answer (explain/interpret/converse), explore (compare approaches, trade-offs,
  feasibility or next-step choices), inspect (query files), operate (data/file management or temporary data scripts),
  plan (an actionable request for a new reviewable workflow), revise (an actionable
  change to a prior workflow), or clarify (a material ambiguity prevents a useful answer).
- relation: continue (explicit/implicit reference to current work), switch (change of
  topic or goal), independent (no dependence on the previous workflow).
- focus: resolve references such as "that method", "instead", "the second option"
  using recent discussion and actual workflow state. State what THIS turn asks for.
- needs_catalog: true if the answer needs platform-specific methods, parameters,
  availability, or input/output facts; false for a general conceptual explanation.
- needs_files: true only if file/dataset inspection is actually relevant/requested.
- offer_workflow: true for answer/explore when the user discusses a concrete processing
  goal and a proposed approach could be turned into a reviewable workflow. This includes
  discussing missing inputs, alternatives or feasibility without requesting a plan yet.
  It is an invitation only, not decision=task. False for greetings, pure definitions,
  file listings/management, already requested plans, unresolved goals with no useful
  approach, or when the user asks for no follow-up suggestions.
These are response strategies, not a fixed sequence of stages. A user may move freely
between them. Do not classify by question words, language, or keyword occurrence.
"What if I have no parcellation?" can ask for alternatives; it is NOT automatically
permission to append producers. Explain viable choices before a route is selected.
"Generate the missing parcellation" requests a plan; "why is it necessary?" requests
an explanation. "Drop SIFT2", "use a different algorithm", "change the target" and
"first do only this step" have different scopes; resolve the actual intent.
For explore, explain reasonable scientific approaches even if the platform cannot yet
automatically compose them. Distinguish scientific feasibility, registered methods,
validated workflow construction, and runtime availability. They are different facts.
Set decision=task ONLY for plan/revise; all other actions are conversational. A method
name, selected Skill, missing input or keyword hint alone never requires planning.
In continue, retain still-relevant explicit user restrictions and honor current changes.
In switch/independent, do not inherit an old goal or its unrelated input restrictions.
An uncertain choice can be answered with options. Clarify only when proceeding would
choose between materially different user goals; do not repeatedly ask for file paths.
If the user asks both a question and for a concrete plan, plan/revise and address the
question in the explanation. Nothing here authorizes execution or filesystem changes.
"""


def response_guidance(policy):
    if policy is None:
        return ''
    invitation = ''
    if policy.offer_workflow and policy.action in {'answer', 'explore'}:
        invitation = ('\n答复后可附一句可选的规划邀请，给出与本轮目标、方法、输入和限制对应的完整请求。'
                      '尚未选定的方法只能作为选项；说明实际接入限制，不声称已创建或能自动执行流程。')
    return ('\n本轮回答策略（后端状态）：' + policy.model_dump_json() +
            '\n直接回应 focus。解释/比较不追加处理；科学可行性与平台接入能力分开说明。'
            '平台事实依据查询，未核实内容说明未知；查询失败或分页不证明不存在。'
            '切换目标不继承无关限制，新建或修订流程仅生成可审阅草案。' + invitation)




class PlanningAlternative(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=200)
    method_ids: list[str] = Field(default_factory=list, max_length=8)
    explanation: str = Field(min_length=1, max_length=1200)
    limitation: str = Field(min_length=1, max_length=800)


class PlanningAdvice(BaseModel):
    model_config = ConfigDict(extra='forbid')
    explanation: str = Field(min_length=1, max_length=1600)
    alternatives: list[PlanningAlternative] = Field(default_factory=list, max_length=3)
    question: str = Field(default='', max_length=400)
