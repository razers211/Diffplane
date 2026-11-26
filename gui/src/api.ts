// Types and calls for the orchestrator API. The GUI talks to the orchestrator only.
import { INTENT_SAVED, isIntentWrite } from "./changes";
import { DRAFT_SAVED, draftHeaders, type ChangeSet } from "./draft";
import type { FacePort } from "./frontPanel";
import type { OverviewData } from "./overview";
import type { DeviceRouting, ImportReport, LibraryChange, RoutingDocument, RoutingTarget } from "./routingIntent";
import type { Contract, GbpService, Microseg, MicrosegMode, SwitchState } from "./microseg";
import type { QosMark, QosView } from "./qos";
import type { DciDocument, DciLink, DciSegment, DciSide } from "./dci";
import type { components, paths } from "./schema";
/** Answers the API declares a response model for (orchestrator/answers.py). */
type Answers = components["schemas"];

// Every call names its route as the orchestrator's OpenAPI schema does (src/schema.d.ts, made from
// openapi.json), so a route changed or removed in the API, or called with a method it does not take,
// fails the type check. Path parameters fill its "{name}"s. Answers are typed by hand below: the
// API declares no response models.
type Method = "get" | "post" | "put" | "patch" | "delete";
type Route<M extends Method> = {
  [P in keyof paths]: [NonNullable<paths[P][M]>] extends [never] ? never : P;
}[keyof paths];
type Query = Record<string, string | number | boolean | null | undefined> | URLSearchParams;
interface Parts { path?: Record<string, string | number>; query?: Query; body?: unknown }
// FastAPI's `:path` parameters (a port name, an overrides path): each piece encoded, the slashes kept.
const SLASHED = new Set(["port", "path"]);

/** The URL of a route: its parameters encoded in place, then the query (unset values left out). */
export function url(route: string, { path, query }: Parts = {}): string {
  const filled = route.replace(/\{(\w+)\}/g, (_, name: string) => {
    if (path?.[name] === undefined) throw new Error(`${route}: no value for ${name}`);
    const value = String(path[name]);
    return SLASHED.has(name) ? value.split("/").map(encodeURIComponent).join("/") : encodeURIComponent(value);
  });
  const search = query instanceof URLSearchParams ? String(query) : Object.entries(query ?? {})
    .filter(([, v]) => v !== undefined && v !== null)
    .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join("&");
  return search ? `${filled}?${search}` : filled;
}

export type LinkStatus = "ok" | "missing" | "mismatch" | "unverified" | "unexpected";
export type EndCheck = LinkStatus;

export type Device = Answers["Device"];

export interface TopologyNode {
  name: string;
  site: string | null;
  role: string | null;
  status: string | null;
  in_netbox: boolean;
  collected: boolean;
}

export interface LinkEnd {
  device: string;
  interface: string;
  oper_up: boolean | null;
  lldp: { system_name: string; port_id: string }[];
  check: EndCheck;
}

export interface TopologyLink {
  source: "netbox" | "lldp";
  status: LinkStatus;
  ends: [LinkEnd, LinkEnd];
}

export interface Topology {
  nodes: TopologyNode[];
  links: TopologyLink[];
  telemetry: { ok: boolean; error: string | null };
}

export type DiffSnapshot = Answers["DiffSnapshot"];

export type DiffJob = Answers["DiffJob"];

// The API's own types, narrowed where the GUI knows the values (statuses, checks, params).
export type DeployStep = Answers["DeployStep"] & {
  status: "pending" | "diffed" | "unchanged" | "deploying" | "verifying" | "verified" | "failed" | "skipped"
    | "upgrading" | "rebooting";   // upgrades
};

export type DeployJob = Omit<Answers["DeployJob"], "steps" | "checks" | "params"> & {
  status: "queued" | "preparing" | "diffed" | "unchanged" | "approved" | "deploying" | "verified" | "failed"
    | "scheduled" | "reapproval" | "cancelled" | "stopped";   // change windows
  checks?: DeployChecks | null;   // pre-deploy checks, as when prepared or last approved
  // change_set: a draft's deploy; new_patches/asked: the window waits for an approver (status reapproval)
  params?: { change_set?: number; new_patches?: Record<string, string>; asked?: string; reapproved_by?: string } | null;
  steps: DeployStep[];
};

// -- pre-deploy checks -------------------------------------------------------------------
export interface CheckFinding { severity: "error" | "warning"; check: string; text: string; objects: string[] }
export interface FabricChecks { fabric: string; findings: CheckFinding[]; blocking: boolean; link_state?: string }
export interface DeployChecks { fabrics: FabricChecks[]; blocking: boolean; at?: string }
export const fetchChecks = (site: string) => get<FabricChecks>("/fabrics/{site}/checks", { path: { site } });

export interface AuditEvent {
  at: string;
  user: string | null;
  action: string;
  job_id: number | null;
  detail: string | null;
}


// Raised on 401 so the app can show the login page.
export const UNAUTHORIZED = "fabric:unauthorized";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: "same-origin",
    ...init,
    headers: { Accept: "application/json", ...draftHeaders(), ...init?.headers },
  });
  if (response.status === 401 && path !== "/auth/login") {
    window.dispatchEvent(new Event(UNAUTHORIZED));
  }
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      // keep the status text
    }
    throw new Error(`${response.status}: ${detail}`);
  }
  if (response.status === 202) {   // saved into the draft: the endpoint's own answer is inside;
    const body = await response.json();   // jobs (deploy, diff, upgrade) answer 202 too, without `change_set`
    if (body && typeof body === "object" && "change_set" in body) {
      window.dispatchEvent(new Event(DRAFT_SAVED));
      return body.result as T;
    }
    return body as T;
  }
  // A saved intent change: the app checks the switches against it (pending bar).
  if (isIntentWrite(init?.method ?? "GET", path)) window.dispatchEvent(new Event(INTENT_SAVED));
  return response.json() as Promise<T>;
}

const get = <T>(route: Route<"get">, parts?: Parts) => request<T>(url(route, parts));
const send = <T>(method: string, route: string, parts: Parts = {}) =>
  request<T>(url(route, parts), {
    method,
    headers: { "Content-Type": "application/json" },
    body: parts.body === undefined ? undefined : JSON.stringify(parts.body),
  });
const post = <T>(route: Route<"post">, parts?: Parts) => send<T>("POST", route, parts);
const put = <T>(route: Route<"put">, parts?: Parts) => send<T>("PUT", route, parts);
const patch = <T>(route: Route<"patch">, parts?: Parts) => send<T>("PATCH", route, parts);
const del = <T>(route: Route<"delete">, parts?: Parts) => send<T>("DELETE", route, parts);

export const fetchDevices = (site?: string) => get<Device[]>("/devices", { query: { site: site || undefined } });
export const fetchTopology = (site?: string) => get<Topology>("/topology", { query: { site: site || undefined } });
export const fetchConfig = (device: string) =>
  get<{ device: string; config: string }>("/devices/{name}/config", { path: { name: device } });

export const fetchDiffJobs = () => get<DiffJob[]>("/diff-jobs");
export const fetchDiffJob = (id: number) => get<DiffJob>("/diff-jobs/{job_id}", { path: { job_id: id } });
export const fetchDeviceDiff = (device: string) => get<DiffSnapshot>("/devices/{name}/diff", { path: { name: device } });
export const startDiffJob = (scope: { site?: string; devices?: string[] }) =>
  post<{ id: number }>("/diff-jobs", { body: scope });

export type Role = "viewer" | "operator" | "approver" | "admin";
/** Fabrics an account is limited to: null reaches every fabric; else site -> may approve deploys there. */
export type FabricLimits = Record<string, boolean> | null;
export type Me = Answers["Me"];
export const fetchMe = () => get<Me>("/auth/me");
export const fetchSetup = () => get<{ needed: boolean }>("/setup");
export const claimSetup = (code: string, username: string, password: string) => post<Me>("/setup", { body: { code, username, password } });
export const changeMyPassword = (current: string, next: string) => post<object>("/auth/password", { body: { current, new: next } });

