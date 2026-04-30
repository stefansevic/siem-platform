/**
 * Color-coded severity pill badge.
 */

import type { Severity } from '../api/types';

const STYLES: Record<Severity, string> = {
  low:      'bg-slate-500/20 text-slate-300 border-slate-500/40',
  medium:   'bg-amber-500/20 text-amber-300 border-amber-500/40',
  high:     'bg-orange-500/20 text-orange-300 border-orange-500/40',
  critical: 'bg-red-500/20 text-red-300 border-red-500/40',
};

interface Props {
  severity: Severity;
}

export function SeverityBadge({ severity }: Props) {
  return (
    <span
      className={[
        'inline-flex items-center px-2 py-0.5 rounded-full',
        'text-xs font-medium border uppercase tracking-wider',
        STYLES[severity] ?? STYLES.low,
      ].join(' ')}
    >
      {severity}
    </span>
  );
}