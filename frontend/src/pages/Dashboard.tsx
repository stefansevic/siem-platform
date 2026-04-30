/**
 * Dashboard — top-level operator view.
 *
 * Auto-refreshes every 5 seconds. Three sections:
 *   1. Stat cards (4 numbers)
 *   2. Severity + Rule breakdown bar charts
 *   3. Activity timeline line chart (events vs incidents)
 */

import { fetchStatsSummary, fetchStatsTimeseries } from '../api/client';
import { usePolling } from '../hooks/usePolling';
import {
  Activity,
  AlertTriangle,
  Clock,
  TrendingUp,
} from 'lucide-react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

const POLL_MS = 5000;

const SEVERITY_COLORS: Record<string, string> = {
  low: '#94a3b8',
  medium: '#f59e0b',
  high: '#f97316',
  critical: '#ef4444',
};

export function Dashboard() {
  const summary = usePolling(fetchStatsSummary, POLL_MS);
  const timeseries = usePolling(
    () => fetchStatsTimeseries(60, 60),
    POLL_MS,
  );

  return (
    <div className="space-y-6">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Dashboard</h1>
        <span className="text-xs text-[var(--color-muted)]">
          Auto-refreshing every {POLL_MS / 1000}s
        </span>
      </header>

      {/* ---------- Error banner ---------- */}
      {(summary.error || timeseries.error) && (
        <div className="bg-red-500/10 border border-red-500/40 text-red-300 rounded-md px-4 py-2 text-sm">
          Failed to reach API Gateway. Make sure it is running on{' '}
          <code className="text-red-200">localhost:8005</code>.
        </div>
      )}

      {/* ---------- Stat cards ---------- */}
      <section className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="Total events"
          value={summary.data?.events_total}
          loading={summary.loading}
          Icon={Activity}
        />
        <StatCard
          label="Events (last hour)"
          value={summary.data?.events_last_hour}
          loading={summary.loading}
          Icon={Clock}
        />
        <StatCard
          label="Open incidents"
          value={summary.data?.incidents_open}
          loading={summary.loading}
          Icon={AlertTriangle}
          accent="text-amber-300"
        />
        <StatCard
          label="Incidents (total)"
          value={summary.data?.incidents_total}
          loading={summary.loading}
          Icon={TrendingUp}
        />
      </section>

      {/* ---------- Two side-by-side bar charts ---------- */}
      <section className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Panel title="Open incidents by severity">
          {summary.data ? (
            <SeverityBarChart data={summary.data.incidents_by_severity} />
          ) : (
            <ChartSkeleton />
          )}
        </Panel>

        <Panel title="Open incidents by rule">
          {summary.data ? (
            <RuleBarChart data={summary.data.incidents_by_rule} />
          ) : (
            <ChartSkeleton />
          )}
        </Panel>
      </section>

      {/* ---------- Timeline ---------- */}
      <section>
        <Panel title="Activity — last 60 minutes">
          {timeseries.data ? (
            <ActivityLineChart
              points={timeseries.data.points}
            />
          ) : (
            <ChartSkeleton />
          )}
        </Panel>
      </section>
    </div>
  );
}

// ============================================
// Sub-components
// ============================================

interface StatCardProps {
  label: string;
  value: number | undefined;
  loading: boolean;
  Icon: React.ComponentType<{ size?: number; className?: string }>;
  accent?: string;
}

function StatCard({ label, value, loading, Icon, accent }: StatCardProps) {
  return (
    <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg p-4">
      <div className="flex items-center justify-between">
        <span className="text-xs uppercase tracking-wider text-[var(--color-muted)]">
          {label}
        </span>
        <Icon
          size={16}
          className={accent ?? 'text-[var(--color-muted)]'}
        />
      </div>
      <div className={`mt-2 text-3xl font-semibold ${accent ?? ''}`}>
        {loading ? '…' : (value ?? 0).toLocaleString()}
      </div>
    </div>
  );
}

interface PanelProps {
  title: string;
  children: React.ReactNode;
}

function Panel({ title, children }: PanelProps) {
  return (
    <div className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg p-4">
      <h2 className="text-sm font-medium text-[var(--color-muted)] mb-3">
        {title}
      </h2>
      {children}
    </div>
  );
}

function ChartSkeleton() {
  return (
    <div className="h-[240px] flex items-center justify-center text-[var(--color-muted)] text-sm">
      Loading…
    </div>
  );
}

// ----- Severity bar chart -----

function SeverityBarChart({ data }: { data: Record<string, number> }) {
  const order = ['low', 'medium', 'high', 'critical'];
  const series = order.map((sev) => ({
    severity: sev.toUpperCase(),
    count: data[sev] ?? 0,
    fill: SEVERITY_COLORS[sev],
  }));

  return (
    <ResponsiveContainer width="100%" height={240}>
      <BarChart data={series} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
        <XAxis dataKey="severity" stroke="#94a3b8" fontSize={12} />
        <YAxis stroke="#94a3b8" fontSize={12} allowDecimals={false} />
        <Tooltip
          contentStyle={{
            backgroundColor: '#1e293b',
            border: '1px solid #334155',
            borderRadius: 6,
            fontSize: 12,
          }}
        />
        <Bar dataKey="count" radius={[4, 4, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

// ----- Rule bar chart -----

function RuleBarChart({ data }: { data: Record<string, number> }) {
  const series = Object.entries(data)
    .map(([rule, count]) => ({
      rule: rule.replace(/_/g, ' '),
      count,
    }))
    .sort((a, b) => b.count - a.count);

  if (series.length === 0) {
    return (
      <div className="h-[240px] flex items-center justify-center text-[var(--color-muted)] text-sm">
        No open incidents.
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={240}>
      <BarChart data={series} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
        <XAxis dataKey="rule" stroke="#94a3b8" fontSize={11} />
        <YAxis stroke="#94a3b8" fontSize={12} allowDecimals={false} />
        <Tooltip
          contentStyle={{
            backgroundColor: '#1e293b',
            border: '1px solid #334155',
            borderRadius: 6,
            fontSize: 12,
          }}
        />
        <Bar dataKey="count" fill="#3b82f6" radius={[4, 4, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

// ----- Timeline line chart -----

interface TimelineProps {
  points: Array<{
    bucket: string;
    event_count: number;
    incident_count: number;
  }>;
}

function ActivityLineChart({ points }: TimelineProps) {
  const series = points.map((p) => ({
    time: new Date(p.bucket).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
    }),
    events: p.event_count,
    incidents: p.incident_count,
  }));

  if (series.length === 0) {
    return (
      <div className="h-[280px] flex items-center justify-center text-[var(--color-muted)] text-sm">
        No activity in the selected window.
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={280}>
      <LineChart data={series} margin={{ top: 10, right: 20, left: -10, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
        <XAxis dataKey="time" stroke="#94a3b8" fontSize={11} />
        <YAxis stroke="#94a3b8" fontSize={12} allowDecimals={false} />
        <Tooltip
          contentStyle={{
            backgroundColor: '#1e293b',
            border: '1px solid #334155',
            borderRadius: 6,
            fontSize: 12,
          }}
        />
        <Legend
          wrapperStyle={{ fontSize: 12, color: '#94a3b8' }}
        />
        <Line
          type="monotone"
          dataKey="events"
          stroke="#3b82f6"
          strokeWidth={2}
          dot={false}
          name="Events"
        />
        <Line
          type="monotone"
          dataKey="incidents"
          stroke="#ef4444"
          strokeWidth={2}
          dot={{ r: 3 }}
          name="Incidents"
        />
      </LineChart>
    </ResponsiveContainer>
  );
}