// -- settings (admins) -------------------------------------------------------------------
export interface SettingRow {
  key: string; label: string; group: string; secret: boolean; kind: string; help: string; applies: string; choices: string[];
  source: "gui" | "env" | "unset"; value: string | null; // null for secrets: never returned
}
export interface SettingsView {
  settings: SettingRow[]; fixed: { key: string; set: boolean; value: string | null }[];
  applied?: string | null;   // after a change: what went wrong applying it (Alertmanager's reload), if anything
}
export const fetchAlertReceiver = () => get<{ receiver: string; chosen: string; problem: string | null }>("/alerts/receiver");
export const sendTestAlert = () => post<{ sent: boolean; receiver: string }>("/alerts/test", { body: {} });
export const fetchSettings = () => get<SettingsView>("/settings");
export const saveSetting = (key: string, value: string) =>
  put<SettingsView>("/settings/{key}", { path: { key }, body: { value } });
export const resetSetting = (key: string) => del<SettingsView>("/settings/{key}", { path: { key } });

// -- accounts (admins) -------------------------------------------------------------------
export interface Account { username: string; role: Role; active: boolean; created_at: string; fabrics: FabricLimits; source: "local" | "ldap" | "radius" }
export const fetchUsers = () => get<Account[]>("/users");
export const createUser = (body: { username: string; password: string; role: Role }) => post<Account>("/users", { body });
export const changeUser = (name: string, body: { role?: Role; password?: string; fabrics?: FabricLimits }) =>
  patch<object>("/users/{name}", { path: { name }, body });
export const deleteUser = (name: string) => del<object>("/users/{name}", { path: { name } });
export const login = (username: string, password: string) =>
  post<Me>("/auth/login", { body: { username, password } });
export const logout = () => post<object>("/auth/logout");

export const fetchDeployJobs = () => get<DeployJob[]>("/deploy-jobs");
export const fetchDeployJob = (id: number) => get<DeployJob>("/deploy-jobs/{job_id}", { path: { job_id: id } });
export const requestDeploy = (scope: { site?: string; devices?: string[]; generators?: string[] }) =>
  post<{ id: number }>("/deploy-jobs", { body: scope });
export const approveDeploy = (id: number, window?: { start: string; end: string }) =>
  post<{ id: number }>("/deploy-jobs/{job_id}/approve", { path: { job_id: id }, body: window ? { window_start: window.start, window_end: window.end } : undefined });
export const cancelDeploy = (id: number) => post<object>("/deploy-jobs/{job_id}/cancel", { path: { job_id: id } });

// -- change windows ---------------------------------------------------------------------
export const fetchChangeSets = (site: string) => get<ChangeSet[]>("/fabrics/{site}/change-sets", { path: { site } });
export const fetchChangeSet = (site: string, id: number) => get<ChangeSet>("/fabrics/{site}/change-sets/{change_set_id}", { path: { site, change_set_id: id } });
export const createChangeSet = (site: string) => post<ChangeSet>("/fabrics/{site}/change-sets", { path: { site } });
export const discardChangeSet = (site: string, id: number) =>
  del<ChangeSet>("/fabrics/{site}/change-sets/{change_set_id}", { path: { site, change_set_id: id } });
export const deployChangeSet = (site: string, id: number) => post<{ job_id: number }>("/fabrics/{site}/change-sets/{change_set_id}/deploy", { path: { site, change_set_id: id } });
export interface UsualWindow { days: string[]; start: string; minutes: number; zone: string }
export interface Span { start: string; end: string }
export interface UsualWindowView { usual: UsualWindow | null; next: Span | null; upcoming: Span[] }
export const fetchUsualWindow = (site: string) => get<UsualWindowView>("/fabrics/{site}/usual-window", { path: { site } });
export const saveUsualWindow = (site: string, usual: UsualWindow | null) =>
  put<UsualWindowView>("/fabrics/{site}/usual-window", { path: { site }, body: usual ?? undefined });
export const fetchAudit = () => get<AuditEvent[]>("/audit");

// -- switch software --------------------------------------------------------------------
export interface SoftwareImage {
  id: number; name: string; kind: "system" | "patch"; release: string; size: number; sha256: string;
  sha256_typed: boolean; uploaded_by: string | null; uploaded_at: string;
  present: boolean;   // the file is on this server (HA: each server keeps its own store)
}
export const fetchImages = () => get<SoftwareImage[]>("/images");
/** The file as the raw body (hundreds of MB): XHR, so the page can show the upload's progress. */
export function uploadImage(file: File, sha256: string, onProgress: (share: number) => void): Promise<SoftwareImage> {
  const params = new URLSearchParams({ name: file.name, ...(sha256.trim() ? { sha256: sha256.trim() } : {}) });
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url("/images" satisfies Route<"post">, { query: params }));
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.setRequestHeader("Accept", "application/json");
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      if (xhr.status === 401) window.dispatchEvent(new Event(UNAUTHORIZED));
      let body: { detail?: string } & Partial<SoftwareImage> = {};
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        // no JSON body
      }
      if (xhr.status === 201) resolve(body as SoftwareImage);
      else reject(new Error(`${xhr.status}: ${body.detail ?? xhr.statusText}`));
    };
    xhr.onerror = () => reject(new Error("the upload was interrupted"));
    xhr.send(file);
  });
}
export async function deleteImage(id: number): Promise<void> {
  const response = await fetch(url("/images/{image_id}" satisfies Route<"delete">, { path: { image_id: id } }), { method: "DELETE", credentials: "same-origin" });
  if (!response.ok) throw new Error(`${response.status}: ${(await response.json().catch(() => ({}))).detail ?? response.statusText}`);
}
/** A download running now: what the image server has sent of it; rate in bytes/s. */
export interface DownloadProgress { device: string; file: string; size: number; bytes: number; started: number; rate: number | null }
export type UpgradeJob = DeployJob & {
  params: { system: number | null; patch: number | null } | null;
  progress?: DownloadProgress | null;   // only from GET /upgrade-jobs/<id>
};
export const fetchUpgradeJobs = () => get<UpgradeJob[]>("/upgrade-jobs");
export const fetchUpgradeJob = (id: number) => get<UpgradeJob>("/upgrade-jobs/{job_id}", { path: { job_id: id } });
export const requestUpgrade = (body: { devices: string[]; system: number | null; patch: number | null }) =>
  post<{ id: number }>("/upgrade-jobs", { body });
export const approveUpgrade = (id: number) => post<{ id: number }>("/upgrade-jobs/{job_id}/approve", { path: { job_id: id } });

export type Segment = Answers["Segment"];

export type Routing = Answers["Routing"];

// IPv6 of a routed segment: its /64's ::1, how hosts get addresses, DHCPv6 servers.
export type Ipv6Hosts = "slaac" | "dhcpv6" | "none";
export interface SegmentIpv6 { gateway: string; hosts: Ipv6Hosts; servers: RelayServer[] }
export const setSegmentIpv6 = (site: string, vni: number, body: { enabled: boolean; hosts?: Ipv6Hosts; servers?: RelayServer[] }) =>
  put<{ vni: number; ipv6: { hosts: Ipv6Hosts; servers: RelayServer[] } | null }>("/segments/{site}/{vni}/ipv6", { path: { site, vni }, body });
export const setIpv6Pool = (site: string, prefix: string) =>
  put<{ prefix: string; relay: string }>("/fabrics/{site}/ipv6-pool", { path: { site }, body: { prefix } });

