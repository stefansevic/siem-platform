/**
 * Events explorer — paginated browse of all normalized events.
 *
 * Use cases:
 *   - "Show me everything that 1.2.3.4 did"
 *   - "Show me all 401s"
 *   - "Show me failed authentications in the last 10 minutes"
 *
 * Auto-refreshes every 5s. Filters combine with AND on the server.
 */

import { useMemo, useState } from 'react';
import { fetchEvents } from '../api/client';
import type { EventQuery } from '../api/client';
import type { Event } from '../api/types';
import { usePolling } from '../hooks/usePolling';
import { formatAbsolute, formatRelative } from '../utils/time';
import { ChevronLeft, ChevronRight, Filter } from 'lucide-react';

const POLL_MS = 5000;
const PAGE_SIZE = 50;

export function Events() {
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState<EventQuery>({});

  const fetcher = useMemo(
    () => () =>
      fetchEvents({
        ...filters,
        page,
        page_size: PAGE_SIZE,
      }),
    [filters, page],
  );

  const { data, error, loading } = usePolling(fetcher, POLL_MS);

  const totalPages = data
    ? Math.max(1, Math.ceil(data.total / PAGE_SIZE))
    : 1;

  const updateFilter = (key: keyof EventQuery, value: string | number) => {
    setFilters((prev) => {
      const next = { ...prev };
      if (value === '' || value === undefined) {
        delete next[key];
      } else {
        (next as Record<string, unknown>)[key] = value;
      }
      return next;
    });
    setPage(1);
  };

  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Events</h1>
        <span className="text-xs text-[var(--color-muted)]">
          Auto-refreshing every {POLL_MS / 1000}s
          {data ? ` — ${data.total.toLocaleString()} matching` : ''}
        </span>
      </header>

      {error && (
        <div className="bg-red-500/10 border border-red-500/40 text-red-300 rounded-md px-4 py-2 text-sm">
          Failed to fetch events: {error.message}
        </div>
      )}

      {/* ---------- Filter bar ---------- */}
      <FilterBar filters={filters} onChange={updateFilter} />

      {/* ---------- Table ---------- */}
      <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-white/5 text-[var(--color-muted)]">
            <tr>
              <Th>When</Th>
              <Th>Source IP</Th>
              <Th>Method</Th>
              <Th>Path</Th>
              <Th>Status</Th>
              <Th>Outcome</Th>
              <Th>User</Th>
              <Th>Origin</Th>
            </tr>
          </thead>
          <tbody>
            {loading && !data ? (
              <RowSpan colSpan={8}>Loading…</RowSpan>
            ) : !data || data.items.length === 0 ? (
              <RowSpan colSpan={8}>No events matching the filters.</RowSpan>
            ) : (
              data.items.map((evt) => <EventRow key={evt.id} event={evt} />)
            )}
          </tbody>
        </table>
      </div>

      {/* ---------- Pagination ---------- */}
      {data && data.total > PAGE_SIZE && (
        <div className="flex items-center justify-between text-sm text-[var(--color-muted)]">
          <span>
            Page {page} of {totalPages.toLocaleString()}
          </span>
          <div className="flex gap-2">
            <PageButton
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              <ChevronLeft size={14} />
              Prev
            </PageButton>
            <PageButton
              disabled={page >= totalPages}
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            >
              Next
              <ChevronRight size={14} />
            </PageButton>
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================
// Sub-components
// ============================================

interface FilterBarProps {
  filters: EventQuery;
  onChange: (key: keyof EventQuery, value: string | number) => void;
}

function FilterBar({ filters, onChange }: FilterBarProps) {
  const inputCls =
    'bg-[var(--color-bg)] border border-[var(--color-border)] rounded-md px-3 py-1.5 ' +
    'text-sm text-[var(--color-text)] focus:outline-none focus:border-[var(--color-accent)]';

  return (
    <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg p-3 flex items-center gap-3 flex-wrap">
      <div className="flex items-center gap-2 text-[var(--color-muted)] text-sm pr-2 border-r border-[var(--color-border)]">
        <Filter size={14} />
        <span>Filters</span>
      </div>

      <input
        className={inputCls + ' w-44'}
        type="text"
        placeholder="Source IP…"
        value={filters.source_ip ?? ''}
        onChange={(e) => onChange('source_ip', e.target.value)}
      />

      <select
        className={inputCls}
        value={filters.event_outcome ?? ''}
        onChange={(e) => onChange('event_outcome', e.target.value)}
      >
        <option value="">Any outcome</option>
        <option value="success">Success</option>
        <option value="failure">Failure</option>
      </select>

      <input
        className={inputCls + ' w-32'}
        type="number"
        placeholder="Status code"
        value={filters.status_code ?? ''}
        onChange={(e) =>
          onChange('status_code', e.target.value ? Number(e.target.value) : '')
        }
      />

      <input
        className={inputCls + ' w-44'}
        type="text"
        placeholder="Username…"
        value={filters.user_name ?? ''}
        onChange={(e) => onChange('user_name', e.target.value)}
      />

      <select
        className={inputCls}
        value={filters.log_source ?? ''}
        onChange={(e) => onChange('log_source', e.target.value)}
      >
        <option value="">Any source</option>
        <option value="nginx">Nginx</option>
        <option value="demo-webapp">Demo webapp</option>
      </select>

      <select
        className={inputCls}
        value={filters.since_minutes ?? ''}
        onChange={(e) =>
          onChange('since_minutes', e.target.value ? Number(e.target.value) : '')
        }
      >
        <option value="">Any time</option>
        <option value="5">Last 5 min</option>
        <option value="15">Last 15 min</option>
        <option value="60">Last hour</option>
        <option value="1440">Last 24h</option>
      </select>
    </div>
  );
}

function EventRow({ event }: { event: Event }) {
  return (
    <tr className="border-t border-[var(--color-border)] hover:bg-white/5 transition-colors">
      <Td title={formatAbsolute(event.timestamp)}>
        {formatRelative(event.timestamp)}
      </Td>
      <Td className="font-mono">{event.source_ip ?? '—'}</Td>
      <Td className="font-mono">{event.http_method ?? '—'}</Td>
      <Td className="font-mono max-w-md truncate" title={event.url_path ?? ''}>
        {event.url_path ?? '—'}
      </Td>
      <Td>
        <StatusCode code={event.http_response_status_code} />
      </Td>
      <Td>
        <Outcome value={event.event_outcome} />
      </Td>
      <Td>{event.user_name ?? '—'}</Td>
      <Td className="text-[var(--color-muted)] text-xs">{event.log_source}</Td>
    </tr>
  );
}

function StatusCode({ code }: { code: number | null }) {
  if (code === null) return <span className="text-[var(--color-muted)]">—</span>;
  let cls = 'text-[var(--color-text)]';
  if (code >= 500) cls = 'text-red-400';
  else if (code >= 400) cls = 'text-amber-400';
  else if (code >= 300) cls = 'text-blue-400';
  else if (code >= 200) cls = 'text-green-400';
  return <span className={`font-mono ${cls}`}>{code}</span>;
}

function Outcome({ value }: { value: string | null }) {
  if (!value) return <span className="text-[var(--color-muted)]">—</span>;
  const cls =
    value === 'success'
      ? 'text-green-400'
      : value === 'failure'
        ? 'text-red-400'
        : 'text-[var(--color-muted)]';
  return <span className={`text-xs uppercase ${cls}`}>{value}</span>;
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th className="text-left font-medium uppercase text-xs tracking-wider px-4 py-2.5">
      {children}
    </th>
  );
}

function Td({
  children,
  className,
  title,
}: {
  children: React.ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <td
      className={['px-4 py-2.5 text-[var(--color-text)]', className ?? ''].join(' ')}
      title={title}
    >
      {children}
    </td>
  );
}

function RowSpan({
  colSpan,
  children,
}: {
  colSpan: number;
  children: React.ReactNode;
}) {
  return (
    <tr>
      <td
        colSpan={colSpan}
        className="px-4 py-8 text-center text-[var(--color-muted)] text-sm"
      >
        {children}
      </td>
    </tr>
  );
}

function PageButton({
  disabled,
  onClick,
  children,
}: {
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      disabled={disabled}
      onClick={onClick}
      className={[
        'flex items-center gap-1 px-3 py-1.5 rounded-md text-sm border transition-colors',
        disabled
          ? 'border-[var(--color-border)] text-[var(--color-muted)] cursor-not-allowed'
          : 'border-[var(--color-border)] hover:bg-white/5 text-[var(--color-text)]',
      ].join(' ')}
    >
      {children}
    </button>
  );
}