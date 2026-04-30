/**
 * Incidents page — list of detected attacks.
 *
 * Auto-refreshes every 5s. Filters combine with AND on the server side.
 * Click a row to open the detail modal (next commit).
 */

import { useMemo, useState } from 'react';
import { fetchIncidents } from '../api/client';
import type { IncidentQuery } from '../api/client';
import type { Incident } from '../api/types';
import { usePolling } from '../hooks/usePolling';
import { SeverityBadge } from '../components/SeverityBadge';
import { IncidentDetailModal } from '../components/IncidentDetailModal';
import { formatAbsolute, formatRelative } from '../utils/time';
import { ChevronLeft, ChevronRight, Filter } from 'lucide-react';
import { RefreshIndicator } from '../components/RefreshIndicator';
import { EmptyState } from '../components/EmptyState';
import { ShieldCheck } from 'lucide-react';

const POLL_MS = 5000;
const PAGE_SIZE = 25;

export function Incidents() {
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState<IncidentQuery>({
    status: 'open',
  });
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Stable fetcher reference so usePolling does not restart constantly.
  const fetcher = useMemo(
    () => () =>
      fetchIncidents({
        ...filters,
        page,
        page_size: PAGE_SIZE,
      }),
    [filters, page],
  );

  const { data, error, loading, lastUpdated, refresh } = usePolling(fetcher, POLL_MS);

  const totalPages = data
    ? Math.max(1, Math.ceil(data.total / PAGE_SIZE))
    : 1;

  const updateFilter = (key: keyof IncidentQuery, value: string) => {
    setFilters((prev) => {
      const next = { ...prev };
      if (value === '') {
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
        <h1 className="text-2xl font-semibold">Incidents</h1>
        <div className="flex items-center gap-3">
          {data && (
            <span className="text-xs text-[var(--color-muted)]">
              {data.total} matching
            </span>
          )}
          <RefreshIndicator loading={loading} lastUpdated={lastUpdated} />
        </div>
      </header>

      {error && (
        <div className="bg-red-500/10 border border-red-500/40 text-red-300 rounded-md px-4 py-2 text-sm">
          Failed to fetch incidents: {error.message}
        </div>
      )}

      {/* ---------- Filter bar ---------- */}
      <FilterBar filters={filters} onChange={updateFilter} />

      {/* ---------- Table ---------- */}
      <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-white/5 text-[var(--color-muted)]">
            <tr>
              <Th>Detected</Th>
              <Th>Rule</Th>
              <Th>Severity</Th>
              <Th>Source IP</Th>
              <Th>User</Th>
              <Th>Events</Th>
              <Th>Status</Th>
            </tr>
          </thead>
          <tbody>
            {loading && !data ? (
              <RowSpan colSpan={7}>Loading…</RowSpan>
            ) : !data || data.items.length === 0 ? (
              <tr>
                <td colSpan={7}>
                  <EmptyState
                    Icon={ShieldCheck}
                    title="No incidents to triage"
                    description="No incidents match the current filters. New attacks will appear here automatically as the platform detects them."
                  />
                </td>
              </tr>
            ) : (
              data.items.map((inc) => (
                <IncidentRow
                  key={inc.id}
                  incident={inc}
                  onClick={() => setSelectedId(inc.id)}
                />
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* ---------- Pagination ---------- */}
      {data && data.total > PAGE_SIZE && (
        <div className="flex items-center justify-between text-sm text-[var(--color-muted)]">
          <span>
            Page {page} of {totalPages}
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

      {/* ---------- Detail modal ---------- */}
      {selectedId && (
        <IncidentDetailModal
          incidentId={selectedId}
          onClose={() => setSelectedId(null)}
          onUpdated={() => {
            refresh();
          }}
        />
      )}
    </div>
  );
}

// ============================================
// Sub-components
// ============================================

interface FilterBarProps {
  filters: IncidentQuery;
  onChange: (key: keyof IncidentQuery, value: string) => void;
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

      <select
        className={inputCls}
        value={filters.status ?? ''}
        onChange={(e) => onChange('status', e.target.value)}
      >
        <option value="">Any status</option>
        <option value="open">Open</option>
        <option value="acknowledged">Acknowledged</option>
        <option value="closed">Closed</option>
        <option value="false_positive">False positive</option>
      </select>

      <select
        className={inputCls}
        value={filters.severity ?? ''}
        onChange={(e) => onChange('severity', e.target.value)}
      >
        <option value="">Any severity</option>
        <option value="low">Low</option>
        <option value="medium">Medium</option>
        <option value="high">High</option>
        <option value="critical">Critical</option>
      </select>

      <select
        className={inputCls}
        value={filters.rule_name ?? ''}
        onChange={(e) => onChange('rule_name', e.target.value)}
      >
        <option value="">Any rule</option>
        <option value="brute_force">Brute force</option>
        <option value="directory_scanning">Directory scanning</option>
        <option value="account_takeover">Account takeover</option>
      </select>

      <input
        className={inputCls + ' w-44'}
        type="text"
        placeholder="Source IP…"
        value={filters.source_ip ?? ''}
        onChange={(e) => onChange('source_ip', e.target.value)}
      />
    </div>
  );
}

interface IncidentRowProps {
  incident: Incident;
  onClick: () => void;
}

function IncidentRow({ incident, onClick }: IncidentRowProps) {
  return (
    <tr
      className="border-t border-[var(--color-border)] hover:bg-white/5 cursor-pointer transition-colors"
      onClick={onClick}
    >
      <Td title={formatAbsolute(incident.detected_at)}>
        {formatRelative(incident.detected_at)}
      </Td>
      <Td className="font-mono">{incident.rule_name}</Td>
      <Td>
        <SeverityBadge severity={incident.severity} />
      </Td>
      <Td className="font-mono">{incident.source_ip ?? '—'}</Td>
      <Td>{incident.target_user_name ?? '—'}</Td>
      <Td>{incident.event_count}</Td>
      <Td>
        <StatusPill status={incident.status} />
      </Td>
    </tr>
  );
}

function StatusPill({ status }: { status: string }) {
  const styles: Record<string, string> = {
    open: 'bg-blue-500/20 text-blue-300 border-blue-500/40',
    acknowledged: 'bg-purple-500/20 text-purple-300 border-purple-500/40',
    closed: 'bg-green-500/20 text-green-300 border-green-500/40',
    false_positive: 'bg-slate-500/20 text-slate-300 border-slate-500/40',
  };
  return (
    <span
      className={[
        'inline-flex items-center px-2 py-0.5 rounded-full text-xs',
        'font-medium border uppercase tracking-wider',
        styles[status] ?? styles.open,
      ].join(' ')}
    >
      {status.replace('_', ' ')}
    </span>
  );
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
      className={['px-4 py-3 text-[var(--color-text)]', className ?? ''].join(' ')}
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