// DHCP relay of a routed segment: each server in the segment's own VPN instance, behind its
// firewall (`service`) or in a peered segment (`segment`, VNI).
export interface RelayServer { address: string; service?: string; segment?: number }
export interface Relay {
  servers: RelayServer[];
  loopback: number; // LoopBack<n> on every gateway leaf
  loopbacks?: Record<string, string>; // switch -> its relay address
}
export const setRelay = (site: string, vni: number, servers: RelayServer[]) =>
  put<{ vni: number; relay: Relay | null }>("/segments/{site}/{vni}/relay", { path: { site, vni }, body: { servers } });
export const setPipPool = (site: string, prefix: string) =>
  put<{ prefix: string; switches: string[] }>("/fabrics/{site}/pip-pool", { path: { site }, body: { prefix } });
export const setRelayPool = (site: string, prefix: string) =>
  put<{ prefix: string }>("/fabrics/{site}/relay-pool", { path: { site }, body: { prefix } });

export interface Service {
  name: string;
  device: string;
  port: string;
  vlan: number;
  address: string;
  next_hop: string;
  asn: number | null; // firewall ASN: eBGP in the service VPN instance; null = no session
  kind: "firewall" | "isp"; // isp: container only; its BGP session is a routing-intent peer
  // On an M-LAG pair's server trunk: both leaves, each leaf's main Vlanif address; `address`
  // is then the shared M-LAG address (on creation: the Vlanif subnet).
  devices?: string[];
  addresses?: Record<string, string> | null;
  // the Vlanif's IPv6 (a subnet on creation: the platform picks ::1) and the next hop.
  ipv6?: ServiceIpv6 | null;
  // Its VPN instance: its own (its name), another service's, or a routed
  // segment's (`<segment>-<bridge domain>`). On creation: omitted = its own.
  vrf?: string | null;
  rd_vlan?: number | null; // the instance's RD VLAN (that of the service that made it); none in a segment's
}

export interface ServiceIpv6 {
  address: string; // on an M-LAG pair: the shared M-LAG address
  next_hop: string;
  addresses?: Record<string, string>; // on an M-LAG pair: each leaf's own address
}

export const L3_VNI_OFFSET = 1_000_000; // L3 VNI = L2 VNI + 1,000,000 (generators/fabric.py)

export interface Port {
  name: string;
  trunk: boolean;
  mlag: boolean; // an M-LAG trunk (both leaves of the pair)
  segments: { vni: number; vid: number | null }[];
}

export const fetchSegments = (site: string) => get<Segment[]>("/segments", { query: { site } });
export const fetchNextBdId = (site: string) => get<{ bd_id: number }>("/segments/next-bd-id", { query: { site } });
export const fetchPorts = (device: string) => get<Port[]>("/devices/{name}/ports", { path: { name: device } });
export const createSegment = (site: string, name: string, vni: number, bd_id: number) =>
  post<Segment>("/segments", { body: { site, name, vni, bd_id } });
export const deleteSegment = (site: string, vni: number) => del<object>("/segments/{site}/{vni}", { path: { site, vni } });
export const renameSegment = (site: string, vni: number, name: string) =>
  patch<{ routed: boolean; vpn_instance?: string; replaces?: string; intent_updated: string[] }>("/segments/{site}/{vni}", { path: { site, vni }, body: { name } });
export const attachSegment = (site: string, vni: number, device: string, iface: string, vid: number | null) =>
  post<{ ports: string[] }>("/segments/{site}/{vni}/attachments", { path: { site, vni }, body: { device, interface: iface, vid } });
export const changeAttachment = (site: string, vni: number, device: string, iface: string, vid: number | null) =>
  patch<{ ports: string[] }>("/segments/{site}/{vni}/attachments", { path: { site, vni }, body: { device, interface: iface, vid } });
export const detachSegment = (site: string, vni: number, device: string, iface: string) =>
  del<{ ports: string[] }>("/segments/{site}/{vni}/attachments", { path: { site, vni }, query: { device, interface: iface } });
export const fetchServices = (site: string) => get<Service[]>("/services", { query: { site } });
export const createService = (site: string, service: Service) =>
  post<Service>("/services", { body: { site, ...service } });
export const setServiceAsn = (site: string, name: string, asn: number | null) =>
  patch<Service>("/services/{site}/{name}", { path: { site, name }, body: { asn } });
export const setServiceIpv6 = (site: string, name: string, ipv6: ServiceIpv6 | null) =>
  put<Service>("/services/{site}/{name}/ipv6", { path: { site, name }, body: ipv6 });
export const deleteService = (site: string, name: string) =>
  del<object>("/services/{site}/{name}", { path: { site, name } });
export const setRouting = (
  site: string,
  vni: number,
  body: { routed: boolean; prefix_length?: number; firewall?: string | null; peers?: number[] },
) => put<object>("/segments/{site}/{vni}/routing", { path: { site, vni }, body });

export type DriftState = "in_sync" | "out_of_sync" | "error" | "unknown";

export type DriftDevice = Answers["DriftDevice"] & { state: DriftState };

export type Drift = Answers["Drift"];

export const fetchDrift = () => get<Drift>("/drift");

export interface InterfaceTraffic {
  interface: string;
  oper_up: boolean | null;
  in_bps?: number; // last minute
  out_bps?: number;
  in_errors?: number; // last 5 minutes
  out_errors?: number;
  in_discards?: number;
  out_discards?: number;
}

export const fetchFaceplate = (device: string) =>
  get<{ device: string; model: string | null; ports: FacePort[] }>("/devices/{name}/faceplate", { path: { name: device } });
export const fetchInterfaces = (device: string) =>
  get<{ device: string; interfaces: InterfaceTraffic[] }>("/devices/{name}/interfaces", { path: { name: device } });

// -- routing intent -----------------------------------------------------------------

export const fetchRoutingDocument = (site: string) => get<RoutingDocument>("/routing", { query: { site } });
export const fetchRoutingTargets = (site: string) => get<RoutingTarget[]>("/routing/{site}/targets", { path: { site } });
export const fetchImports = (site: string) => get<ImportReport[]>("/routing/{site}/imports", { path: { site } });
export const saveRouting = (site: string, target: string, routing: DeviceRouting) =>
  put<RoutingDocument>("/routing/{site}/devices/{device}", { path: { site, device: target }, body: routing });
export const saveTarget = (site: string, target: string, routing: DeviceRouting, library: LibraryChange) =>
  put<RoutingDocument>("/routing/{site}/targets/{target}", { path: { site, target }, body: { routing, library } });
export const fetchLibraryUse = (site: string) =>
  get<{ used_by: Record<"prefix_lists" | "route_policies", Record<string, string[]>> }>("/routing/{site}/library", { path: { site } });
export const importNow = (site: string, device: string) =>
  post<ImportReport>("/routing/{site}/devices/{device}/import", { path: { site, device } });

// -- fabric architecture ------------------------------------------------------------

