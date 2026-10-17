import { ClapperboardIcon, DownloadIcon, LoaderCircleIcon, PlusIcon, XIcon } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
// @ts-expect-error shared JSX component has no declaration file
import VoiceSelector from '@shared/components/VoiceSelector';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Progress } from '@/components/ui/progress';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { useProfiles } from '@/hooks/use-profiles';
import { describeError } from '@/lib/api/client';
import {
  createNarrationJob,
  deleteNarrationJob,
  getNarrationJob,
  narrationVideoUrl,
  type NarrationJob,
} from '@/lib/api/narration';
import { beginAppActivity } from '@/lib/app-activity';
import { buildSegments, type SegmentRow } from './segments';

const POLL_MS = 1000;
let rowCounter = 0;
const newRow = (start = 0): SegmentRow => ({ key: ++rowCounter, text: '', start: String(start), end: '' });

export function NarrationPage() {
  const { t } = useTranslation();
  const profiles = useProfiles();
  const picker = useRef<HTMLInputElement>(null);
  const player = useRef<HTMLVideoElement>(null);
  const [video, setVideo] = useState<File | null>(null);
  const [videoUrl, setVideoUrl] = useState('');
  const [timed, setTimed] = useState(false);
  const [script, setScript] = useState('');
  const [rows, setRows] = useState<SegmentRow[]>([newRow()]);
  const [voice, setVoice] = useState('');
  const [keepOriginal, setKeepOriginal] = useState(false);
  const [volume, setVolume] = useState(25);
  const [job, setJob] = useState<NarrationJob | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!video) return setVideoUrl('');
    const url = URL.createObjectURL(video);
    setVideoUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [video]);

  const reset = () => {
    setJob(null);
    setError('');
  };
  const ingest = (file: File | undefined) => {
    if (!file) return;
    if (!file.type.startsWith('video/') && !/\.(mp4|m4v|mov|mkv|webm|avi)$/i.test(file.name)) {
      setError(t('narration.not_video'));
      return;
    }
    reset();
    setVideo(file);
  };
  const patchRow = (key: number, patch: Partial<SegmentRow>) =>
    setRows((current) => current.map((row) => (row.key === key ? { ...row, ...patch } : row)));

  const built = timed ? buildSegments(rows) : { segments: [], error: null };
  const hasText = timed ? built.segments.length > 0 : script.trim().length > 0;
  const canRun = Boolean(video && hasText && !built.error && !busy);

  const run = async () => {
    if (!video || !canRun) return;
    setBusy(true);
    reset();
    const finish = beginAppActivity('synthesis');
    let current: NarrationJob | null = null;
    try {
      current = await createNarrationJob({
        video,
        script,
        segments: built.segments,
        profileId: voice && !voice.includes(':') ? voice : '',
        keepOriginal,
        originalVolume: volume / 100,
      });
      setJob(current);
      while (current.status === 'queued' || current.status === 'running') {
        await new Promise((resolve) => setTimeout(resolve, POLL_MS));
        current = await getNarrationJob(current.id);
        setJob(current);
      }
      if (current.status === 'error') setError(current.error ?? t('narration.failed'));
    } catch (e) {
      setError(describeError(e));
    } finally {
      finish();
      setBusy(false);
    }
  };

  const done = job?.status === 'done';
  const pct = job && job.progress.total ? (job.progress.done / job.progress.total) * 100 : 0;

  return (
    <div className="mx-auto h-full w-full max-w-3xl space-y-6 overflow-y-auto p-6">
      <header>
        <h2 className="flex items-center gap-2 text-lg font-semibold">
          <ClapperboardIcon className="size-5" aria-hidden="true" />
          {t('narration.title')}
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">{t('narration.subtitle')}</p>
      </header>

      <section className="space-y-3" aria-label={t('narration.video')}>
        <input
          ref={picker}
          type="file"
          accept="video/*,.mp4,.m4v,.mov,.mkv,.webm,.avi"
          className="sr-only"
          aria-label={t('narration.video')}
          onChange={(event) => {
            ingest(event.target.files?.[0]);
            event.target.value = '';
          }}
        />
        {videoUrl ? (
          <video
            ref={player}
            src={videoUrl}
            controls
            className="max-h-80 w-full rounded-lg bg-black"
            aria-label={t('narration.preview')}
          />
        ) : (
          <Button
            variant="outline"
            className="h-auto min-h-32 w-full whitespace-normal border-dashed p-4"
            onClick={() => picker.current?.click()}
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => {
              event.preventDefault();
              ingest(event.dataTransfer.files[0]);
            }}
          >
            {t('narration.drop_video')}
          </Button>
        )}
        {video ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <span className="min-w-0 flex-1 truncate">{video.name}</span>
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => picker.current?.click()}>
              {t('narration.change_video')}
            </Button>
          </div>
        ) : null}
      </section>

      <section className="space-y-3" aria-label={t('narration.script')}>
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-sm font-medium">{t('narration.script')}</h3>
          <div className="flex items-center gap-2 text-sm">
            <Switch checked={timed} onCheckedChange={setTimed} aria-label={t('narration.timed')} />
            <span>{t('narration.timed')}</span>
          </div>
        </div>
        {timed ? (
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">{t('narration.timed_hint')}</p>
            {rows.map((row, index) => (
              <div key={row.key} className="grid grid-cols-[5rem_5rem_1fr_auto] items-start gap-2">
                <Input
                  inputMode="decimal"
                  value={row.start}
                  aria-label={t('narration.start')}
                  placeholder={t('narration.start')}
                  onChange={(event) => patchRow(row.key, { start: event.target.value })}
                />
                <Input
                  inputMode="decimal"
                  value={row.end}
                  aria-label={t('narration.end')}
                  placeholder={t('narration.end')}
                  onChange={(event) => patchRow(row.key, { end: event.target.value })}
                />
                <Textarea
                  rows={2}
                  value={row.text}
                  aria-label={t('narration.segment_text', { n: index + 1 })}
                  placeholder={t('narration.segment_placeholder')}
                  onChange={(event) => patchRow(row.key, { text: event.target.value })}
                />
                <div className="flex flex-col gap-1">
                  <Button
                    variant="ghost"
                    size="xs"
                    disabled={!player.current}
                    onClick={() =>
                      patchRow(row.key, { start: (player.current?.currentTime ?? 0).toFixed(1) })
                    }
                  >
                    {t('narration.use_current_time')}
                  </Button>
                  <Button
                    variant="ghost"
                    size="xs"
                    disabled={rows.length <= 1}
                    aria-label={t('narration.remove_segment', { n: index + 1 })}
                    onClick={() => setRows((current) => current.filter((r) => r.key !== row.key))}
                  >
                    <XIcon />
                  </Button>
                </div>
              </div>
            ))}
            <Button
              variant="secondary"
              size="sm"
              onClick={() =>
                setRows((current) => [
                  ...current,
                  newRow(Math.round((player.current?.currentTime ?? 0) * 10) / 10),
                ])
              }
            >
              <PlusIcon data-icon="inline-start" />
              {t('narration.add_segment')}
            </Button>
            {built.error ? (
              <p className="text-sm text-destructive" role="alert">
                {t(built.error.key, built.error.vars)}
              </p>
            ) : null}
          </div>
        ) : (
          <Textarea
            rows={6}
            value={script}
            aria-label={t('narration.script')}
            placeholder={t('narration.script_placeholder')}
            onChange={(event) => setScript(event.target.value)}
          />
        )}
      </section>

      <section className="space-y-3" aria-label={t('narration.voice')}>
        <h3 className="text-sm font-medium">{t('narration.voice')}</h3>
        <VoiceSelector
          value={voice}
          onChange={setVoice}
          profiles={profiles.data ?? []}
          gallery={false}
          menuPortal
          ariaLabel={t('narration.voice')}
          placeholder={t('convert.pick_voice')}
        />
        <div className="flex items-center gap-3 text-sm">
          <Switch
            checked={keepOriginal}
            onCheckedChange={setKeepOriginal}
            aria-label={t('narration.keep_original')}
          />
          <span>{t('narration.keep_original')}</span>
        </div>
        {keepOriginal ? (
          <div className="flex items-center gap-3 text-sm">
            <Label htmlFor="narration-volume">{t('narration.original_volume')}</Label>
            <input
              id="narration-volume"
              type="range"
              min={0}
              max={100}
              value={volume}
              onChange={(event) => setVolume(Number(event.target.value))}
            />
            <span className="tabular-nums">{volume}%</span>
          </div>
        ) : null}
      </section>

      <div className="space-y-3">
        <Button disabled={!canRun} onClick={() => void run()}>
          {busy ? (
            <LoaderCircleIcon className="animate-spin motion-reduce:animate-none" />
          ) : (
            <ClapperboardIcon />
          )}
          {busy ? t('narration.generating') : t('narration.generate')}
        </Button>
        {busy && job ? (
          <Progress value={pct}>
            <span className="text-xs text-muted-foreground" role="status">
              {t('narration.progress', { done: job.progress.done, total: job.progress.total })}
            </span>
          </Progress>
        ) : null}
        {error ? (
          <p className="text-sm text-destructive" role="alert">
            {error}
          </p>
        ) : null}
      </div>

      {done && job ? (
        <section className="space-y-3" aria-label={t('narration.result')}>
          <h3 className="text-sm font-medium">{t('narration.result')}</h3>
          <video
            src={narrationVideoUrl(job.id)}
            controls
            className="max-h-80 w-full rounded-lg bg-black"
            aria-label={t('narration.result')}
          />
          {job.report?.needs_trim ? (
            <p className="text-sm text-muted-foreground" role="note">
              {t('narration.trimmed_warning')}
            </p>
          ) : null}
          <div className="flex gap-2">
            <Button
              render={<a href={narrationVideoUrl(job.id)} download="narrated-video" />}
              variant="secondary"
            >
              <DownloadIcon data-icon="inline-start" />
              {t('narration.download')}
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                void deleteNarrationJob(job.id).catch(() => undefined);
                reset();
              }}
            >
              {t('narration.discard')}
            </Button>
          </div>
        </section>
      ) : null}
    </div>
  );
}
