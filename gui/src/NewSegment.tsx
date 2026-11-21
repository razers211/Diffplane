import { Term } from "./Term";
import { useEffect, useState, type ReactNode } from "react";
import {
  attachSegment,
  createSegment,
  fetchArchitecture,
  fetchDevices,
  fetchDiffJob,
  fetchNextBdId,
  fetchPorts,
  requestDeploy,
  setRelay,
  setRouting,
  setSegmentIpv6,
  startDiffJob,
  type DiffJob,
  type FabricArchitecture,
  type Port,
  type Segment,
  type Service,
} from "./api";
import { DiffBadge, DiffText } from "./DiffView";
import {
  emptyDraft,
  gatewayPlace,
  followingVni,
  gatewaySwitches,
  PREFIX_CHOICES,
  segmentVpn,
  plan,
  portErrors,
  relayServers,
  routingErrors,
  segmentErrors,
  suggestedVni,
  type Draft,
  type Write,
} from "./segmentFlow";
import { Faceplate } from "./Faceplate";
import { refusal, trunkOrPort } from "./frontPanel";
import { attachableLeaves } from "./placement";
import { parseVid } from "./vid";
import { currentDraft } from "./draft";

// The guided "New segment" flow: segment, ports, routing, review, then a
// preview of the change on the switches and a deploy request. Every write goes through the
// orchestrator's usual checks; nothing reaches a switch before a deploy is approved.

const STEPS = ["Segment", "Ports", "Routing", "Review"] as const;

function message(e: unknown): string {
  return e instanceof Error ? e.message.replace(/^\d+: /, "") : String(e);
}

type Progress = { write: Write; state: "pending" | "done" | "failed"; error?: string };

