/**
 * Modal for viewing a single incident in detail and triaging its status.
 *
 * Behavior:
 *   - Loads the incident on open via fetchIncident(id).
 *   - Loads contributing events in parallel for the per-event table.
 *   - Allows the operator to change status and add notes; PATCHes the
 *     change and notifies the parent so the table refreshes.
 *   - Closes on Escape, click outside, or the X button.
 */

import { useCallback, useEffect, useState } from 'react';
import { X, AlertCircle } from 'lucide-react';
import {
  fetchEventsByIds,
  fetchIncident,
  updateIncidentStatus,
} from '../api/client';
import type { Event, Incident, IncidentStatus } from '../api/types';
import { SeverityBadge } from './SeverityBadge';
import { formatAbsolute, formatRelative } from '../utils/time';

interface Props {
  incidentId: string;
  onClose: () => void;
  onUpdated: () => void;
}

const STATUS_OPTIONS: IncidentStatus[] = [
  'open',
  'acknowledged',
  'closed',
  'false_positive',
];

export function IncidentDetailModal({ incidentId, onClose, onUpdated }: Props) {
  const [incident, setIncident] = useState<Incident | null>(null);
  const [events, setEvents] = useState<Event[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Form state for status update
  const [newStatus, setNewStatus] = useState<IncidentStatus>('open');
  const [notes, setNotes] = useState('');
  const [saving, setSaving] = useState(false);

  // ----- Load incident + contributing events -----
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setEvents(null);

    fetchIncident(incidentId)
      .then(async (inc) => {
        if (cancelled) return;
        setIncident(inc);
        setNewStatus(inc.status);
        setNotes(inc.notes ?? '');
        setError(null);

        // Enrich contributing event IDs with full records
        if (inc.contributing_events && inc.contributing_events.length > 0) {
          const enriched = await fetchEventsByIds(inc.contributing_events);
          if (!cancelled) {
            // Sort newest-first to match operator expectations
            enriched.sort(
              (a, b) =>
                new Date(b.timestamp).getTime() -
                new Date(a.timestamp).getTime(),
            );
            setEvents(enriched);
          }
        } else {
          setEvents([]);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [incidentId]);

  // ----- Close on Escape -----
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const onBackdropClick = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (e.target === e.currentTarget) onClose();
    },
    [onClose],
  );

  const onSave = async () => {
    if (!incident) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await updateIncidentStatus(incident.id, {
        status: newStatus,
        notes: notes || undefined,
      });
      setIncident(updated);
      onUpdated();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      onClick={onBackdropClick}
      className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4"
    >
      <div
        className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg w-full max-w-4xl max-h-[90vh] overflow-hidden flex flex-col"
      >
        {/* ---------- Header ---------- */}
        <header className="flex items-center justify-between px-6 py-4 border-b border-[var(--color-border)]">
          <div>
            <h2 className="text-lg font-semibold">Incident detail</h2>
            <p className="text-xs text-[var(--color-muted)] mt-0.5 font-mono">
              {incidentId}
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-[var(--color-muted)] hover:text-[var(--color-text)] transition-colors"
            aria-label="Close"
          >
            <X size={20} />
          </button>
        </header>

        {/* ---------- Body ---------- */}
        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-5">
          {error && (
            <div className="bg-red-500/10 border border-red-500/40 text-red-300 rounded-md px-4 py-2 text-sm flex items-center gap-2">
              <AlertCircle size={16} />
              <span>{error}</span>
            </div>
          )}

          {loading ? (
            <div className="text-[var(--color-muted)] text-sm">Loading…</div>
          ) : incident ? (
            <>
              {/* ---------- Summary grid ---------- */}
              <div className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
                <Field label="Rule" value={<span className="font-mono">{incident.rule_name}</span>} />
                <Field label="Severity" value={<SeverityBadge severity={incident.severity} />} />
                <Field label="Source IP" value={<span className="font-mono">{incident.source_ip ?? '—'}</span>} />
                <Field label="Target user" value={incident.target_user_name ?? '—'} />
                <Field label="Event count" value={incident.event_count.toString()} />
                <Field label="Current status" value={<code className="text-xs">{incident.status}</code>} />
                <Field
                  label="First event"
                  value={
                    <span title={formatAbsolute(incident.first_event_at)}>
                      {formatRelative(incident.first_event_at)}
                    </span>
                  }
                />
                <Field
                  label="Last event"
                  value={
                    <span title={formatAbsolute(incident.last_event_at)}>
                      {formatRelative(incident.last_event_at)}
                    </span>
                  }
                />
                <Field
                  label="Detected at"
                  value={formatAbsolute(incident.detected_at)}
                />
              </div>

              {/* ---------- Details JSON ---------- */}
              {incident.details && (
                <Section title="Rule details">
                  <pre className="bg-[var(--color-bg)] border border-[var(--color-border)] rounded-md p-3 text-xs overflow-x-auto font-mono text-[var(--color-text)]">
{JSON.stringify(incident.details, null, 2)}
                  </pre>
                </Section>
              )}

              {/* ---------- Contributing events table ---------- */}
              {incident.contributing_events && incident.contributing_events.length > 0 && (
                <Section
                  title={`Contributing events (${incident.contributing_events.length})`}
                >
                  <ContributingEventsTable
                    events={events}
                    expectedCount={incident.contributing_events.length}
                  />
                </Section>
              )}

              {/* ---------- Triage form ---------- */}
              <Section title="Triage">
                <div className="space-y-3">
                  <label className="block">
                    <span className="text-xs text-[var(--color-muted)] uppercase tracking-wider">
                      Status
                    </span>
                    <select
                      className="mt-1 w-full bg-[var(--color-bg)] border border-[var(--color-border)] rounded-md px-3 py-1.5 text-sm focus:outline-none focus:border-[var(--color-accent)]"
                      value={newStatus}
                      onChange={(e) => setNewStatus(e.target.value as IncidentStatus)}
                    >
                      {STATUS_OPTIONS.map((s) => (
                        <option key={s} value={s}>
                          {s.replace('_', ' ')}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label className="block">
                    <span className="text-xs text-[var(--color-muted)] uppercase tracking-wider">
                      Notes (optional)
                    </span>
                    <textarea
                      className="mt-1 w-full bg-[var(--color-bg)] border border-[var(--color-border)] rounded-md px-3 py-2 text-sm focus:outline-none focus:border-[var(--color-accent)] resize-none"
                      rows={3}
                      placeholder="Operator notes…"
                      value={notes}
                      onChange={(e) => setNotes(e.target.value)}
                    />
                  </label>
                </div>
              </Section>
            </>
          ) : null}
        </div>

        {/* ---------- Footer ---------- */}
        <footer className="px-6 py-3 border-t border-[var(--color-border)] flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-4 py-1.5 rounded-md text-sm border border-[var(--color-border)] hover:bg-white/5 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={onSave}
            disabled={
              saving ||
              loading ||
              !incident ||
              (newStatus === incident.status && notes === (incident.notes ?? ''))
            }
            className="px-4 py-1.5 rounded-md text-sm bg-[var(--color-accent)] text-white hover:bg-blue-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
        </footer>
      </div>
    </div>
  );
}

// ============================================
// Sub-components
// ============================================

function Field({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div>
      <div className="text-xs text-[var(--color-muted)] uppercase tracking-wider">
        {label}
      </div>
      <div className="mt-1">{value}</div>
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <h3 className="text-xs text-[var(--color-muted)] uppercase tracking-wider mb-2">
        {title}
      </h3>
      {children}
    </div>
  );
}

interface ContributingEventsTableProps {
  events: Event[] | null;
  expectedCount: number;
}

function ContributingEventsTable({
  events,
  expectedCount,
}: ContributingEventsTableProps) {
  if (events === null) {
    return (
      <div className="bg-[var(--color-bg)] border border-[var(--color-border)] rounded-md p-3 text-xs text-[var(--color-muted)]">
        Loading {expectedCount} events…
      </div>
    );
  }

  if (events.length === 0) {
    return (
      <div className="bg-[var(--color-bg)] border border-[var(--color-border)] rounded-md p-3 text-xs text-[var(--color-muted)]">
        No contributing events available.
      </div>
    );
  }

  return (
    <div className="bg-[var(--color-bg)] border border-[var(--color-border)] rounded-md overflow-hidden">
      <div className="max-h-64 overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="bg-white/5 text-[var(--color-muted)] sticky top-0">
            <tr>
              <ECth>When</ECth>
              <ECth>Method</ECth>
              <ECth>Path</ECth>
              <ECth>Status</ECth>
              <ECth>Outcome</ECth>
              <ECth>User</ECth>
            </tr>
          </thead>
          <tbody>
            {events.map((evt) => (
              <tr
                key={evt.id}
                className="border-t border-[var(--color-border)] hover:bg-white/5 transition-colors"
              >
                <ECtd title={formatAbsolute(evt.timestamp)}>
                  {formatRelative(evt.timestamp)}
                </ECtd>
                <ECtd className="font-mono">{evt.http_method ?? '—'}</ECtd>
                <ECtd
                  className="font-mono max-w-[180px] truncate"
                  title={evt.url_path ?? ''}
                >
                  {evt.url_path ?? '—'}
                </ECtd>
                <ECtd>
                  <ECStatusCode code={evt.http_response_status_code} />
                </ECtd>
                <ECtd>
                  <ECOutcome value={evt.event_outcome} />
                </ECtd>
                <ECtd>{evt.user_name ?? '—'}</ECtd>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ECth({ children }: { children: React.ReactNode }) {
  return (
    <th className="text-left font-medium uppercase tracking-wider px-3 py-2">
      {children}
    </th>
  );
}

function ECtd({
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
      className={['px-3 py-2 text-[var(--color-text)]', className ?? ''].join(' ')}
      title={title}
    >
      {children}
    </td>
  );
}

function ECStatusCode({ code }: { code: number | null }) {
  if (code === null) return <span className="text-[var(--color-muted)]">—</span>;
  let cls = 'text-[var(--color-text)]';
  if (code >= 500) cls = 'text-red-400';
  else if (code >= 400) cls = 'text-amber-400';
  else if (code >= 300) cls = 'text-blue-400';
  else if (code >= 200) cls = 'text-green-400';
  return <span className={`font-mono ${cls}`}>{code}</span>;
}

function ECOutcome({ value }: { value: string | null }) {
  if (!value) return <span className="text-[var(--color-muted)]">—</span>;
  const cls =
    value === 'success'
      ? 'text-green-400'
      : value === 'failure'
        ? 'text-red-400'
        : 'text-[var(--color-muted)]';
  return <span className={`uppercase ${cls}`}>{value}</span>;
}