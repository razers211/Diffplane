import { ApplyHint } from "./Draft";
import { Term } from "./Term";
import { Fragment, useState } from "react";
import {
  deleteFabric, fetchArchitecture, fetchFabricDeletion, fetchFabrics, saveGatewayMac, saveSnmp, setDeploysAllowed,
  type FabricDeletion, type FabricSettings,
} from "./api";
import { FabricNetStream } from "./FabricNetStream";
import { FabricOspfAuth } from "./FabricOspfAuth";
import { FabricSwitchLogin } from "./FabricSwitchLogin";
import { FabricOverridesPanel } from "./FabricOverrides";
import { FabricSyslog } from "./FabricSyslog";
import { FabricSnmp } from "./FabricSnmp";
import { UsualWindowPanel } from "./Windows";
import { FabricIpv6Pool } from "./Ipv6";
import { FabricRelayPool } from "./Relay";
import { PipLayout } from "./PipLayout";
import { FabricNtp } from "./FabricNtp";
import { RollbackPoints } from "./RollbackPoints";
import { useCan, useLimits } from "./role";
import { useFetch } from "./useFetch";

// The fabric's settings: read only, as the fabric YAML or New fabric
// wrote them, except NTP, which the GUI owns. New and Import fabric start here.

const clean = (e: unknown) => (e instanceof Error ? e.message : String(e)).replace(/^\d+: /, "");

const POOLS: [keyof FabricSettings["pools"], string][] = [
  ["loopback", "Loopbacks (LoopBack0)"], ["vtep", "VTEPs (LoopBack1, LoopBack2 with PIPs)"], ["p2p", "Spine-leaf links"],
  ["mlag_bypass", "M-LAG bypass links"], ["gateways", "Segment gateways"],
  ["pip", "PIPs (LoopBack1; the pairs' VTEPs on LoopBack2)"],
];

