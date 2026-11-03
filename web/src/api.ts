export type Host = {
  id: string;
  status: "up" | "down";
  metrics: Record<string, number>;
  last_seen: string;
};

export type IncidentStatus = "open" | "acknowledged" | "resolved";

export type Incident = {
  id: number;
  host_id: string;
  rule_name: string;
  status: IncidentStatus;
  message: string;
  opened_at: string;
  acknowledged_at: string | null;
  resolved_at: string | null;
};

export type IncidentAction = "ack" | "resolve";

// One chart point. t is epoch milliseconds: the history endpoint and the event stream
// format timestamps differently, so strings can't be compared.
export type Point = { t: number; value: number };

export type MetricsEvent = {
  samples: { host_id: string; name: string; value: number; ts: string }[];
};

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { signal });
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return (await response.json()) as T;
}

export async function fetchHosts(): Promise<Host[]> {
  return (await getJson<{ hosts: Host[] }>("/v1/hosts")).hosts;
}

export async function fetchIncidents(): Promise<Incident[]> {
  return (await getJson<{ incidents: Incident[] }>("/v1/incidents")).incidents;
}

export async function fetchHistory(
  hostId: string,
  metric: string,
  since: Date,
  signal: AbortSignal,
): Promise<Point[]> {
  const query = new URLSearchParams({
    host_id: hostId,
    name: metric,
    since: since.toISOString(),
  });
  const body = await getJson<{ samples: { ts: string; value: number }[] }>(
    `/v1/metrics?${query}`,
    signal,
  );
  return body.samples.map((s) => ({ t: Date.parse(s.ts), value: s.value }));
}

/** Returns null on success, or the reason the action was refused. */
export async function postIncidentAction(
  id: number,
  action: IncidentAction,
): Promise<string | null> {
  const response = await fetch(`/v1/incidents/${id}/${action}`, { method: "POST" });
  if (response.ok) return null;
  const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
  return typeof body?.detail === "string" ? body.detail : `HTTP ${response.status}`;
}
