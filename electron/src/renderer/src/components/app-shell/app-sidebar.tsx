import { Link, useRouterState } from '@tanstack/react-router';
import {
  AudioLinesIcon,
  BookOpenIcon,
  ClapperboardIcon,
  FilmIcon,
  FingerprintIcon,
  MicIcon,
  PackageIcon,
  SettingsIcon,
  WandSparklesIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import { isMac } from '../bridge';
import { ThemeToggle } from './theme-toggle';

type Destination = {
  to: '/clone' | '/design' | '/dub' | '/narration' | '/transcriptions' | '/audiobook' | '/settings';
  /** Search-free path prefix that marks the item active. */
  match: string;
  /** Prefixes that belong to another item and must not mark this one active. */
  exclude?: string;
  label: string;
  icon: typeof FingerprintIcon;
};

const tools: Destination[] = [
  { to: '/clone', match: '/clone', label: 'appNav.clone', icon: FingerprintIcon },
  { to: '/design', match: '/design', label: 'appNav.design', icon: WandSparklesIcon },
  { to: '/dub', match: '/dub', label: 'appNav.dub', icon: FilmIcon },
  { to: '/narration', match: '/narration', label: 'appNav.narration', icon: ClapperboardIcon },
  { to: '/transcriptions', match: '/transcriptions', label: 'appNav.transcribe', icon: MicIcon },
  { to: '/audiobook', match: '/audiobook', label: 'appNav.audiobook', icon: BookOpenIcon },
];

const models = {
  to: '/settings',
  match: '/settings/models',
  label: 'appNav.models',
  icon: PackageIcon,
} satisfies Destination;
const settings = {
  to: '/settings',
  match: '/settings',
  exclude: '/settings/models',
  label: 'appNav.settings',
  icon: SettingsIcon,
} satisfies Destination;

function isActive(pathname: string, { match, exclude }: Destination): boolean {
  const inside = (prefix: string) => pathname === prefix || pathname.startsWith(`${prefix}/`);
  return inside(match) && !(exclude && inside(exclude));
}

function SidebarLink({ destination, pathname }: { destination: Destination; pathname: string }) {
  const { t } = useTranslation();
  const Icon = destination.icon;
  const active = isActive(pathname, destination);
  // Models lives under Settings: link straight to its page, not the last-used tab.
  const to = destination === models ? '/settings/models' : destination.to;
  return (
    <Link
      to={to}
      aria-current={active ? 'page' : undefined}
      className={cn(
        'app-no-drag group flex w-full flex-col items-center gap-1 rounded-(--control-radius) px-0.5 py-2 text-[11px] leading-none font-medium text-muted-foreground outline-none transition-colors',
        'hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring',
        active && 'bg-accent text-foreground',
      )}
    >
      <Icon
        className={cn('size-5 transition-colors', active && 'text-primary')}
        aria-hidden="true"
      />
      <span className="max-w-full truncate">{t(destination.label)}</span>
    </Link>
  );
}

/**
 * The one navigation surface: a fixed, compact column on the left with the
 * main tools. Everything else (voice library, engine status, support links)
 * lives inside the tools or Settings, so the centre shows only the open tool.
 */
export function AppSidebar() {
  const { t } = useTranslation();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const mac = isMac();
  return (
    <nav
      aria-label={t('nav.primary')}
      data-slot="app-sidebar"
      className="flex h-full w-[88px] shrink-0 flex-col border-r border-border/60 bg-sidebar"
    >
      <div
        className={cn(
          'workspace-titlebar flex shrink-0 items-center justify-center',
          mac && 'items-end pb-2',
        )}
      >
        <Link
          to="/clone"
          aria-label={t('app.name')}
          className="app-no-drag rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <AudioLinesIcon className="size-7 text-primary" aria-hidden="true" />
        </Link>
      </div>
      <div className="studio-scrollbar flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto px-2 py-1">
        {tools.map((destination) => (
          <SidebarLink key={destination.to} destination={destination} pathname={pathname} />
        ))}
      </div>
      <div className="flex shrink-0 flex-col items-center gap-1 border-t border-border/60 px-2 py-2">
        <SidebarLink destination={models} pathname={pathname} />
        <SidebarLink destination={settings} pathname={pathname} />
        <ThemeToggle />
      </div>
    </nav>
  );
}
