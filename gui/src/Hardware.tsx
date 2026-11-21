import { MicrosegTcam } from "./MicrosegState";
import { useEffect } from "react";
import { fetchHardware, type Hardware as HW, type OpticLevel, type OpticRow } from "./api";
import { useFetch } from "./useFetch";

// A switch's hardware: power, fans, CPU, memory, temperatures and optical
// modules, as the collector read them (each every 50 s). Thresholds are the switch's or the module's
// own. An optic's level counts only on a port in use; unused modules are shown grey, never as a problem.

const REFRESH_MS = 50_000;

export const LEVEL_PILL: Record<OpticLevel, string> = { ok: "pill good", warning: "pill warnpill", alarm: "pill bad", unused: "pill" };
const LEVEL_TEXT: Record<OpticLevel, string> = { ok: "ok", warning: "warning", alarm: "alarm", unused: "not in use" };

const fmt = (v: number | null | undefined, digits = 1) => (v === null || v === undefined ? "—" : v.toFixed(digits));

function Load({ usage, threshold }: { usage: number; threshold: number | null }) {
  return (
    <div className="load" title={threshold !== null ? `the switch's overload threshold is ${threshold} %` : undefined}>
      <span className="track"><i style={{ width: `${Math.min(100, usage)}%`, background: threshold !== null && usage >= threshold ? "var(--bad)" : undefined }} /></span>
      <span className="num faint">{usage} %{threshold !== null ? ` of ${threshold}` : ""}</span>
    </div>
  );
}

/** Received power on the module's own scale: alarm zones red, warning zones amber, the reading as a tick. */
export function RxBar({ optic }: { optic: OpticRow }) {
  const t = optic.thresholds.rx_power ?? {};
  const rx = optic.channels.map((c) => c.rx_power).filter((v): v is number => v !== undefined);
  if (t.low_alarm === undefined || t.high_alarm === undefined || rx.length === 0) return null;
  const lo = t.low_alarm - 4, hi = t.high_alarm + 2;
  const x = (v: number) => (Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo) * 100;
  return (
    <svg className="rxbar" viewBox="0 0 100 10" preserveAspectRatio="none" aria-hidden="true">
      <rect x={0} y={3} width={x(t.low_alarm)} height={4} fill="var(--bad)" opacity={0.35} />
      {t.low_warn !== undefined && <rect x={x(t.low_alarm)} y={3} width={x(t.low_warn) - x(t.low_alarm)} height={4} fill="var(--warn)" opacity={0.35} />}
      <rect x={x(t.low_warn ?? t.low_alarm)} y={3} width={x(t.high_warn ?? t.high_alarm) - x(t.low_warn ?? t.low_alarm)} height={4} fill="var(--good)" opacity={0.35} />
      {t.high_warn !== undefined && <rect x={x(t.high_warn)} y={3} width={x(t.high_alarm) - x(t.high_warn)} height={4} fill="var(--warn)" opacity={0.35} />}
      <rect x={x(t.high_alarm)} y={3} width={100 - x(t.high_alarm)} height={4} fill="var(--bad)" opacity={0.35} />
      {rx.map((v, i) => <rect key={i} x={x(v) - 0.8} y={0} width={1.6} height={10} fill="var(--text)" />)}
    </svg>
  );
}

