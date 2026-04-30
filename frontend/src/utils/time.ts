/**
 * Time formatting helpers used across tables.
 *
 * Returns a relative phrase ("5m ago") for compact display, plus an
 * absolute timestamp string for tooltips.
 *
 * Self-contained — no date-fns / dayjs dependency to keep bundle small.
 */

export function formatRelative(isoString: string): string {
  const then = new Date(isoString).getTime();
  const now = Date.now();
  const diffSeconds = Math.round((now - then) / 1000);

  if (diffSeconds < 0) return 'in the future';
  if (diffSeconds < 5) return 'just now';
  if (diffSeconds < 60) return `${diffSeconds}s ago`;

  const minutes = Math.floor(diffSeconds / 60);
  if (minutes < 60) return `${minutes}m ago`;

  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;

  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;

  // Older than a month — show date directly
  return new Date(isoString).toLocaleDateString();
}

export function formatAbsolute(isoString: string): string {
  return new Date(isoString).toLocaleString([], {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}