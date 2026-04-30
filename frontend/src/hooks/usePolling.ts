/**
 * Periodically calls an async fetcher and exposes loading/error/data state.
 *
 * Cleans itself up on unmount or when the fetcher reference changes.
 * Uses a ref to ignore late responses if the component already unmounted.
 *
 * Usage:
 *   const { data, error, loading, lastUpdated } = usePolling(
 *     () => fetchStatsSummary(),
 *     5000,
 *   );
 */

import { useEffect, useRef, useState } from 'react';

export interface PollingState<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
  /** ISO timestamp of the most recent successful fetch, or null if never. */
  lastUpdated: string | null;
  /** Re-runs the fetcher immediately. */
  refresh: () => void;
}

export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
): PollingState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);

  const [tick, setTick] = useState(0);

  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    let cancelled = false;

    const runOnce = async () => {
      try {
        const result = await fetcherRef.current();
        if (!cancelled) {
          setData(result);
          setError(null);
          setLastUpdated(new Date().toISOString());
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err : new Error(String(err)));
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    runOnce();
    const id = setInterval(runOnce, intervalMs);

    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [intervalMs, tick]);

  return {
    data,
    error,
    loading,
    lastUpdated,
    refresh: () => setTick((t) => t + 1),
  };
}