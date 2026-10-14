import type { OutputSlotSpec, InputSlotSpec, MethodOption, ParameterSpec, ParameterValue } from './workflowTypes';

export function optionKey(value: ParameterValue): string {
  return `${typeof value}:${String(value)}`;
}

export function decodeOption(spec: ParameterSpec, key: string): ParameterValue {
  return spec.options.find((option) => optionKey(option.value) === key)?.value ?? key;
}

export function conditionMatches(
  condition: Record<string, ParameterValue[]>,
  values: Record<string, ParameterValue>
): boolean {
  return Object.entries(condition).every(([parameterId, allowed]) => {
    const current = values[parameterId];
    return (
      current !== undefined &&
      current !== null &&
      allowed.some((item) => optionKey(item) === optionKey(current))
    );
  });
}

export function parameterValues(
  method: MethodOption,
  selected: Record<string, ParameterValue>
): Record<string, ParameterValue> {
  return Object.fromEntries(
    method.parameters
      .map((spec): [string, ParameterValue] => [spec.id, Object.hasOwn(selected, spec.id) ? selected[spec.id] : spec.default])
      .filter((entry) => entry[1] !== null)
  );
}

export function inputRequired(
  slot: InputSlotSpec,
  values: Record<string, ParameterValue>
): boolean {
  return (
    slot.required ||
    (Object.keys(slot.required_when ?? {}).length > 0 && conditionMatches(slot.required_when ?? {}, values)) ||
    (slot.required_when_any ?? []).some(condition => conditionMatches(condition, values))
  );
}

export function visibleParameters(
  method: MethodOption,
  selected: Record<string, ParameterValue>
): ParameterSpec[] {
  const values = parameterValues(method, selected);
  return method.parameters.filter((spec) => conditionMatches(spec.visible_when ?? {}, values));
}

export function parameterEnabled(spec: ParameterSpec, values: Record<string, ParameterValue>): boolean {
  return conditionMatches(spec.visible_when ?? {}, values) && conditionMatches(spec.enabled_when ?? {}, values);
}

export function parameterRequired(spec: ParameterSpec, values: Record<string, ParameterValue>): boolean {
  return parameterEnabled(spec, values) && (spec.required ||
    (Object.keys(spec.required_when ?? {}).length > 0 && conditionMatches(spec.required_when ?? {}, values)));
}

/** Share execution's active-value semantics without losing zero, false or omission. */
export function effectiveParameterValues(method: {parameters: ParameterSpec[]}, selected: Record<string, ParameterValue>): Record<string, ParameterValue> {
  const values = Object.fromEntries(method.parameters.map(p => [p.id, Object.hasOwn(selected, p.id) ? selected[p.id] : p.default]));
  return Object.fromEntries(method.parameters.filter(p => parameterEnabled(p, values) && values[p.id] !== null).map(p => [p.id, values[p.id]]));
}

export function activeOutputSlots(method: {parameters: ParameterSpec[]; output_slots?: OutputSlotSpec[]}, selected: Record<string, ParameterValue>): OutputSlotSpec[] {
  const values = effectiveParameterValues(method, selected);
  return (method.output_slots ?? []).filter(slot => conditionMatches(slot.generated_when ?? {}, values));
}

/** required_when is not an applicability rule. Collapse conditionally optional
 * inputs by default, but always allow revealing them and retain existing bindings. */
export function displayedInputSlots(method: {parameters: ParameterSpec[]; input_slots?: InputSlotSpec[]}, selected: Record<string, ParameterValue>, bindings: Record<string, unknown> = {}, showConditional = false) {
  const values = effectiveParameterValues(method, selected);
  return (method.input_slots ?? []).map(slot => {
    const requiredNow = inputRequired(slot, values);
    const conditionalOptional = !requiredNow && (Object.keys(slot.required_when ?? {}).length > 0 || (slot.required_when_any?.length ?? 0) > 0);
    return {...slot, requiredNow, conditionalOptional};
  }).filter(slot => !slot.conditionalOptional || showConditional || Boolean(bindings[slot.id]));
}
