/**
 * Event search page — Elasticsearch-backed.
 *
 * Filter form posts query parameters into the URL, so a search can
 * be bookmarked or shared. Submitting the form updates URL params,
 * which in turn triggers a fetch via useSearchParams + useEffect.
 */

import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Search as SearchIcon, X, Inbox } from 'lucide-react';
import { searchEvents } from '../api/client';
import type { EventSearchQuery } from '../api/client';
import type { EventList } from '../api/types';
import { EmptyState } from '../components/EmptyState';
import { RefreshIndicator } from '../components/RefreshIndicator';
import { formatRelative, formatAbsolute } from '../utils/time';

const PAGE_SIZE = 50;

// Form fields the user can fill in. We coerce these to/from URL
// params on submit and on first load.
interface FormState {
  q: string;
  source_ip: string;
  user_name: string;
  event_outcome: '' | 'success' | 'failure';
  http_method: string;
  status_min: string;
  status_max: string;
  since: string;
  until: string;
}

const EMPTY_FORM: FormState = {
  q: '',
  source_ip: '',
  user_name: '',
  event_outcome: '',
  http_method: '',
  status_min: '',
  status_max: '',
  since: '',
  until: '',
};

function paramsToForm(params: URLSearchParams): FormState {
  return {
    q:             params.get('q') ?? '',
    source_ip:     params.get('source_ip') ?? '',
    user_name:     params.get('user_name') ?? '',
    event_outcome: (params.get('event_outcome') as FormState['event_outcome']) ?? '',
    http_method:   params.get('http_method') ?? '',
    status_min:    params.get('status_min') ?? '',
    status_max:    params.get('status_max') ?? '',
    since:         params.get('since') ?? '',
    until:         params.get('until') ?? '',
  };
}

function formToQuery(form: FormState, page: number): EventSearchQuery {
  const q: EventSearchQuery = { page, page_size: PAGE_SIZE };
  if (form.q)             q.q = form.q;
  if (form.source_ip)     q.source_ip = form.source_ip;
  if (form.user_name)     q.user_name = form.user_name;
  if (form.event_outcome) q.event_outcome = form.event_outcome;
  if (form.http_method)   q.http_method = form.http_method.toUpperCase();
  if (form.status_min)    q.status_min = parseInt(form.status_min, 10);
  if (form.status_max)    q.status_max = parseInt(form.status_max, 10);
  if (form.since)         q.since = form.since;
  if (form.until)         q.until = form.until;
  return q;
}

function formToParams(form: FormState, page: number): URLSearchParams {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(form)) {
    if (value) params.set(key, value);
  }
  if (page > 1) params.set('page', String(page));
  return params;
}