export function NewSegment({
  site,
  segments,
  services,
  architecture,
  onClose,
  onChanged,
}: {
  site: string;
  segments: Segment[];
  services: Service[];
  architecture: FabricArchitecture | null;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [step, setStep] = useState(0);
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [touched, setTouched] = useState(false);
  const [vniByHand, setVniByHand] = useState(false);   // the VNI follows the BD until typed in
  const [otherPrefix, setOtherPrefix] = useState(false);
  const [progress, setProgress] = useState<Progress[] | null>(null);
  const set = (patch: Partial<Draft>) => setDraft((d) => ({ ...d, ...patch }));

  // Start from the next free bridge domain and its suggested VNI.
  useEffect(() => {
    fetchNextBdId(site)
      .then(({ bd_id }) => setDraft((d) => (d.bd ? d : { ...d, bd: String(bd_id), vni: String(suggestedVni(bd_id)) })))
      .catch(() => undefined);
  }, [site]);

  const segErrors = segmentErrors(draft, segments);
  const pErrors = portErrors(draft);
  const rError = routingErrors(draft);
  const stepOk = [Object.keys(segErrors).length === 0, pErrors.every((e) => e === null), rError === null, true];
  const firewalls = services.filter((s) => s.kind === "firewall");
  const routedOthers = segments.filter((s) => s.fabric && s.routing);

  const next = () => {
    setTouched(true);
    if (stepOk[step]) {
      setTouched(false);
      setStep(step + 1);
    }
  };

  const run = async () => {
    const writes = plan(draft);
    const states: Progress[] = writes.map((write) => ({ write, state: "pending" }));
    setProgress([...states]);
    const vni = Number(draft.vni);
    for (const [i, entry] of states.entries()) {
      try {
        if (entry.write.kind === "create") await createSegment(site, draft.name.trim(), vni, Number(draft.bd));
        else if (entry.write.kind === "attach") {
          const vid = parseVid(entry.write.port.vid);
          await attachSegment(site, vni, entry.write.port.device, entry.write.port.port, vid.ok ? vid.vid : null);
        } else if (entry.write.kind === "ipv6") {
          await setSegmentIpv6(site, vni, { enabled: true, hosts: draft.ipv6 || "slaac" });
        } else if (entry.write.kind === "relay") {
          await setRelay(site, vni, relayServers(draft).map((address) => ({ address })));
        } else {
          await setRouting(site, vni, {
            routed: true, prefix_length: Number(draft.prefixLength), firewall: draft.firewall || null, peers: draft.peers,
          });
        }
        states[i] = { ...entry, state: "done" };
      } catch (e) {
        states[i] = { ...entry, state: "failed", error: message(e) };
        setProgress([...states]);
        onChanged();
        return;
      }
      setProgress([...states]);
    }
    onChanged();
  };

  const finished = progress !== null && progress.every((p) => p.state === "done");
  const failed = progress?.some((p) => p.state === "failed") ?? false;

  const vpnPreview = segmentVpn(draft.name.trim() || "name", draft.bd || "?");
  const switches = gatewaySwitches(architecture?.architecture, architecture?.gateway_pair ?? null, draft);

  return (
    <section className="wz" aria-label="New segment">
      <header className="wz-head">
        <h2>New segment</h2>
        <button className="close" onClick={onClose} aria-label="Close">×</button>
      </header>
      <div className="wz-body">
        <ol className="wz-steps">
          {STEPS.map((label, i) => {
            const state = progress !== null || i < step ? "done" : i === step ? "now" : "";
            return (
              <li key={label} className={state}>
                <span className="n">{state === "done" ? "✓" : i + 1}</span>
                <span>{label}</span>
              </li>
            );
          })}
        </ol>

        <div className="wz-pane">
          {progress === null && (
            <div className="wz-slide" key={step}>
              {step === 0 && (
                <>
                  <Field label="What is the segment called?" error={touched ? segErrors.name : undefined}>
                    <input id="seg-name" className="wz-big" value={draft.name} onChange={(e) => set({ name: e.target.value })} placeholder="e.g. web" autoFocus autoComplete="off" />
                  </Field>
                  <div className="wz-cards">
                    <label className={`wz-card${touched && segErrors.bd ? " invalid" : ""}`}>
                      <span className="k"><Term k="BD">Bridge domain</Term> · next free</span>
                      <input id="seg-bd" className="v" value={draft.bd} inputMode="numeric" onChange={(e) => {
                        const bd = e.target.value;
                        set({ bd, ...(vniByHand ? {} : { vni: followingVni(bd) }) });
                      }} />
                      {touched && segErrors.bd && <span className="field-error">{segErrors.bd}</span>}
                    </label>
                    <label className={`wz-card${touched && segErrors.vni ? " invalid" : ""}`}>
                      <span className="k"><Term k="VNI">VNI</Term> · {vniByHand ? "set by hand" : "10000 + bridge domain"}</span>
                      <input id="seg-vni" className="v" value={draft.vni} inputMode="numeric" onChange={(e) => { setVniByHand(true); set({ vni: e.target.value }); }} />
                      {touched && segErrors.vni && <span className="field-error">{segErrors.vni}</span>}
                    </label>
                    <div className="wz-card vpn">
                      <span className="k"><Term k="VPN">VPN instance</Term>, if routed</span>
                      <span className="v mono">{vpnPreview}</span>
                    </div>
                  </div>
                  <p className="muted small">The numbers are proposed for you; click one to change it.</p>
                </>
              )}

              {step === 1 && <PortsStep site={site} draft={draft} set={set} errors={pErrors} />}

              {step === 2 && (
                <>
                  <button type="button" className={`wz-toggle${draft.routed ? " on" : ""}`} aria-pressed={draft.routed}
                    onClick={() => set({ routed: !draft.routed, firewall: !draft.routed ? draft.firewall || firewalls[0]?.name || "" : draft.firewall })}>
                    <span className="sw" aria-hidden="true" />
                    <span><b>Routed</b> · hosts in {draft.name.trim() || "the segment"} get a <Term k="gateway">gateway</Term> and reach other networks</span>
                  </button>
                  <RoutingPicture name={draft.name.trim() || "segment"} routed={draft.routed} switches={switches} firewall={draft.firewall} />
                  {!draft.routed && <p className="muted">Layer 2 only: hosts reach each other inside the segment, nothing else.</p>}
                  {draft.routed && (
                    <>
                      <p className="muted small">The gateway will be {gatewayPlace(architecture?.architecture, architecture?.gateway_pair ?? null, draft)}.</p>
                      <div className="wz-group">
                        <span className="k">Default route out of the segment</span>
                        <div className="wz-options">
                          {[...firewalls.map((s) => ({ v: s.name, b: `firewall ${s.name}`, t: `on ${s.device} ${s.port}` })), { v: "", b: "none", t: "stays inside the fabric" }].map((o) => (
                            <button type="button" key={o.v || "none"} className="wz-option" aria-pressed={draft.firewall === o.v} onClick={() => set({ firewall: o.v })}>
                              <b>{o.b}</b><span>{o.t}</span>
                            </button>
                          ))}
                        </div>
                      </div>
                      <div className="wz-group">
                        <span className="k">Gateway subnet size · from the fabric's gateway pool</span>
                        <div className="chips">
                          {PREFIX_CHOICES.map((l) => (
                            <button type="button" key={l} className="chip" aria-pressed={!otherPrefix && draft.prefixLength === l}
                              onClick={() => { setOtherPrefix(false); set({ prefixLength: l }); }}>/{l} · {2 ** (32 - Number(l)) - 2} hosts</button>
                          ))}
                          <button type="button" className="chip" aria-pressed={otherPrefix} onClick={() => setOtherPrefix(true)}>other…</button>
                          {otherPrefix && (
                            <span className="inline">/<input id="seg-prefix" className="narrow" value={draft.prefixLength} inputMode="numeric" autoFocus
                              onChange={(e) => set({ prefixLength: e.target.value })} aria-label="Gateway prefix length" /></span>
                          )}
                        </div>
                        {rError && <span className="field-error">{rError}</span>}
                      </div>
                      <div className="wz-group">
                        <span className="k">IPv6 · optional</span>
                        {architecture?.ipv6_pool ? (
                          <div className="chips" role="group" aria-label="IPv6">
                            {([["", "IPv4 only"], ["slaac", "dual-stack, SLAAC"], ["dhcpv6", "dual-stack, DHCPv6"], ["none", "dual-stack, manual"]] as const).map(([v, label]) => (
                              <button type="button" key={v || "off"} className="chip" aria-pressed={draft.ipv6 === v} onClick={() => set({ ipv6: v })}>{label}</button>
                            ))}
                          </div>
                        ) : <span className="muted small">Needs the fabric's IPv6 pool (Fabric page).</span>}
                        {draft.ipv6 === "dhcpv6" && <span className="muted small">Add its DHCPv6 servers on the segment's page afterwards.</span>}
                      </div>
                      <div className="wz-group">
                        <span className="k">DHCP relay · optional</span>
                        {architecture?.relay_pool ? (
                          <>
                            <input id="seg-relay" value={draft.relay} onChange={(e) => set({ relay: e.target.value })}
                              placeholder="DHCP servers in this segment's VPN instance, e.g. 10.100.0.50" aria-label="DHCP relay servers" />
                            <span className="muted small">Servers behind a firewall or in another segment: on the segment's page afterwards.</span>
                          </>
                        ) : <span className="muted small">Needs the fabric's relay loopback pool (Fabric page).</span>}
                      </div>
                      <div className="wz-group">
                        <span className="k">Reach other segments directly</span>
                        <div className="chips">
                          {routedOthers.length === 0 && <span className="muted small">no other routed segment</span>}
                          {routedOthers.map((s) => (
                            <button type="button" key={s.vni} className="chip" aria-pressed={draft.peers.includes(s.vni)}
                              onClick={() => set({ peers: draft.peers.includes(s.vni) ? draft.peers.filter((p) => p !== s.vni) : [...draft.peers, s.vni] })}>
                              {s.name}
                            </button>
                          ))}
                        </div>
                      </div>
                    </>
                  )}
                </>
              )}

              {step === 3 && (
                <>
                  <dl className="wz-review">
                    <dt>Name</dt><dd><b>{draft.name.trim()}</b></dd>
                    <dt>Bridge domain / VNI</dt><dd className="mono">{draft.bd} / {draft.vni}</dd>
                    <dt>Ports</dt><dd>{draft.ports.length ? <span className="picked-chips">{draft.ports.map((p) => <span key={p.device + p.port}>{p.device} {p.port} · {p.vid.trim() ? `VLAN ${p.vid.trim()}` : "untagged"}</span>)}</span> : <span className="muted">none yet (attach later)</span>}</dd>
                    <dt>Routing</dt><dd>{draft.routed ? <>routed · VPN instance <span className="mono">{vpnPreview}</span> · {draft.firewall ? `via firewall ${draft.firewall}` : "no default route"}</> : "layer 2 only"}</dd>
                  </dl>
                  <p className="muted small">{currentDraft()
                    ? `Create saves this into draft #${currentDraft()!.id}, in this order: nothing changes now; it is applied at its window's start.`
                    : "Create writes this to intent, in this order. Nothing reaches a switch before a deploy is approved."}</p>
                  <ol className="writes">{plan(draft).map((w, i) => <li key={i}>{w.label}</li>)}</ol>
                </>
              )}

              <div className="wz-nav">
                <button type="button" onClick={() => setStep(step - 1)} disabled={step === 0}>← Back</button>
                {step < 3 ? (
                  <button type="button" className="primary" onClick={next}>Next →</button>
                ) : (
                  <button type="button" className="primary" onClick={run}>Create the segment</button>
                )}
              </div>
            </div>
          )}

          {progress !== null && (
            <div className="wz-slide">
              {finished && (
                <div className="wz-done">
                  <svg className="done-mark" viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="32" r="29" /><path d="M19 33l9 9 17-19" /></svg>
                  <div><h3>{draft.name.trim()} saved</h3><p className="muted">{currentDraft() ? `In draft #${currentDraft()!.id}: its patch is shown when you prepare its deploy (Changes › Pending).` : "The yellow bar shows what reaches the switches."}</p></div>
                </div>
              )}
              <ol className="writes progress">
                {progress.map((p, i) => (
                  <li key={i} className={p.state}>
                    <span className="mark" aria-hidden="true">{p.state === "done" ? "✓" : p.state === "failed" ? "✕" : "…"}</span>
                    <span>
                      {p.write.label}
                      {p.error && <span className="error-text"> — {p.error}</span>}
                    </span>
                  </li>
                ))}
              </ol>
              {failed && (
                <p className="error">
                  Stopped at the failed step; the steps before it are saved. Fix it from the segment's row below, or delete the segment.
                </p>
              )}
              {finished && !currentDraft() && <Preview site={site} />}
              <div className="wz-nav">
                <span />
                <button type="button" onClick={onClose}>Close</button>
              </div>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

/** Where the gateway lands, drawn: hosts, the switches that answer as gateway, the exit. */
function RoutingPicture({ name, routed, switches, firewall }: { name: string; routed: boolean; switches: string[]; firewall: string }) {
  const shown = switches.length ? switches.slice(0, 4) : ["(no port yet)"];
  const h = Math.max(120, shown.length * 46 + 30);
  return (
    <svg className="wz-picture" viewBox={`0 0 540 ${h}`} role="img" aria-label="Where the gateway is">
      <rect x="10" y={h / 2 - 25} width="140" height="50" rx="25" className="hosts" />
      <text x="80" y={h / 2 + 5} textAnchor="middle" className="t">{name} · hosts</text>
      {shown.map((s, i) => {
        const y = 22 + i * 46;
        return (
          <g key={s}>
            <line x1="150" y1={h / 2} x2="240" y2={y + 15} className="wire" />
            <rect x="240" y={y} width="130" height="30" rx="6" className="sw" />
            <text x="305" y={y + 20} textAnchor="middle" className="t mono">{s}</text>
            {routed && switches.length > 0 && (
              <g className="pop" style={{ animationDelay: `${i * 120}ms` }}>
                <circle cx="384" cy={y + 15} r="11" className="gw" />
                <text x="384" y={y + 19} textAnchor="middle" className="gwt">G</text>
              </g>
            )}
          </g>
        );
      })}
      {routed && (
        <g className="pop" style={{ animationDelay: `${shown.length * 120}ms` }}>
          <line x1="395" y1={h / 2} x2="440" y2={h / 2} className="exit" />
          <rect x="440" y={h / 2 - 17} width="92" height="34" rx="6" className={firewall ? "fw" : "none"} />
          <text x="486" y={h / 2 + 4} textAnchor="middle" className="t">{firewall ? `firewall ${firewall}` : "no exit"}</text>
        </g>
      )}
    </svg>
  );
}

function Field({ label, hint, error, children }: { label: string; hint?: string; error?: string; children: ReactNode }) {
  return (
    <div className={`field${error ? " invalid" : ""}`}>
      <span className="field-label">
        {label}
        {hint && <small>{hint}</small>}
      </span>
      {children}
      {error && <span className="field-error">{error}</span>}
    </div>
  );
}

function PortsStep({ site, draft, set, errors }: { site: string; draft: Draft; set: (p: Partial<Draft>) => void; errors: (string | null)[] }) {
  const [devices, setDevices] = useState<string[]>([]);
  const [device, setDevice] = useState("");
  const [ports, setPorts] = useState<Port[]>([]);
  const [tagging, setTagging] = useState<"untagged" | "bd" | "custom">("untagged");
  const [vid, setVid] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([fetchDevices(site), fetchArchitecture(site)])
      .then(([all, arch]) => {
        const leaves = attachableLeaves(all, arch.architecture === "centralized").map((d) => d.name);
        setDevices(leaves);
        setDevice((d) => d || leaves[0] || "");
      })
      .catch((e) => setError(message(e)));
  }, [site]);
  useEffect(() => {
    setPorts([]);
    if (!device) return;
    fetchPorts(device).then(setPorts).catch((e) => setError(`Could not list ${device}'s ports: ${message(e)}`));
  }, [device]);

  const vidFor = () => (tagging === "untagged" ? "" : tagging === "bd" ? draft.bd : vid);
  const picked = draft.ports.filter((p) => p.device === device).map((p) => p.port);
  const toggle = (target: string) => {
    if (picked.includes(target)) return set({ ports: draft.ports.filter((p) => !(p.device === device && p.port === target)) });
    if (tagging === "custom") {
      const parsed = parseVid(vid);
      if (!parsed.ok || parsed.vid === null) return setError(parsed.ok ? "Give the VLAN ID first" : parsed.error);
    }
    setError(null);
    const trunk = !!ports.find((p) => p.name === target)?.mlag;
    set({ ports: [...draft.ports, { device, port: target, trunk, vid: vidFor() }] });
  };

  return (
    <>
      <p className="muted">Click the ports that carry the segment. A port in an M-LAG trunk picks the trunk, on both leaves of the pair. You can also attach ports later.</p>
      <div className="wz-group">
        <span className="k">Leaf</span>
        <div className="chips">
          {devices.map((d) => (
            <button type="button" key={d} className="chip" aria-pressed={device === d} onClick={() => setDevice(d)}>
              {d}{draft.ports.some((p) => p.device === d) ? ` · ${draft.ports.filter((p) => p.device === d).length}` : ""}
            </button>
          ))}
        </div>
      </div>
      <div className="wz-group">
        <span className="k">Tagging for the next ports you click</span>
        <div className="chips">
          <button type="button" className="chip" aria-pressed={tagging === "untagged"} onClick={() => setTagging("untagged")}>untagged</button>
          <button type="button" className="chip" aria-pressed={tagging === "bd"} onClick={() => setTagging("bd")}>tagged, VLAN {draft.bd || "= BD"}</button>
          <button type="button" className="chip" aria-pressed={tagging === "custom"} onClick={() => setTagging("custom")}>tagged, other VLAN</button>
          {tagging === "custom" && <input id="port-vid" className="narrow" value={vid} onChange={(e) => setVid(e.target.value)} inputMode="numeric" placeholder="VLAN" />}
        </div>
      </div>
      {device && (
        <Faceplate
          device={device}
          refresh={ports.length}
          pick={{
            selected: picked,
            refuse: (p) => refusal(p, new Set(ports.map((x) => x.name))),
            target: trunkOrPort,
            onToggle: (t) => toggle(t),
          }}
        />
      )}
      {error && <p className="error">{error}</p>}
      {draft.ports.length > 0 && (
        <div className="picked-chips">
          {draft.ports.map((p, i) => (
            <span key={`${p.device}:${p.port}`} className={errors[i] ? "bad" : ""} title={errors[i] ?? undefined}>
              {p.device} {p.port} · {p.vid.trim() ? `VLAN ${p.vid.trim()}` : "untagged"}{p.trunk ? " · pair" : ""}
              <button type="button" className="x" aria-label={`Remove ${p.device} ${p.port}`} onClick={() => set({ ports: draft.ports.filter((_, k) => k !== i) })}>×</button>
            </span>
          ))}
        </div>
      )}
    </>
  );
}

/** A read-only diff of the fabric after the change, then a deploy request. */
function Preview({ site }: { site: string }) {
  const [job, setJob] = useState<DiffJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [requesting, setRequesting] = useState(false);

  const start = async () => {
    setError(null);
    try {
      const { id } = await startDiffJob({ site });
      setJob(await fetchDiffJob(id));
    } catch (e) {
      setError(message(e));
    }
  };

  useEffect(() => {
    if (!job || job.status !== "running") return;
    const id = window.setTimeout(() => fetchDiffJob(job.id).then(setJob).catch((e) => setError(message(e))), 2000);
    return () => window.clearTimeout(id);
  }, [job]);

  const deploy = async () => {
    setRequesting(true);
    setError(null);
    try {
      const { id } = await requestDeploy({ site });
      window.location.hash = `#/changes/deploys/${id}`;
    } catch (e) {
      setError(message(e));
      setRequesting(false);
    }
  };

  const changed = (job?.snapshots ?? []).filter((s) => s.status !== "ok" || s.has_changes);
  return (
    <div className="preview">
      <h3>The change on the switches</h3>
      {!job && (
        <p>
          <button className="primary" onClick={start}>Preview on the switches</button>{" "}
          <span className="muted">Runs a read-only diff of every active switch of {site}.</span>
        </p>
      )}
      {job?.status === "running" && <p className="muted">Comparing each switch with the new intent…</p>}
      {job && job.status !== "running" && (
        <>
          {changed.length === 0 ? (
            <p className="muted">No active switch changes (the ports may be on planned switches).</p>
          ) : (
            changed.map((snap) => (
              <div key={snap.device} className="diff-card">
                <h2>
                  <span className="mono">{snap.device}</span> <DiffBadge snap={snap} />
                </h2>
                <DiffText snap={snap} />
              </div>
            ))
          )}
          <p>
            <button className="primary" onClick={deploy} disabled={requesting}>
              {requesting ? "Preparing…" : "Prepare deploy"}
            </button>{" "}
            <span className="muted">Computes each switch's patch for approval on the Deploys page.</span>
          </p>
        </>
      )}
      {error && <p className="error">{error}</p>}
    </div>
  );
}
