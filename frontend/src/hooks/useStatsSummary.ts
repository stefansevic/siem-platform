/**
 * Stats summary cached and shared across components.
 *
 * Why a custom hook: both Sidebar (badge counts) and Dashboard (cards)
 * read /stats/summary. Without sharing, each instance would poll on
 * its own. This hook polls once and exposes the result.
 *
 * Note: kept simple — no Context provider, no tanstack-query. Each
 * caller hits its own usePolling, but they all hit the same endpoint
 * which the API Gateway can serve trivially.
 */

import { fetchStatsSummary } from '../api/client';
import { usePolling } from './usePolling';

const POLL_MS = 5000;

export function useStatsSummary() {
  return usePolling(fetchStatsSummary, POLL_MS);
}