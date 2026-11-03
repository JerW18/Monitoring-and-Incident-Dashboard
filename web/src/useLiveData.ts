import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchHistory,
  fetchHosts,
  fetchIncidents,
  type Host,
  type Incident,
  type MetricsEvent,
  type Point,
} from "./api";

export const CHART_METRIC = "cpu";
export const CHART_WINDOW_MS = 10 * 60 * 1000;
// A host going down publishes no event, because its Redis key just expires. Only a
// refetch can turn its tile red.
const HOST_POLL_MS = 5000;
const RECONNECT_MS = 2000;

/** Merges by timestamp, so events that arrive during a history fetch aren't lost or doubled. */
function mergePoints(current: Point[], incoming: Point[], now: number): Point[] {
  const byTime = new Map(current.map((p) => [p.t, p]));
  for (const point of incoming) byTime.set(point.t, point);
  return [...byTime.values()]
    .filter((p) => p.t >= now - CHART_WINDOW_MS)
    .sort((a, b) => a.t - b.t);
}

export type LiveData = {
  connected: boolean;
  hosts: Host[];
  hostsAvailable: boolean;
  incidents: Incident[];
  points: Point[];
  refreshIncidents: () => Promise<void>;
  selectedHost: string | null;
};

/** Charts the chosen host, or the first host until someone chooses one. */
export function useLiveData(chosenHost: string | null): LiveData {
  const [connected, setConnected] = useState(false);
  const [hosts, setHosts] = useState<Host[]>([]);
  const [hostsAvailable, setHostsAvailable] = useState(true);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [points, setPoints] = useState<Point[]>([]);
  // Bumped on every resync, to refetch the chart history.
  const [resyncCount, setResyncCount] = useState(0);
  const selectedHost = chosenHost ?? hosts[0]?.id ?? null;

  // The event handlers outlive any one render, so they read the selection through a ref.
  const selectedRef = useRef(selectedHost);
  selectedRef.current = selectedHost;

  const refreshHosts = useCallback(async () => {
    try {
      setHosts(await fetchHosts());
      setHostsAvailable(true);
    } catch {
      setHostsAvailable(false); // keep the last known hosts, and say they may be stale
    }
  }, []);

  const refreshIncidents = useCallback(async () => {
    try {
      setIncidents(await fetchIncidents());
    } catch {
      // The connection indicator already shows the API is unreachable, and resync refetches.
    }
  }, []);

  useEffect(() => {
    let source: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;

    const connect = () => {
      source = new EventSource("/v1/events");

      // Always the first event, sent once the subscription is live, so fetching now
      // can't miss anything. Sent again whenever events may have been lost.
      source.addEventListener("resync", () => {
        setConnected(true);
        void refreshHosts();
        void refreshIncidents();
        setResyncCount((count) => count + 1);
      });

      source.addEventListener("incident", () => void refreshIncidents());

      source.addEventListener("metrics", (message: MessageEvent<string>) => {
        const { samples } = JSON.parse(message.data) as MetricsEvent;
        setHosts((current) =>
          current.map((host) => {
            const own = samples.filter((s) => s.host_id === host.id);
            if (own.length === 0) return host;
            const metrics = { ...host.metrics };
            for (const s of own) metrics[s.name] = s.value;
            return { ...host, status: "up", metrics };
          }),
        );
        const chartable = samples
          .filter((s) => s.host_id === selectedRef.current && s.name === CHART_METRIC)
          .map((s) => ({ t: Date.parse(s.ts), value: s.value }));
        if (chartable.length > 0) {
          setPoints((current) => mergePoints(current, chartable, Date.now()));
        }
      });

      source.onerror = () => {
        setConnected(false);
        // The browser retries a dropped connection by itself, but gives up for good after
        // an error response such as a 502 during an API restart.
        if (source?.readyState === EventSource.CLOSED) {
          retry = setTimeout(connect, RECONNECT_MS);
        }
      };
    };

    connect();
    return () => {
      clearTimeout(retry);
      source?.close();
    };
  }, [refreshHosts, refreshIncidents]);

  useEffect(() => {
    const timer = setInterval(() => void refreshHosts(), HOST_POLL_MS);
    return () => clearInterval(timer);
  }, [refreshHosts]);

  // Switching hosts empties the chart at once, rather than showing the old host's data.
  useEffect(() => setPoints([]), [selectedHost]);

  useEffect(() => {
    if (!selectedHost) return;
    // Aborting discards a slow response for a previous selection.
    const controller = new AbortController();
    const since = new Date(Date.now() - CHART_WINDOW_MS);
    fetchHistory(selectedHost, CHART_METRIC, since, controller.signal)
      .then((history) => setPoints((current) => mergePoints(current, history, Date.now())))
      .catch(() => {
        // Aborted, or the API is unreachable. The next resync fetches it again.
      });
    return () => controller.abort();
  }, [selectedHost, resyncCount]);

  return { connected, hosts, hostsAvailable, incidents, points, refreshIncidents, selectedHost };
}