export interface FabricArchitecture {
  site: string;
  architecture: "symmetric" | "centralized";
  gateway_pair: string[] | null; // centralized: the M-LAG pair holding every gateway
  ntp: Ntp;
  netstream: NetStream;
  syslog: Syslog;
  relay_pool: string | null;     // the fabric's relay loopback pool
  ipv6_pool: string | null;      // the fabric's IPv6 pool
  settings: FabricSettings;      // read only (Fabric page); SNMP and gateway MAC editable
  deploys_allowed: boolean;
}
export interface FabricSettings {
  asn: number | null;
  pools: Partial<Record<"loopback" | "vtep" | "p2p" | "mlag_bypass" | "gateways" | "pip", string>>;
  bypass_vlan: number | null;
  gateway_mac: string | null;
  snmp_location: string | null;
  snmp_contact: string | null;
}
export interface Ntp {
  servers: string[]; // empty: the platform manages no NTP on this fabric
  vpn_instance: string | null; // e.g. _management_vpn_ when reached over MEth
}
export interface NetStream {
  enabled: boolean;
  host: string | null; // the collector, reached over MEth in _management_vpn_
  port: number | null;
}
export interface Syslog {
  enabled: boolean;
  host: string | null; // the receiver (the southbound VIP in HA), reached over MEth in _management_vpn_
  port: number | null;
}
export const saveSyslog = (site: string, s: Syslog) => put<Syslog>("/fabrics/{site}/syslog", { path: { site }, body: s });
export const fetchArchitecture = (site: string) => get<FabricArchitecture>("/fabrics/{site}", { path: { site } });
export const saveNetStream = (site: string, n: NetStream) => put<NetStream>("/fabrics/{site}/netstream", { path: { site }, body: n });
export const saveNtp = (site: string, ntp: Ntp) => put<Ntp>("/fabrics/{site}/ntp", { path: { site }, body: ntp });
// fabric settings set in the GUI, deploys allowed per fabric, deleting a fabric
export const saveSnmp = (site: string, location: string, contact: string) => put<object>("/fabrics/{site}/snmp", { path: { site }, body: { location, contact } });
export const saveGatewayMac = (site: string, mac: string) => put<{ mac: string }>("/fabrics/{site}/gateway-mac", { path: { site }, body: { mac } });
export const setDeploysAllowed = (site: string, allowed: boolean, confirm = "") =>
  put<{ deploys_allowed: boolean }>("/fabrics/{site}/deploys", { path: { site }, body: { allowed, confirm } });
export interface FabricDeletion {
  fabric: string; name: string; devices: string[]; active: string[]; cables: number; segments: number; services: number;
  prefixes: number; addresses: number; yaml: string | null; blocker: string | null;
}
export const fetchFabricDeletion = (site: string) => get<FabricDeletion>("/fabrics/{site}/deletion", { path: { site } });
export const deleteFabric = (site: string, confirm: string) => del<FabricDeletion>("/fabrics/{site}", { path: { site }, body: { confirm } });

export type Fabric = Answers["Fabric"];
export const fetchFabrics = () => get<Fabric[]>("/fabrics");

export const fetchOverview = (site?: string) => get<OverviewData>("/overview", { query: { site: site || undefined } });

// -- traffic on the map and per-port history -------------------------------------

export type FabricTraffic = Record<string, Record<string, { in_bps?: number; out_bps?: number }>>;
export const fetchTraffic = (site?: string) =>
  get<{ interfaces: FabricTraffic; telemetry: { ok: boolean; error: string | null } }>("/traffic", { query: { site: site || undefined } });

export interface HistoryPoint {
  t: number; // unix seconds
  in_bps: number | null;
  out_bps: number | null;
}
export const fetchHistory = (device: string, iface: string, minutes: number) =>
  get<{ device: string; interface: string; minutes: number; step: number; points: HistoryPoint[] }>("/devices/{name}/history", { path: { name: device }, query: { interface: iface, minutes } });

// -- Eth-Trunks from the GUI -----------------------------------------------------------

export type TrunkKind = "peer-link" | "m-lag" | "single";
export interface Trunk {
  name: string;
  number: number;
  members: string[];
  kind: TrunkKind;
  segments: number; // segments attached to it
  service: boolean; // a service's dedicated port
}
export interface DeviceTrunks {
  device: string;
  peer: string | null; // the M-LAG peer, if the device is in a pair
  trunks: Trunk[];
  free_ports: string[];
  pair_free_ports: string[] | null; // free on both devices of the pair
  next_number: number;
}
export const fetchTrunks = (device: string) => get<DeviceTrunks>("/devices/{name}/trunks", { path: { name: device } });
export const createTrunk = (device: string, body: { number: number; members: string[]; mlag: boolean }) =>
  post<unknown>("/devices/{name}/trunks", { path: { name: device }, body });
export const deleteTrunk = (device: string, trunk: string) =>
  del<unknown>("/devices/{name}/trunks/{trunk}", { path: { name: device, trunk } });

// -- location ----------------------------------------------------------------------------

export interface RackDevice {
  name: string;
  position: number | null;
  face: "front" | "rear" | null;
  u_height: number;
}
export interface Rack {
  name: string;
  u_height: number;
  devices: RackDevice[];
}
export interface FabricRacks {
  fabric: string;
  locations: { name: string; racks: Rack[] }[];
}
export interface Placement {
  location: string | null;
  rack: string | null;
  position: number | null;
  face: "front" | "rear" | null;
}
export const fetchRacks = (site: string) => get<FabricRacks>("/fabrics/{site}/racks", { path: { site } });
export const addRack = (site: string, body: { location: string; rack: string; u_height: number }) =>
  post<unknown>("/fabrics/{site}/racks", { path: { site }, body });
export const deleteRack = (site: string, location: string, rack: string) =>
  del<unknown>("/fabrics/{site}/racks", { path: { site }, query: { location, rack } });
export const placeDevice = (device: string, place: Placement) =>
  put<Placement>("/devices/{name}/location", { path: { name: device }, body: place });

// -- switch lifecycle --------------------------------------------------------------------

export const activateDevice = (device: string, body: { address: string; interface: string }) =>
  put<{ device: string; status: string; address: string; pinned: boolean }>("/devices/{name}/management", { path: { name: device }, body });
export const decommissionDevice = (device: string) =>
  del<{ device: string; status: string; freed: string[] }>("/devices/{name}/management", { path: { name: device } });

export interface BuildOptions {
  fabric: string;
  models: Record<string, string[]>; // known models and their front-panel ports
  devices: { name: string; role: string; free_ports: string[]; paired?: boolean; model?: string | null }[];
  bypass: boolean; // the fabric can take M-LAG pairs
  centralized?: boolean; // spine pairs are offered in a centralized fabric only
  gateway_pair?: string[];
  inventory?: InventorySwitch[]; // switches not yet in a fabric, to pick from
  fresh?: boolean; // a new fabric: a centralized one needs its gateway pair in this batch
}
export interface SpineLink { spine_port: string; leaf: string; leaf_port: string }
export interface Uplink { leaf: string; leaf_port: string; spine: string; spine_port: string }
export const fetchBuildOptions = (site: string) => get<BuildOptions>("/fabrics/{site}/build", { path: { site } });
export const addSpine = (site: string, body: { name: string; model: string; links: SpineLink[] }) =>
  post<{ devices: string[]; changes: string[] }>("/fabrics/{site}/spines", { path: { site }, body });
export const addLeafPair = (site: string, body: {
  leaves: string[]; model: string; role: string; peer_link_trunk: number; peer_link_members: string[]; uplinks: Uplink[];
}) => post<{ devices: string[]; changes: string[] }>("/fabrics/{site}/leaf-pairs", { path: { site }, body });

export interface KeyInfo { key: string; type: string; sha256: string; md5: string }
export interface HostKeyOffer {
  device: string;
  address: string;
  offered: Record<"22" | "830", KeyInfo[]>;
  pinned: Record<"22" | "830", KeyInfo[]>;
  unchanged: boolean;
  same_on_both_ports: boolean;
  unsupported: Record<string, string[]>;
}
export const fetchHostKeys = (device: string) => post<HostKeyOffer>("/devices/{name}/hostkeys/fetch", { path: { name: device } });
export const pinHostKeys = (device: string, keys: string[]) =>
  put<{ device: string; fingerprints: string[] }>("/devices/{name}/hostkeys", { path: { name: device }, body: { keys } });
export const forgetHostKeys = (device: string) => del<unknown>("/devices/{name}/hostkeys", { path: { name: device } });

// -- what a switch is, read from it -------------------------------------------------------------

export interface SwitchHardware {
  device: string;
  model: string | null;
  serial: string;
  reported: { model: string; version: string; patch: string } | null;   // the collector's last read
}
export interface SwitchReadResult {
  device: string; model: string; version: string; patch: string; serial: string; was_model: string | null;
  added: string[]; retyped: string[]; removed: string[]; blocked: string[]; skipped: string[]; changed: boolean;
}
export const fetchSwitchHardware = (device: string) => get<SwitchHardware>("/devices/{name}/inventory", { path: { name: device } });
export const readSwitch = (device: string) => post<SwitchReadResult>("/devices/{name}/inventory", { path: { name: device } });

