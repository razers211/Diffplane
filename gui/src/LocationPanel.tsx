import { useState } from "react";
import { addRack, fetchDevices, fetchRacks, placeDevice, type FabricRacks } from "./api";
import { freePositions, placeLabel } from "./location";
import { useCan } from "./role";
import { useFetch } from "./useFetch";

// Where a switch is: location (room/hall), rack, U position and face,
// kept in NetBox. Not device config: no deploy, nothing in a diff.

const clean = (e: unknown) => (e instanceof Error ? e.message : String(e)).replace(/^\d+: /, "");

export function LocationPanel({ name }: { name: string }) {
  const devices = useFetch(() => fetchDevices(), []);
  const device = devices.data?.find((d) => d.name === name);
  const site = device?.site ?? "";
  const racks = useFetch(() => (site ? fetchRacks(site) : Promise.resolve(null)), [site]);
  const canOperate = useCan("operator");
  const [editing, setEditing] = useState(false);

  if (!device) return null;
  return (
    <div className="panel location" data-tour="location">
      <header>
        <h2>Location</h2>
        <span className="hint">{site}</span>
        {canOperate && racks.data && !editing && <button onClick={() => setEditing(true)}>Change</button>}
      </header>
      {!editing ? (
        <p className="empty">
          <span className="mono">{placeLabel(device)}</span>
        </p>
      ) : (
        racks.data && (
          <PlaceForm name={name} site={site} racks={racks.data} current={device}
            onDone={() => { setEditing(false); devices.reload(); racks.reload(); }}
            onRacks={racks.reload} onCancel={() => setEditing(false)} />
        )
      )}
      {racks.error && <p className="error empty">{racks.error}</p>}
    </div>
  );
}

function PlaceForm({ name, site, racks, current, onDone, onRacks, onCancel }: {
  name: string; site: string; racks: FabricRacks;
  current: { location: string | null; rack: string | null; position: number | null; face: "front" | "rear" | null };
  onDone: () => void; onRacks: () => void; onCancel: () => void;
}) {
  const options = racks.locations.flatMap((l) => l.racks.map((r) => ({ key: `${l.name}\u0000${r.name}`, location: l.name, rack: r })));
  const [choice, setChoice] = useState(current.rack ? `${current.location}\u0000${current.rack}` : "");
  const [position, setPosition] = useState(current.position ? String(current.position) : "");
  const [face, setFace] = useState<"front" | "rear">(current.face ?? "front");
  const [error, setError] = useState<string | null>(null);
  const [newRack, setNewRack] = useState({ location: racks.locations[0]?.name ?? "", rack: "", u: "42" });
  const [adding, setAdding] = useState(options.length === 0);

  const selected = options.find((o) => o.key === choice);
  const height = selected?.rack.devices.find((d) => d.name === name)?.u_height ?? 1;
  const free = selected ? freePositions(selected.rack, height, name) : [];

  const save = async () => {
    setError(null);
    try {
      await placeDevice(name, selected
        ? { location: selected.location, rack: selected.rack.name, position: position ? Number(position) : null,
            face: position ? face : null }
        : { location: null, rack: null, position: null, face: null });
      onDone();
    } catch (e) {
      setError(clean(e));
    }
  };
  const createRack = async () => {
    setError(null);
    try {
      await addRack(site, { location: newRack.location.trim(), rack: newRack.rack.trim(), u_height: Number(newRack.u) });
      setChoice(`${newRack.location.trim()}\u0000${newRack.rack.trim()}`);
      setAdding(false);
      onRacks();
    } catch (e) {
      setError(clean(e));
    }
  };

  return (
    <div className="form-row place-form">
      {!adding ? (
        <>
          <label>
            Rack
            <select value={choice} onChange={(e) => { setChoice(e.target.value); setPosition(""); }}>
              <option value="">not placed</option>
              {options.map((o) => (
                <option key={o.key} value={o.key}>{o.location} / {o.rack.name} ({o.rack.u_height} U)</option>
              ))}
            </select>
          </label>
          {selected && (
            <>
              <label>
                U position
                <select value={position} onChange={(e) => setPosition(e.target.value)}>
                  <option value="">not set</option>
                  {free.map((u) => <option key={u} value={u}>U{u}</option>)}
                </select>
              </label>
              {position && (
                <span className="toggle" role="group" aria-label="Face">
                  <button aria-pressed={face === "front"} onClick={() => setFace("front")}>Front</button>
                  <button aria-pressed={face === "rear"} onClick={() => setFace("rear")}>Rear</button>
                </span>
              )}
            </>
          )}
          <div className="inline">
            <button className="primary" onClick={save}>Save</button>
            <button onClick={onCancel}>Cancel</button>
            <button className="linklike small" onClick={() => setAdding(true)}>New rack…</button>
          </div>
        </>
      ) : (
        <>
          <label>
            Location (room or hall)
            <input value={newRack.location} onChange={(e) => setNewRack({ ...newRack, location: e.target.value })}
              list="known-locations" placeholder="Hall A" />
            <datalist id="known-locations">
              {racks.locations.map((l) => <option key={l.name} value={l.name} />)}
            </datalist>
          </label>
          <label>
            Rack
            <input value={newRack.rack} onChange={(e) => setNewRack({ ...newRack, rack: e.target.value })} placeholder="R01" />
          </label>
          <label>
            Height (U)
            <input className="narrow mono" value={newRack.u} onChange={(e) => setNewRack({ ...newRack, u: e.target.value })} inputMode="numeric" />
          </label>
          <div className="inline">
            <button className="primary" onClick={createRack} disabled={!newRack.location.trim() || !newRack.rack.trim()}>Create rack</button>
            {options.length > 0 && <button onClick={() => setAdding(false)}>Back</button>}
            <button onClick={onCancel}>Cancel</button>
          </div>
        </>
      )}
      {error && <p className="error">{error}</p>}
    </div>
  );
}