export function Fabric({ site }: { site: string }) {
  const canOperate = useCan("operator");
  const fabric = useFetch(() => (site ? fetchArchitecture(site) : Promise.resolve(null)), [site]);
  const fabrics = useFetch(fetchFabrics, []);
  const [notice, setNotice] = useState<string | null>(null);
  const isAdmin = useCan("admin");
  const creates = canOperate && !useLimits();   // a limited account neither creates nor imports
  const f = fabric.data;
  const changed = (text: string) => { setNotice(text); fabric.reload(); };

  return (
    <section className="fabric-page">
      <div className="toolbar">
        <h1 className="sr-only">Fabric</h1>
        {creates && <a className="button" href="#/fabric/import">Import fabric</a>}
        {creates && <a className="button primary" href="#/fabric/new">New fabric</a>}
      </div>
      {!site && (
        <>
          <p className="muted">Choose a fabric (top right) to see its settings.</p>
          <ul className="changelist panel">
            {(fabrics.data ?? []).map((x) => (
              <li key={x.site}><b>{x.name}</b><span className="muted">{x.architecture}</span></li>
            ))}
          </ul>
        </>
      )}
      {fabric.error && <p className="error">{fabric.error}</p>}
      {notice && <p className="notice">{notice} <ApplyHint /></p>}
      {f && (
        <div className="cols">
          <section className="panel" aria-label="Settings">
            <header><h2>Settings</h2><span className="hint">SNMP and the gateway MAC can be edited; the rest is fixed</span></header>
            <dl className="settings">
              <dt>Architecture</dt>
              <dd>
                {f.architecture === "centralized"
                  ? <><Term k="centralized">Centralized</Term>: every gateway on <span className="mono">{(f.gateway_pair ?? []).join(" + ")}</span></>
                  : <><Term k="symmetric">Symmetric</Term>: each leaf is the gateway of the segments attached to it</>}
              </dd>
              <dt>ASN</dt><dd className="mono">{f.settings.asn ?? "—"}</dd>
              <dt>Switch vendor</dt><dd>{f.settings.vendor === "arista" ? "Arista EOS" : "Huawei CloudEngine"}</dd>
              <dt><Term k="gateway">Gateway</Term> MAC</dt>
              <dd><Editable value={f.settings.gateway_mac ?? ""} mono can={canOperate}
                save={(v) => saveGatewayMac(site, v)} done={(v) => changed(`Gateway MAC set to ${v}.`)} /></dd>
              <dt>M-LAG bypass VLAN</dt><dd className="mono">{f.settings.bypass_vlan ?? "—"}</dd>
              <dt>SNMP location</dt>
              <dd><Editable value={f.settings.snmp_location ?? ""} can={canOperate}
                save={(v) => saveSnmp(site, v, f.settings.snmp_contact ?? "")} done={() => changed("SNMP location saved.")} /></dd>
              <dt>SNMP contact</dt>
              <dd><Editable value={f.settings.snmp_contact ?? ""} can={canOperate}
                save={(v) => saveSnmp(site, f.settings.snmp_location ?? "", v)} done={() => changed("SNMP contact saved.")} /></dd>
            </dl>
          </section>
          <section className="panel" aria-label="Address pools">
            <header><h2>Address pools</h2><span className="hint">NetBox allocates from these</span></header>
            <dl className="settings">
              {POOLS.filter(([key]) => key !== "pip" || f.settings.pools.pip).map(([key, label]) => (
                <Fragment key={key}><dt>{label}</dt><dd className="mono">{f.settings.pools[key] ?? "—"}</dd></Fragment>
              ))}
            </dl>
            {!f.settings.pools.pip && site && <PipLayout site={site} onSaved={changed} />}
          </section>
        </div>
      )}
      {f && site && (
        <section className="panel ntp-panel" aria-label="NTP">
          <header><h2>NTP</h2><span className="hint">managed here</span></header>
          <div className="pad">
            <FabricNtp site={site} ntp={f.ntp} onSaved={(text) => { setNotice(text); fabric.reload(); }} />
          </div>
        </section>
      )}
      {f && site && (
        <section className="panel ntp-panel" aria-label="NetStream" data-tour="netstream">
          <header><h2>NetStream</h2><span className="hint">flow samples</span></header>
          <div className="pad">
            <FabricNetStream site={site} netstream={f.netstream} onSaved={(text) => { setNotice(text); fabric.reload(); }} />
          </div>
        </section>
      )}
      {f && site && (
        <section className="panel ntp-panel" aria-label="Syslog" data-tour="syslog">
          <header><h2>Syslog</h2><span className="hint">warnings and worse</span></header>
          <div className="pad">
            <FabricSyslog site={site} syslog={f.syslog} onSaved={(text) => { setNotice(text); fabric.reload(); }} />
          </div>
        </section>
      )}
      {f && site && (
        <section className="panel ntp-panel" aria-label="Monitoring access">
          <header><h2>Monitoring access</h2><span className="hint">SNMP v2c and v3</span></header>
          <div className="pad">
            <FabricSnmp site={site} onSaved={(text) => { setNotice(text); fabric.reload(); }} />
          </div>
        </section>
      )}
      {f && site && <FabricOverridesPanel site={site} onSaved={(text) => { setNotice(text); fabric.reload(); }} />}
      {f && site && (
        <section className="panel ntp-panel" aria-label="Link authentication">
          <header><h2>Link authentication</h2><span className="hint">OSPF keys</span></header>
          <div className="pad">
            <FabricOspfAuth site={site} onSaved={(text) => { setNotice(text); fabric.reload(); }} />
          </div>
        </section>
      )}
      {f && site && (
        <section className="panel ntp-panel" aria-label="Switch login">
          <header><h2>Switch login</h2><span className="hint">RADIUS for people</span></header>
          <div className="pad">
            <FabricSwitchLogin site={site} onSaved={(text) => { setNotice(text); fabric.reload(); }} />
          </div>
        </section>
      )}
      {f && site && (
        <section className="panel ntp-panel" aria-label="DHCP relay">
          <header><h2>Addressing for relay and IPv6</h2></header>
          <div className="pad">
            <FabricRelayPool site={site} pool={f.relay_pool} onSaved={(text) => { setNotice(text); fabric.reload(); }} />
            <FabricIpv6Pool site={site} pool={f.ipv6_pool} onSaved={(text) => { setNotice(text); fabric.reload(); }} />
          </div>
        </section>
      )}
      {f && site && <UsualWindowPanel site={site} />}
      {f && site && <RollbackPoints site={site} />}
      {f && site && isAdmin && <DeploysPanel site={site} allowed={f.deploys_allowed} onChanged={() => fabric.reload()} />}
      {f && site && isAdmin && <DangerZone site={site} />}
    </section>
  );
}

