import { ApplyHint } from "./Draft";
import { Term } from "./Term";
import { useEffect, useState, type FormEvent } from "react";
import {
  attachSegment,
  changeAttachment,
  deleteSegment,
  detachSegment,
  renameSegment,
  fetchDevices,
  fetchPorts,
  fetchSegments,
  fetchArchitecture,
  fetchServices,
  type Port,
  type Segment,
  type Service,
} from "./api";
import { NewSegment } from "./NewSegment";
import { SegmentIpv6 } from "./Ipv6";
import { SegmentRelay } from "./Relay";
import { SegmentRouting, Services } from "./Routing";
import { useCan } from "./role";
import { useFetch } from "./useFetch";
import { attachableLeaves } from "./placement";
import { Faceplate } from "./Faceplate";
import { Exits } from "./Exits";
import { refusal, trunkOrPort } from "./frontPanel";
import { nameError, segmentVpn } from "./segmentFlow";
import { parseVid, vidLabel } from "./vid";

function message(e: unknown): string {
  return e instanceof Error ? e.message.replace(/^\d+: /, "") : String(e);
}

export function Segments({ site }: { site: string }) {
  if (!site) {
    return (
      <section>
        <div className="toolbar">
          <h1 className="sr-only">Segments</h1>
        </div>
        <p className="muted">Segments belong to a fabric: choose one at the top right (e.g. example).</p>
      </section>
    );
  }
  return <FabricSegments key={site} site={site} />;
}

function FabricSegments({ site }: { site: string }) {
  const { data, error, reload } = useFetch(() => fetchSegments(site), [site]);
  const services = useFetch(() => fetchServices(site), [site]);
  const architecture = useFetch(() => fetchArchitecture(site), [site]);
  const canOperate = useCan("operator");
  const [notice, setNotice] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const centralized = architecture.data?.architecture === "centralized";

  const changed = (text: string) => {
    setNotice(text);
    reload();
    services.reload();
  };

  return (
    <div className="page">
      {creating && (
        <NewSegment
          site={site}
          segments={(data ?? []).filter((s) => s.fabric)}
          services={services.data ?? []}
          architecture={architecture.data}
          onClose={() => setCreating(false)}
          onChanged={() => {
            reload();
            services.reload();
          }}
        />
      )}

      <section>
        <div className="toolbar">
          <h1 className="sr-only">Segments</h1>
          {canOperate && !creating && (
            <button className="primary" data-tour="new-segment" onClick={() => setCreating(true)}>
              New segment
            </button>
          )}
        </div>
        <p className="muted">
          {centralized
            ? `Centralized fabric: every gateway, segment VPN instance and service is on the gateway pair ${(architecture.data?.gateway_pair ?? []).join(" + ")}; the other leaves are layer 2 only.`
            : architecture.data
              ? "Symmetric fabric: each leaf where a routed segment is attached is its anycast gateway."
              : ""}{" "}
          Changes are written to NetBox only; nothing reaches a switch before a deploy is approved.
        </p>
        {notice && (
          <p className="notice">
            {notice} <ApplyHint />
          </p>
        )}
        {error && <p className="error">{error}</p>}
        {data && data.length === 0 && <p className="muted">No segments yet{canOperate ? ": start with New segment." : "."}</p>}
        {data && data.length > 0 && (
          <table className="devices segments" data-tour="segments">
            <thead>
              <tr>
                <th>Segment</th>
                <th><Term k="VNI">VNI</Term></th>
                <th><Term k="BD">BD</Term></th>
                <th>Routing</th>
                <th>Ports</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.map((segment) => {
                const key = segment.vni + (segment.fabric ? 0 : 0.5);
                const expanded = open === key;
                return (
                  <SegmentRow
                    key={`${segment.fabric}:${segment.vni}`}
                    segment={segment}
                    segments={data}
                    services={services.data ?? []}
                    centralized={centralized}
                    relayPool={architecture.data?.relay_pool ?? null}
                    ipv6Pool={architecture.data?.ipv6_pool ?? null}
                    expanded={expanded}
                    onToggle={() => setOpen(expanded ? null : key)}
                    onDone={changed}
                  />
                );
              })}
            </tbody>
          </table>
        )}
      </section>

      {data && data.some((s) => s.fabric) && (
        <Exits site={site} segments={data.filter((s) => s.fabric)} services={services.data ?? []} onDone={changed} />
      )}

      <section data-tour="services">
        <h2>Services</h2>
        <Services site={site} services={services.data ?? []} segments={(data ?? []).filter((s) => s.fabric)} onDone={changed} />
        {services.error && <p className="error">{services.error}</p>}
      </section>
    </div>
  );
}

