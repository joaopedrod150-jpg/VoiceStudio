import { apiFetch, apiJson, apiPath } from './client';

export interface NarrationSegmentInput {
  text: string;
  start: number;
  end?: number | null;
}

export interface NarrationReportSegment {
  id: string;
  start: number;
  end: number;
  audio_rate: number;
  status: string;
  natural_s: number;
  trimmed: boolean;
  overflow_s: number;
}

export interface NarrationJob {
  id: string;
  status: 'queued' | 'running' | 'done' | 'error';
  progress: { done: number; total: number };
  error: string | null;
  report: { segments: NarrationReportSegment[]; needs_trim: boolean; video_seconds: number } | null;
}

export interface CreateNarrationInput {
  video: File;
  /** Whole-video script; ignored when `segments` is non-empty. */
  script: string;
  segments: NarrationSegmentInput[];
  profileId: string;
  language?: string;
  keepOriginal: boolean;
  originalVolume: number;
}

export function createNarrationJob(input: CreateNarrationInput): Promise<NarrationJob> {
  const form = new FormData();
  form.append('video', input.video, input.video.name);
  form.append('script', input.script);
  if (input.segments.length) form.append('segments', JSON.stringify(input.segments));
  if (input.profileId) form.append('profile_id', input.profileId);
  form.append('language', input.language || 'Auto');
  form.append('keep_original', String(input.keepOriginal));
  form.append('original_volume', String(input.originalVolume));
  return apiJson<NarrationJob>('/narration/jobs', { method: 'POST', body: form });
}

export function getNarrationJob(id: string, signal?: AbortSignal): Promise<NarrationJob> {
  return apiJson<NarrationJob>(`/narration/jobs/${encodeURIComponent(id)}`, { signal });
}

export function narrationVideoUrl(id: string): string {
  return apiPath(`/narration/jobs/${encodeURIComponent(id)}/video`);
}

export async function deleteNarrationJob(id: string): Promise<void> {
  await apiFetch(`/narration/jobs/${encodeURIComponent(id)}`, { method: 'DELETE' });
}
