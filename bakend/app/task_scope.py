"""Distinguish explicit reuse of existing artifacts from requests to produce them."""
from __future__ import annotations

import re


ARTIFACT_PRODUCERS = {
    'dwi.five_tt': r'5tt(?:gen)?|five[- _]tissue|五组织(?:分割)?',
    'dwi.fod': r'fod|纤维方向分布',
    'dwi.response': r'响应函数|response functions?',
    'dwi.tractography': r'纤维追踪结果|tractogram|streamlines',
}
_REUSE = re.compile(r'已有|现有|已经有|已完成|已生成|复用|沿用|existing|already (?:have|generated|computed)|reuse|provided', re.I)
_ACTION = re.compile(r'(?:重新|重做|重新进行)?(?:生成|计算|估计|重建|分割)|regenerate|recompute|generate|compute|estimate|rebuild', re.I)
_NEGATION = re.compile(r'不要|无需|不必|不用|不需要|do not|don.t|without|no need', re.I)
_ARTIFACT = re.compile('|'.join(ARTIFACT_PRODUCERS.values())+r'|\bdwi\b', re.I)


def existing_input_capabilities(message: str) -> set[str]:
    """Only explicit reuse/exclusion constrains routing; file presence alone never does.

    A separate explicit regeneration request wins over a mention of old data.
    Unknown phrasing is left to semantic normalization and normal input validation.
    """
    reuse: set[str] = set()
    requested: set[str] = set()
    for clause in re.split(r'[，,。;；!?！？\n]|\b(?:but|then)\b|但是|然后', message.lower()):
        for capability, pattern in ARTIFACT_PRODUCERS.items():
            for match in re.finditer(pattern, clause, re.I):
                before, after = clause[:match.start()], clause[match.end():]
                earlier = list(_ARTIFACT.finditer(before))
                action_prefix = before[earlier[-1].end():] if earlier else before
                actions = list(_ACTION.finditer(action_prefix))
                action = actions[-1] if actions else None
                negated = bool(_NEGATION.search(action_prefix))
                if action and len(action_prefix)-action.end() <= 32 and not negated:
                    requested.add(capability)
                elif not negated and re.search(r'(?:重新进行|重新做|重做|重新)\s*$', action_prefix) and re.match(r'\s*(?:计算|估计|生成|分割)', after):
                    requested.add(capability)
                elif (re.match(r'\s*(?:需要)?(?:重新|重做|再生成)|\s+(?:must be |should be )?(?:regenerated|recomputed)', after)
                      and not _ARTIFACT.search(re.split(r'并|以及|然后|\band\b|\bthen\b', after, maxsplit=1)[0])):
                    requested.add(capability)
                elif _REUSE.search(before) or negated or re.match(r'\s*(?:已存在|已生成|已完成|不需要|无需|is available|already exists)', after):
                    reuse.add(capability)
    return reuse-requested
