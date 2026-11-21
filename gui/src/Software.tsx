import { useEffect, useMemo, useState } from "react";
import {
  approveUpgrade, deleteImage, fetchDevices, fetchImages, fetchUpgradeJob, fetchUpgradeJobs, requestUpgrade, uploadImage,
  type SoftwareImage, type UpgradeJob,
} from "./api";
import { Rollout, Status, when } from "./Deploys";
import { useCan, useMayApprove } from "./role";
import { TopologyView } from "./TopologyView";
import { downloadLine, mapMark, patchesFor, planLines, size } from "./software";
import { useFetch } from "./useFetch";
import { href } from "./nav";

// Switches › Software: the image store (admins upload and delete), an
// upgrade request (operators), the upgrades; each upgrade is approved like a deploy, from its page.

const POLL_MS = 3000;
const ACTIVE = ["preparing", "approved", "deploying"];

export function Software({ site }: { site: string }) {
  const images = useFetch(fetchImages, []);
  return (
    <div className="page">
      <Images images={images.data} error={images.error} reload={images.reload} />
      <RequestUpgrade site={site} images={(images.data ?? []).filter((i) => i.present)} />
      <UpgradeJobs site={site} />
    </div>
  );
}

function Images({ images, error, reload }: { images: SoftwareImage[] | null; error: string | null; reload: () => void }) {
  const isAdmin = useCan("admin");
  const [file, setFile] = useState<File | null>(null);
  const [sha256, setSha256] = useState("");
  const [progress, setProgress] = useState<number | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);

  const upload = async () => {
    if (!file) return;
    setProblem(null);
    setProgress(0);
    try {
      await uploadImage(file, sha256, setProgress);
      setFile(null);
      setSha256("");
      reload();
    } catch (e) {
      setProblem(e instanceof Error ? e.message : String(e));
    } finally {
      setProgress(null);
    }
  };
  const remove = async (id: number) => {
    setProblem(null);
    try {
      await deleteImage(id);
      reload();
    } catch (e) {
      setProblem(e instanceof Error ? e.message : String(e));
    } finally {
      setDeleting(null);
    }
  };

  return (
    <section data-tour="software-images">
      <div className="toolbar"><h1>Switch software</h1></div>
      <p className="muted">
        Huawei's system software (<span className="mono">.cc</span>) and patches (<span className="mono">.PAT</span>), uploaded
        once under Huawei's own file name. The platform computes each file's SHA-256; type the one from Huawei's download page
        too and a damaged download is refused. Switches fetch them from the platform when an upgrade runs.
      </p>
      {error && <p className="error">Could not load the images: {error}</p>}
      {images && images.length === 0 && <p className="muted">No image yet.</p>}
      {images && images.length > 0 && (
        <table className="devices">
          <thead>
            <tr><th>File</th><th>Kind</th><th>Release</th><th className="num">Size</th><th>SHA-256</th><th>Uploaded</th>{isAdmin && <th />}</tr>
          </thead>
          <tbody>
            {images.map((i) => (
              <tr key={i.id}>
                <td className="mono">
                  {i.name}
                  {!i.present && <span className="pill bad" title="each server keeps its own store">not on this server: upload it again</span>}
                </td>
                <td>{i.kind === "system" ? "system software" : "patch"}</td>
                <td className="mono">{i.release}</td>
                <td className="num">{size(i.size)}</td>
                <td className="mono small" title={i.sha256}>
                  {i.sha256.slice(0, 16)}…{i.sha256_typed && <span className="pill good" title="matched the one typed at upload">matched Huawei's</span>}
                </td>
                <td>{when(i.uploaded_at)}<div className="faint small">by {i.uploaded_by ?? "—"}</div></td>
                {isAdmin && (
                  <td>
                    {deleting === i.id ? (
                      <span className="confirm-inline">
                        Delete it? <button className="danger small" onClick={() => remove(i.id)}>Delete</button>{" "}
                        <button className="small" onClick={() => setDeleting(null)}>Keep</button>
                      </span>
                    ) : (
                      <button className="small" onClick={() => setDeleting(i.id)}>Delete</button>
                    )}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {isAdmin && (
        <div className="upload-row" data-tour="software-upload">
          <input id="image-file" type="file" accept=".cc,.PAT,.pat" onChange={(e) => setFile(e.target.files?.[0] ?? null)} disabled={progress !== null} />
          <input id="image-sha256" className="mono" placeholder="SHA-256 from Huawei's download page (optional)" value={sha256}
            onChange={(e) => setSha256(e.target.value)} disabled={progress !== null} />
          <button className="primary" onClick={upload} disabled={!file || progress !== null}>
            {progress === null ? "Upload" : `Uploading ${Math.round(progress * 100)} %`}
          </button>
          {progress !== null && (
            <span className="upload-bar" role="progressbar" aria-valuenow={Math.round(progress * 100)} aria-valuemin={0} aria-valuemax={100}>
              <span style={{ width: `${progress * 100}%` }} />
            </span>
          )}
        </div>
      )}
      {problem && <p className="error">{problem}</p>}
    </section>
  );
}

function RequestUpgrade({ site, images }: { site: string; images: SoftwareImage[] }) {
  const canOperate = useCan("operator");
  const devices = useFetch(() => (site ? fetchDevices(site) : Promise.resolve([])), [site]);
  const [system, setSystem] = useState<number | "">("");
  const [patch, setPatch] = useState<number | "">("");
  const [chosen, setChosen] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const systems = images.filter((i) => i.kind === "system");
  const systemImage = systems.find((i) => i.id === system) ?? null;
  const patches = patchesFor(images, systemImage);
  const ready = (devices.data ?? []).filter((d) => d.status === "active" && d.primary_ip && d.pinned);
  useEffect(() => setChosen([]), [site]);
  useEffect(() => {
    if (patch !== "" && !patches.some((p) => p.id === patch)) setPatch("");
  }, [patch, patches]);
  if (!canOperate) return null;

  const toggle = (name: string) => setChosen(chosen.includes(name) ? chosen.filter((n) => n !== name) : [...chosen, name]);
  const plan = async () => {
    setBusy(true);
    setProblem(null);
    try {
      const { id } = await requestUpgrade({ devices: chosen, system: system === "" ? null : system, patch: patch === "" ? null : patch });
      window.location.hash = href.upgrade(id);
    } catch (e) {
      setProblem(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section data-tour="software-request">
      <h2>Upgrade switches</h2>
      {!site ? (
        <p className="muted">Choose a fabric first: an upgrade covers switches of one fabric.</p>
      ) : (
        <>
          <p className="muted">
            Planning reads each switch (version, startup files, flash) and changes nothing. Once approved, the switches go one at
            a time, in the order ticked: old images deleted, the new ones copied and checked, next startup set, then a reboot
            (about 5 minutes down) and the checks of a deploy. A switch of an M-LAG pair reboots only while its partner is healthy.
          </p>
          <div className="form-row">
            <label>System software
              <select id="upgrade-system" value={system} onChange={(e) => setSystem(e.target.value ? Number(e.target.value) : "")}>
                <option value="">— keep the running one —</option>
                {systems.map((i) => <option key={i.id} value={i.id}>{i.release} · {i.name}</option>)}
              </select>
            </label>
            <label>Patch
              <select id="upgrade-patch" value={patch} onChange={(e) => setPatch(e.target.value ? Number(e.target.value) : "")}>
                <option value="">— none —</option>
                {patches.map((i) => <option key={i.id} value={i.id}>{i.release} · {i.name}</option>)}
              </select>
            </label>
          </div>
          {devices.error && <p className="error">{devices.error}</p>}
          {devices.data && ready.length === 0 && <p className="muted">No active switch with pinned host keys in {site}.</p>}
          <ul className="pick-list" data-tour="software-switches">
            {ready.map((d) => (
              <li key={d.name}>
                <label className="check">
                  <input type="checkbox" checked={chosen.includes(d.name)} onChange={() => toggle(d.name)} />
                  <span className="mono">{d.name}</span>
                  {chosen.includes(d.name) && <span className="chip accent">{chosen.indexOf(d.name) + 1}</span>}
                  <span className="faint small">{d.role} · {d.primary_ip}</span>
                </label>
              </li>
            ))}
          </ul>
          <button className="primary" onClick={plan} disabled={busy || !chosen.length || (system === "" && patch === "")}>
            {busy ? "Planning…" : `Plan the upgrade of ${chosen.length} switch${chosen.length === 1 ? "" : "es"}`}
          </button>
          {problem && <p className="error">{problem}</p>}
        </>
      )}
    </section>
  );
}

const images = (job: UpgradeJob, all: SoftwareImage[]) =>
  [job.params?.system, job.params?.patch].map((id) => all.find((i) => i.id === id)?.release).filter(Boolean).join(" + ");

export function UpgradeJobs({ site }: { site: string }) {
  const { data, error, reload } = useFetch(fetchUpgradeJobs, []);
  const store = useFetch(fetchImages, []);
  const jobs = (data ?? []).filter((j) => !site || j.site === site);
  const active = jobs.some((j) => ACTIVE.includes(j.status));
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(reload, POLL_MS);
    return () => window.clearInterval(timer);
  }, [active, reload]);
  if (data && jobs.length === 0) return null;
  return (
    <section data-tour="upgrade-list">
      <h2>Upgrades{site ? ` — ${site}` : ""}</h2>
      {error && <p className="error">{error}</p>}
      {jobs.length > 0 && (
        <table className="devices">
          <thead><tr><th>Upgrade</th><th>Status</th><th>To</th><th>Switches</th><th>Requested</th><th>Approved by</th></tr></thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.id} className="clickable" onClick={() => (window.location.hash = href.upgrade(job.id))}>
                <td><a href={href.upgrade(job.id)}><strong>#{job.id}</strong></a><div className="faint small">{job.site}</div></td>
                <td><Status status={job.status} /></td>
                <td className="mono small">{images(job, store.data ?? []) || "—"}</td>
                <td>{job.steps.map((s) => s.device).join(", ")}</td>
                <td>{when(job.created_at)}<div className="faint small">by {job.requested_by ?? "—"}</div></td>
                <td>{job.approved_by ?? <span className="faint">—</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

/** The file a switch is fetching now, as far as the platform's image server has sent it. */
function Download({ p }: { p: NonNullable<UpgradeJob["progress"]> }) {
  const { share, text } = downloadLine(p);
  return (
    <div className="download" data-tour="upgrade-download">
      <div className="download-head">
        <span className="mono">{p.device}</span> fetching <span className="mono">{p.file}</span>
        <b className="num">{Math.floor(share * 100)} %</b>
      </div>
      <span className="upload-bar" role="progressbar" aria-label={`Download of ${p.file}`} aria-valuenow={Math.floor(share * 100)}
        aria-valuemin={0} aria-valuemax={100}>
        <span style={{ width: `${share * 100}%` }} />
      </span>
      <div className="faint small">{text}</div>
    </div>
  );
}

export function UpgradeJobDetail({ id }: { id: number }) {
  const { data, error, reload } = useFetch(() => fetchUpgradeJob(id), [id]);
  const store = useFetch(fetchImages, []);
  const canApprove = useMayApprove(data?.site);
  const [confirmed, setConfirmed] = useState(false);
  const [approving, setApproving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const active = data && ACTIVE.includes(data.status);
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(reload, POLL_MS);
    return () => window.clearInterval(timer);
  }, [active, reload]);
  const planned = useMemo(() => (data ? data.steps.filter((s) => s.patch) : []), [data]);

  const approve = async () => {
    setProblem(null);
    setApproving(true);
    try {
      await approveUpgrade(id);
      reload();
    } catch (e) {
      setProblem(e instanceof Error ? e.message : String(e));
    } finally {
      setApproving(false);
    }
  };

  return (
    <div className="page">
      <section>
        <div className="toolbar">
          <a href="#/changes/history">← History</a>
          <h1>Upgrade #{id}</h1>
          {data && <Status status={data.status} />}
        </div>
        {error && <p className="error">{error}</p>}
        {data && (
          <p className="muted">
            {data.site} · to <span className="mono">{images(data, store.data ?? []) || "—"}</span> · requested by {data.requested_by} at{" "}
            {when(data.created_at)}{data.approved_by && ` · approved by ${data.approved_by} at ${when(data.approved_at)}`}
          </p>
        )}
        {data?.error && <p className="error">{data.error}</p>}
        {data && data.steps.length > 0 && <Rollout steps={data.steps} />}
        {data?.progress && <Download p={data.progress} />}
      </section>

      {data && data.steps.length > 0 && data.site && (
        <TopologyView site={data.site} title="On the map" deploy={Object.fromEntries(data.steps.map((s) => [s.device, mapMark(s.status)]))} />
      )}

      {data?.status === "diffed" && (
        <section className="approval">
          <h2>Approve this upgrade</h2>
          <p>
            <strong>{planned.length}</strong> switch{planned.length === 1 ? "" : "es"} will be upgraded and <strong>rebooted</strong>,
            one at a time, each verified (the new release, an empty diff, a fresh poll, no link lost) before the next. Each plan is
            read again just before its switch's turn and must equal the one below.
          </p>
          {canApprove ? (
            <div className="approve-row">
              <label className="check">
                <input id="upgrade-confirm" type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
                I have read every plan below, and the reboots may happen now
              </label>
              <button className="primary" disabled={!confirmed || approving} onClick={approve}>
                {approving ? "Approving…" : `Approve and upgrade ${planned.length} switch${planned.length === 1 ? "" : "es"}`}
              </button>
            </div>
          ) : (
            <p className="muted">Waiting for someone who may approve deploys of {data.site}.</p>
          )}
          {problem && <p className="error">{problem}</p>}
        </section>
      )}

      {data && data.steps.length > 0 && (
        <section data-tour="upgrade-plans">
          <h2>Plans</h2>
          {data.steps.map((step) => (
            <details key={step.device} className="patch-card" open={data.steps.length <= 3 || step.status === "failed"}>
              <summary>
                <span className="chev" aria-hidden="true" />
                <a href={href.switchPage(step.device)} className="mono" onClick={(e) => e.stopPropagation()}>{step.device}</a>
                <Status status={step.status} />
              </summary>
              {step.error && <pre className="config error-text">{step.error}</pre>}
              {step.patch ? (
                <pre className="config plan">
                  {planLines(step.patch).map((line, i) => (
                    <div key={i} className={`plan-${line.kind}`}>
                      {line.text}{line.note && <span className="faint">   {line.note}</span>}
                    </div>
                  ))}
                </pre>
              ) : step.status === "unchanged" && <p className="muted small">Already runs what was asked: nothing to do.</p>}
            </details>
          ))}
        </section>
      )}

      {data?.log && data.log.length > 0 && (
        <section>
          <details open={data.status === "failed" || !!active}>
            <summary><h2>Log</h2></summary>
            <pre className="config log">
              {data.log.map((line, i) => (
                <div key={i} className={`log-${line.level}`}>
                  {new Date(line.at).toLocaleTimeString()} {line.device ? `[${line.device}] ` : ""}{line.message}
                </div>
              ))}
            </pre>
          </details>
        </section>
      )}
    </div>
  );
}
