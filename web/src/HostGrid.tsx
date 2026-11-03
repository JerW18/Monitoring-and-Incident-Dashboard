import type { Host } from "./api";

type Props = {
  hosts: Host[];
  selected: string | null;
  onSelect: (id: string) => void;
};

const METRICS = ["cpu", "mem", "disk"];

export function HostGrid({ hosts, selected, onSelect }: Props) {
  if (hosts.length === 0) {
    return <p className="empty">No hosts have reported yet.</p>;
  }
  return (
    <ul className="host-grid">
      {hosts.map((host) => (
        <li key={host.id}>
          <button
            type="button"
            className={`host-tile host-${host.status}`}
            aria-pressed={host.id === selected}
            onClick={() => onSelect(host.id)}
          >
            <span className="host-head">
              <span className="host-name">{host.id}</span>
              <span className={`status status-${host.status}`}>
                <span aria-hidden="true">{host.status === "up" ? "●" : "✕"}</span>{" "}
                {host.status === "up" ? "Up" : "Down"}
              </span>
            </span>
            <span className="host-metrics">
              {METRICS.map((metric) => (
                <span key={metric} className="host-metric">
                  <span className="metric-label">{metric}</span>
                  <span className="metric-value">
                    {host.metrics[metric] === undefined
                      ? "–"
                      : `${host.metrics[metric].toFixed(0)}%`}
                  </span>
                </span>
              ))}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