export function Search() {
  const [searchParams, setSearchParams] = useSearchParams();
  const initialForm = useMemo(() => paramsToForm(searchParams), []);
  const initialPage = parseInt(searchParams.get('page') ?? '1', 10);

  // Pending form state — what the user is typing
  const [form, setForm] = useState<FormState>(initialForm);
  // Submitted state — drives the actual API call
  const [submitted, setSubmitted] = useState<FormState>(initialForm);
  const [page, setPage] = useState(initialPage);

  const [data, setData] = useState<EventList | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);

  // Whether the user has submitted a search at least once.
  // Until then, we show a hint instead of an empty results table.
  const hasSearched =
    Object.values(submitted).some((v) => v !== '') ||
    searchParams.has('page');

  // Fetch whenever submitted state or page changes.
  useEffect(() => {
    if (!hasSearched) return;

    let cancelled = false;
    setLoading(true);
    setError(null);

    searchEvents(formToQuery(submitted, page))
      .then((result) => {
        if (!cancelled) {
          setData(result);
          setLastUpdated(new Date().toISOString());
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [submitted, page, hasSearched]);

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    // Reject empty searches — vague queries against an unbounded log
    // table are a recipe for slow pages and accidental data dumps.
    const hasFilter = Object.values(form).some((v) => v !== '');
    if (!hasFilter) {
      setError('Add at least one filter to search.');
      return;
    }
    setError(null);
    setSubmitted(form);
    setPage(1);
    setSearchParams(formToParams(form, 1));
  }

  function onClear() {
    setForm(EMPTY_FORM);
    setSubmitted(EMPTY_FORM);
    setPage(1);
    setSearchParams(new URLSearchParams());
    setData(null);
  }

  function onPageChange(newPage: number) {
    setPage(newPage);
    setSearchParams(formToParams(submitted, newPage));
  }

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 0;

  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Search events</h1>
        <div className="flex items-center gap-3">
          {data && (
            <span className="text-xs text-[var(--color-muted)]">
              {data.total.toLocaleString()} matching
            </span>
          )}
          <RefreshIndicator loading={loading} lastUpdated={lastUpdated} />
        </div>
      </header>

      {/* ---------- Filter form ---------- */}
      <form
        onSubmit={onSubmit}
        className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-md p-4 space-y-3"
      >
        {/* Free-text q + actions */}
        <div className="flex items-center gap-2">
          <div className="flex-1 relative">
            <SearchIcon
              size={14}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--color-muted)]"
            />
            <input
              type="text"
              placeholder="Search across user, path, user-agent…"
              value={form.q}
              onChange={(e) => setForm({ ...form, q: e.target.value })}
              className="w-full bg-[var(--color-bg)] border border-[var(--color-border)] rounded-md pl-9 pr-3 py-2 text-sm focus:outline-none focus:border-[var(--color-accent)]"
            />
          </div>
          <button
            type="submit"
            className="px-4 py-2 rounded-md text-sm bg-[var(--color-accent)] text-white hover:bg-blue-600 transition-colors"
          >
            Search
          </button>
          <button
            type="button"
            onClick={onClear}
            className="px-3 py-2 rounded-md text-sm border border-[var(--color-border)] text-[var(--color-muted)] hover:bg-white/5 transition-colors"
          >
            <X size={14} className="inline mr-1" />
            Clear
          </button>
        </div>

        {/* Structured filters */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          <FieldInput
            label="Source IP"
            value={form.source_ip}
            onChange={(v) => setForm({ ...form, source_ip: v })}
            placeholder="172.18.0.1"
          />
          <FieldInput
            label="User name"
            value={form.user_name}
            onChange={(v) => setForm({ ...form, user_name: v })}
            placeholder="alice"
          />
          <FieldSelect
            label="Outcome"
            value={form.event_outcome}
            onChange={(v) => setForm({ ...form, event_outcome: v as FormState['event_outcome'] })}
            options={[
              { value: '',        label: 'Any' },
              { value: 'success', label: 'Success' },
              { value: 'failure', label: 'Failure' },
            ]}
          />
          <FieldInput
            label="HTTP method"
            value={form.http_method}
            onChange={(v) => setForm({ ...form, http_method: v })}
            placeholder="GET, POST…"
          />
          <FieldInput
            label="Status min"
            type="number"
            value={form.status_min}
            onChange={(v) => setForm({ ...form, status_min: v })}
            placeholder="100"
          />
          <FieldInput
            label="Status max"
            type="number"
            value={form.status_max}
            onChange={(v) => setForm({ ...form, status_max: v })}
            placeholder="599"
          />
          <FieldInput
            label="Since (ISO)"
            value={form.since}
            onChange={(v) => setForm({ ...form, since: v })}
            placeholder="2026-05-03T00:00:00Z"
          />
          <FieldInput
            label="Until (ISO)"
            value={form.until}
            onChange={(v) => setForm({ ...form, until: v })}
            placeholder="2026-05-03T23:59:59Z"
          />
        </div>
      </form>

      {/* ---------- Error ---------- */}
      {error && (
        <div className="bg-red-500/10 border border-red-500/40 text-red-300 rounded-md px-4 py-2 text-sm">
          {error}
        </div>
      )}

      {/* ---------- Results ---------- */}
      <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-md overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-white/5 text-[var(--color-muted)] text-xs uppercase tracking-wider">
            <tr>
              <Th>When</Th>
              <Th>Source IP</Th>
              <Th>User</Th>
              <Th>Method</Th>
              <Th>Path</Th>
              <Th>Status</Th>
              <Th>Outcome</Th>
            </tr>
          </thead>
          <tbody>
            {!hasSearched ? (
              <tr>
                <td colSpan={7}>
                  <EmptyState
                    Icon={SearchIcon}
                    title="Search the event log"
                    description="Use the filters above to narrow down. The search is full-text and supports source IP, user, status code, time range, and free text."
                  />
                </td>
              </tr>
            ) : loading && !data ? (
              <tr>
                <td colSpan={7} className="px-4 py-6 text-center text-[var(--color-muted)] text-sm">
                  Searching…
                </td>
              </tr>
            ) : !data || data.items.length === 0 ? (
              <tr>
                <td colSpan={7}>
                  <EmptyState
                    Icon={Inbox}
                    title="No matching events"
                    description="Try widening the time range or relaxing some filters."
                  />
                </td>
              </tr>
            ) : (
              data.items.map((evt) => (
                <tr
                  key={evt.id}
                  className="border-t border-[var(--color-border)] hover:bg-white/5 transition-colors"
                >
                  <Td title={formatAbsolute(evt.timestamp)}>
                    {formatRelative(evt.timestamp)}
                  </Td>
                  <Td className="font-mono text-xs">{evt.source_ip ?? '—'}</Td>
                  <Td>{evt.user_name ?? '—'}</Td>
                  <Td className="font-mono text-xs">{evt.http_method ?? '—'}</Td>
                  <Td
                    className="font-mono text-xs max-w-[260px] truncate"
                    title={evt.url_path ?? ''}
                  >
                    {evt.url_path ?? '—'}
                  </Td>
                  <Td>
                    <StatusCode code={evt.http_response_status_code} />
                  </Td>
                  <Td>
                    <Outcome value={evt.event_outcome} />
                  </Td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* ---------- Pagination ---------- */}
      {data && totalPages > 1 && (
        <div className="flex items-center justify-between text-sm">
          <button
            onClick={() => onPageChange(Math.max(1, page - 1))}
            disabled={page <= 1}
            className="px-3 py-1.5 rounded-md border border-[var(--color-border)] hover:bg-white/5 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Previous
          </button>
          <span className="text-[var(--color-muted)]">
            Page {page} of {totalPages}
          </span>
          <button
            onClick={() => onPageChange(Math.min(totalPages, page + 1))}
            disabled={page >= totalPages}
            className="px-3 py-1.5 rounded-md border border-[var(--color-border)] hover:bg-white/5 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}

// ============================================
// Sub-components
// ============================================

function FieldInput({
  label,
  value,
  onChange,
  placeholder,
  type = 'text',
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
}) {
  return (
    <label className="block">
      <span className="text-xs text-[var(--color-muted)] uppercase tracking-wider">
        {label}
      </span>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="mt-1 w-full bg-[var(--color-bg)] border border-[var(--color-border)] rounded-md px-2 py-1.5 text-sm focus:outline-none focus:border-[var(--color-accent)]"
      />
    </label>
  );
}

function FieldSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <label className="block">
      <span className="text-xs text-[var(--color-muted)] uppercase tracking-wider">
        {label}
      </span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 w-full bg-[var(--color-bg)] border border-[var(--color-border)] rounded-md px-2 py-1.5 text-sm focus:outline-none focus:border-[var(--color-accent)]"
      >
        {options.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return <th className="text-left font-medium px-4 py-2.5">{children}</th>;
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

function StatusCode({ code }: { code: number | null }) {
  if (code === null) return <span className="text-[var(--color-muted)]">—</span>;
  let cls = 'text-[var(--color-text)]';
  if (code >= 500) cls = 'text-red-400';
  else if (code >= 400) cls = 'text-amber-400';
  else if (code >= 300) cls = 'text-blue-400';
  else if (code >= 200) cls = 'text-green-400';
  return <span className={`font-mono text-xs ${cls}`}>{code}</span>;
}

function Outcome({ value }: { value: string | null }) {
  if (!value) return <span className="text-[var(--color-muted)]">—</span>;
  const cls =
    value === 'success'
      ? 'text-green-400'
      : value === 'failure'
        ? 'text-red-400'
        : 'text-[var(--color-muted)]';
  return <span className={`uppercase text-xs font-medium ${cls}`}>{value}</span>;
}