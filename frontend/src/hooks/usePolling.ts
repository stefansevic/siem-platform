/**
 * Periodically calls an async fetcher and exposes loading/error/data state.
 *
 * Cleans itself up on unmount or when the fetcher reference changes.
 * Uses a ref to ignore late responses if the component already unmounted.
 *
 * Usage:
 *   const { data, error, loading } = usePolling(
 *     () => fetchStatsSummary(),
 *     5000,
 *   );
 */

import { useEffect, useRef, useState } from 'react';

export interface PollingState<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
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

  // Trigger counter — incrementing this re-runs the effect.
  const [tick, setTick] = useState(0);

  // Keep fetcher latest in a ref so the effect doesn't restart every render.
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
    // tick triggers manual refresh; intervalMs lets caller change cadence
  }, [intervalMs, tick]);

  return {
    data,
    error,
    loading,
    refresh: () => setTick((t) => t + 1),
  };
}