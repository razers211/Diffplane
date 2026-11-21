import { useState } from "react";
import { addSwitches, fetchBuildOptions, fetchInventory, type BuildOptions, type ImportSession } from "./api";
import {
  GENERIC, autoLinks, batchProblem, linksForDetected, cableOn, canBeGateway, emptySwitch, inventoryChoices, leafChoices, nextNames, peerLinkChoices, placeDetected, portChoices, spineChoices, toBatch,
  unpairedSpines, withDetected, type Kind, type LinkDraft, type SwitchDraft,
} from "./buildForm";
import { BuildMap } from "./BuildMap";
import { DetectSwitches } from "./DetectSwitches";
import { useCan } from "./role";
import { useFetch } from "./useFetch";

// Add switches to a fabric (several at once, lone leaves, optional management
// addresses; spine pairs in a centralized fabric). Leaf pairs, lone leaves, spines and spine pairs
// are modelled in NetBox with everything the seed
// would give them, cabled to new or existing switches. A switch given its management address is
// added active (its host keys are pinned afterwards, on its device page); the others stay planned
// (activate later, or zero-touch provisioning). Nothing is deployed from here.

const clean = (e: unknown) => (e instanceof Error ? e.message : String(e)).replace(/^\d+: /, "");
const KIND_LABEL: Record<Kind, string> = { pair: "Leaf pair (M-LAG)", leaf: "Lone leaf", spine: "Spine", spinepair: "Spine pair (M-LAG)" };

export function AddSwitches({ site }: { site: string }) {
  const options = useFetch(
    () => (site ? Promise.all([fetchBuildOptions(site), fetchInventory()]).then(([o, inventory]) => ({ ...o, inventory })) : Promise.resolve(null)),
    [site]);
  const canOperate = useCan("operator");
  const [done, setDone] = useState<{ devices: string[]; active: string[]; gateway_pair: string[] | null } | null>(null);
  const [round, setRound] = useState(0);   // a fresh form after each addition

  if (!site) return <section><p className="muted">Pick a fabric first (top right).</p></section>;
  return (
    <section className="add-switches">
      <div className="toolbar">
        <a href="#/switches">← Switches</a>
        <h1>Add switches to {site}</h1>
      </div>
      <p className="muted">
        Add any number of leaf pairs, lone leaves and spines in one go. Each is modelled with its ports, cables,
        point-to-point and loopback addresses (allocated by NetBox). Detect switches that are already reachable: they are
        built with the model and ports they report, added <b>active</b>, and their approved host keys are pinned.
        A switch of an unknown model can be placed as <b>Generic</b>: it is cabled once it is detected and placed.
        The others are added <b>planned</b>: activate them later, or provision them with ZTP. The existing switches they
        are cabled to show the new links in their diff.
      </p>
      {!canOperate && <p className="muted">Adding switches needs the operator role.</p>}
      {options.error && <p className="error">{options.error}</p>}
      {done && (
        <p className="notice">
          Added {done.devices.map((d, i) => (
            <span key={d}>{i > 0 && ", "}<a className="mono" href={`#/switches/${encodeURIComponent(d)}`}>{d}</a></span>
          ))}
          {done.active.length > 0 ? `; active: ${done.active.join(", ")} (pin their host keys on their device pages)` : "; all planned"}.
          {done.gateway_pair && <> The gateway pair is now <b>{done.gateway_pair.join(" + ")}</b>: the next deploy moves the gateways there.</>}
        </p>
      )}
      {canOperate && options.data && (
        <BatchForm key={round} prefix={site} opts={options.data} onRemoved={options.reload}
          onSubmit={async (batch) => {
            const r = await addSwitches(site, batch);
            setDone({ devices: r.devices, active: r.active, gateway_pair: r.gateway_pair ?? null });
            setRound(round + 1);
            options.reload();
          }} />
      )}
    </section>
  );
}

