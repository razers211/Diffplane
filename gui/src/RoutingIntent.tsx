import { currentDraft } from "./draft";
import { useState, type FormEvent, type ReactNode } from "react";
import { fetchImports, fetchRoutingDocument, fetchRoutingTargets, fetchServices, importNow, saveRouting } from "./api";
import { RouteFlow } from "./RouteFlow";
import { RoutingSimple } from "./RoutingSimple";
import {
  count,
  keyOf,
  kindsSummary,
  names,
  parseAreas,
  parseNumber,
  putItem,
  putRule,
  removeItem,
  removeRule,
  shortError,
  targetLabel,
  type Action,
  type DeviceRouting,
  type ImportReport,
  type KeyedKind,
  type NamedKind,
  type Parsed,
  type RoutingDocument,
  type RoutingTarget,
} from "./routingIntent";
import { useCan } from "./role";
import { Tiles } from "./Tiles";
import { useFetch } from "./useFetch";

// Routing intent: per unpaired device or per M-LAG pair (identical on both leaves).
// Every save replaces the target's intent; the orchestrator validates it (only forms verified
// on the switch) and retires what is no longer there, which the next deploy removes.

function message(e: unknown): string {
  return e instanceof Error ? e.message.replace(/^\d+: /, "") : String(e);
}

const when = (at: number | null) => (at ? new Date(at * 1000).toLocaleString() : "not yet");
const href = (target: string) => `#/network/routing/${encodeURIComponent(target)}`;
const MAX32 = 4294967295;
// Placeholders while no fabric is chosen (hooks run before the early return).
const NO_TARGETS: RoutingTarget[] = [];
const NO_DOCUMENT: RoutingDocument = {};
const NO_IMPORTS: ImportReport[] = [];

function NoFabric() {
  return (
    <section>
      <h1 className="sr-only">Routing</h1>
      <p className="muted">Enter a fabric (top right) to see its routing intent.</p>
    </section>
  );
}

// -- list of targets ---------------------------------------------------------------------------