/** A value shown, with Edit for those who may change it. */
function Editable({ value, can, save, done, mono = false }: {
  value: string; can: boolean; mono?: boolean; save: (v: string) => Promise<unknown>; done: (v: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const [error, setError] = useState<string | null>(null);
  if (!editing) {
    return (
      <span className="inline">
        <span className={mono ? "mono" : ""}>{value || "—"}</span>
        {can && <button className="linklike small" onClick={() => { setDraft(value); setError(null); setEditing(true); }}>Edit</button>}
      </span>
    );
  }
  return (
    <form className="inline" onSubmit={async (e) => {
      e.preventDefault();
      setError(null);
      try {
        await save(draft);
        setEditing(false);
        done(draft);
      } catch (err) {
        setError(clean(err));
      }
    }}>
      <input className={mono ? "mono" : ""} autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} />
      <button type="submit" className="small primary">Save</button>
      <button type="button" className="small" onClick={() => setEditing(false)}>Cancel</button>
      {error && <span className="error small">{error}</span>}
    </form>
  );
}

/** Deploys to this fabric on or off (admins). Turning them on asks for the fabric's name. */
function DeploysPanel({ site, allowed, onChanged }: { site: string; allowed: boolean; onChanged: () => void }) {
  const [asking, setAsking] = useState(false);
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);
  const turn = async (on: boolean) => {
    setError(null);
    try {
      await setDeploysAllowed(site, on, typed);
      setAsking(false);
      setTyped("");
      onChanged();
    } catch (e) {
      setError(clean(e));
    }
  };
  return (
    <section className="panel deploys-panel" aria-label="Deploys">
      <header><h2>Deploys to {site}</h2><span className={allowed ? "pill good" : "pill"}>{allowed ? "allowed" : "off"}</span></header>
      <div className="pad">
        <p className="muted small">
          {allowed
            ? "Approved deploys are sent to this fabric's switches in waves, never both switches of a pair at once."
            : "Nothing can be deployed to this fabric: changes are prepared and checked, never sent."}
        </p>
        {allowed ? (
          <button className="danger small" onClick={() => turn(false)}>Turn deploys off</button>
        ) : asking ? (
          <span className="inline">
            <input className="mono" autoFocus placeholder={site} value={typed} onChange={(e) => setTyped(e.target.value)} aria-label="Fabric name" />
            <button className="primary small" disabled={typed.trim() !== site} onClick={() => turn(true)}>Allow deploys</button>
            <button className="small" onClick={() => setAsking(false)}>Cancel</button>
            <span className="muted small">Type <b className="mono">{site}</b> to confirm.</span>
          </span>
        ) : (
          <button className="small" onClick={() => setAsking(true)}>Allow deploys…</button>
        )}
        {error && <p className="error small">{error}</p>}
      </div>
    </section>
  );
}

/** Deleting the fabric (admins): only when every switch is planned; its name typed. */
function DangerZone({ site }: { site: string }) {
  const [plan, setPlan] = useState<FabricDeletion | null>(null);
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const ask = async () => {
    setError(null);
    try {
      setPlan(await fetchFabricDeletion(site));
    } catch (e) {
      setError(clean(e));
    }
  };
  const remove = async () => {
    setBusy(true);
    setError(null);
    try {
      await deleteFabric(site, typed);
      window.location.hash = "#/fabric";
      window.location.reload();
    } catch (e) {
      setError(clean(e));
      setBusy(false);
    }
  };
  return (
    <section className="panel danger-zone" aria-label="Delete this fabric">
      <header><h2>Delete this fabric</h2></header>
      <div className="pad">
        {!plan ? (
          <>
            <p className="muted small">Removes the fabric and everything of it from the platform. Nothing is sent to any switch.</p>
            <button className="danger small" onClick={ask}>Delete {site}…</button>
          </>
        ) : (
          <>
            <p>
              Deleting <b>{plan.name}</b> removes {plan.devices.length} switch{plan.devices.length === 1 ? "" : "es"} ({plan.devices.join(", ") || "none"}),
              {" "}{plan.cables} cable{plan.cables === 1 ? "" : "s"}, {plan.segments} segment{plan.segments === 1 ? "" : "s"},
              {" "}{plan.services} service{plan.services === 1 ? "" : "s"}, {plan.prefixes} address pools and subnets and {plan.addresses} addresses,
              its routing intent and its racks. The switches keep their running config; the history of deploys and checks stays.
            </p>
            {plan.yaml && <p className="warning small">This fabric comes from <code>seed/fabrics/{plan.yaml}</code>: the next seed creates it again.</p>}
            {plan.blocker ? (
              <p className="error small">Not yet: {plan.blocker}. Decommission them on their Settings tab.</p>
            ) : (
              <span className="inline">
                <input className="mono" autoFocus placeholder={site} value={typed} onChange={(e) => setTyped(e.target.value)} aria-label="Fabric name" />
                <button className="danger small" disabled={busy || typed.trim() !== site} onClick={remove}>{busy ? "Deleting…" : "Delete for good"}</button>
                <button className="small" onClick={() => { setPlan(null); setTyped(""); }}>Cancel</button>
                <span className="muted small">Type <b className="mono">{site}</b> to confirm.</span>
              </span>
            )}
          </>
        )}
        {error && <p className="error small">{error}</p>}
      </div>
    </section>
  );
}

