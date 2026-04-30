/**
 * Left sidebar with navigation links to all pages.
 *
 * Uses NavLink from react-router-dom so the active page gets a
 * highlighted style automatically. The Incidents link shows a badge
 * with the open-incident count, colored by the highest active severity.
 */

import { NavLink } from 'react-router-dom';
import {
  LayoutDashboard,
  AlertTriangle,
  ScrollText,
  Shield,
  Search,
} from 'lucide-react';
import type { ComponentType } from 'react';
import { HealthIndicator } from './HealthIndicator';
import { useStatsSummary } from '../hooks/useStatsSummary';


interface NavItem {
  to: string;
  label: string;
  Icon: ComponentType<{ size?: number; className?: string }>;
}

const NAV_ITEMS: NavItem[] = [
  { to: '/',          label: 'Dashboard', Icon: LayoutDashboard },
  { to: '/incidents', label: 'Incidents', Icon: AlertTriangle },
  { to: '/events',    label: 'Events',    Icon: ScrollText },
  { to: '/rules',     label: 'Rules',     Icon: Shield },
  { to: '/search',    label: 'Search',    Icon: Search },
];

/**
 * Pick the highest-severity color among open incidents.
 * Returns null if there are no open incidents.
 */
function pickBadgeColor(
  bySeverity: Record<string, number> | undefined,
): string | null {
  if (!bySeverity) return null;
  if ((bySeverity.critical ?? 0) > 0) return 'bg-red-500';
  if ((bySeverity.high ?? 0) > 0) return 'bg-orange-500';
  if ((bySeverity.medium ?? 0) > 0) return 'bg-amber-500';
  if ((bySeverity.low ?? 0) > 0) return 'bg-slate-500';
  return null;
}


export function Sidebar() {
  const { data: stats } = useStatsSummary();
  const openCount = stats?.incidents_open ?? 0;
  const badgeColor = pickBadgeColor(stats?.incidents_by_severity);

  return (
    <aside className="w-60 shrink-0 border-r border-[var(--color-border)] bg-[var(--color-surface)] flex flex-col">
      <div className="px-5 py-5 border-b border-[var(--color-border)]">
        <div className="text-base font-semibold tracking-wide">
          SIEM Platform
        </div>
        <div className="text-xs text-[var(--color-muted)] mt-0.5">
          Web Attack Detection
        </div>
      </div>

      <nav className="flex-1 px-2 py-4 space-y-1">
        {NAV_ITEMS.map(({ to, label, Icon }) => {
          const showBadge = to === '/incidents' && openCount > 0;
          return (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                [
                  'flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors',
                  isActive
                    ? 'bg-[var(--color-accent)]/15 text-[var(--color-accent)]'
                    : 'text-[var(--color-muted)] hover:bg-white/5 hover:text-[var(--color-text)]',
                ].join(' ')
              }
            >
              <Icon size={16} />
              <span className="flex-1">{label}</span>
              {showBadge && (
                <span
                  className={[
                    'inline-flex items-center justify-center min-w-[20px] h-5 px-1.5',
                    'rounded-full text-xs font-semibold text-white',
                    badgeColor ?? 'bg-slate-500',
                  ].join(' ')}
                >
                  {openCount}
                </span>
              )}
            </NavLink>
          );
        })}
      </nav>

      <div className="px-5 py-3 border-t border-[var(--color-border)]">
        <HealthIndicator />
        <div className="text-xs text-[var(--color-muted)] mt-2">
          v0.1.0 — Diplomski rad
        </div>
      </div>
    </aside>
  );
}