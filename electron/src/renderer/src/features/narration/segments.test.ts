import { expect, it } from 'vitest';
import { buildSegments, type SegmentRow } from './segments';

const row = (text: string, start: string, end = '', key = 1): SegmentRow => ({ key, text, start, end });

it('sorts segments by start, ignores empty rows and accepts decimal commas', () => {
  const { segments, error } = buildSegments([
    row('second', '4,5', '', 1),
    row('', '2', '', 2),
    row('first', '0', '4', 3),
  ]);
  expect(error).toBeNull();
  expect(segments).toEqual([
    { text: 'first', start: 0, end: 4 },
    { text: 'second', start: 4.5, end: null },
  ]);
});

it.each([
  [[row('a', 'x')], 'narration.err_time'],
  [[row('a', '-1')], 'narration.err_time'],
  [[row('a', '0', '0.1')], 'narration.err_short'],
  [[row('a', '0', '5', 1), row('b', '4', '', 2)], 'narration.err_overlap'],
  [[row('a', '0', '', 1), row('b', '0.1', '', 2)], 'narration.err_overlap'],
])('reports the first problem (%#)', (rows, key) => {
  expect(buildSegments(rows).error?.key).toBe(key);
});

it('returns no segments for an empty form', () => {
  expect(buildSegments([row('', '0')])).toEqual({ segments: [], error: null });
});