export const fetchRemoval = (device: string) =>
  get<{ devices: string[]; links: string[]; unpair: string | null }>("/devices/{name}/removal", { path: { name: device } });
export const removeDevice = (device: string) =>
  del<{ devices: string[]; links: string[] }>("/devices/{name}", { path: { name: device } });

// -- zero-touch provisioning ----------------------------------------------------------

export interface ZtpRegistration {
  mac: string;
  address: string;
  gateway: string;
  interface: string;
  registered_at: number;
  offered?: Record<"22" | "830", string[]>; // host keys the switch reported, waiting for approval
  offered_keys?: Record<"22" | "830", KeyInfo[]>; // the same with fingerprints
  expires_at?: number; // end of its provisioning window (unix seconds): DHCP and TFTP stop serving it
  expired?: boolean; // past that window and not booted: register again to retry
  reported_at?: number;
}
export const fetchZtp = () => get<{ registrations: Record<string, ZtpRegistration>; configured: boolean }>("/ztp");
export const registerZtp = (device: string, body: { mac: string; address: string; gateway: string; interface?: string }) =>
  put<ZtpRegistration>("/devices/{name}/ztp", { path: { name: device }, body });
export const unregisterZtp = (device: string) => del<unknown>("/devices/{name}/ztp", { path: { name: device } });
export const fetchZtpFiles = (device: string) =>
  get<{ device: string; intermediate: string; config: string }>("/devices/{name}/ztp/files", { path: { name: device } });
export const approveZtp = (device: string, keys: string[]) =>
  post<{ device: string; address: string; fingerprints: string[] }>("/devices/{name}/ztp/approve", { path: { name: device }, body: { keys } });
export const addSwitches = (site: string, body: unknown) =>
  post<{ devices: string[]; active: string[]; changes: string[]; gateway_pair: string[] | null }>("/fabrics/{site}/switches", { path: { site }, body });

// -- new fabric and switch inventory ---------------------------------------------------

export interface InventorySwitch { serial: string; name: string; model: string | null; mac: string | null }
export const fetchInventory = () => get<InventorySwitch[]>("/inventory");
export const fetchModels = () => get<string[]>("/inventory/models");
export const addInventory = (body: { serial: string; model: string; mac: string | null }) =>
  post<InventorySwitch>("/inventory", { body });
export const updateInventory = (serial: string, body: { serial: string; model: string; mac: string | null }) =>
  put<InventorySwitch>("/inventory/{serial}", { path: { serial }, body });
export const removeInventory = (serial: string) => del<{ removed: string }>("/inventory/{serial}", { path: { serial } });

export type Pools = Record<"loopback" | "vtep" | "p2p" | "mlag_bypass" | "gateways" | "pip", string>;
export interface AddressPlan { prefixes: Pools; gateway_mac: string; bypass_vlan: number; models: Record<string, string[]> }
export const proposeAddressPlan = (supernet: string) => post<AddressPlan>("/fabrics/address-plan", { body: { supernet } });
export interface NewFabricSettings {
  name: string;
  asn: number;
  architecture: "symmetric" | "centralized";
  prefixes: Pools;
  bypass_vlan: number;
  gateway_mac: string;
  ntp_servers: string[];
  ntp_vpn_instance: string | null;
  snmp_location: string | null;
  snmp_contact: string | null;
}
export const createFabric = (settings: NewFabricSettings, switches: unknown) =>
  post<{ site: string; devices: string[]; active: string[]; gateway_pair: string[] | null }>("/fabrics", { body: { settings, switches } });

// -- route view: read from the switch when asked, nothing stored ------------------------

export interface RibRoute {
  vpn: string; prefix: string; protocol: string; process: number; preference: number; cost: number;
  next_hop: string; interface: string; active: boolean; state: string; flags: string; age: string; tag: number;
}
export interface BgpRoute {
  prefix: string; next_hop: string; status: string; valid: boolean; best: boolean; internal: boolean;
  med: number | null; local_preference: number | null; preferred_value: number | null; as_path: string; origin: string | null;
}
export interface BgpPeerIntent {
  vpn: string | null; address: string; asn: number;
  import_policy: string | null; export_policy: string | null; import_prefix_list: string | null; export_prefix_list: string | null;
}
export interface BgpPeerState {
  address: string; asn: number; received: number; sent: number; up_down: string; state: string; prefixes: number;
  intent?: BgpPeerIntent | null;
}
export interface Simulated { accepted: boolean; by: string[]; changes: { local_preference?: number } }
export interface BgpView { device: string; vpn: string | null; target: string | null; local_asn: number | null; peers: BgpPeerState[]; routes: BgpRoute[] }
export interface BgpSimulation {
  device: string; vpn: string | null; target: string | null; peer: BgpPeerState; intent: BgpPeerIntent | null;
  policies: Record<string, unknown[]>;
  export: (BgpRoute & { simulated: Simulated; advertised: boolean; ibgp_rule: boolean })[];
  import: (BgpRoute & { simulated: Simulated })[];
}
export const fetchRoutes = (device: string) => get<{ device: string; routes: RibRoute[] }>("/devices/{name}/routes", { path: { name: device } });
export const fetchBgp = (device: string, vpn: string | null) => get<BgpView>("/devices/{name}/bgp", { path: { name: device }, query: { vpn: vpn || undefined } });
export const fetchBgpSimulation = (device: string, vpn: string | null, peer: string) =>
  get<BgpSimulation>("/devices/{name}/bgp/simulation", { path: { name: device }, query: { peer, vpn: vpn || undefined } });

// -- import an existing fabric: sessions live in the orchestrator's memory -----------------

export interface ImportSwitch {
  address: string;
  offered: Record<string, KeyInfo[]>;
  same_on_both_ports: boolean;
  approved: boolean;
  approved_by: string | null;
  sysname: string | null;
  read: boolean;
  error: string | null;
  in_netbox: boolean;
  detected: DetectedSwitch | null; // New fabric and Add switches: what the switch reported over NETCONF
}
export interface DetectedSwitch { model: string; version: string; patch: string; serial: string; ports: Record<string, string> }
export interface ImportSession { id: string; switches: ImportSwitch[] }
export interface ImportChoices {
  name: string;
  pools: Partial<Pools>;
  gateway_mac: string | null;
  roles: Record<string, string>;
  architecture: "symmetric" | "centralized" | null;
  gateway_pair: string[] | null;
}
export interface ImportPlan {
  asn: number | null;
  architecture: string;
  gateway_pair: string[] | null;
  bypass_vlan: number | null;
  gateway_mac: string | null;
  devices: { name: string; model: string | null; role: string | null; management: string | null; planned: boolean; loopback: string | null; vtep: string | null }[];
  pairs: { devices: string[]; peer_link: string; peer_link_members: string[]; mlag_trunks: Record<string, string[]> }[];
  links: { spine: string; spine_port: string; leaf: string; leaf_port: string; network: string | null; found_by: string }[];
  segments: { vni: number; bd_id: number; name: string; gateway: string | null; firewall: string | null; peers: number[] }[];
  services: { name: string; devices: string[]; port: string; vlan: number; address: string; next_hop: string | null; asn: number | null; kind: string }[];
  attachments: { device: string; port: string; vni: number; vid: number | null }[];
  trunks: { device: string; name: string; members: string[] }[];
  pools: Partial<Pools>;
  overrides: Record<string, unknown>;
  misfits: Record<string, string[]>;
}
export interface ImportPreview { plan?: ImportPlan; patches?: Record<string, string>; entries?: OverrideEntry[]; error?: string }
export const startImport = (addresses: string[]) => post<ImportSession>("/imports", { body: { addresses } });
export const approveImportKeys = (id: string, address: string, keys: string[]) =>
  post<ImportSession>("/imports/{import_id}/approve", { path: { import_id: id }, body: { address, keys } });
