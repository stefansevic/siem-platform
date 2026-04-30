/**
 * Reusable empty-state placeholder.
 *
 * Shown when a list query returns zero rows. Lets the user know that
 * the system is working — there's just nothing to show given the
 * current filters or activity.
 */

import type { ComponentType } from 'react';

interface Props {
  Icon: ComponentType<{ size?: number; className?: string }>;
  title: string;
  description?: string;
}

export function EmptyState({ Icon, title, description }: Props) {
  return (
    <div className="flex flex-col items-center justify-center py-16 px-6 text-center">
      <div className="rounded-full bg-[var(--color-bg)] border border-[var(--color-border)] p-4 mb-4">
        <Icon size={28} className="text-[var(--color-muted)]" />
      </div>
      <h3 className="text-base font-medium text-[var(--color-text)] mb-1.5">
        {title}
      </h3>
      {description && (
        <p className="text-sm text-[var(--color-muted)] max-w-sm">
          {description}
        </p>
      )}
    </div>
  );
}