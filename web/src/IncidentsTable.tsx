import { useEffect, useState } from "react";
import { postIncidentAction, type Incident, type IncidentAction } from "./api";

type Props = { incidents: Incident[]; onChanged: () => Promise<void> };

const STATUS_ICON = { open: "▲", acknowledged: "◐", resolved: "✓" } as const;
const ERROR_VISIBLE_MS = 8000;

function formatTime(iso: string | null): string {
  return iso ? new Date(iso).toLocaleTimeString() : "–";
}

export function IncidentsTable({ incidents, onChanged }: Props) {
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  // A message about one click shouldn't linger long after it stopped being relevant.
  useEffect(() => {
    if (!error) return;
    const timer = setTimeout(() => setError(null), ERROR_VISIBLE_MS);
    return () => clearTimeout(timer);
  }, [error]);

  const act =async (incident: Incident, action: IncidentAction) => {
    setBusy(incident.id);
    setError(null);
    try {
      const refused = await postIncidentAction(incident.id, action);
      // Refused usually means it changed under us, such as auto-resolving just before the click.
      if (refused) setError(`Incident #${incident.id}: ${refused}`);
    } catch {
      setError(`Incident #${incident.id}: the API couldn't be reached.`);
    }
    await onChanged();
    setBusy(null);
  };

  return (
    <>
      {error && (
        <p className="action-error" role="alert">
          {error}
        </p>
      )}
      {incidents.length === 0 ? (
        <p className="empty">No incidents.</p>
      ) : (
        <div className="table-scroll">
          <table className="incidents">
            <thead>
              <tr>
                <th scope="col">Status</th>
                <th scope="col">Host</th>
                <th scope="col">Rule</th>
                <th scope="col">Condition</th>
                <th scope="col">Opened</th>
                <th scope="col">Resolved</th>
                <th scope="col">
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {incidents.map((incident) => (
                <tr key={incident.id} className={`incident-${incident.status}`}>
                  <td>
                    <span className={`status status-${incident.status}`}>
                      <span aria-hidden="true">{STATUS_ICON[incident.status]}</span>{" "}
                      {incident.status}
                    </span>
                  </td>
                  <td>{incident.host_id}</td>
                  <td>{incident.rule_name}</td>
                  <td>{incident.message}</td>
                  <td>{formatTime(incident.opened_at)}</td>
                  <td>{formatTime(incident.resolved_at)}</td>
                  <td className="actions">
                    {incident.status === "open" && (
                      <button
                        type="button"
                        disabled={busy === incident.id}
                        onClick={() => void act(incident, "ack")}
                      >
                        Ack
                      </button>
                    )}
                    {incident.status !== "resolved" && (
                      <button
                        type="button"
                        disabled={busy === incident.id}
                        onClick={() => void act(incident, "resolve")}
                      >
                        Resolve
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