/** The switches and links of one submission; also the new-fabric wizard's switch step. */
export function BatchForm({ prefix, opts: given, onSubmit, verb = "Add", blocked = null, onRemoved }: {
  prefix: string; opts: BuildOptions; onSubmit: (batch: ReturnType<typeof toBatch> & { detect: string | null }) => Promise<void>; verb?: string;
  onRemoved?: () => void; // a planned switch was removed from the fabric on the map
  blocked?: string | null; // why the rest of the page cannot be sent yet
}) {
  const site = prefix;
  const [session, setSession] = useState<ImportSession | null>(null);   // switches detected for this batch
  const opts = withDetected(given, session);
  const [chosen, setModel] = useState<string | null>(null);   // the model new boxes on the map get
  const model = chosen ?? Object.keys(opts.models)[0] ?? GENERIC;
  const [drafts, setDrafts] = useState<SwitchDraft[]>([]);
  const [links, setLinks] = useState<LinkDraft[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const problem = blocked ?? batchProblem(opts, drafts, links);
  const spines = spineChoices(opts, drafts);
  const leaves = leafChoices(opts, drafts);
  const spare = unpairedSpines(opts);
  const kinds: Kind[] = opts.centralized ? ["pair", "leaf", "spine", "spinepair"] : ["pair", "leaf", "spine"];
  const isExisting = (n: string) => opts.devices.some((d) => d.name === n.trim());

  const setDraft = (i: number, d: Partial<SwitchDraft>) => setDrafts(drafts.map((x, k) => (k === i ? { ...x, ...d } : x)));
  const setName = (i: number, j: number, name: string) =>
    setDraft(i, { names: drafts[i].names.map((n, k) => (k === j ? name : n)) });
  const setMgmt = (i: number, j: number, address: string) =>
    setDraft(i, { management: drafts[i].management.map((a, k) => (k === j ? address : a)) });
  const setStock = (i: number, j: number, serial: string) =>
    setDraft(i, { inventory: drafts[i].inventory.map((s, k) => (k === j ? serial : s)) });
  const setLink = (i: number, l: Partial<LinkDraft>) => setLinks(links.map((x, k) => (k === i ? { ...x, ...l } : x)));
  // A new switch with proposed names, cabled to every spine (a leaf) or every leaf (a spine) on the
  // fastest free ports; a cable drawn on the map the same way.
  const add = (kind: Kind) => {
    const names = nextNames(opts, drafts, prefix, kind);
    const next = [...drafts, { ...emptySwitch(kind, model), names }];
    setDrafts(next);
    setLinks(autoLinks(opts, next, links, names, kind === "spine" || kind === "spinepair"));
  };
  const cable = (spine: string, leaf: string) => {
    const generic = [spine, leaf].find((n) => drafts.some((d) => d.model === GENERIC && d.names.some((x) => x.trim() === n)));
    if (generic) return `${generic} is generic: detect it first, so its ports are known.`;
    const link = cableOn(opts, drafts, links, spine, leaf);
    if (!link) return `No free port left on ${portChoices(opts, drafts, links, spine, links.length).length ? leaf : spine}.`;
    setLinks([...links, link]);
    return null;
  };
  // An inventory switch dropped onto a new box: that box becomes it.
  const stock = (name: string, serial: string) => {
    const i = drafts.findIndex((d) => d.names.some((n) => n.trim() === name));
    const j = i < 0 ? -1 : drafts[i].names.findIndex((n) => n.trim() === name);
    if (j < 0) return `${name} is not new in this batch.`;
    if (!inventoryChoices(opts, drafts, drafts[i], j).some((s) => s.serial === serial)) {
      const s = opts.inventory?.find((x) => x.serial === serial);
      return s && s.model !== drafts[i].model ? `${serial} is a ${s.model}, ${name} a ${drafts[i].model}.` : `${serial} is already picked.`;
    }
    setStock(i, j, serial);
    return null;
  };
  const drop = (name: string) => {
    const gone = drafts.find((d) => d.names.some((n) => n.trim() === name))?.names.map((n) => n.trim()) ?? [name];
    setDrafts(drafts.filter((d) => !d.names.some((n) => n.trim() === name)));
    setLinks(links.filter((l) => !gone.includes(l.spine) && !gone.includes(l.leaf)));
  };
  const toggleMember = (i: number, port: string) => {
    const m = drafts[i].peerLinkMembers;
    setDraft(i, { peerLinkMembers: m.includes(port) ? m.filter((p) => p !== port) : [...m, port] });
  };

  const save = async () => {
    setError(null);
    setBusy(true);
    try {
      await onSubmit({ ...toBatch(drafts, links, opts), detect: session?.switches.some((s) => s.detected) ? session.id : null });
    } catch (e) {
      setError(clean(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <DetectSwitches session={session} onSession={setSession}
        names={drafts.flatMap((d) => d.names.map((n) => n.trim())).filter((n) => n && !isExisting(n))}
        onUse={(name, s) => {
          if (!s.detected) return;
          const next = placeDetected(drafts, name, s.address, s.detected.model);
          setDrafts(next);
          setLinks(linksForDetected(opts, drafts, next, links, name));
        }} />
      <BuildMap site={site} opts={opts} drafts={drafts} links={links} model={model} onModel={setModel} onAdd={add} onLink={cable} onStock={stock} onDrop={drop} onRemoved={onRemoved} />
      <div className="panel">
        <header>
          <h2>Switches</h2>
          <span className="hint">{drafts.length} to add</span>
          <span className="inline actions-right">
            {kinds.map((k) => (
              <button key={k} className="small" onClick={() => add(k)}>+ {KIND_LABEL[k]}</button>
            ))}
          </span>
        </header>
        {drafts.length === 0 && (
          <p className="empty">Add a leaf pair, a lone leaf or a spine{opts.centralized ? ", or an M-LAG spine pair" : ""}.</p>
        )}
        {spare.length > 0 && <datalist id="spare-spines">{spare.map((n) => <option key={n} value={n} />)}</datalist>}
        {drafts.map((d, i) => (
          <div key={i} className="form-row build-form switch-row">
            <b className="kind">{KIND_LABEL[d.kind]}</b>
            {d.names.map((n, j) => (
              <span key={j} className="inline">
                <label>{d.kind === "pair" ? `Leaf ${j + 1}` : d.kind === "spinepair" ? `Spine ${j + 1}` : "Name"}
                  <input className="mono" value={n} onChange={(e) => setName(i, j, e.target.value)}
                    list={d.kind === "spinepair" ? "spare-spines" : undefined}
                    placeholder={d.kind === "spinepair" ? (j === 0 && spare[0] ? spare[0] : `${site}-spine${2 + j}`)
                      : d.kind === "spine" ? `${site}-spine2` : `${site}-leaf${3 + j}`} />
                </label>
                {d.kind === "spinepair" && isExisting(n) ? (
                  <span className="muted small">existing spine: joins the pair</span>
                ) : (
                  <>
                    <label>Management (optional)
                      <input className="mono" value={d.management[j]} onChange={(e) => setMgmt(i, j, e.target.value)} placeholder="planned" />
                    </label>
                    {(opts.inventory?.length ?? 0) > 0 && (
                      <label>From inventory
                        <select className="mono" value={d.inventory[j]} onChange={(e) => setStock(i, j, e.target.value)}>
                          <option value="">—</option>
                          {inventoryChoices(opts, drafts, d, j).map((s) => (
                            <option key={s.serial} value={s.serial}>{s.serial}{s.mac ? ` (${s.mac})` : ""}</option>
                          ))}
                        </select>
                      </label>
                    )}
                  </>
                )}
              </span>
            ))}
            <label>Model
              <select value={d.model} onChange={(e) => setDraft(i, { model: e.target.value, peerLinkMembers: [], inventory: d.inventory.map(() => "") })}>
                {Object.keys(opts.models).map((m) => <option key={m} value={m}>{m}</option>)}
                <option value={GENERIC}>Generic: detect its ports</option>
              </select>
            </label>
            {d.kind !== "spine" && d.kind !== "spinepair" && (
              <label>Role
                <select value={d.role} onChange={(e) => setDraft(i, { role: e.target.value as SwitchDraft["role"] })}>
                  <option value="leaf">leaf</option>
                  <option value="border-leaf">border-leaf</option>
                </select>
              </label>
            )}
            {(d.kind === "pair" || d.kind === "spinepair") && (
              <>
                <label>Peer-link trunk
                  <input className="mono narrow" value={d.peerLinkTrunk} onChange={(e) => setDraft(i, { peerLinkTrunk: e.target.value })} inputMode="numeric" />
                </label>
                <fieldset>
                  <legend>Peer-link members (the same ports on both {d.kind === "pair" ? "leaves" : "spines"}, cabled port to port)</legend>
                  {peerLinkChoices(opts, d).map((p) => (
                    <label key={p} className="check mono small">
                      <input type="checkbox" checked={d.peerLinkMembers.includes(p)} onChange={() => toggleMember(i, p)} />
                      {p}
                    </label>
                  ))}
                </fieldset>
              </>
            )}
            {d.kind === "leaf" && <p className="muted small">No M-LAG peer: its own VTEP, no DFS group, peer-link or bypass (unverified on a switch).</p>}
            {canBeGateway(opts, d) && (
              <label className="check">
                <input type="checkbox" checked={d.gateway} onChange={(e) => setDraft(i, { gateway: e.target.checked })} />
                {opts.fresh
                  ? "The gateway pair: every routed segment's gateways and VPN instances are here."
                  : <>Becomes the gateway pair{opts.gateway_pair?.length ? ` (instead of ${opts.gateway_pair.join(" + ")})` : ""}: every routed
                    segment's gateways and VPN instances move here at the next deploy.</>}
                {d.kind === "spinepair" && " Otherwise it is layer 2, like the other leaves."}
              </label>
            )}
            <button className="linklike small" onClick={() => setDrafts(drafts.filter((_, k) => k !== i))}>Remove</button>
          </div>
        ))}
      </div>

      <div className="panel">
        <header>
          <h2>Links</h2>
          <span className="hint">spine ↔ leaf, each with at least one new switch; one point-to-point /30 per link</span>
          <span className="inline actions-right">
            <button className="small" onClick={() => setLinks([...links, { spine: spines[0] ?? "", spine_port: "", leaf: "", leaf_port: "" }])}>+ Link</button>
          </span>
        </header>
        {links.length === 0 && <p className="empty">No link yet. Every new leaf needs at least one uplink when the fabric has spines.</p>}
        <div className="build-form links">
          {links.map((l, i) => (
            <div key={i} className="link-row">
              <select value={l.spine} onChange={(e) => setLink(i, { spine: e.target.value, spine_port: "" })} aria-label="Spine">
                <option value="">spine</option>
                {spines.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
              <select className="mono" value={l.spine_port} onChange={(e) => setLink(i, { spine_port: e.target.value })} aria-label="Spine port">
                <option value="">port</option>
                {l.spine && portChoices(opts, drafts, links, l.spine, i).map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
              <span className="faint">↔</span>
              <select value={l.leaf} onChange={(e) => setLink(i, { leaf: e.target.value, leaf_port: "" })} aria-label="Leaf">
                <option value="">leaf</option>
                {leaves.map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
              <select className="mono" value={l.leaf_port} onChange={(e) => setLink(i, { leaf_port: e.target.value })} aria-label="Leaf port">
                <option value="">port</option>
                {l.leaf && portChoices(opts, drafts, links, l.leaf, i).map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
              <button className="linklike small" onClick={() => setLinks(links.filter((_, k) => k !== i))}>Remove</button>
            </div>
          ))}
        </div>
      </div>

      <div className="inline submit-row">
        <button className="primary" onClick={save} disabled={problem !== null || busy}>
          {busy ? `${verb === "Add" ? "Adding" : "Creating"}…` : `${verb} ${drafts.reduce((n, d) => n + d.names.length, 0) || ""} switch${drafts.reduce((n, d) => n + d.names.length, 0) === 1 ? "" : "es"}`}
        </button>
        {(error ?? problem) && <span className={error ? "error" : "muted"}>{error ?? problem}</span>}
      </div>
    </>
  );
}
