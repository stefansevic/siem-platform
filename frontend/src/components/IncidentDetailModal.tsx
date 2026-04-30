/**
 * Modal for viewing a single incident in detail and triaging its status.
 *
 * Behavior:
 *   - Loads the incident on open via fetchIncident(id).
 *   - Shows summary, full details JSON, and contributing event IDs.
 *   - Allows the operator to change status and add notes; PATCHes the
 *     change and notifies the parent so the table refreshes.
 *   - Closes on Escape, click outside, or the X button.
 */

import { useCallback, useEffect, useState } from 'react';
import { X, AlertCircle } from 'lucide-react';
import { fetchIncident, updateIncidentStatus } from '../api/client';
import type { Incident, IncidentStatus } from '../api/types';
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
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Form state for status update
  const [newStatus, setNewStatus] = useState<IncidentStatus>('open');
  const [notes, setNotes] = useState('');
  const [saving, setSaving] = useState(false);

  // ----- Load incident -----
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchIncident(incidentId)
      .then((inc) => {
        if (cancelled) return;
        setIncident(inc);
        setNewStatus(inc.status);
        setNotes(inc.notes ?? '');
        setError(null);
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
        className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg w-full max-w-3xl max-h-[90vh] overflow-hidden flex flex-col"
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

              {/* ---------- Contributing events ---------- */}
              {incident.contributing_events && incident.contributing_events.length > 0 && (
                <Section
                  title={`Contributing events (${incident.contributing_events.length})`}
                >
                  <div className="bg-[var(--color-bg)] border border-[var(--color-border)] rounded-md p-3 max-h-32 overflow-y-auto">
                    <ul className="text-xs font-mono text-[var(--color-muted)] space-y-1">
                      {incident.contributing_events.map((id) => (
                        <li key={id}>{id}</li>
                      ))}
                    </ul>
                  </div>
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