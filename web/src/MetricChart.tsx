import { useEffect, useRef, useState, type PointerEvent } from "react";
import type { Point } from "./api";
import { CHART_WINDOW_MS } from "./useLiveData";

type Props = { hostId: string; points: Point[] };

const HEIGHT = 240;
const MARGIN = { top: 12, right: 52, bottom: 26, left: 40 };
const Y_TICKS = [0, 25, 50, 75, 100];
const X_TICK_MS = 2 * 60 * 1000;
// Samples further apart than this are drawn as a break: an unbroken line would claim data
// we don't have. It matches the alert engine's largest allowed gap.
const MAX_GAP_MS = 10_000;

function formatTime(t: number, withSeconds = false): string {
  return new Date(t).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: withSeconds ? "2-digit" : undefined,
  });
}

function linePath(points: Point[], x: (t: number) => number, y: (v: number) => number) {
  return points
    .map((p, i) => {
      const previous = points[i - 1];
      const moveTo = !previous || p.t - previous.t > MAX_GAP_MS;
      return `${moveTo ? "M" : "L"}${x(p.t).toFixed(1)},${y(p.value).toFixed(1)}`;
    })
    .join("");
}

export function MetricChart({ hostId, points }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const [hover, setHover] = useState<Point | null>(null);

  useEffect(() => {
    const element = container.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setWidth(entry.contentRect.width);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // The time axis keeps moving even when a stopped host sends nothing.
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const plotWidth = Math.max(width - MARGIN.left - MARGIN.right, 1);
  const plotHeight = HEIGHT - MARGIN.top - MARGIN.bottom;
  const start = now - CHART_WINDOW_MS;
  const x = (t: number) => MARGIN.left + ((t - start) / CHART_WINDOW_MS) * plotWidth;
  const y = (value: number) =>
    MARGIN.top + plotHeight - (Math.min(Math.max(value, 0), 100) / 100) * plotHeight;

  const visible = points.filter((p) => p.t >= start);
  const latest = visible[visible.length - 1];

  const xTicks: number[] = [];
  for (let t = Math.ceil(start / X_TICK_MS) * X_TICK_MS; t <= now; t += X_TICK_MS) xTicks.push(t);

  const onPointerMove = (event: PointerEvent<SVGSVGElement>) => {
    if (visible.length === 0) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const t = start + ((event.clientX - bounds.left - MARGIN.left) / plotWidth) * CHART_WINDOW_MS;
    const nearest = visible.reduce((best, p) =>
      Math.abs(p.t - t) < Math.abs(best.t - t) ? p : best,
    );
    setHover(nearest);
  };

  const hoverLeft = hover ? Math.min(x(hover.t) + 10, Math.max(width - 120, 0)) : 0;

  return (
    <figure className="chart">
      <figcaption>
        <span className="chart-title">CPU, {hostId}</span>
        <span className="chart-subtitle">Percent, last 10 minutes</span>
      </figcaption>
      <div className="chart-plot" ref={container}>
        {width > 0 && (
          <svg
            width={width}
            height={HEIGHT}
            role="img"
            aria-label={
              latest
                ? `CPU for ${hostId} over the last 10 minutes. Latest value ${latest.value.toFixed(0)} percent.`
                : `No recent CPU data for ${hostId}.`
            }
            onPointerMove={onPointerMove}
            onPointerLeave={() => setHover(null)}
          >
            {Y_TICKS.map((tick) => (
              <g key={tick}>
                <line
                  className="chart-grid"
                  x1={MARGIN.left}
                  x2={MARGIN.left + plotWidth}
                  y1={y(tick)}
                  y2={y(tick)}
                />
                <text className="chart-tick" x={MARGIN.left - 8} y={y(tick) + 4} textAnchor="end">
                  {tick}
                </text>
              </g>
            ))}
            {xTicks.map((tick) => (
              <text
                key={tick}
                className="chart-tick"
                x={x(tick)}
                y={HEIGHT - 8}
                textAnchor="middle"
              >
                {formatTime(tick)}
              </text>
            ))}
            <path className="chart-line" d={linePath(visible, x, y)} />
            {latest && (
              <>
                <circle className="chart-dot" cx={x(latest.t)} cy={y(latest.value)} r={4} />
                <text className="chart-end-label" x={x(latest.t) + 9} y={y(latest.value) + 4}>
                  {latest.value.toFixed(0)}%
                </text>
              </>
            )}
            {hover && (
              <>
                <line
                  className="chart-crosshair"
                  x1={x(hover.t)}
                  x2={x(hover.t)}
                  y1={MARGIN.top}
                  y2={MARGIN.top + plotHeight}
                />
                <circle className="chart-dot" cx={x(hover.t)} cy={y(hover.value)} r={4} />
              </>
            )}
          </svg>
        )}
        {hover && (
          <div className="chart-tooltip" style={{ left: hoverLeft, top: MARGIN.top }}>
            <strong>{hover.value.toFixed(1)}%</strong>
            <span>{formatTime(hover.t, true)}</span>
          </div>
        )}
        {visible.length === 0 && <p className="chart-empty">No recent data for this host.</p>}
      </div>
      <details className="chart-table">
        <summary>Show recent values as a table</summary>
        <table>
          <thead>
            <tr>
              <th scope="col">Time</th>
              <th scope="col">CPU</th>
            </tr>
          </thead>
          <tbody>
            {visible
              .slice(-10)
              .reverse()
              .map((p) => (
                <tr key={p.t}>
                  <td>{formatTime(p.t, true)}</td>
                  <td>{p.value.toFixed(1)}%</td>
                </tr>
              ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}