export const detectImport = (id: string) => post<ImportSession>("/imports/{import_id}/detect", { path: { import_id: id } });
export const readImport = (id: string) => post<ImportSession>("/imports/{import_id}/read", { path: { import_id: id } });
export const previewImport = (id: string, choices: ImportChoices) =>
  post<ImportPreview>("/imports/{import_id}/preview", { path: { import_id: id }, body: choices });
export const writeImport = (id: string, choices: ImportChoices) =>
  post<{ site: string; devices: string[]; misfits: Record<string, string[]> }>("/imports/{import_id}/write", { path: { import_id: id }, body: choices });

// a leaf's role (leaf <-> border-leaf), its M-LAG peer with it
export const setDeviceRole = (name: string, role: "leaf" | "border-leaf") =>
  put<{ devices: string[]; role: string; changed: boolean }>("/devices/{name}/role", { path: { name }, body: { role } });

// -- config backups ----------------------------------------------------------------------
export interface ConfigVersion {
  id: number; device: string; site: string | null; taken_at: string; reason: "scheduled" | "deploy" | "manual";
  job_id: number | null; requested_by: string | null; lines: number;
}
export const fetchConfigVersions = (name: string) =>
  get<{ versions: ConfigVersion[]; problem: string | null; keep_days: number; keep_last: number }>("/devices/{name}/configs", { path: { name } });
export const fetchConfigVersion = (name: string, id: number) =>
  get<ConfigVersion & { config: string }>("/devices/{name}/configs/{version_id}", { path: { name, version_id: id } });
export const fetchConfigDiff = (name: string, older: number, newer: number) =>
  get<{ older: ConfigVersion; newer: ConfigVersion; diff: string }>("/devices/{name}/configs/{older}/diff/{newer}", { path: { name, older, newer } });
export const backUpConfig = (name: string) =>
  post<ConfigVersion & { new: boolean }>("/devices/{name}/configs", { path: { name } });

// -- rollback points ---------------------------------------------------------------------
export interface RollbackPoint {
  id: number; site: string; job_id: number | null; taken_at: string;
  segments: number; services: number; routing_targets: number; trunks: number;
}
export interface RollbackOperation { kind: string; text: string }
export interface RollbackPlan { fabric: string; snapshot: RollbackPoint; scope: "fabric" | string[]; operations: RollbackOperation[]; notes: string[] }
export const fetchRollbackPoints = (site: string) => get<RollbackPoint[]>("/fabrics/{site}/intent-snapshots", { path: { site } });
export interface RollbackResult extends RollbackPlan {
  done: string[]; failed: { text: string; error: string } | null; remaining: string[];
}
export const applyRollback = (site: string, id: number, expected: number, device?: string) =>
  post<RollbackResult>("/fabrics/{site}/intent-snapshots/{snapshot_id}/rollback", { path: { site, snapshot_id: id }, body: { device: device ?? null, expected } });
export const previewRollback = (site: string, id: number, device?: string) =>
  get<RollbackPlan>("/fabrics/{site}/intent-snapshots/{snapshot_id}/rollback", { path: { site, snapshot_id: id }, query: { device: device || undefined } });

// -- directory sign-in -------------------------------------------------------------------
export interface DirectoryView {
  settings: SettingRow[]; providers: ("ldap" | "radius")[]; defaults: Record<string, string>;
}
export interface GroupMapping { group: string; role: Role; fabrics: FabricLimits }
export interface DirectoryTestResult {
  outcome: "granted" | "unknown" | "refused" | "no-access" | "unavailable";
  source: "ldap" | "radius" | null; role: Role | null; fabrics: FabricLimits; detail: string; groups: string[];
}
export const fetchDirectory = () => get<DirectoryView>("/directory");
export const testDirectory = (username: string, password: string) =>
  post<DirectoryTestResult>("/directory/test", { body: { username, password } });

// -- control plane -----------------------------------------------------------------------
export interface BgpPeerRow {
  vpn: string; peer: string; family: string; state: string; remote_as: string; up: boolean; seconds: number | null;
  prefixes: { received?: number; active?: number; advertised?: number }; switch: string | null; expected_down: boolean;
}
export interface ControlPlane {
  device: string; collected: boolean; problems: string[]; refused: string[];
  bgp: BgpPeerRow[];
  ospf: { vpn: string; area: string; interface: string; neighbor: string; state: string; up: boolean; switch: string | null }[];
  mlag: { role: string; state: string; causation: string; heartbeat: boolean | null; partner: string | null;
    expected_down: boolean; peer_links: Record<string, boolean>; ports: Record<string, boolean> } | null;
  routes: Record<string, Record<string, { total?: number; active?: number }>>;
  tunnels: { source: string; destination: string; up: boolean; switch: string | null }[];
  macs: Record<string, number>;
}
export const fetchControlPlane = (name: string) => get<ControlPlane>("/devices/{name}/control-plane", { path: { name } });

// -- hardware ----------------------------------------------------------------------------
export type OpticLevel = "ok" | "warning" | "alarm" | "unused";
export interface OpticRow {
  port: string; vendor: string | null; type: string | null; part: string | null; serial: string | null;
  temperature: number | null; voltage: number | null;
  channels: { channel: string; rx_power?: number; tx_power?: number; bias?: number }[];
  thresholds: Record<string, Record<string, number>>;   // measure -> level (high_alarm, low_warn, ...) -> value
  in_use: boolean; level: OpticLevel; reasons: string[];
}
export interface Hardware {
  device: string; collected: boolean; problems: string[]; refused: string[];
  optics: OpticRow[];
  fans: { fan: string; present: boolean; speed: number | null; rpm: number | null }[];
  power: { module: string; ok: boolean; status: string | null; input: number | null; output: number | null }[];
  cpu: { slot: string; usage: number; threshold: number | null; ok: boolean }[];
  memory: { slot: string; usage: number; threshold: number | null; ok: boolean }[];
  sensors: { slot: string; sensor: string; celsius: number; normal: boolean | null; thresholds: Record<string, number>; ok: boolean }[];
}
export const fetchHardware = (name: string) => get<Hardware>("/devices/{name}/hardware", { path: { name } });

// -- health and timeline -------------------------------------------------------------------
export type HealthLevel = "good" | "warning" | "critical";
export interface Scored {
  kind: "switch" | "link" | "segment"; name: string; score: number; level: HealthLevel;
  reasons: { points: number; text: string }[];
}
export interface FabricHealth {
  fabric: string; score: number; level: HealthLevel; worst: { kind: string; name: string } | null;
  reasons: { points: number; text: string }[]; others_below_good: number;
  switches: Scored[]; links: Scored[]; segments: Scored[]; planned: string[];
}
export const fetchHealth = (site: string) => get<FabricHealth>("/fabrics/{site}/health", { path: { site } });

export interface TimelineEvent {
  at: string; site: string | null; device: string | null; kind: string; subject: string; state: string;
  previous: string | null; severity: "problem" | "recovered" | "change"; detail: string | null;
  user: string | null; job_id: number | null;
}
export const fetchEvents = (filters: { site?: string; device?: string; kind?: string; since?: string; limit?: number }) => {
  const query = new URLSearchParams(Object.entries(filters).filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => [k, String(v)]));
  return get<TimelineEvent[]>("/events", { query });
};

