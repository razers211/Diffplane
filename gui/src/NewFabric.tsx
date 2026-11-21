import { useState } from "react";
import { createFabric, fetchFabrics, fetchInventory, proposeAddressPlan, type AddressPlan, type BuildOptions } from "./api";
import { BatchForm } from "./AddSwitches";
import { emptySettings, POOL_LABELS, settingsProblem, slugOf, toSettings, type SettingsDraft } from "./newFabric";
import { useCan } from "./role";
import { useFetch } from "./useFetch";

// A new fabric from the GUI: its settings, an address plan proposed from
// one private subnet (editable), and its first switches with their roles and links, picked from
// the inventory or typed in. Everything is checked by the orchestrator before the first write;
// the fabric then exists in NetBox only (no YAML). Every switch starts planned unless given its
// management address; nothing is deployed from here.

const clean = (e: unknown) => (e instanceof Error ? e.message : String(e)).replace(/^\d+: /, "");

export function NewFabric({ onCreated }: { onCreated: (site: string) => void }) {
  const canOperate = useCan("operator");
  const fabrics = useFetch(fetchFabrics, []);
  const inventory = useFetch(fetchInventory, []);
  const [s, setS] = useState<SettingsDraft>(emptySettings);
  const [plan, setPlan] = useState<AddressPlan | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  const set = (d: Partial<SettingsDraft>) => setS({ ...s, ...d });

  const propose = async () => {
    setPlanError(null);
    try {
      const p = await proposeAddressPlan(s.supernet.trim());
      setPlan(p);
      setS({ ...s, prefixes: p.prefixes, bypassVlan: String(p.bypass_vlan), gatewayMac: s.gatewayMac || p.gateway_mac });
    } catch (e) {
      setPlanError(clean(e));
    }
  };

  if (!canOperate) return <section><p className="muted">Creating a fabric needs the operator role.</p></section>;
  const problem = settingsProblem(s, (fabrics.data ?? []).map((f) => f.site));
  const opts: BuildOptions | null = plan && {
    fabric: slugOf(s.name) || "new", models: plan.models, devices: [], bypass: true,
    centralized: s.architecture === "centralized", gateway_pair: [], inventory: inventory.data ?? [], fresh: true,
  };

  return (
    <section className="add-switches new-fabric">
      <div className="toolbar">
        <a href="#/fabric">← Fabric</a>
        <h1>New fabric</h1>
      </div>
      <p className="muted">
        The settings a fabric YAML would hold, then its first switches. Everything is checked before anything is written; the
        fabric then lives in NetBox only. Switches without a management address are added <b>planned</b> (activate them on
        their device page, or provision them with ZTP). Nothing is deployed from here.
      </p>

      <div className="panel">
        <header data-tour="new-fabric-settings"><h2>Fabric</h2><span className="hint">site {slugOf(s.name) || "—"}</span></header>
        <div className="form-row build-form">
          <label>Name<input value={s.name} onChange={(e) => set({ name: e.target.value })} placeholder="dc2" /></label>
          <label>ASN<input className="mono" value={s.asn} onChange={(e) => set({ asn: e.target.value })} inputMode="numeric" placeholder="65002" /></label>
          <fieldset>
            <legend>Architecture</legend>
            <label className="check">
              <input type="radio" checked={s.architecture === "symmetric"} onChange={() => set({ architecture: "symmetric" })} />
              Symmetric IRB: anycast gateways on every leaf where a segment is; border leaves route (2-byte ASN)
            </label>
            <label className="check">
              <input type="radio" checked={s.architecture === "centralized"} onChange={() => set({ architecture: "centralized" })} />
              Centralized (asymmetric) IRB: every gateway on one M-LAG pair of spines or border leaves; the other leaves are layer 2
            </label>
          </fieldset>
        </div>
      </div>

      <div className="panel">
        <header data-tour="new-fabric-pools"><h2>Address plan</h2><span className="hint">fabric-internal addressing, its own namespace in NetBox</span></header>
        <div className="form-row build-form">
          <label>Private subnet to split
            <input className="mono" value={s.supernet} onChange={(e) => set({ supernet: e.target.value })} placeholder="10.92.0.0/16" />
          </label>
          <button onClick={propose}>{plan ? "Propose again" : "Propose a split"}</button>
          {planError && <span className="error">{planError}</span>}
        </div>
        {s.prefixes && (
          <div className="form-row build-form">
            {POOL_LABELS.map(([key, label]) => (
              <label key={key}>{label}
                <input className="mono" value={s.prefixes![key]} onChange={(e) => set({ prefixes: { ...s.prefixes!, [key]: e.target.value } })} />
              </label>
            ))}
            <label>M-LAG bypass VLAN
              <input className="mono narrow" value={s.bypassVlan} onChange={(e) => set({ bypassVlan: e.target.value })} inputMode="numeric" />
            </label>
            <label>Anycast gateway MAC
              <input className="mono" value={s.gatewayMac} onChange={(e) => set({ gatewayMac: e.target.value })} />
            </label>
          </div>
        )}
      </div>

      <div className="panel">
        <header><h2>Services</h2><span className="hint">optional</span></header>
        <div className="form-row build-form">
          <label>NTP servers
            <input className="mono" value={s.ntpServers} onChange={(e) => set({ ntpServers: e.target.value })} placeholder="empty = not managed" />
          </label>
          <label>NTP through VPN instance
            <input className="mono" value={s.ntpVpn} onChange={(e) => set({ ntpVpn: e.target.value })} placeholder="global table" />
          </label>
          <label>SNMP location<input value={s.snmpLocation} onChange={(e) => set({ snmpLocation: e.target.value })} /></label>
          <label>SNMP contact<input value={s.snmpContact} onChange={(e) => set({ snmpContact: e.target.value })} /></label>
        </div>
      </div>

      {inventory.error && <p className="error">{inventory.error}</p>}
      {opts ? (
        <BatchForm key={s.architecture} prefix={opts.fabric} opts={opts} verb="Create fabric with"
          blocked={problem}
          onSubmit={async (switches) => {
            const r = await createFabric(toSettings(s), switches);
            onCreated(r.site);
          }} />
      ) : (
        <p className="muted">Propose the address plan to add the switches.</p>
      )}
    </section>
  );
}