function SegmentRow({
  segment,
  segments,
  services,
  centralized,
  relayPool,
  ipv6Pool,
  expanded,
  onToggle,
  onDone,
}: {
  segment: Segment;
  segments: Segment[];
  services: Service[];
  centralized: boolean;
  relayPool: string | null;
  ipv6Pool: string | null;
  expanded: boolean;
  onToggle: () => void;
  onDone: (t: string) => void;
}) {
  const canOperate = useCan("operator");
  const r = segment.routing;
  const peerNames = (r?.peers ?? []).map((vni) => segments.find((s) => s.vni === vni)?.name ?? `VNI ${vni}`);
  return (
    <>
      <tr className={expanded ? "open" : ""}>
        <td>
          <button className="linklike" onClick={onToggle} aria-expanded={expanded}>
            <span className="chev" aria-hidden="true">{expanded ? "▾" : "▸"}</span> <strong>{segment.name}</strong>
          </button>
          {segment.fabric === null && <span className="pill bad">unassigned</span>}
        </td>
        <td className="num">{segment.vni}</td>
        <td className="num">{segment.bd_id}</td>
        <td>
          {r ? (
            <>
              <span className="pill info">routed</span> <span className="mono small">{r.gateway}</span>
              {r.firewall && <span className="pill">via {r.firewall}</span>}
              {peerNames.length > 0 && <span className="pill">↔ {peerNames.join(", ")}</span>}
            </>
          ) : (
            <span className="faint">layer 2</span>
          )}
        </td>
        <td>
          {segment.attachments.length === 0 ? (
            <span className="faint">none</span>
          ) : (
            <span className="small">
              {segment.attachments.slice(0, 2).map((a) => `${a.device} ${a.interface}`).join(", ")}
              {segment.attachments.length > 2 && ` +${segment.attachments.length - 2}`}
            </span>
          )}
        </td>
        <td>
          <div className="actions">
            <button onClick={onToggle}>{expanded ? "Close" : canOperate ? "Edit" : "Details"}</button>
          </div>
        </td>
      </tr>
      {expanded && (
        <tr className="detail">
          <td colSpan={6}>
            <SegmentCard segment={segment} segments={segments} services={services} centralized={centralized}
                         relayPool={relayPool} ipv6Pool={ipv6Pool} onDone={onDone} />
          </td>
        </tr>
      )}
    </>
  );
}

function AttachForm({ segments, site, onDone }: { segments: Segment[]; site: string; onDone: (t: string) => void }) {
  // In a segment's details: attach that segment to one more port.
  const devices = useFetch(() => fetchDevices(site), [site]);
  const arch = useFetch(() => fetchArchitecture(site), [site]);
  const leaves = attachableLeaves(devices.data ?? [], arch.data?.architecture === "centralized");
  const [vni, setVni] = useState(String(segments[0].vni));
  const [device, setDevice] = useState("");
  const [ports, setPorts] = useState<Port[]>([]);
  const [port, setPort] = useState("");
  const [vid, setVid] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loadingPorts, setLoadingPorts] = useState(false);

  useEffect(() => {
    setPorts([]);
    setPort("");
    setError(null);
    if (!device) return;
    setLoadingPorts(true);
    fetchPorts(device)
      .then(setPorts)
      .catch((e) => setError(`Could not list ${device}'s ports: ${message(e)}`))
      .finally(() => setLoadingPorts(false));
  }, [device]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    const parsed = parseVid(vid);
    if (!parsed.ok) return setError(parsed.error);
    try {
      const { ports: written } = await attachSegment(site, Number(vni), device, port, parsed.vid);
      setVid("");
      onDone(`Attached (${vidLabel(parsed.vid)}) on ${written.join(" and ")}.`);
      fetchPorts(device).then(setPorts);
    } catch (e) {
      setError(message(e));
    }
  };

  return (
    <form className="form-row attach" onSubmit={submit}>
      <h3>Attach to another port</h3>
      {segments.length > 1 && (
        <label>
          Segment
          <select value={vni} onChange={(e) => setVni(e.target.value)}>
            {segments.map((s) => (
              <option key={s.vni} value={s.vni}>
                {s.name} (VNI {s.vni})
              </option>
            ))}
          </select>
        </label>
      )}
      <label>
        Leaf
        <select value={device} onChange={(e) => setDevice(e.target.value)}>
          <option value="">choose…</option>
          {leaves.map((d) => (
            <option key={d.name} value={d.name}>
              {d.name}
              {d.status !== "active" ? ` (${d.status})` : ""}
            </option>
          ))}
        </select>
      </label>
      {device && !loadingPorts && (
        <div className="attach-plate">
          <span className="muted small">Click the port on {device}{port ? "" : " (a trunk member picks its trunk)"}:</span>
          <Faceplate
            device={device}
            refresh={ports.length}
            pick={{
              selected: port ? [port] : [],
              refuse: (p) => refusal(p, new Set(ports.map((x) => x.name))),
              target: trunkOrPort,
              onToggle: (t) => setPort(port === t ? "" : t),
            }}
          />
          {port && (
            <div className="picked-chips">
              <span key={port}>{port}{ports.find((p) => p.name === port)?.mlag ? " · M-LAG trunk: both leaves" : ""}</span>
            </div>
          )}
        </div>
      )}
      <label>
        VLAN ID <input value={vid} onChange={(e) => setVid(e.target.value)} placeholder="empty = untagged" />
      </label>
      <button type="submit" className="primary" disabled={!device || !port}>
        Attach
      </button>
      {error && <p className="error">{error}</p>}
    </form>
  );
}

