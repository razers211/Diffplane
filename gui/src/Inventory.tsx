import { useState } from "react";
import { addInventory, fetchInventory, fetchModels, removeInventory, updateInventory, type InventorySwitch } from "./api";
import { useCan, useLimits } from "./role";
import { useFetch } from "./useFetch";

// Switches not yet in a fabric: model, serial number and MEth MAC, kept
// in NetBox's `inventory` site. New fabric and Add switches pick from here; a switch picked
// leaves the inventory and takes its serial and MAC into the fabric (ZTP answers that MAC).

const clean = (e: unknown) => (e instanceof Error ? e.message : String(e)).replace(/^\d+: /, "");

export function Inventory() {
  const limited = !!useLimits();                                // the inventory is not a fabric's
  const canOperate = useCan("operator") && !limited;
  const list = useFetch(fetchInventory, []);
  const models = useFetch(fetchModels, []);
  const [serial, setSerial] = useState("");
  const [model, setModel] = useState("");
  const [mac, setMac] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [editMac, setEditMac] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const run = async (call: () => Promise<unknown>, text: string) => {
    setError(null);
    setNotice(null);
    try {
      await call();
      setNotice(text);
      list.reload();
      return true;
    } catch (e) {
      setError(clean(e));
      return false;
    }
  };
  const chosenModel = model || models.data?.[0] || "";
  const add = async () => {
    if (await run(() => addInventory({ serial: serial.trim(), model: chosenModel, mac: mac.trim() || null }), `${serial.trim()} added.`)) {
      setSerial("");
      setMac("");
    }
  };
  const saveMac = async (s: InventorySwitch) => {
    if (await run(() => updateInventory(s.serial, { serial: s.serial, model: s.model ?? chosenModel, mac: editMac.trim() || null }),
      `${s.serial} updated.`)) setEditing(null);
  };

  const rows = list.data ?? [];
  return (
    <section>
      <div className="toolbar">
        <h1>Inventory</h1>
        <span className="muted">{rows.length} switch{rows.length === 1 ? "" : "es"} not yet in a fabric</span>
      </div>
      {notice && <p className="notice">{notice}</p>}
      {(error ?? list.error) && <p className="error">{error ?? list.error}</p>}
      {canOperate && (
        <div className="panel" data-tour="inventory-add">
          <header><h2>Add a switch</h2><span className="hint">only models with a known port layout</span></header>
          <div className="form-row">
            <label>Serial number<input className="mono" value={serial} onChange={(e) => setSerial(e.target.value)} /></label>
            <label>Model
              <select value={chosenModel} onChange={(e) => setModel(e.target.value)}>
                {(models.data ?? []).map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </label>
            <label>MEth MAC (optional)
              <input className="mono" value={mac} onChange={(e) => setMac(e.target.value)} placeholder="for ZTP" />
            </label>
            <button className="primary" onClick={add} disabled={!serial.trim() || !chosenModel}>Add</button>
          </div>
        </div>
      )}
      <div className="panel" data-tour="inventory-list">
        <table>
          <thead>
            <tr><th>Serial</th><th>Model</th><th>MEth MAC</th><th /></tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr><td colSpan={4} className="empty">No switch waiting. Add one above; New fabric and Add switches pick from here.</td></tr>
            )}
            {rows.map((s) => (
              <tr key={s.serial}>
                <td className="mono">{s.serial}</td>
                <td>{s.model}</td>
                <td className="mono">
                  {editing === s.serial ? (
                    <span className="inline">
                      <input className="mono" value={editMac} onChange={(e) => setEditMac(e.target.value)} placeholder="none" />
                      <button className="small primary" onClick={() => saveMac(s)}>Save</button>
                      <button className="small" onClick={() => setEditing(null)}>Cancel</button>
                    </span>
                  ) : (s.mac ?? <span className="faint">none</span>)}
                </td>
                <td>
                  {canOperate && editing !== s.serial && (
                    <span className="inline">
                      <button className="linklike small" onClick={() => { setEditing(s.serial); setEditMac(s.mac ?? ""); }}>Edit MAC</button>
                      <button className="linklike small" onClick={() => run(() => removeInventory(s.serial), `${s.serial} removed.`)}>Remove</button>
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