// -- capacity ------------------------------------------------------------------------------
export interface Forecast {
  status: "rising" | "not rising" | "over threshold" | "not enough data" | "no speed";
  days: number | null; earliest: number | null; latest: number | null;   // days from today; latest null: may never
  days_of_data: number; threshold_bps: number | null; slope_bps_per_day: number | null; today_bps: number | null;
}
export interface CapacityPort {
  device: string; interface: string; speed_bps: number | null;
  in_p95_bps: number | null; out_p95_bps: number | null; in_avg_bps: number | null; out_avg_bps: number | null;
  p95_percent: number | null; forecast: Forecast;
}
export interface FabricCapacity { site: string; days: number; threshold: number; ports: CapacityPort[] }
export interface PortCapacity extends CapacityPort {
  days: number; threshold: number;
  hours: { t: number; in_avg_bps?: number | null; in_p95_bps?: number | null; out_avg_bps?: number | null; out_p95_bps?: number | null }[];
  daily: { day: string; p95_bps: number }[];
}
export const fetchCapacity = (site: string, days: number) =>
  get<FabricCapacity>("/fabrics/{site}/capacity", { path: { site }, query: { days } });
export const fetchPortCapacity = (device: string, iface: string, days: number) =>
  get<PortCapacity>("/devices/{name}/capacity", { path: { name: device }, query: { interface: iface, days } });

// -- path trace ------------------------------------------------------------------------------
export type TraceResult = "delivered" | "gateway" | "firewall" | "isp" | "drop" | "unknown";
export interface TraceHop { kind: "host" | "bridge" | "vxlan" | "gateway" | "route" | "end" | "drop"; text: string; from: string; switch: string | null }
export interface TraceEnd {
  query: string; ip: string | null; mac: string | null; segment: string | null; service: string | null; gateway: boolean;
  switch: string | null; port: string | null; found: string[];
}
export interface Trace {
  fabric: string; architecture: string; source: TraceEnd; destination: TraceEnd; hops: TraceHop[]; result: TraceResult;
  switches: string[]; read: string[]; unread: string[];
}

// -- reachability -----------------------------------------------------------------------------
export interface ReachCheck {
  check: string; ok: boolean | null; text: string; from: string; switch: string | null; page: string | null; direction: "there" | "back";
}
type ReachPath = Pick<Trace, "source" | "destination" | "hops" | "result" | "switches">;
export interface Reach {
  fabric: string; architecture: string; verdict: "reaches" | "blocked" | "leaves" | "unknown"; sentence: string;
  stop: ReachCheck | null; there: ReachPath; back: ReachPath; checks: ReachCheck[]; switches: string[]; read: string[]; unread: string[];
}
export interface KnownHost { value: string; kind: "host" | "mac" | "gateway" | "service"; label: string }
export const fetchReach = (site: string, source: string, destination: string, proto?: string, port?: number) =>
  get<Reach>("/fabrics/{site}/reach", { path: { site }, query: { from: source, to: destination, proto: proto || undefined, port: port || undefined } });
export const fetchKnownHosts = (site: string) => get<{ hosts: KnownHost[]; unread: string[] }>("/fabrics/{site}/reach/hosts", { path: { site } });

// -- flows ---------------------------------------------------------------------------------
export type FlowKind = "edge" | "vxlan";
interface FlowSums { bytes: number; packets: number; records: number; bps: number }
export interface Flows {
  site: string; device: string | null; kind: FlowKind; minutes: number; total: FlowSums;
  talkers: (FlowSums & { address: string; segment: string })[];
  conversations: (FlowSums & { src: string; dst: string; src_segment: string; dst_segment: string; proto: number; dst_port: number })[];
  segments: (FlowSums & { src_segment: string; dst_segment: string })[];
  ports: (FlowSums & { switch: string; interface: string })[];
}
export const fetchFlows = (site: string, minutes: number, kind: FlowKind, device?: string) =>
  device ? get<Flows>("/devices/{name}/flows", { path: { name: device }, query: { minutes, kind } })
    : get<Flows>("/fabrics/{site}/flows", { path: { site }, query: { minutes, kind } });

// -- microsegmentation: EPGs and contracts of a fabric ------------------------------------
export const fetchMicroseg = (site: string) => get<Microseg>("/fabrics/{site}/microsegmentation", { path: { site } });
export const setMicrosegMode = (site: string, mode: MicrosegMode) => put<{ mode: MicrosegMode }>("/fabrics/{site}/microsegmentation/mode", { path: { site }, body: { mode } });
export interface GroupBody { segment: number; name: string; prefixes: string[]; macs: string[]; inside: "deny" | null }
export const createGroup = (site: string, body: GroupBody) => post<object>("/fabrics/{site}/microsegmentation/groups", { path: { site }, body });
export const updateGroup = (site: string, id: number, body: GroupBody) => put<object>("/fabrics/{site}/microsegmentation/groups/{epg}", { path: { site, epg: id }, body });
export const deleteGroup = (site: string, id: number) => del<object>("/fabrics/{site}/microsegmentation/groups/{epg}", { path: { site, epg: id } });
export const setEpgInside = (site: string, id: number, inside: "permit" | "deny") =>
  put<object>("/fabrics/{site}/microsegmentation/epgs/{epg}/inside", { path: { site, epg: id }, body: { inside } });
export const setContract = (site: string, from: number, to: number, services: GbpService[]) =>
  put<Contract>("/fabrics/{site}/microsegmentation/contracts", { path: { site }, body: { from, to, services } });
export const deleteContract = (site: string, from: number, to: number) => del<object>("/fabrics/{site}/microsegmentation/contracts/{source}/{destination}", { path: { site, source: from, destination: to } });
export const fetchSwitchMicroseg = (device: string) => get<SwitchState>("/devices/{name}/microsegmentation", { path: { name: device } });

// -- QoS: the queue plan and DSCP marks of a fabric ------------------------------------------
export const fetchQos = (site: string) => get<QosView>("/fabrics/{site}/qos", { path: { site } });
export const setQosEnabled = (site: string, enabled: boolean) => put<{ enabled: boolean }>("/fabrics/{site}/qos/enabled", { path: { site }, body: { enabled } });
export const setQosSegment = (site: string, vni: number, mark: QosMark | null) =>
  mark ? put<object>("/fabrics/{site}/qos/segments/{vni}", { path: { site, vni }, body: { mark } }) : del<object>("/fabrics/{site}/qos/segments/{vni}", { path: { site, vni } });
export const setQosPort = (site: string, device: string, port: string, mark: QosMark | null) =>
  put<object>("/fabrics/{site}/qos/ports", { path: { site }, body: { device, port, mark } });

// -- DC links: EVPN DCI between fabrics (approvers) -----------------------------------------
export const fetchDci = () => get<DciDocument>("/dci");
export const setDciLink = (name: string, sides: DciSide[], split_group: string) =>
  put<DciLink>("/dci/links/{name}", { path: { name }, body: { sides, split_group } });
export const deleteDciLink = (name: string) => del<object>("/dci/links/{name}", { path: { name } });
export const setDciSegment = (link: string, segment: string, vnis: Record<string, number>) =>   // DCI VNI picked by the platform
  put<DciSegment>("/dci/links/{name}/segments/{segment}", { path: { name: link, segment }, body: { vnis } });
export const deleteDciSegment = (link: string, segment: string) => del<object>("/dci/links/{name}/segments/{segment}", { path: { name: link, segment } });

// -- syslog --------------------------------------------------------------------------------
export interface LogMessage {
  time: string;          // received, UTC
  severity: number;      // 0 emergency ... 7 debugging
  severity_name: string;
  facility: number;
  tag: string;           // VRP's module/severity/brief, e.g. IFNET/4/LINK_STATE
  message: string;
  device?: string;       // the sending switch
}
export const fetchDeviceLogs = (name: string, filters: { severity?: number; text?: string; hours?: number }) => {
  const query = new URLSearchParams(Object.entries(filters).filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => [k, String(v)]));
  return get<LogMessage[]>("/devices/{name}/logs", { path: { name }, query });
};
export const fetchFabricLogs = (site: string, filters: { severity?: number; hours?: number; limit?: number }) => {   // NOC
  const query = new URLSearchParams(Object.entries(filters).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)]));
  return get<LogMessage[]>("/fabrics/{site}/logs", { path: { site }, query });
};

