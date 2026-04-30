/**
 * TypeScript types matching the API Gateway DTO models.
 * Keep in sync with services/api-gateway/app/models.py
 */

export type Severity = 'low' | 'medium' | 'high' | 'critical';
export type IncidentStatus = 'open' | 'acknowledged' | 'closed' | 'false_positive';

// ----- Events -----

export interface Event {
  id: string;
  timestamp: string;
  event_category: string;
  event_outcome: string | null;
  event_action: string | null;
  source_ip: string | null;
  user_name: string | null;
  http_method: string | null;
  url_path: string | null;
  http_response_status_code: number | null;
  user_agent: string | null;
  log_source: string;
}

export interface EventList {
  items: Event[];
  total: number;
  page: number;
  page_size: number;
}

// ----- Incidents -----

export interface Incident {
  id: string;
  rule_name: string;
  rule_version: string | null;
  severity: Severity;
  first_event_at: string;
  last_event_at: string;
  detected_at: string;
  source_ip: string | null;
  target_user_name: string | null;
  event_count: number;
  details: Record<string, unknown> | null;
  contributing_events: string[] | null;
  status: IncidentStatus;
  notes: string | null;
}

export interface IncidentList {
  items: Incident[];
  total: number;
  page: number;
  page_size: number;
}

export interface IncidentStatusUpdate {
  status: IncidentStatus;
  notes?: string;
}

// ----- Stats -----

export interface StatsSummary {
  events_total: number;
  events_last_hour: number;
  incidents_open: number;
  incidents_total: number;
  incidents_by_severity: Record<string, number>;
  incidents_by_rule: Record<string, number>;
}

export interface TimeBucket {
  bucket: string;
  event_count: number;
  incident_count: number;
}

export interface StatsTimeseries {
  interval_seconds: number;
  points: TimeBucket[];
}

// ----- Rules -----

export interface Rule {
  name: string;
  description: string;
  severity: Severity;
  threshold: number;
  window_seconds: number;
}