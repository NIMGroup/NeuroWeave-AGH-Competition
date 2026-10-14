import { compatible, nodeMethod } from './artifactSemantics';
import { ui } from '../../lib/libraryText';
import {
  displayedInputSlots,
  activeOutputSlots
} from '../workflows/workflowConditions';
import type { Draft, InputPort, Method, Node } from './libraryApi';
import {
  linkSources,
  sharedGroup,
  displayBindings,
  conditionDescription,
  disconnectInput
} from './pipelineGraph';
import { button, field } from './pipelineEditorStyles';
import { stageCondition } from './pipelineCopy';
import { PipelineMethodParameters } from './PipelineMethodParameters';

type Props = {
  draft: Draft;
  definitions: Record<string, Method>;
  selectedNode?: Node;
  showConditionalInputs: boolean;
  busy: boolean;
  change: (draft: Draft) => void;
  update: (id: string, next: Partial<Node>) => void;
  connect: (from: string, output: string, to: string, input: string) => void;
  detach: (port: InputPort) => void;
  onRemove: (id: string) => void;
};
/** Conditional ports and parameter controls project the registered method contract. */
export function PipelineNodeInspector({
  draft,
  definitions,
  selectedNode,
  showConditionalInputs,
  busy,
  change,
  update,
  connect,
  detach,
  onRemove
}: Props) {
  const selectedMethod = selectedNode && definitions[selectedNode.method_id];
  return (
    <aside
      aria-label={ui('选中步骤参数')}
      className="min-h-0 h-full overflow-auto rounded border border-line p-3"
    >
      {!selectedNode ? (
        <p className="text-sm text-subtle">
          {ui('选中画布节点，在这里查看端口和调整参数。')}
        </p>
      ) : (
        <fieldset disabled={busy} className="min-w-0">
          <h3 className="break-words text-sm font-semibold">
            {selectedMethod?.label ?? selectedNode.method_id}
          </h3>
          {!!Object.keys(selectedNode.when??{}).length&&<p className="mt-2 text-xs text-warning">{ui('执行条件：{0}',stageCondition(selectedNode,draft))}</p>}
          <button
            className={button + ' my-2'}
            onClick={() => onRemove(selectedNode.id)}
          >
            {ui('移除节点及连线')}
          </button>
          {selectedMethod ? (
            <>
              <details className="mb-3 text-xs">
                <summary className="cursor-pointer">{ui('方法说明')}</summary>
                <p className="whitespace-pre-wrap break-words text-subtle">
                  {selectedMethod.description}
                </p>
              </details>
              {!selectedMethod.runtime_status.available && (
                <p className="mb-3 text-xs text-warning">
                  {ui('当前环境：')}
                  {selectedMethod.runtime_status.reason ?? ui('不可用')}
                  {ui('。可编排保存，执行前需满足环境。')}
                </p>
              )}
              <h4 className="mb-2 text-xs font-semibold">{ui('输入连接')}</h4>
              {displayedInputSlots(
                selectedMethod,
                selectedNode.parameters,
                displayBindings(selectedNode, draft.shared_inputs ?? []),
                showConditionalInputs
              ).map((slot) => (
                <label key={slot.id} className="mb-3 block text-xs">
                  {slot.label} · {slot.type} ·{' '}
                  {slot.requiredNow ? ui('必需') : ui('可选')}
                  {slot.conditionalOptional && (
                    <span className="block text-subtle">
                      {ui('当前参数下非必需；已有连接会保留。')}
                    </span>
                  )}
                  {sharedGroup(draft.shared_inputs ?? [], {
                    step_id: selectedNode.id,
                    input_slot_id: slot.id
                  }) && (
                    <span className="my-1 block rounded border border-purple-400 p-2 text-purple-600">
                      {ui('↔ 共享输入：只需选择一次文件')}
                      <button
                        type="button"
                        className={button + ' ml-2'}
                        onClick={() =>
                          detach({
                            step_id: selectedNode.id,
                            input_slot_id: slot.id
                          })
                        }
                      >
                        {ui('解除共享')}
                      </button>
                    </span>
                  )}
                  {slot.cardinality === 'many' &&
                    selectedNode.inputs[slot.id] && (
                      <div className="my-1 rounded border border-line p-2">
                        {ui('文件顺序（继续连接会追加）')}
                        {linkSources(selectedNode.inputs[slot.id]).map(
                          (l, i) => (
                            <div
                              key={l.step_id + '/' + l.output_slot_id}
                              className="flex items-center gap-1"
                            >
                              <span className="min-w-0 flex-1 break-all">
                                {i + 1}. {l.step_id} / {l.output_slot_id}
                              </span>
                              <button
                                className={button}
                                onClick={() => {
                                  const left = linkSources(
                                    selectedNode.inputs[slot.id]
                                  ).filter((_, j) => j !== i);
                                  const inputs = { ...selectedNode.inputs };
                                  if (left.length)
                                    inputs[slot.id] = {
                                      ...left[0],
                                      additional_sources: left.slice(1)
                                    };
                                  else delete inputs[slot.id];
                                  update(selectedNode.id, { inputs });
                                }}
                              >
                                {ui('移除')}
                              </button>
                            </div>
                          )
                        )}
                      </div>
                    )}
                  <select
                    disabled={
                      !!sharedGroup(draft.shared_inputs ?? [], {
                        step_id: selectedNode.id,
                        input_slot_id: slot.id
                      })
                    }
                    className={field}
                    value={
                      selectedNode.inputs[slot.id]
                        ? `${selectedNode.inputs[slot.id].step_id}/${selectedNode.inputs[slot.id].output_slot_id}`
                        : ''
                    }
                    onChange={(e) => {
                      if (!e.target.value)
                        change({
                          ...draft,
                          steps: disconnectInput(
                            draft.steps,
                            selectedNode.id,
                            slot.id
                          )
                        });
                      else {
                        const [id, output] = e.target.value.split('/');
                        connect(id, output, selectedNode.id, slot.id);
                      }
                    }}
                  >
                    <option value="">{ui('整体输入（运行前选择文件）')}</option>
                    {draft.steps
                      .filter((s) => s.id !== selectedNode.id)
                      .flatMap(
                        (s) =>
                          (definitions[s.method_id]
                            ? nodeMethod(
                                s,
                                definitions,
                                draft.steps
                              )!.output_slots.filter(
                                (o) =>
                                  compatible(o.type, slot.type) &&
                                  (activeOutputSlots(
                                    definitions[s.method_id],
                                    s.parameters
                                  ).some((a) => a.id === o.id) ||
                                    (selectedNode.inputs[slot.id]?.step_id ===
                                      s.id &&
                                      selectedNode.inputs[slot.id]
                                        ?.output_slot_id === o.id))
                              )
                            : []
                          ).map((o) => (
                            <option
                              key={s.id + '/' + o.id}
                              value={s.id + '/' + o.id}
                            >
                              {definitions[s.method_id].label} ·{' '}
                              {s.id.slice(-4)} / {o.label}
                            </option>
                          )) ?? []
                      )}
                  </select>
                  <span className="block break-words text-subtle">
                    {slot.description}
                  </span>
                  {!!Object.keys(slot.required_when ?? {}).length && (
                    <span className="block text-subtle">
                      {ui('条件必需：')}
                      {conditionDescription(selectedMethod, slot.required_when)}
                    </span>
                  )}
                </label>
              ))}
              <PipelineMethodParameters method={selectedMethod} node={selectedNode} disabled={busy}
                onChange={(id, value) => update(selectedNode.id, {
                  parameters: { ...selectedNode.parameters, [id]: value }
                })}/>
            </>
          ) : (
            <p className="text-xs">{ui('正在加载参数定义…')}</p>
          )}
        </fieldset>
      )}
    </aside>
  );
}