function SegmentCard({
  segment,
  segments,
  services,
  centralized,
  relayPool,
  ipv6Pool,
  onDone,
}: {
  segment: Segment;
  segments: Segment[];
  services: Service[];
  centralized: boolean;
  relayPool: string | null;
  ipv6Pool: string | null;
  onDone: (t: string) => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const canOperate = useCan("operator");
  const fabric = segment.fabric ?? "unassigned";

  const act = async (action: () => Promise<unknown>, text: string) => {
    setError(null);
    try {
      await action();
      onDone(text);
    } catch (e) {
      setError(message(e));
    }
  };

  const changeVid = (device: string, iface: string, current: number | null) => {
    const answer = window.prompt(`New VLAN ID for ${device} ${iface} (empty = untagged)`, current?.toString() ?? "");
    if (answer === null) return;
    const parsed = parseVid(answer);
    if (!parsed.ok) return setError(parsed.error);
    act(
      () => changeAttachment(fabric, segment.vni, device, iface, parsed.vid),
      `${device} ${iface} now ${vidLabel(parsed.vid)}.`,
    );
  };

  const rename = () => {
    const bd = segment.bd_id ?? "";   // none only on a segment from before fabrics
    const answer = window.prompt(`New name for segment ${segment.name}`, segment.name)?.trim();
    if (!answer || answer === segment.name) return;
    const problem = nameError(answer, bd);
    if (problem) return setError(problem);
    if (segment.routing && !window.confirm(
      `${segment.name} is routed: its VPN instance ${segmentVpn(segment.name, bd)} is replaced by ` +
      `${segmentVpn(answer, bd)} at the next deploy, and routing in this segment is interrupted ` +
      "during that deploy. Routing intent that names the instance follows. Rename?")) return;
    act(() => renameSegment(fabric, segment.vni, answer), `Segment ${segment.name} renamed to ${answer}.`);
  };

  return (
    <div className="segment-detail">
      <div className="toolbar">
        <h2>
          {segment.name} <span className="muted">VNI {segment.vni} · BD {segment.bd_id}</span>
          {segment.fabric === null && <span className="pill bad">unassigned (from before fabrics)</span>}
        </h2>
        {canOperate && segment.fabric && <button onClick={rename}>Rename</button>}
        {canOperate && (
        <button
          disabled={segment.attachments.length > 0 || segment.routing !== null}
          title={
            segment.attachments.length
              ? "Detach it from every port first"
              : segment.routing
                ? "Stop routing it first"
                : ""
          }
          onClick={() =>
            window.confirm(`Delete segment ${segment.name}?`) &&
            act(() => deleteSegment(fabric, segment.vni), `Segment ${segment.name} deleted.`)
          }
        >
          Delete
        </button>
        )}
      </div>
      <SegmentRouting
        key={JSON.stringify(segment.routing)}
        segment={segment}
        segments={segments}
        services={services}
        onDone={onDone}
        centralized={centralized}
      />
      <SegmentRelay key={`relay-${JSON.stringify(segment.routing?.relay ?? null)}`} segment={segment} segments={segments}
                    centralized={centralized} relayPool={relayPool} onDone={onDone} />
      <SegmentIpv6 key={`ipv6-${JSON.stringify(segment.routing?.ipv6 ?? null)}`} segment={segment} segments={segments}
                   services={services} centralized={centralized} pool={ipv6Pool} onDone={onDone} />
      {segment.attachments.length === 0 ? (
        <p className="muted">Not attached to any port.</p>
      ) : (
        <table className="devices">
          <thead>
            <tr>
              <th>Leaf</th>
              <th>Port</th>
              <th>Encapsulation</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {segment.attachments.map((a) => (
              <tr key={`${a.device}:${a.interface}`}>
                <td>{a.device}</td>
                <td className="mono">{a.interface}</td>
                <td>{vidLabel(a.vid)}</td>
                <td>
                  {canOperate && (
                  <div className="actions">
                  <button onClick={() => changeVid(a.device, a.interface, a.vid)}>Change VLAN</button>
                  <button
                    onClick={() =>
                      act(
                        () => detachSegment(fabric, segment.vni, a.device, a.interface),
                        `Detached from ${a.device} ${a.interface}.`,
                      )
                    }
                  >
                    Detach
                  </button>
                  </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {canOperate && segment.fabric && <AttachForm segments={[segment]} site={segment.fabric} onDone={onDone} />}
      {error && <p className="error">{error}</p>}
    </div>
  );
}
