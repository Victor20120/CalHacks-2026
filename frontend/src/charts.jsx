import { useLayoutEffect, useRef, useState } from "react";

// Line charts of a run, one per measure (small multiples, one shared step axis).
// One series per chart: one colour, no legend, the title names the measure. Warning and
// Critical alerts are marked at their step in the status colours, with a label.
// Hovering (or arrow keys on a focused chart) shows the value at that step in every chart.
// `full` sets the axes, so a replay of `run` grows inside a fixed frame.

const HEIGHT = 200;
const M = { top: 24, right: 16, bottom: 26, left: 64 };
const SEVERITY_RANK = { informational: 0, warning: 1, critical: 2 };
const SEVERITY_LABEL = { warning: "Warning", critical: "Critical" };

export function RunCharts({ run, full = run, limit = 3 }) {
  const [hoverStep, setHoverStep] = useState(null);
  return (
    <div className="run-charts">
      {full.metrics.slice(0, limit).map((metric) => (
        <LineChart
          key={metric}
          metric={metric}
          points={run.series[metric] || []}
          frame={full.series[metric] || []}
          markers={markersFor(run, metric)}
          frameMarkers={markersFor(full, metric)}
          hoverStep={hoverStep}
          onHover={setHoverStep}
        />
      ))}
    </div>
  );
}

