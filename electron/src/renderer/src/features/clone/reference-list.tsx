import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { LoaderCircleIcon, MicIcon, PlusIcon, XIcon } from 'lucide-react';
import { useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import {
  addProfileReferences,
  deleteProfileReference,
  listProfileReferences,
} from '@/lib/api/profiles';
import { describeError } from '@/lib/api/client';
import { probeAudioDuration } from '@/lib/audio/probe';
import { queryKeys } from '@/lib/query';
import { RecordZone } from './reference-input';

const ACCEPT = 'audio/*,.mp3,.wav,.m4a,.flac,.ogg,.aac,.webm';

/** Seconds of the merged clip the backend builds (services/voice_reference.py). */
export const MERGED_REFERENCE_SECONDS = 15;

export interface PendingRecording {
  file: File;
  seconds: number | null;
}

function fmt(seconds: number | null | undefined): string {
  return seconds == null ? '–' : `${seconds.toFixed(1)}s`;
}

/** Pick several audio files at once, or record more takes. */
function RecordingAdder({
  onFiles,
  disabled,
}: {
  onFiles: (files: File[]) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const id = useId();
  const input = useRef<HTMLInputElement>(null);
  const [recording, setRecording] = useState(false);
  return (
    <div className="flex flex-col gap-2">
      <input
        ref={input}
        id={id}
        type="file"
        multiple
        accept={ACCEPT}
        className="sr-only"
        onChange={(event) => {
          const files = Array.from(event.target.files ?? []);
          event.target.value = '';
          if (files.length) onFiles(files);
        }}
      />
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          variant="secondary"
          size="sm"
          disabled={disabled}
          onClick={() => input.current?.click()}
        >
          <PlusIcon data-icon="inline-start" />
          {t('multiRef.add')}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={disabled}
          onClick={() => setRecording((value) => !value)}
          aria-pressed={recording}
        >
          <MicIcon data-icon="inline-start" />
          {t('clone.record')}
        </Button>
      </div>
      {recording ? (
        <RecordZone
          onAccept={(file) => {
            onFiles([file]);
            setRecording(false);
          }}
        />
      ) : null}
    </div>
  );
}

function Row({
  name,
  seconds,
  note,
  onRemove,
  removeLabel,
  disabled,
}: {
  name: string;
  seconds: number | null;
  note?: string;
  onRemove?: () => void;
  removeLabel: string;
  disabled?: boolean;
}) {
  return (
    <li className="flex min-w-0 items-center gap-2 text-sm">
      <span className="min-w-0 flex-1 truncate">{name}</span>
      {note ? (
        <span className="truncate text-[length:var(--text-caption)] text-destructive">{note}</span>
      ) : null}
      <span className="text-[length:var(--text-caption)] text-muted-foreground tabular-nums">
        {fmt(seconds)}
      </span>
      {onRemove ? (
        <Button
          type="button"
          variant="ghost"
          size="xs"
          disabled={disabled}
          aria-label={removeLabel}
          onClick={onRemove}
        >
          <XIcon />
        </Button>
      ) : null}
    </li>
  );
}

/** Extra recordings collected before a new clone profile is saved. */
export function ExtraRecordings({
  items,
  onChange,
  baseSeconds,
}: {
  items: PendingRecording[];
  onChange: (items: PendingRecording[]) => void;
  baseSeconds: number | null;
}) {
  const { t } = useTranslation();
  const add = async (files: File[]) => {
    const probed = await Promise.all(
      files.map(async (file) => ({ file, seconds: await probeAudioDuration(file) })),
    );
    onChange([...items, ...probed]);
  };
  const total = (baseSeconds ?? 0) + items.reduce((sum, item) => sum + (item.seconds ?? 0), 0);
  return (
    <section className="flex flex-col gap-2 rounded-lg bg-muted/25 p-3">
      <h4 className="text-sm font-medium">{t('multiRef.title')}</h4>
      <p className="text-[length:var(--text-caption)] text-muted-foreground">
        {t('multiRef.hint', { max: MERGED_REFERENCE_SECONDS })}
      </p>
      {items.length > 0 ? (
        <>
          <ul className="flex flex-col gap-1">
            {items.map((item, index) => (
              <Row
                key={`${item.file.name}-${index}`}
                name={item.file.name}
                seconds={item.seconds}
                removeLabel={t('multiRef.remove', { name: item.file.name })}
                onRemove={() => onChange(items.filter((_, i) => i !== index))}
              />
            ))}
          </ul>
          <p className="text-[length:var(--text-caption)] text-muted-foreground" role="status">
            {t('multiRef.total', { seconds: total.toFixed(1), count: items.length + 1 })}
          </p>
        </>
      ) : null}
      <RecordingAdder onFiles={(files) => void add(files)} />
    </section>
  );
}

/** Source recordings of a saved clone: list, add more, remove. */
export function SavedRecordings({
  profileId,
  disabled,
}: {
  profileId: string;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const key = [...queryKeys.profiles, profileId, 'references'];
  const refs = useQuery({ queryKey: key, queryFn: () => listProfileReferences(profileId) });
  const done = async () => {
    await client.invalidateQueries({ queryKey: queryKeys.profiles });
  };
  const fail = (err: unknown) => toast.error(t('multiRef.error', { message: describeError(err) }));
  const add = useMutation({
    mutationFn: (files: File[]) => addProfileReferences(profileId, files),
    onSuccess: done,
    onError: fail,
  });
  const remove = useMutation({
    mutationFn: (refId: string) => deleteProfileReference(profileId, refId),
    onSuccess: done,
    onError: fail,
  });
  const busy = add.isPending || remove.isPending;
  const list = refs.data?.references ?? [];
  return (
    <div className="flex flex-col gap-2 border-t border-border/50 pt-2">
      <h4 className="text-sm font-medium">{t('multiRef.title')}</h4>
      <p className="text-[length:var(--text-caption)] text-muted-foreground">
        {t('multiRef.hint', { max: MERGED_REFERENCE_SECONDS })}
      </p>
      {list.length > 0 ? (
        <>
          <ul className="flex flex-col gap-1">
            {list.map((ref) => (
              <Row
                key={ref.id}
                name={ref.original_name}
                seconds={ref.duration}
                note={
                  ref.status === 'rejected'
                    ? t('multiRef.skipped', { reason: ref.reason })
                    : undefined
                }
                removeLabel={t('multiRef.remove', { name: ref.original_name })}
                disabled={disabled || busy || list.length <= 1}
                onRemove={() => remove.mutate(ref.id)}
              />
            ))}
          </ul>
          <p className="text-[length:var(--text-caption)] text-muted-foreground" role="status">
            {t('multiRef.total', {
              seconds: (refs.data?.source_seconds ?? 0).toFixed(1),
              count: list.length,
            })}
          </p>
        </>
      ) : null}
      {busy ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
          <LoaderCircleIcon className="size-4 animate-spin motion-reduce:animate-none" />
          {t('multiRef.processing')}
        </p>
      ) : null}
      <RecordingAdder disabled={disabled || busy} onFiles={(files) => add.mutate(files)} />
    </div>
  );
}
