/**
 * Axios HTTP client configured for the SIEM API Gateway.
 *
 * The base URL is taken from VITE_API_BASE_URL at build time.
 * Default points to the local docker-compose stack on port 8005.
 */

import axios from 'axios';
import type {
  Event,
  EventList,
  Incident,
  IncidentList,
  IncidentStatusUpdate,
  Rule,
  StatsSummary,
  StatsTimeseries,
} from './types';

const baseURL =
  (import.meta.env.VITE_API_BASE_URL as string) || 'http://localhost:8005';

const http = axios.create({
  baseURL,
  timeout: 10_000,
  headers: { 'Content-Type': 'application/json' },
});

// ----- Stats -----

export async function fetchStatsSummary(): Promise<StatsSummary> {
  const { data } = await http.get<StatsSummary>('/stats/summary');
  return data;
}

export async function fetchStatsTimeseries(
  minutes = 60,
  intervalSeconds = 60,
): Promise<StatsTimeseries> {
  const { data } = await http.get<StatsTimeseries>('/stats/timeseries', {
    params: { minutes, interval_seconds: intervalSeconds },
  });
  return data;
}

// ----- Events -----

/**
 * Filters for the Elasticsearch-backed event search endpoint.
 * Mirrors the parameters accepted by GET /events/search on the API
 * Gateway. All fields are optional; an empty query returns the
 * latest events.
 */
export interface EventSearchQuery {
  q?: string;
  source_ip?: string;
  user_name?: string;
  event_outcome?: 'success' | 'failure';
  http_method?: string;
  status_min?: number;
  status_max?: number;
  since?: string;   // ISO-8601 timestamp
  until?: string;   // ISO-8601 timestamp
  page?: number;
  page_size?: number;
}

export interface EventQuery {
  page?: number;
  page_size?: number;
  source_ip?: string;
  event_category?: string;
  event_outcome?: string;
  user_name?: string;
  status_code?: number;
  log_source?: string;
  since_minutes?: number;
}

export async function fetchEvents(query: EventQuery = {}): Promise<EventList> {
  const { data } = await http.get<EventList>('/events', { params: query });
  return data;
}

export async function fetchEvent(id: string): Promise<Event> {
  const { data } = await http.get<Event>(`/events/${id}`);
  return data;
}
/**
 * Fetch many events in parallel by ID.
 * Used by the incident detail modal to enrich contributing_events.
 * Failures (e.g. event was deleted) are skipped silently.
 */
export async function fetchEventsByIds(ids: string[]): Promise<Event[]> {
  const results = await Promise.allSettled(ids.map((id) => fetchEvent(id)));
  return results
    .filter((r): r is PromiseFulfilledResult<Event> => r.status === 'fulfilled')
    .map((r) => r.value);
}

/**
 * Search events via the Elasticsearch-backed endpoint.
 * Returns up to `page_size` matches sorted by timestamp descending.
 */
export async function searchEvents(
  query: EventSearchQuery = {},
): Promise<EventList> {
  const { data } = await http.get<EventList>('/events/search', {
    params: query,
  });
  return data;
}

// ----- Incidents -----

export interface IncidentQuery {
  page?: number;
  page_size?: number;
  status?: string;
  severity?: string;
  rule_name?: string;
  source_ip?: string;
  since_minutes?: number;
}

export async function fetchIncidents(
  query: IncidentQuery = {},
): Promise<IncidentList> {
  const { data } = await http.get<IncidentList>('/incidents', { params: query });
  return data;
}

export async function fetchIncident(id: string): Promise<Incident> {
  const { data } = await http.get<Incident>(`/incidents/${id}`);
  return data;
}

export async function updateIncidentStatus(
  id: string,
  body: IncidentStatusUpdate,
): Promise<Incident> {
  const { data } = await http.patch<Incident>(`/incidents/${id}/status`, body);
  return data;
}

// ----- Rules -----

export async function fetchRules(): Promise<Rule[]> {
  const { data } = await http.get<Rule[]>('/rules');
  return data;
}

// ----- Health -----

export async function fetchHealth(): Promise<{ status: string }> {
  const { data } = await http.get<{ status: string }>('/health');
  return data;
}