export function RoutingTargets({ site }: { site: string }) {
  const targets = useFetch(() => (site ? fetchRoutingTargets(site) : Promise.resolve(NO_TARGETS)), [site]);
  const doc = useFetch(() => (site ? fetchRoutingDocument(site) : Promise.resolve(NO_DOCUMENT)), [site]);
  const imports = useFetch(() => (site ? fetchImports(site) : Promise.resolve(NO_IMPORTS)), [site]);
  if (!site) return <NoFabric />;

  const list = targets.data ?? [];
  const objects = list.reduce((n, t) => n + count(doc.data?.devices?.[t.target]), 0);
  const pending = list.reduce((n, t) => n + count(doc.data?.retired?.[t.target]), 0);
  const reports = imports.data ?? [];
  const adopted = reports.reduce((n, r) => n + r.imported.length, 0);
  const unmanaged = reports.reduce((n, r) => n + r.unmanaged.length, 0);
  const failed = reports.filter((r) => r.error).length;

  return (
    <div className="page">
    {targets.data && (
      <Tiles
        label="Routing intent"
        tiles={[
          { key: "targets", label: "Devices and pairs", value: list.length, sub: `${list.filter((t) => t.target.includes("+")).length} M-LAG pairs · ${list.filter((t) => !t.target.includes("+")).length} single`, tone: "info", icon: "devices" },
          { key: "objects", label: "Objects in intent", value: objects, sub: "peers, policies, filters, routes, OSPF", tone: "info", icon: "routing" },
          { key: "pending", label: "Pending removal", value: pending, sub: pending ? "removed from intent, not yet deployed" : "nothing waiting", tone: pending ? "warn" : "good", icon: "deploys" },
          { key: "import", label: "Auto-import", value: adopted, sub: failed ? `${failed} switch${failed === 1 ? "" : "es"} could not be read` : `adopted · ${unmanaged} left unmanaged`, tone: failed ? "bad" : "good", icon: "drift" },
        ]}
      />
    )}
    <section>
      <div className="toolbar">
        <h1 className="sr-only">Routing</h1>
      </div>
      <p className="muted">
        BGP peers, policies, filters, aggregates, static routes and OSPF processes, per device; an M-LAG pair has one
        intent, generated identically on both leaves. Hand-made routing objects on a switch are imported automatically
        after each drift check and before each deploy.
      </p>
      {targets.error && <p className="error">Could not load targets: {targets.error}</p>}
      {doc.error && <p className="error">Could not load routing intent: {doc.error}</p>}
      {list.length > 0 && (
        <table className="devices" data-tour="routing-targets">
          <thead>
            <tr>
              <th>Device or pair</th>
              <th>Intent</th>
              <th>VPN instances</th>
            </tr>
          </thead>
          <tbody>
            {list.map((t) => {
              const kinds = kindsSummary(doc.data?.devices?.[t.target]);
              const retiring = count(doc.data?.retired?.[t.target]);
              return (
                <tr key={t.target} className="clickable" onClick={() => (window.location.hash = href(t.target))}>
                  <td>
                    <a className="mono" href={href(t.target)}>{targetLabel(t.target)}</a>{" "}
                    {t.target.includes("+") && <span className="chip accent">pair</span>}
                    <div className="small">
                      {t.devices.map((d) => (
                        <span key={d.name} className={`pill status-${d.status}`}>{d.status}</span>
                      ))}
                    </div>
                  </td>
                  <td>
                    {kinds.length === 0 && !retiring && <span className="faint">nothing yet</span>}
                    {kinds.map((k) => (
                      <span key={k} className="chip">{k}</span>
                    ))}{" "}
                    {retiring > 0 && <span className="chip warn">{retiring} pending removal</span>}
                  </td>
                  <td>
                    {t.vpns.length ? t.vpns.map((v) => <span key={v} className="chip mono">{v}</span>) : <span className="faint">global table only</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
      <ImportTable site={site} reports={imports.data ?? []} devices={null} onDone={imports.reload} />
    </div>
  );
}

// -- auto-import report --------------------------------------------------------------------------

function ImportTable({
  site,
  reports,
  devices,
  onDone,
}: {
  site: string;
  reports: ImportReport[];
  devices: RoutingTarget["devices"] | null; // null: every device with a report
  onDone: () => void;
}) {
  const canOperate = useCan("operator");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const rows = devices
    ? devices.filter((d) => d.status === "active").map((d) => reports.find((r) => r.device === d.name) ?? { device: d.name, imported: [], unmanaged: [], error: null, at: null })
    : reports;

  const run = async (device: string) => {
    setBusy(device);
    setError(null);
    try {
      await importNow(site, device);
      onDone();
    } catch (e) {
      setError(`${device}: ${message(e)}`);
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="panel rsec" data-tour="routing-import">
      <header>
        <h2>Auto-import</h2>
      </header>
      <div className="rsec-body">
      <p className="muted">
        The latest read of each switch: hand-made objects adopted into intent, and what is left alone (unsupported
        forms, or objects that differ between the two leaves of a pair). Unmanaged objects are never changed.
      </p>
      {rows.length === 0 && <p className="muted">No import has run since the orchestrator started.</p>}
      {rows.length > 0 && (
        <table className="devices">
          <thead>
            <tr>
              <th>Device</th>
              <th>Last import</th>
              <th>Adopted</th>
              <th>Unmanaged</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.device}>
                <td>{r.device}</td>
                <td>
                  {when(r.at)}
                  {r.error && (
                    <details className="import-error">
                      <summary className="error-text">{shortError(r.error)}</summary>
                      <pre className="config error-text">{r.error}</pre>
                    </details>
                  )}
                </td>
                <td>{r.imported.length ? r.imported.join(", ") : <span className="muted">none</span>}</td>
                <td>{r.unmanaged.length ? r.unmanaged.join("; ") : <span className="muted">none</span>}</td>
                <td>
                  {canOperate && (
                    <button onClick={() => run(r.device)} disabled={busy !== null}>
                      {busy === r.device ? "Reading…" : "Import now"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {error && <p className="error">{error}</p>}
      </div>
    </section>
  );
}

// -- one target -----------------------------------------------------------------------------------

type Save = (next: DeviceRouting, what: string) => Promise<boolean>;

interface SectionProps {
  routing: DeviceRouting;
  vpns: string[];
  save: Save;
  edit: boolean;
}

export function RoutingTargetPage({ site, target }: { site: string; target: string }) {
  const doc = useFetch(() => (site ? fetchRoutingDocument(site) : Promise.resolve(NO_DOCUMENT)), [site]);
  const targets = useFetch(() => (site ? fetchRoutingTargets(site) : Promise.resolve(NO_TARGETS)), [site]);
  const imports = useFetch(() => (site ? fetchImports(site) : Promise.resolve(NO_IMPORTS)), [site]);
  const services = useFetch(() => (site ? fetchServices(site) : Promise.resolve([])), [site]);
  const edit = useCan("operator");
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState(() => { try { return localStorage.getItem("routing-view") ?? "simple"; } catch { return "simple"; } });
  if (!site) return <NoFabric />;
  const pick = (v: string) => { setView(v); try { localStorage.setItem("routing-view", v); } catch { /* per-viewer only */ } };

  const routing: DeviceRouting = doc.data?.devices?.[target] ?? {};
  const retired = doc.data?.retired?.[target];
  const info = targets.data?.find((t) => t.target === target);
  const vpns = info?.vpns ?? [];

  const save: Save = async (next, what) => {
    setError(null);
    setNotice(null);
    try {
      await saveRouting(site, target, next);
      setNotice(`${what}. ${currentDraft() ? `It is in draft #${currentDraft()!.id}, applied at its window's start.` : "Prepare a deploy to apply it."}`);
      doc.reload();
      return true;
    } catch (e) {
      setError(message(e));
      return false;
    }
  };
  const props: SectionProps = { routing, vpns, save, edit };
  // An ISP service's instance: its own, or a shared one (a second ISP link joining another's).
  const ispVpns = [...new Set((services.data ?? []).filter((v) => v.kind === "isp").map((v) => v.vrf ?? v.name))]
    .filter((v) => vpns.includes(v));

  return (
    <div className="page">
    <section>
      <div className="toolbar" data-tour="routing-target">
        <a href="#/network/routing">← Routing</a>
        <h1>{targetLabel(target)}</h1>
        <span className="toggle" data-tour="routing-view" role="group" aria-label="View">
          <button aria-pressed={view === "simple"} onClick={() => pick("simple")}>Simple</button>
          <button aria-pressed={view === "expert"} onClick={() => pick("expert")}>Expert view</button>
        </span>
      </div>
      {targets.data && !info && <p className="error">{target} is not a device or M-LAG pair of {site}.</p>}
      <p className="muted">
        {target.includes("+")
          ? "An M-LAG pair: everything here is generated identically on both leaves. "
          : ""}
        VPN instances available here: {vpns.length ? vpns.join(", ") : "none"} (and the global table).
      </p>
      {doc.error && <p className="error">Could not load routing intent: {doc.error}</p>}
      {retired && count(retired) > 0 && (
        <p className="warning">
          {count(retired)} object{count(retired) === 1 ? "" : "s"} removed from intent stay owned until a verified deploy
          has removed them from the switch.
        </p>
      )}
      {notice && <p className="notice">{notice}</p>}
      {error && <p className="error">{error}</p>}
    </section>
      {doc.data && view === "simple" && (
        <RoutingSimple routing={routing} vpns={vpns} save={save} edit={edit}
                       ispVpns={ispVpns}
                       imports={(imports.data ?? []).filter((r) => info?.devices.some((d) => d.name === r.device))}
                       onImport={async () => {
                         setError(null);
                         try { for (const d of (info?.devices ?? []).filter((x) => x.status === "active")) await importNow(site, d.name); } catch (e) { setError(message(e)); }
                         imports.reload(); doc.reload();
                       }} />
      )}
      {doc.data && view === "expert" && (
        <>
          <RouteFlow routing={routing} vpns={vpns} save={save} edit={edit} services={services.data ?? []}
                     devices={(info?.devices ?? []).map((d) => d.name)} ispVpns={ispVpns} />
          <PrefixLists {...props} />
          <AsPathFilters {...props} />
          <CommunityFilters {...props} />
          <RoutePolicies {...props} />
          <StaticRoutes {...props} />
          <BgpPeers {...props} />
          <Aggregates {...props} />
          <OspfProcesses {...props} />
          <JsonEditor {...props} />
        </>
      )}
      {view === "expert" && <ImportTable site={site} reports={imports.data ?? []} devices={info?.devices ?? []} onDone={() => {
        imports.reload();
        doc.reload();
      }} />}
    </div>
  );
}

// -- building blocks ----------------------------------------------------------------------------

function Section({ title, help, children }: { title: string; help?: string; children: ReactNode }) {
  return (
    <section className="panel rsec" data-tour="routing-section">
      <header>
        <h2>{title}</h2>
      </header>
      <div className="rsec-body">
        {help && <p className="muted">{help}</p>}
        {children}
      </div>
    </section>
  );
}

function Rows({ head, rows }: { head: string[]; rows: { key: string; cells: ReactNode[]; remove?: () => void }[] }) {
  if (rows.length === 0) return <p className="muted">None.</p>;
  return (
    <table className="devices">
      <thead>
        <tr>
          {head.map((h) => (
            <th key={h}>{h}</th>
          ))}
          <th />
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.key}>
            {r.cells.map((c, i) => (
              <td key={i} className={i > 0 ? "mono" : undefined}>
                {c}
              </td>
            ))}
            <td>
              {r.remove && (
                <div className="actions">
                  <button onClick={r.remove}>Remove</button>
                </div>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Text({ label, value, set, placeholder }: { label: string; value: string; set: (v: string) => void; placeholder?: string }) {
  return (
    <label>
      {label} <input value={value} onChange={(e) => set(e.target.value)} placeholder={placeholder} />
    </label>
  );
}

function Choice({ label, value, set, options }: { label: string; value: string; set: (v: string) => void; options: [string, string][] }) {
  return (
    <label>
      {label}
      <select value={value} onChange={(e) => set(e.target.value)}>
        {options.map(([v, text]) => (
          <option key={v} value={v}>
            {text}
          </option>
        ))}
      </select>
    </label>
  );
}

const ACTIONS: [string, string][] = [
  ["permit", "permit"],
  ["deny", "deny"],
];
const vpnOptions = (vpns: string[], global = true): [string, string][] => [
  ...(global ? ([["", "global table"]] as [string, string][]) : []),
  ...vpns.map((v): [string, string] => [v, v]),
];
const pick = (list: string[]): [string, string][] => [["", "none"], ...list.map((n): [string, string] => [n, n])];
const opt = (v: string | null | undefined) => v || null;
const show = (v: unknown) => (v === null || v === undefined || v === "" ? "—" : String(v));

/** Run parsers in order; the first error is shown. */
function all<T extends unknown[]>(...results: { [K in keyof T]: Parsed<T[K]> }): Parsed<T> {
  for (const r of results) if (!r.ok) return r;
  return { ok: true, value: results.map((r) => (r as { value: unknown }).value) as T };
}

function Form({ title, onSubmit, children, error }: { title: string; onSubmit: () => Promise<void> | void; children: ReactNode; error: string | null }) {
  const submit = (e: FormEvent) => {
    e.preventDefault();
    void onSubmit();
  };
  return (
    <form className="form-row" onSubmit={submit}>
      {children}
      <button type="submit">{title}</button>
      {error && <p className="error">{error}</p>}
    </form>
  );
}

function useFormError(): [string | null, (r: Parsed<unknown>) => boolean, (e: string | null) => void] {
  const [error, setError] = useState<string | null>(null);
  const check = (r: Parsed<unknown>) => {
    setError(r.ok ? null : r.error);
    return r.ok;
  };
  return [error, check, setError];
}

const required = (text: string, label: string): Parsed<string> =>
  text.trim() ? { ok: true, value: text.trim() } : { ok: false, error: `${label} is required` };

function ruleRows(props: SectionProps, kind: NamedKind, cells: (rule: never) => ReactNode[]) {
  const lists = (props.routing[kind] ?? {}) as Record<string, ({ index: number } | { node: number })[]>;
  return Object.entries(lists).flatMap(([name, rules]) =>
    rules.map((rule) => {
      const num = "node" in rule ? rule.node : rule.index;
      return {
        key: `${name} ${num}`,
        cells: [name, num, ...cells(rule as never)],
        remove: props.edit
          ? () => {
              if (window.confirm(`Remove ${name} ${num}?`)) void props.save(removeRule(props.routing, kind, name, num), `Removed ${name} ${num}`);
            }
          : undefined,
      };
    }),
  );
}

function itemRows<T>(props: SectionProps, kind: KeyedKind, cells: (item: T) => ReactNode[]) {
  return ((props.routing[kind] ?? []) as T[]).map((item) => {
    const key = keyOf(kind, item as never);
    return {
      key,
      cells: cells(item),
      remove: props.edit
        ? () => {
            if (window.confirm(`Remove ${key.trim()}?`)) void props.save(removeItem(props.routing, kind, key), `Removed ${key.trim()}`);
          }
        : undefined,
    };
  });
}

// -- sections ----------------------------------------------------------------------------------

function PrefixLists(props: SectionProps) {
  const [f, setF] = useState({ name: "", index: "10", action: "permit", prefix: "", ge: "", le: "" });
  const [error, check] = useFormError();
  const set = (k: keyof typeof f) => (v: string) => setF({ ...f, [k]: v });
  const add = async () => {
    const r = all<[string, number | null, string, number | null, number | null]>(
      required(f.name, "Name"), parseNumber(f.index, "Index", 1, MAX32), required(f.prefix, "Prefix"),
      parseNumber(f.ge, "greater-equal", 0, 128, true), parseNumber(f.le, "less-equal", 0, 128, true),
    );
    if (!check(r) || !r.ok) return;
    const [name, index, prefix, ge, le] = r.value;
    const rule = { index: index as number, action: f.action as Action, prefix, ge, le };
    if (await props.save(putRule(props.routing, "prefix_lists", name, rule), `Saved ip-prefix ${name} index ${index}`)) setF({ ...f, prefix: "", ge: "", le: "" });
  };
  return (
    <Section title="Prefix lists" help="ip ip-prefix, or ip ipv6-prefix for IPv6 prefixes (one family per list). Give less-equal alone, or both; greater-equal alone is not verified on the switch and is refused.">
      <Rows head={["Name", "Index", "Action", "Prefix", "ge", "le"]} rows={ruleRows(props, "prefix_lists", (r: { action: string; prefix: string; ge?: number | null; le?: number | null }) => [r.action, r.prefix, show(r.ge), show(r.le)])} />
      {props.edit && (
        <Form title="Add rule" onSubmit={add} error={error}>
          <Text label="Name" value={f.name} set={set("name")} placeholder="TO-ISP" />
          <Text label="Index" value={f.index} set={set("index")} />
          <Choice label="Action" value={f.action} set={set("action")} options={ACTIONS} />
          <Text label="Prefix" value={f.prefix} set={set("prefix")} placeholder="10.100.0.0/16" />
          <Text label="greater-equal" value={f.ge} set={set("ge")} />
          <Text label="less-equal" value={f.le} set={set("le")} placeholder="24" />
        </Form>
      )}
    </Section>
  );
}

function AsPathFilters(props: SectionProps) {
  const [f, setF] = useState({ name: "", index: "10", action: "permit", regex: "" });
  const [error, check] = useFormError();
  const set = (k: keyof typeof f) => (v: string) => setF({ ...f, [k]: v });
  const add = async () => {
    const r = all<[string, number | null, string]>(required(f.name, "Name"), parseNumber(f.index, "Index", 1, MAX32), required(f.regex, "Regular expression"));
    if (!check(r) || !r.ok) return;
    const [name, index, regex] = r.value;
    if (await props.save(putRule(props.routing, "as_path_filters", name, { index: index as number, action: f.action as Action, regex }), `Saved as-path-filter ${name}`)) setF({ ...f, regex: "" });
  };
  return (
    <Section title="AS-path filters" help="ip as-path-filter, one word regular expression.">
      <Rows head={["Name", "Index", "Action", "Regex"]} rows={ruleRows(props, "as_path_filters", (r: { action: string; regex: string }) => [r.action, r.regex])} />
      {props.edit && (
        <Form title="Add rule" onSubmit={add} error={error}>
          <Text label="Name" value={f.name} set={set("name")} />
          <Text label="Index" value={f.index} set={set("index")} />
          <Choice label="Action" value={f.action} set={set("action")} options={ACTIONS} />
          <Text label="Regex" value={f.regex} set={set("regex")} placeholder="^65100$" />
        </Form>
      )}
    </Section>
  );
}

function CommunityFilters(props: SectionProps) {
  const [f, setF] = useState({ name: "", index: "10", action: "permit", community: "" });
  const [error, check] = useFormError();
  const set = (k: keyof typeof f) => (v: string) => setF({ ...f, [k]: v });
  const add = async () => {
    const r = all<[string, number | null, string]>(required(f.name, "Name"), parseNumber(f.index, "Index", 1, MAX32), required(f.community, "Community"));
    if (!check(r) || !r.ok) return;
    const [name, index, community] = r.value;
    if (await props.save(putRule(props.routing, "community_filters", name, { index: index as number, action: f.action as Action, community }), `Saved community-filter ${name}`)) setF({ ...f, community: "" });
  };
  return (
    <Section title="Community filters" help="ip community-filter basic, one community (nn:nn) per rule.">
      <Rows head={["Name", "Index", "Action", "Community"]} rows={ruleRows(props, "community_filters", (r: { action: string; community: string }) => [r.action, r.community])} />
      {props.edit && (
        <Form title="Add rule" onSubmit={add} error={error}>
          <Text label="Name" value={f.name} set={set("name")} />
          <Text label="Index" value={f.index} set={set("index")} />
          <Choice label="Action" value={f.action} set={set("action")} options={ACTIONS} />
          <Text label="Community" value={f.community} set={set("community")} placeholder="65001:100" />
        </Form>
      )}
    </Section>
  );
}

function RoutePolicies(props: SectionProps) {
  const [f, setF] = useState({ name: "", node: "10", action: "permit", match: "", localPref: "" });
  const [error, check] = useFormError();
  const set = (k: keyof typeof f) => (v: string) => setF({ ...f, [k]: v });
  const add = async () => {
    const r = all<[string, number | null, number | null]>(required(f.name, "Name"), parseNumber(f.node, "Node", 0, 65535), parseNumber(f.localPref, "Local preference", 0, MAX32, true));
    if (!check(r) || !r.ok) return;
    const [name, node, localPref] = r.value;
    const rule = { node: node as number, action: f.action as Action, match_ip_prefix: opt(f.match), apply_local_preference: localPref };
    if (await props.save(putRule(props.routing, "route_policies", name, rule), `Saved route-policy ${name} node ${node}`)) setF({ ...f, localPref: "" });
  };
  return (
    <Section title="Route-policies" help="route-policy nodes: match a prefix list, set the local preference. Other clauses are not verified yet.">
      <Rows head={["Name", "Node", "Action", "Match ip-prefix", "Local preference"]} rows={ruleRows(props, "route_policies", (r: { action: string; match_ip_prefix?: string | null; apply_local_preference?: number | null }) => [r.action, show(r.match_ip_prefix), show(r.apply_local_preference)])} />
      {props.edit && (
        <Form title="Add node" onSubmit={add} error={error}>
          <Text label="Name" value={f.name} set={set("name")} />
          <Text label="Node" value={f.node} set={set("node")} />
          <Choice label="Action" value={f.action} set={set("action")} options={ACTIONS} />
          <Choice label="Match ip-prefix" value={f.match} set={set("match")} options={pick(names(props.routing, "prefix_lists"))} />
          <Text label="Local preference" value={f.localPref} set={set("localPref")} />
        </Form>
      )}
    </Section>
  );
}

function StaticRoutes(props: SectionProps) {
  const [f, setF] = useState({ vpn: "", prefix: "", nextHop: "NULL0", preference: "" });
  const [error, check] = useFormError();
  const set = (k: keyof typeof f) => (v: string) => setF({ ...f, [k]: v });
  const add = async () => {
    const r = all<[string, string, number | null]>(required(f.prefix, "Prefix"), required(f.nextHop, "Next hop"), parseNumber(f.preference, "Preference", 1, 255, true));
    if (!check(r) || !r.ok) return;
    const [prefix, next_hop, preference] = r.value;
    const route = { vpn: opt(f.vpn), prefix, next_hop, preference };
    if (await props.save(putItem(props.routing, "static_routes", route), `Saved static route ${prefix}`)) setF({ ...f, prefix: "" });
  };
  return (
    <Section title="Static routes" help="ip route-static, to an address or NULL0.">
      <Rows head={["VPN instance", "Prefix", "Next hop", "Preference"]} rows={itemRows(props, "static_routes", (s: { vpn?: string | null; prefix: string; next_hop: string; preference?: number | null }) => [show(s.vpn ?? "global"), s.prefix, s.next_hop, show(s.preference)])} />
      {props.edit && (
        <Form title="Add route" onSubmit={add} error={error}>
          <Choice label="VPN instance" value={f.vpn} set={set("vpn")} options={vpnOptions(props.vpns)} />
          <Text label="Prefix" value={f.prefix} set={set("prefix")} placeholder="10.60.0.0/16" />
          <Text label="Next hop" value={f.nextHop} set={set("nextHop")} />
          <Text label="Preference" value={f.preference} set={set("preference")} />
        </Form>
      )}
    </Section>
  );
}

function BgpPeers(props: SectionProps) {
  const empty = { vpn: "", address: "", asn: "", importPolicy: "", exportPolicy: "", importList: "", exportList: "" };
  const [f, setF] = useState(empty);
  const [error, check] = useFormError();
  const set = (k: keyof typeof f) => (v: string) => setF({ ...f, [k]: v });
  const policies = pick(names(props.routing, "route_policies"));
  const lists = pick(names(props.routing, "prefix_lists"));
  const add = async () => {
    const r = all<[string, number | null]>(required(f.address, "Address"), parseNumber(f.asn, "ASN", 1, MAX32));
    if (!check(r) || !r.ok) return;
    const [address, asn] = r.value;
    const peer = {
      vpn: opt(f.vpn), address, asn: asn as number,
      import_policy: opt(f.importPolicy), export_policy: opt(f.exportPolicy),
      import_prefix_list: opt(f.importList), export_prefix_list: opt(f.exportList),
    };
    if (await props.save(putItem(props.routing, "bgp_peers", peer), `Saved BGP peer ${address}`)) setF(empty);
  };
  return (
    <Section title="BGP peers" help="eBGP/iBGP peers in the fabric ASN's BGP: in the global table (ipv4-family or ipv6-family unicast, by the peer's address) or a VPN instance (an IPv6 peer needs the instance's IPv6). Its prefix lists are its own family. A firewall service's own session is set on the service, not here.">
      <Rows
        head={["VPN instance", "Address", "ASN", "Import", "Export"]}
        rows={itemRows(props, "bgp_peers", (p: { vpn?: string | null; address: string; asn: number; import_policy?: string | null; export_policy?: string | null; import_prefix_list?: string | null; export_prefix_list?: string | null }) => [
          show(p.vpn ?? "global"), p.address, p.asn,
          [p.import_policy && `policy ${p.import_policy}`, p.import_prefix_list && `ip-prefix ${p.import_prefix_list}`].filter(Boolean).join(", ") || "—",
          [p.export_policy && `policy ${p.export_policy}`, p.export_prefix_list && `ip-prefix ${p.export_prefix_list}`].filter(Boolean).join(", ") || "—",
        ])}
      />
      {props.edit && (
        <Form title="Add peer" onSubmit={add} error={error}>
          <Choice label="VPN instance" value={f.vpn} set={set("vpn")} options={vpnOptions(props.vpns)} />
          <Text label="Address" value={f.address} set={set("address")} placeholder="192.0.2.1" />
          <Text label="ASN" value={f.asn} set={set("asn")} placeholder="65100" />
          <Choice label="Import policy" value={f.importPolicy} set={set("importPolicy")} options={policies} />
          <Choice label="Export policy" value={f.exportPolicy} set={set("exportPolicy")} options={policies} />
          <Choice label="Import ip-prefix" value={f.importList} set={set("importList")} options={lists} />
          <Choice label="Export ip-prefix" value={f.exportList} set={set("exportList")} options={lists} />
        </Form>
      )}
    </Section>
  );
}

function Aggregates(props: SectionProps) {
  const [f, setF] = useState({ vpn: "", prefix: "", suppress: "" });
  const [error, check] = useFormError();
  const set = (k: keyof typeof f) => (v: string) => setF({ ...f, [k]: v });
  const add = async () => {
    const r = required(f.prefix, "Prefix");
    if (!check(r) || !r.ok) return;
    const aggregate = {
      vpn: opt(f.vpn), prefix: r.value,
      detail_suppressed: f.suppress === "detail",
      suppress_policy: f.suppress && f.suppress !== "detail" ? f.suppress.slice("policy:".length) : null,
    };
    if (await props.save(putItem(props.routing, "aggregates", aggregate), `Saved aggregate ${r.value}`)) setF({ ...f, prefix: "" });
  };
  const options: [string, string][] = [
    ["", "none"],
    ["detail", "detail-suppressed"],
    ...names(props.routing, "route_policies").map((n): [string, string] => [`policy:${n}`, `suppress-policy ${n}`]),
  ];
  return (
    <Section title="Aggregates" help="BGP aggregate in the global table or a VPN instance's family.">
      <Rows head={["VPN instance", "Prefix", "Suppression"]} rows={itemRows(props, "aggregates", (a: { vpn?: string | null; prefix: string; detail_suppressed?: boolean; suppress_policy?: string | null }) => [show(a.vpn ?? "global"), a.prefix, a.detail_suppressed ? "detail-suppressed" : a.suppress_policy ? `suppress-policy ${a.suppress_policy}` : "—"])} />
      {props.edit && (
        <Form title="Add aggregate" onSubmit={add} error={error}>
          <Choice label="VPN instance" value={f.vpn} set={set("vpn")} options={vpnOptions(props.vpns)} />
          <Text label="Prefix" value={f.prefix} set={set("prefix")} placeholder="10.200.0.0/16" />
          <Choice label="Suppression" value={f.suppress} set={set("suppress")} options={options} />
        </Form>
      )}
    </Section>
  );
}

function OspfProcesses(props: SectionProps) {
  const [f, setF] = useState({ process: "", vpn: "", routerId: "", areas: "0.0.0.0" });
  const [error, check] = useFormError();
  const set = (k: keyof typeof f) => (v: string) => setF({ ...f, [k]: v });
  const add = async () => {
    const r = all<[number | null, string, string, string[]]>(parseNumber(f.process, "Process", 2, 65535), required(f.vpn, "VPN instance"), required(f.routerId, "Router ID"), parseAreas(f.areas));
    if (!check(r) || !r.ok) return;
    const [process, vpn, router_id, areas] = r.value;
    if (await props.save(putItem(props.routing, "ospf", { process: process as number, vpn, router_id, areas }), `Saved ospf ${process}`)) setF({ ...f, process: "", routerId: "" });
  };
  return (
    <Section title="OSPF processes" help="An OSPF process in a VPN instance (process 1 is the fabric underlay). Areas only; interfaces join through their own configuration.">
      <Rows head={["Process", "VPN instance", "Router ID", "Areas"]} rows={itemRows(props, "ospf", (o: { process: number; vpn: string; router_id: string; areas?: string[] }) => [o.process, o.vpn, o.router_id, (o.areas ?? []).join(", ") || "—"])} />
      {props.edit && (
        <Form title="Add process" onSubmit={add} error={error}>
          <Text label="Process" value={f.process} set={set("process")} placeholder="2" />
          <Choice label="VPN instance" value={f.vpn} set={set("vpn")} options={[["", "choose…"] as [string, string], ...vpnOptions(props.vpns, false)]} />
          <Text label="Router ID" value={f.routerId} set={set("routerId")} placeholder="192.0.2.10" />
          <Text label="Areas" value={f.areas} set={set("areas")} />
        </Form>
      )}
    </Section>
  );
}

function JsonEditor(props: SectionProps) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (!props.edit) return null;
  const open = () => {
    setError(null);
    setText(JSON.stringify(props.routing, null, 2));
  };
  const apply = async () => {
    let parsed: DeviceRouting;
    try {
      parsed = JSON.parse(text ?? "{}") as DeviceRouting;
    } catch (e) {
      setError(`Not valid JSON: ${message(e)}`);
      return;
    }
    if (await props.save(parsed, "Saved")) setText(null);
  };
  return (
    <Section title="Edit as JSON" help="The whole intent of this device or pair, for anything the forms above do not cover. The orchestrator validates it on save.">
      {text === null ? (
        <button onClick={open}>Edit as JSON</button>
      ) : (
        <>
          <textarea className="json" value={text} onChange={(e) => setText(e.target.value)} rows={18} spellCheck={false} />
          <div className="inline">
            <button className="primary" onClick={apply}>
              Save
            </button>
            <button onClick={() => setText(null)}>Cancel</button>
          </div>
          {error && <p className="error">{error}</p>}
        </>
      )}
    </Section>
  );
}
