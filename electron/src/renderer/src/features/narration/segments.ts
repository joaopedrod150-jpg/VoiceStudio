import type { NarrationSegmentInput } from '@/lib/api/narration';

export interface SegmentRow {
  key: number;
  text: string;
  start: string;
  end: string;
}

export interface SegmentError {
  key: string;
  vars?: Record<string, unknown>;
}

const MIN_SLOT_S = 0.3;

/**
 * Turn the editable rows into API segments. Rows without text are ignored;
 * the first problem found is returned so the form can say what to fix
 * (the backend validates again).
 */
export function buildSegments(rows: SegmentRow[]): {
  segments: NarrationSegmentInput[];
  error: SegmentError | null;
} {
  const parsed: { n: number; text: string; start: number; end: number | null }[] = [];
  for (const [index, row] of rows.entries()) {
    const text = row.text.trim();
    if (!text) continue;
    const start = Number(row.start.replace(',', '.'));
    const end = row.end.trim() === '' ? null : Number(row.end.replace(',', '.'));
    if (!Number.isFinite(start) || start < 0 || (end !== null && !Number.isFinite(end))) {
      return { segments: [], error: { key: 'narration.err_time', vars: { n: index + 1 } } };
    }
    parsed.push({ n: index + 1, text, start, end });
  }
  parsed.sort((a, b) => a.start - b.start);
  for (const [i, seg] of parsed.entries()) {
    const next = parsed[i + 1];
    if (seg.end !== null && seg.end - seg.start < MIN_SLOT_S) {
      return { segments: [], error: { key: 'narration.err_short', vars: { n: seg.n } } };
    }
    if (next && (next.start - seg.start < MIN_SLOT_S || (seg.end !== null && seg.end > next.start))) {
      return { segments: [], error: { key: 'narration.err_overlap', vars: { n: seg.n } } };
    }
  }
  return {
    segments: parsed.map(({ text, start, end }) => ({ text, start, end })),
    error: null,
  };
}
