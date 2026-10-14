export type ClassName = string | false | null | undefined;

/**
 * Minimal class-name joiner.
 *
 * Deliberately tiny: no object/array syntax and no dependency, because the
 * codebase only ever needs conditional string concatenation.
 */
export function cx(...values: ClassName[]): string {
  let result = '';
  for (const value of values) {
    if (!value) continue;
    result = result ? `${result} ${value}` : value;
  }
  return result;
}