// The same numbers as a table, so no value is only reachable by hovering.
export function RunNumbers({ run, limit = 3 }) {
  const metrics = run.metrics.slice(0, limit);
  const rows = new Map();
  for (const metric of metrics) {
    for (const [step, value] of run.series[metric] || []) {
      if (!rows.has(step)) rows.set(step, {});
      rows.get(step)[metric] = value;
    }
  }
  return (
    <table className="numbers-table">
      <thead>
        <tr>
          <th>Step</th>
          {metrics.map((m) => (
            <th key={m}>{m}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {[...rows.entries()]
          .sort((a, b) => a[0] - b[0])
          .map(([step, values]) => (
            <tr key={step}>
              <td>{step}</td>
              {metrics.map((m) => (
                <td key={m}>{m in values ? formatValue(values[m]) : ""}</td>
              ))}
            </tr>
          ))}
      </tbody>
    </table>
  );
}

function LineChart({ metric, points, frame, markers, frameMarkers, hoverStep, onHover }) {
  const [box, width] = useWidth();
  const frameSteps = frame.map(([s]) => s);
  const steps = points.map(([s]) => s);
  const finite = frame.filter(([, v]) => v !== null).map(([, v]) => v);

  let svg = null;
  let tip = null;
  let scale = null;
  if (width > 0 && frameSteps.length) {
    const plotRight = width - M.right;
    const plotBottom = HEIGHT - M.bottom;
    const x0 = Math.min(...frameSteps);
    const x1 = Math.max(x0 + 1, ...frameSteps, ...frameMarkers.map((m) => m.step));
    const x = (s) => M.left + ((s - x0) / (x1 - x0)) * (plotRight - M.left);
    scale = finite.length ? yScale(finite, M.top, plotBottom) : null;

    let d = "";
    let pen = false;
    for (const [s, v] of points) {
      if (v === null || !scale || (scale.log && v <= 0)) {
        pen = false; // a NaN/Inf point breaks the line; its marker shows where
        continue;
      }
      d += `${pen ? "L" : "M"}${x(s).toFixed(1)},${scale.at(v).toFixed(1)}`;
      pen = true;
    }

    let labelEnd = -Infinity;
    const markerMarks = markers.map((m) => {
      const mx = x(m.step);
      const text = `${SEVERITY_LABEL[m.severity]} · ${m.step}`;
      const textWidth = text.length * 6.4 + 12;
      const flip = mx + textWidth > width;
      const start = flip ? mx - textWidth : mx;
      const showLabel = start > labelEnd + 4; // labels that would collide go to the tooltip
      if (showLabel) labelEnd = start + textWidth;
      return (
        <g key={m.step} className={`marker marker-${m.severity}`}>
          <line x1={mx} x2={mx} y1={M.top} y2={plotBottom} />
          {showLabel && (
            <>
              <rect x={flip ? mx - 8 : mx + 2} y={M.top - 15} width={6} height={6} />
              <text x={flip ? mx - 12 : mx + 12} y={M.top - 9} textAnchor={flip ? "end" : "start"}>
                {text}
              </text>
            </>
          )}
        </g>
      );
    });

    let crosshair = null;
    const near = hoverStep === null ? null : nearest(points, hoverStep);
    if (near) {
      const [s, v] = near;
      const hx = x(s);
      const at = markers.filter((m) => m.step === s).flatMap((m) => m.texts);
      tip = (
        <div className="chart-tip line-tip" style={{ left: hx > width - 230 ? hx - 222 : hx + 12, top: M.top }}>
          <strong>{formatValue(v)}</strong>
          <span className="muted">step {s}</span>
          {at.map((t, i) => (
            <span key={i}>{t}</span>
          ))}
        </div>
      );
      crosshair = (
        <g className="crosshair">
          <line x1={hx} x2={hx} y1={M.top} y2={plotBottom} />
          {v !== null && scale && !(scale.log && v <= 0) && (
            <rect className="point" x={hx - 4} y={scale.at(v) - 4} width={8} height={8} />
          )}
        </g>
      );
    }

    const xTicks = linearTicks(x0, x1, 6).filter((t) => t >= x0 && t <= x1);
    svg = (
      <svg width={width} height={HEIGHT} role="img" aria-label={`${metric} by step`}>
        {scale &&
          scale.ticks.map((t, i) => (
            <g key={i}>
              <line className="grid" x1={M.left} x2={plotRight} y1={t.y} y2={t.y} />
              <text className="tick" x={M.left - 8} y={t.y + 4} textAnchor="end">
                {t.label}
              </text>
            </g>
          ))}
        <line className="axis" x1={M.left} x2={plotRight} y1={plotBottom} y2={plotBottom} />
        {xTicks.map((t) => (
          <text key={t} className="tick" x={x(t)} y={plotBottom + 17} textAnchor="middle">
            {t}
          </text>
        ))}
        {markerMarks}
        <path className="series" d={d} />
        {crosshair}
        <rect
          className="hit"
          x={M.left}
          y={M.top}
          width={Math.max(0, plotRight - M.left)}
          height={plotBottom - M.top}
          onPointerMove={(e) => {
            if (!points.length) return;
            const left = e.currentTarget.getBoundingClientRect().left;
            const step = x0 + ((e.clientX - left) / (plotRight - M.left)) * (x1 - x0);
            onHover(nearest(points, step)[0]);
          }}
          onPointerLeave={() => onHover(null)}
        />
      </svg>
    );
  }

  function onKeyDown(event) {
    if (!steps.length || (event.key !== "ArrowLeft" && event.key !== "ArrowRight")) return;
    event.preventDefault();
    const index = hoverStep === null ? steps.length - 1 : steps.indexOf(nearest(points, hoverStep)[0]);
    const next = Math.min(steps.length - 1, Math.max(0, index + (event.key === "ArrowRight" ? 1 : -1)));
    onHover(steps[next]);
  }

  return (
    <section className="chart-card">
      <h4>
        {metric} <span className="muted small">by step{scale?.log ? " · log scale" : ""}</span>
      </h4>
      <div
        className="chart-box"
        ref={box}
        tabIndex={steps.length ? 0 : -1}
        aria-label={`${metric} by step, ${steps.length} values. Left and right arrow keys read them.`}
        onKeyDown={onKeyDown}
        onFocus={() => hoverStep === null && steps.length && onHover(steps[steps.length - 1])}
        onBlur={() => onHover(null)}
      >
        {svg}
        {tip}
      </div>
    </section>
  );
}

// Alerts at a step for this measure (or for the whole run), plus NaN/Inf points; the worst severity wins.
function markersFor(run, metric) {
  const byStep = new Map();
  function add(step, severity, text) {
    const marker = byStep.get(step) || { step, severity, texts: [] };
    if (SEVERITY_RANK[severity] > SEVERITY_RANK[marker.severity]) marker.severity = severity;
    marker.texts.push(text);
    byStep.set(step, marker);
  }
  for (const a of run.alerts) {
    if (a.step !== null && a.severity !== "informational" && (!a.metric || a.metric === metric)) add(a.step, a.severity, a.message);
  }
  for (const [step, value] of run.series[metric] || []) {
    if (value === null && !byStep.has(step)) add(step, "critical", `${metric} is NaN or Inf at step ${step}.`);
  }
  return [...byStep.values()].sort((a, b) => a.step - b.step);
}

// Log scale when every value is positive and they span 1,000x or more (a blow-up does).
function yScale(values, top, bottom) {
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (min > 0 && max / min >= 1e3) {
    const lo = Math.floor(Math.log10(min));
    const hi = Math.max(lo + 1, Math.ceil(Math.log10(max)));
    const stride = Math.max(1, niceStep((hi - lo) / 4)); // whole powers of ten: 1e100, 1e200, ...
    const y = (p) => bottom - ((p - lo) / (hi - lo)) * (bottom - top);
    const ticks = [{ y: y(lo), label: powerLabel(lo) }];
    for (let p = Math.ceil((lo + stride / 2) / stride) * stride; p <= hi; p += stride) {
      ticks.push({ y: y(p), label: powerLabel(p) });
    }
    return { log: true, at: (v) => y(Math.log10(v)), ticks };
  }
  if (min === max) {
    min -= Math.abs(min) || 1;
    max += Math.abs(max) || 1;
  }
  const step = niceStep((max - min) / 4);
  const lo = Math.floor(min / step) * step;
  const hi = Math.ceil(max / step) * step;
  const y = (v) => bottom - ((v - lo) / (hi - lo)) * (bottom - top);
  const ticks = linearTicks(lo, hi, 5).map((t) => ({ y: y(t), label: formatTick(t) }));
  return { log: false, at: y, ticks };
}

function linearTicks(lo, hi, count) {
  const step = niceStep((hi - lo) / count);
  const ticks = [];
  for (let t = Math.ceil(lo / step) * step; t <= hi + step * 1e-9 && ticks.length < 12; t += step) {
    ticks.push(Number(t.toPrecision(12)));
  }
  return ticks;
}

function niceStep(raw) {
  if (!(raw > 0)) return 1;
  const power = 10 ** Math.floor(Math.log10(raw));
  const f = raw / power;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * power;
}

function nearest(points, step) {
  if (!points.length) return null;
  let best = points[0];
  for (const p of points) if (Math.abs(p[0] - step) < Math.abs(best[0] - step)) best = p;
  return best;
}

function powerLabel(p) {
  return p >= 0 && p <= 3 ? String(10 ** p) : `1e${p}`;
}

function formatTick(v) {
  return Math.abs(v) >= 1e5 || (v !== 0 && Math.abs(v) < 1e-3) ? v.toExponential(0) : String(v);
}

export function formatValue(v) {
  if (v === null || v === undefined) return "NaN or Inf";
  if (v !== 0 && (Math.abs(v) >= 1e5 || Math.abs(v) < 1e-3)) return v.toExponential(3);
  return String(Number(v.toPrecision(5)));
}

function useWidth() {
  const ref = useRef(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return undefined;
    setWidth(Math.floor(element.getBoundingClientRect().width));
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}
