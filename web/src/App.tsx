import { useState } from "react";
import { HostGrid } from "./HostGrid";
import { IncidentsTable } from "./IncidentsTable";
import { MetricChart } from "./MetricChart";
import { useLiveData } from "./useLiveData";

export function App() {
  const [chosenHost, setChosenHost] = useState<string | null>(null);
  const { connected, hosts, hostsAvailable, incidents, points, refreshIncidents, selectedHost } =
    useLiveData(chosenHost);

  const down = hosts.filter((host) => host.status === "down").length;
  const active = incidents.filter((incident) => incident.status !== "resolved").length;

  return (
    <main className="page">
      <header className="page-header">
        <h1>Monitoring Dashboard</h1>
        <span className={`connection ${connected ? "connection-live" : "connection-lost"}`}>
          <span aria-hidden="true">{connected ? "●" : "○"}</span>{" "}
          {connected ? "Live" : "Reconnecting…"}
        </span>
      </header>

      {!connected && (
        <p className="banner" role="status">
          Not receiving live updates. What's shown below may be out of date.
        </p>
      )}
      {connected && !hostsAvailable && (
        <p className="banner" role="status">
          Host status is unavailable. The tiles show the last known state.
        </p>
      )}

      <section aria-labelledby="hosts-heading">
        <h2 id="hosts-heading">
          Hosts <span className="count">{hosts.length - down} up, {down} down</span>
        </h2>
        <HostGrid hosts={hosts} selected={selectedHost} onSelect={setChosenHost} />
      </section>

      {selectedHost && (
        <section aria-label="Metric chart">
          <MetricChart hostId={selectedHost} points={points} />
        </section>
      )}

      <section aria-labelledby="incidents-heading">
        <h2 id="incidents-heading">
          Incidents <span className="count">{active} active</span>
        </h2>
        <IncidentsTable incidents={incidents} onChanged={refreshIncidents} />
      </section>
    </main>
  );
}
