import { Term } from "./Term";
import { Faceplate } from "./Faceplate";
import { refusal } from "./frontPanel";
import { useState } from "react";
import { createTrunk, deleteTrunk, fetchTrunks, type DeviceTrunks, type TrunkKind } from "./api";
import { useCan } from "./role";
import { candidatePorts, deleteBlocker, trunkProblem } from "./trunkForm";
import { useFetch } from "./useFetch";

// Eth-Trunks of a device: an M-LAG trunk on a pair (made on both
// devices), or a single trunk on one leaf or spine. LACP static only. Changes are intent: they
// reach the switch through a reviewed deploy, like everything else.

const KIND: Record<TrunkKind, [string, string]> = {
  "peer-link": ["peer-link", "pill"],
  "m-lag": ["M-LAG", "pill info"],
  single: ["single", "pill"],
};

const clean = (e: unknown) => (e instanceof Error ? e.message : String(e)).replace(/^\d+: /, "");

export function Trunks({ name }: { name: string }) {
  const { data, error, reload } = useFetch(() => fetchTrunks(name), [name]);
  const canOperate = useCan("operator");
  const [adding, setAdding] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const remove = async (trunk: string, both: boolean) => {
    setDeleteError(null);
    try {
      await deleteTrunk(name, trunk);
      setDone(`${trunk} deleted${both && data?.peer ? ` on ${name} and ${data.peer}` : ""}. Deploy to remove it from the switch.`);
      reload();
    } catch (e) {
      setDeleteError(clean(e));
    }
  };

  return (
    <div className="panel trunks">
      <header>
        <h2><Term k="Eth-Trunk">Eth-Trunks</Term></h2>
        <span className="hint">LACP static{data?.peer ? ` · M-LAG pair with ${data.peer}` : ""}</span>
        {canOperate && data && !adding && (
          <button onClick={() => { setAdding(true); setDone(null); }}>New trunk</button>
        )}
      </header>
      {error && <p className="error empty">{error.startsWith("404") ? "This device has no trunks to manage." : error}</p>}
      {done && <p className="notice empty">{done} <a href="#/changes">Review and deploy</a></p>}
      {deleteError && <p className="error empty">{deleteError}</p>}
      {adding && data && (
        <NewTrunk data={data} onCancel={() => setAdding(false)}
          onCreated={(text) => { setAdding(false); setDone(text); reload(); }} />
      )}
      {data && (data.trunks.length === 0 ? (
        <p className="empty">No Eth-Trunk on {name}.</p>
      ) : (
        <div className="tablewrap">
          <table className="devices">
            <thead>
              <tr><th>Trunk</th><th>Kind</th><th>Members</th><th>Used by</th>{canOperate && <th />}</tr>
            </thead>
            <tbody>
              {data.trunks.map((t) => {
                const blocker = deleteBlocker(t);
                return (
                  <tr key={t.name}>
                    <td className="mono">{t.name}</td>
                    <td><span className={KIND[t.kind][1]}>{KIND[t.kind][0]}</span></td>
                    <td className="mono small">{t.members.join(", ") || <span className="faint">none</span>}</td>
                    <td>
                      {t.service ? "service" : t.segments ? `${t.segments} segment${t.segments > 1 ? "s" : ""}` : <span className="faint">—</span>}
                    </td>
                    {canOperate && (
                      <td className="nowrap">
                        <DeleteButton trunk={t.name} blocker={blocker} both={t.kind === "m-lag"} peer={data.peer}
                          onConfirm={() => remove(t.name, t.kind === "m-lag")} />
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}

function DeleteButton({ trunk, blocker, both, peer, onConfirm }: {
  trunk: string; blocker: string | null; both: boolean; peer: string | null; onConfirm: () => void;
}) {
  const [asking, setAsking] = useState(false);
  if (blocker) return <span className="faint small" title={blocker}>kept: {blocker}</span>;
  if (!asking) return <button className="linklike small" onClick={() => setAsking(true)}>Delete</button>;
  return (
    <span className="inline">
      <span className="small">Delete {trunk}{both && peer ? ` on both devices` : ""}?</span>
      <button className="danger small" onClick={onConfirm}>Delete</button>
      <button className="small" onClick={() => setAsking(false)}>Cancel</button>
    </span>
  );
}

function NewTrunk({ data, onCancel, onCreated }: {
  data: DeviceTrunks; onCancel: () => void; onCreated: (text: string) => void;
}) {
  const [mlag, setMlag] = useState(data.peer !== null);
  const [number, setNumber] = useState(String(data.next_number));
  const [members, setMembers] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const ports = candidatePorts(data, mlag);
  const problem = trunkProblem(data, number, members, mlag);
  const toggle = (port: string) =>
    setMembers((m) => (m.includes(port) ? m.filter((p) => p !== port) : [...m, port]));
  const switchKind = (value: boolean) => {
    setMlag(value);
    const offered = new Set(candidatePorts(data, value));
    setMembers((m) => m.filter((p) => offered.has(p)));
  };

  const save = async () => {
    setError(null);
    setBusy(true);
    try {
      await createTrunk(data.device, { number: Number(number), members, mlag });
      onCreated(`Eth-Trunk${number} created${mlag ? ` on ${data.device} and ${data.peer}` : ` on ${data.device}`}. ` +
        "Deploy to put it on the switch.");
    } catch (e) {
      setError(clean(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="form-row trunk-form">
      {data.peer && (
        <span className="toggle" role="group" aria-label="Trunk kind">
          <button aria-pressed={mlag} onClick={() => switchKind(true)}>M-LAG on both devices</button>
          <button aria-pressed={!mlag} onClick={() => switchKind(false)}>Only {data.device}</button>
        </span>
      )}
      <label>
        Trunk number
        <input className="mono narrow" value={number} onChange={(e) => setNumber(e.target.value)} inputMode="numeric" />
      </label>
      <div className="attach-plate">
        <span className="muted small">Members: click the ports{mlag ? ` (the same ports are used on ${data.peer} too)` : ""}.</span>
        <Faceplate
          device={data.device}
          pick={{
            selected: members,
            refuse: (p) => refusal(p, new Set(ports), (x) => x.name, mlag ? `not free on ${data.peer} as well` : "not available for this"),
            onToggle: (t) => toggle(t),
          }}
        />
        {members.length > 0 && <div className="picked-chips">{members.map((m) => <span key={m}>{m}</span>)}</div>}
      </div>
      <p className="muted small">
        {mlag
          ? `M-LAG ID ${number || "?"} (the trunk number), mode lacp-static, on ${data.device} and ${data.peer}.`
          : `Mode lacp-static, on ${data.device} only.`}{" "}
        Free ports are front-panel ports in no trunk, with no address, segments or service.
      </p>
      <div className="inline">
        <button className="primary" onClick={save} disabled={problem !== null || busy}>
          {busy ? "Creating…" : "Create trunk"}
        </button>
        <button onClick={onCancel}>Cancel</button>
      </div>
      {(error ?? (members.length ? problem : null)) && <p className="error">{error ?? problem}</p>}
    </div>
  );
}
