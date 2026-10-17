import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, vars?: Record<string, unknown>) =>
      vars ? `${key}:${Object.values(vars).join('|')}` : key,
  }),
}));
vi.mock('sonner', () => ({ toast: { error: vi.fn() } }));
vi.mock('@/lib/audio/probe', () => ({
  probeAudioDuration: async (file: File) => (file.name === 'long.wav' ? 7 : 3),
}));
vi.mock('./reference-input', () => ({ RecordZone: () => null }));
import { ExtraRecordings, type PendingRecording } from './reference-list';

afterEach(cleanup);

const clip = (name: string) => new File(['a'], name, { type: 'audio/wav' });

it('lists added recordings with durations, the total, and lets one be removed', async () => {
  const onChange = vi.fn();
  const { container, rerender } = render(
    <ExtraRecordings items={[]} onChange={onChange} baseSeconds={5} />,
  );
  fireEvent.change(container.querySelector('input[type="file"]')!, {
    target: { files: [clip('a.wav'), clip('long.wav')] },
  });
  await vi.waitFor(() => expect(onChange).toHaveBeenCalledOnce());
  const added = onChange.mock.calls[0][0] as PendingRecording[];
  expect(added.map((item) => [item.file.name, item.seconds])).toEqual([
    ['a.wav', 3],
    ['long.wav', 7],
  ]);

  rerender(<ExtraRecordings items={added} onChange={onChange} baseSeconds={5} />);
  expect(screen.getByText('a.wav')).toBeTruthy();
  expect(screen.getByText('7.0s')).toBeTruthy();
  // 5 s base + 3 + 7 across 3 recordings.
  expect(screen.getByRole('status').textContent).toBe('multiRef.total:15.0|3');

  fireEvent.click(screen.getByLabelText('multiRef.remove:a.wav'));
  expect((onChange.mock.calls[1][0] as PendingRecording[]).map((i) => i.file.name)).toEqual([
    'long.wav',
  ]);
});