export function Hardware({ name }: { name: string }) {
  const data = useFetch(() => fetchHardware(name), [name]);
  useEffect(() => {
    const id = window.setInterval(data.reload, REFRESH_MS);
    return () => window.clearInterval(id);
  }, [data.reload]);
  if (data.error) return <p className="error">{data.error}</p>;
  if (!data.data) return <p className="muted">Loading…</p>;
  const h: HW = data.data;
  if (!h.collected) {
    return <p className="empty muted">Nothing collected yet: the collector reads the hardware of active, pinned switches every 50 s.</p>;
  }
  const used = h.optics.filter((o) => o.in_use);
  const unused = h.optics.filter((o) => !o.in_use);

  return (
    <div className="hardware" data-tour="hardware">
      {h.problems.length > 0 && (
        <div className="notice error-box">
          <b>Needs a look:</b>
          <ul>{h.problems.map((x) => <li key={x}>{x}</li>)}</ul>
        </div>
      )}
      <div className="cols">
        <section className="panel">
          <header><h2>Power</h2><span className="hint">per power module</span></header>
          <ul className="changelist">
            {h.power.map((p) => (
              <li key={p.module}>
                <span><span className="mono">{p.module}</span> <span className="muted small">
                  {p.input !== null ? `${p.input} W in, ${fmt(p.output, 0)} W out` : "no input"}</span></span>
                <span className={p.ok ? "pill good" : "pill bad"}>{p.status ?? "?"}</span>
              </li>
            ))}
          </ul>
          <header><h2>Fans</h2></header>
          <ul className="changelist">
            {h.fans.map((f) => (
              <li key={f.fan}>
                <span><span className="mono">{f.fan}</span> <span className="muted small">
                  {f.present ? `${f.rpm ?? "?"} rpm, ${f.speed ?? "?"} %` : ""}</span></span>
                <span className={f.present ? "pill good" : "pill bad"}>{f.present ? "present" : "missing"}</span>
              </li>
            ))}
          </ul>
        </section>
        <section className="panel">
          <header><h2>CPU and memory</h2><span className="hint">per board</span></header>
          <dl className="settings empty">
            {h.cpu.map((c) => <div key={`c${c.slot}`}><dt>CPU, slot {c.slot}</dt><dd><Load usage={c.usage} threshold={c.threshold} /></dd></div>)}
            {h.memory.map((m) => <div key={`m${m.slot}`}><dt>Memory, slot {m.slot}</dt><dd><Load usage={m.usage} threshold={m.threshold} /></dd></div>)}
          </dl>
          <header><h2>Temperatures</h2><span className="hint">°C · thresholds minor / major / fatal</span></header>
          <div className="tablewrap">
            <table className="devices">
              <thead><tr><th>Slot</th><th>Sensor</th><th className="num">Now</th><th className="num">Thresholds</th><th /></tr></thead>
              <tbody>
                {h.sensors.map((s) => (
                  <tr key={`${s.slot}-${s.sensor}`}>
                    <td className="mono small">{s.slot}</td>
                    <td>{s.sensor === "power" ? "power supply" : s.sensor}</td>
                    <td className="num">{s.celsius}</td>
                    <td className="num faint">{"minor" in s.thresholds ? `${s.thresholds.minor} / ${s.thresholds.major} / ${s.thresholds.fatal}` : "—"}</td>
                    <td><span className={s.ok ? "pill good" : "pill bad"}>{s.ok ? "ok" : "hot"}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
      <section className="panel">
        <header><h2>Optical modules</h2><span className="hint">{used.length} on ports in use · {unused.length} not in use (never alarmed)</span></header>
        {h.optics.length === 0 ? <p className="empty muted">No optical module.</p> : <OpticsTable optics={[...used, ...unused]} />}
      </section>
      <MicrosegTcam name={name} />
      {h.refused.length > 0 && <p className="muted small">This switch does not answer: {h.refused.join(", ")} (left out).</p>}
    </div>
  );
}

export function OpticsTable({ optics }: { optics: OpticRow[] }) {
  return (
    <div className="tablewrap">
      <table className="devices optics">
        <thead><tr><th>Port</th><th>Module</th><th className="num">Rx dBm</th><th>Rx on its scale</th><th className="num">Tx dBm</th>
          <th className="num">Bias mA</th><th className="num">°C</th><th>Level</th></tr></thead>
        <tbody>
          {optics.map((o) => (
            <tr key={o.port} className={o.in_use ? "" : "faded"} title={o.reasons.join("\n") || undefined}>
              <td className="mono">{o.port}</td>
              <td className="small">{o.type ?? "?"} <span className="faint">{[o.vendor, o.part].filter(Boolean).join(" ")}</span></td>
              <td className="num">{o.channels.map((c) => fmt(c.rx_power)).join(" / ")}</td>
              <td><RxBar optic={o} /></td>
              <td className="num">{o.channels.map((c) => fmt(c.tx_power)).join(" / ")}</td>
              <td className="num">{o.channels.map((c) => fmt(c.bias)).join(" / ")}</td>
              <td className="num">{fmt(o.temperature)}</td>
              <td><span className={LEVEL_PILL[o.level]}>{LEVEL_TEXT[o.level]}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
