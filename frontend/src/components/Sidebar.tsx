/**
 * Left sidebar with navigation links to all pages.
 *
 * Uses NavLink from react-router-dom so the active page gets a
 * highlighted style automatically.
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

export function Sidebar() {
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
        {NAV_ITEMS.map(({ to, label, Icon }) => (
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
            <span>{label}</span>
          </NavLink>
        ))}
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