// -- port settings: name, on/off and frame size of server ports and trunks ------------------
export interface PortSetting {
  device: string; port: string; description: string; enabled: boolean; frame: number | null;
  speed: number | null;   // copper ports: fixed Mbit/s; null: negotiated (auto)
  copper: boolean; refusal: string | null; managed: boolean; carries: string[]; both: string[];
}
export interface PortAdoption {
  switches: string[]; ports: number;
  changes: { device: string; port: string; netbox: PortValues; switch: PortValues }[];
}
export interface PortValues { description: string; enabled: boolean; frame: number | null; speed: number | null }
export const fetchPortSettingsManaged = (site: string) => get<{ enabled: boolean }>("/fabrics/{site}/port-settings", { path: { site } });
export const fetchPortAdoption = (site: string) => get<PortAdoption>("/fabrics/{site}/port-settings/adoption", { path: { site } });
export const setPortSettingsManaged = (site: string, enabled: boolean) =>
  put<{ enabled: boolean; adopted: string[] }>("/fabrics/{site}/port-settings/enabled", { path: { site }, body: { enabled } });
// -- port protection: storm control, BPDU guard and loop detection on server ports ----------
export type ProtectionMode = "off" | "watch" | "on";
export type ProtectionTemplate = "server" | "hypervisor";
export interface ProtectionView {
  mode: ProtectionMode;
  ports: Record<string, Record<string, ProtectionTemplate>>;   // switch -> port or Eth-Trunk -> template
  plan: { storm: Record<string, number>; templates: Record<ProtectionTemplate, { edge: boolean }>; recovery: number };
  tripped: ProtectionTrip[];   // what protection does now (the collector's last 25 s control poll)
}
export interface ProtectionTrip {
  device: string; port: string; cause: string; state: string; detail: string; reopen: number | null; text: string;
}
export const fetchProtection = (site: string) => get<ProtectionView>("/fabrics/{site}/protection", { path: { site } });
export const setProtectionMode = (site: string, mode: ProtectionMode) =>
  put<{ mode: ProtectionMode }>("/fabrics/{site}/protection/mode", { path: { site }, body: { mode } });
export const setPortProtection = (device: string, port: string, template: ProtectionTemplate | null) =>
  put<{ switches: string[] }>("/devices/{name}/protection/{port}", { path: { name: device, port }, body: { template } });
export const fetchPortSetting = (device: string, port: string) => get<PortSetting>("/devices/{name}/port-settings/{port}", { path: { name: device, port } });
export const savePortSetting = (device: string, port: string, change: Partial<Pick<PortSetting, "description" | "enabled" | "frame" | "speed">>) =>
  put<PortSetting & { switches: string[] }>("/devices/{name}/port-settings/{port}", { path: { name: device, port }, body: change });

// -- monitoring access: v2c, SNMPv3 readers and trap targets; passwords write-only --------
export interface SnmpReader { auth: string; priv: string }
export interface SnmpTrap { address: string; reader: string }
export interface SnmpAccess {
  v2c: boolean;
  readers: Record<string, SnmpReader>;
  traps: Record<string, SnmpTrap>;
  passwords?: string[];   // readers whose passwords are stored (never the passwords)
  algorithms?: { auth: string[]; priv: string[] };
}
export interface SnmpSwitchState { device: string; readers?: Record<string, "ok" | "missing" | "no-passwords" | "different">; error?: string }
export type OspfAuth = { enabled: boolean; keys: number[]; send: number | null; stage: "off" | "on" | "added" | "moved" };
export type OverrideEntry = {
  path: string; key: string; scope: string; target: string | null; area: string; text: string;
  derived: string; standard: boolean; value: unknown;
};
export type FabricOverrides = { imported: boolean; entries: OverrideEntry[]; misfits: Record<string, string[]> };
export const fetchOverrides = (site: string) => get<FabricOverrides>("/fabrics/{site}/overrides", { path: { site } });
export const previewOverride = (site: string, path: string) =>
  post<{ patches: Record<string, string> }>("/fabrics/{site}/overrides/preview", { path: { site }, body: { path } });
export const makeStandard = (site: string, path: string) =>
  del<{ path: string }>("/fabrics/{site}/overrides/{path}", { path: { site, path } });
export type SwitchLogin = { enabled: boolean; domain: string; servers: string[]; key_set: boolean; accounts: { name: string; level: number }[] };
export const fetchSwitchLogin = (site: string) => get<SwitchLogin>("/fabrics/{site}/switch-login", { path: { site } });
export const setSwitchLogin = (site: string, body: { enabled: boolean; domain: string; servers: string[]; key?: string }) =>
  put<SwitchLogin>("/fabrics/{site}/switch-login", { path: { site }, body });
export const addSwitchAccount = (site: string, name: string, level: number, password: string) =>
  put<SwitchLogin>("/fabrics/{site}/switch-accounts/{name}", { path: { site, name }, body: { level, password } });
export const removeSwitchAccount = (site: string, name: string) => del<SwitchLogin>("/fabrics/{site}/switch-accounts/{name}", { path: { site, name } });
export const fetchOspfAuth = (site: string) => get<OspfAuth>("/fabrics/{site}/ospf-auth", { path: { site } });
export const setOspfAuth = (site: string, enabled: boolean) => put<OspfAuth>("/fabrics/{site}/ospf-auth", { path: { site }, body: { enabled } });
export const rollOspfKey = (site: string, action: "add" | "send" | "retire") => post<OspfAuth>("/fabrics/{site}/ospf-auth/roll", { path: { site }, body: { action } });
export const fetchSnmpState = (site: string) => get<SnmpSwitchState[]>("/fabrics/{site}/snmp/state", { path: { site } });
export const fetchSnmpAccess = (site: string) => get<SnmpAccess>("/fabrics/{site}/snmp", { path: { site } });
export const setSnmpV2c = (site: string, on: boolean) => put<SnmpAccess>("/fabrics/{site}/snmp/v2c", { path: { site }, body: { on } });
export const setSnmpReader = (site: string, name: string, r: SnmpReader) =>
  put<SnmpAccess>("/fabrics/{site}/snmp/readers/{name}", { path: { site, name }, body: r });
export const deleteSnmpReader = (site: string, name: string) => del<SnmpAccess>("/fabrics/{site}/snmp/readers/{name}", { path: { site, name } });
export const setSnmpPasswords = (site: string, name: string, auth: string, priv: string) =>
  put<{ reader: string; stored: boolean }>("/fabrics/{site}/snmp/readers/{name}/passwords", { path: { site, name }, body: { auth, priv } });
export const setSnmpTrap = (site: string, name: string, t: SnmpTrap) =>
  put<SnmpAccess>("/fabrics/{site}/snmp/traps/{name}", { path: { site, name }, body: t });
export const deleteSnmpTrap = (site: string, name: string) => del<SnmpAccess>("/fabrics/{site}/snmp/traps/{name}", { path: { site, name } });
export const copySnmpAccess = (site: string, source: string) => post<SnmpAccess>("/fabrics/{site}/snmp/copy", { path: { site }, body: { source } });
export const fetchPasswordJob = (id: number) => get<DeployJob>("/snmp-password-jobs/{job_id}", { path: { job_id: id } });
export const fetchPasswordJobs = () => get<DeployJob[]>("/snmp-password-jobs");
export const requestPasswordJob = (site: string, readers: string[]) => post<{ id: number }>("/snmp-password-jobs", { body: { site, readers } });
export const approvePasswordJob = (id: number) => post<{ id: number }>("/snmp-password-jobs/{job_id}/approve", { path: { job_id: id } });
