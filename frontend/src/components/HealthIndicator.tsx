/**
 * Small status dot showing whether the API Gateway is reachable.
 * Polls /health every 10 seconds.
 */

import { useEffect, useState } from 'react';
import { fetchHealth } from '../api/client';

type Status = 'unknown' | 'healthy' | 'unreachable';

export function HealthIndicator() {
  const [status, setStatus] = useState<Status>('unknown');

  useEffect(() => {
    let cancelled = false;

    const check = async () => {
      try {
        await fetchHealth();
        if (!cancelled) setStatus('healthy');
      } catch {
        if (!cancelled) setStatus('unreachable');
      }
    };

    check();
    const id = setInterval(check, 10_000);

    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const config: Record<Status, { dot: string; label: string; color: string }> = {
    unknown:     { dot: 'bg-slate-500',  label: 'Connecting…',  color: 'text-slate-400' },
    healthy:     { dot: 'bg-green-500',  label: 'API connected', color: 'text-green-400' },
    unreachable: { dot: 'bg-red-500',    label: 'API offline',   color: 'text-red-400' },
  };

  const { dot, label, color } = config[status];

  return (
    <div className="flex items-center gap-2 text-xs">
      <span className={`relative flex h-2 w-2`}>
        <span
          className={[
            'absolute inline-flex h-full w-full rounded-full opacity-75',
            status === 'healthy' ? 'bg-green-500 animate-ping' : '',
          ].join(' ')}
        />
        <span className={`relative inline-flex rounded-full h-2 w-2 ${dot}`} />
      </span>
      <span className={color}>{label}</span>
    </div>
  );
}