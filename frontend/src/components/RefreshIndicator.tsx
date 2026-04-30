/**
 * Compact "last updated" indicator with a spinning icon while refreshing.
 *
 * Auto-tracks "X seconds ago" so the user can see when the next refresh
 * is due without staring at a separate clock.
 */

import { useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';

interface Props {
  loading: boolean;
  /** ISO timestamp string of the most recent successful fetch. */
  lastUpdated: string | null;
}

export function RefreshIndicator({ loading, lastUpdated }: Props) {
  // Re-render every second so "Xs ago" stays current.
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="flex items-center gap-2 text-xs text-[var(--color-muted)]">
      <RefreshCw
        size={12}
        className={loading ? 'animate-spin text-[var(--color-accent)]' : ''}
      />
      <span>{describe(loading, lastUpdated)}</span>
    </div>
  );
}

function describe(loading: boolean, lastUpdated: string | null): string {
  if (loading && !lastUpdated) return 'Loading…';
  if (loading) return 'Refreshing…';
  if (!lastUpdated) return 'Never';

  const diff = Math.round((Date.now() - new Date(lastUpdated).getTime()) / 1000);
  if (diff < 2)  return 'Updated just now';
  if (diff < 60) return `Updated ${diff}s ago`;
  if (diff < 3600) return `Updated ${Math.floor(diff / 60)}m ago`;
  return `Updated ${Math.floor(diff / 3600)}h ago`;
}