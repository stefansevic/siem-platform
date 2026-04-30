/**
 * Rules page — overview of active correlation rules and their thresholds.
 *
 * Static reference for the operator: "what is currently being detected
 * and at what cost?". The data comes from the Gateway, which currently
 * returns a hardcoded list (see services/api-gateway/app/main.py).
 */

import { useEffect, useState } from 'react';
import { fetchRules } from '../api/client';
import type { Rule } from '../api/types';
import { SeverityBadge } from '../components/SeverityBadge';
import { Shield } from 'lucide-react';

export function Rules() {
  const [rules, setRules] = useState<Rule[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchRules()
      .then((data) => {
        if (!cancelled) setRules(data);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-2xl font-semibold">Detection rules</h1>
        <p className="text-sm text-[var(--color-muted)] mt-1">
          Active correlation rules. Triggers from these rules become
          incidents in the dashboard.
        </p>
      </header>

      {error && (
        <div className="bg-red-500/10 border border-red-500/40 text-red-300 rounded-md px-4 py-2 text-sm">
          Failed to fetch rules: {error}
        </div>
      )}

      {!rules && !error && (
        <div className="text-[var(--color-muted)] text-sm">Loading…</div>
      )}

      {rules && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {rules.map((rule) => (
            <RuleCard key={rule.name} rule={rule} />
          ))}
        </div>
      )}
    </div>
  );
}

// ============================================
// Sub-components
// ============================================

function RuleCard({ rule }: { rule: Rule }) {
  return (
    <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg p-5 flex flex-col gap-3">
      <header className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <Shield size={16} className="text-[var(--color-accent)]" />
          <h2 className="text-base font-semibold font-mono">{rule.name}</h2>
        </div>
        <SeverityBadge severity={rule.severity} />
      </header>

      <p className="text-sm text-[var(--color-muted)] leading-relaxed">
        {rule.description}
      </p>

      <dl className="grid grid-cols-2 gap-3 mt-1 pt-3 border-t border-[var(--color-border)]">
        <div>
          <dt className="text-xs text-[var(--color-muted)] uppercase tracking-wider">
            Threshold
          </dt>
          <dd className="text-lg font-semibold mt-0.5">{rule.threshold}</dd>
        </div>
        <div>
          <dt className="text-xs text-[var(--color-muted)] uppercase tracking-wider">
            Window
          </dt>
          <dd className="text-lg font-semibold mt-0.5">
            {formatWindow(rule.window_seconds)}
          </dd>
        </div>
      </dl>
    </div>
  );
}

function formatWindow(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) {
    const minutes = Math.round(seconds / 60);
    return `${minutes}m`;
  }
  const hours = Math.round(seconds / 3600);
  return `${hours}h`;
}