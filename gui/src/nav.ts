// Hash routes: five menu entries, each with its own pages. The orchestrator
// only serves static files, so every page is a hash. Addresses of older versions redirect.
//
//   #/overview                #/overview/health           #/overview/timeline     #/overview/capacity   #/overview/flows   #/overview/noc[/wall]
//   #/network                 #/network/routing           #/network/routing/<device or pair>   #/network/trace   #/network/security   #/network/qos   #/network/ports   #/network/protection
//   #/switches                #/switches/inventory        #/switches/add        #/switches/software
//   #/switches/<name>[/<tab>] tabs: ports (default), trunks, routing, control, hardware, flows, logs, history, config, settings
//   #/changes                 #/changes/history           #/changes/audit
//   #/changes/deploys/<id>    #/changes/checks/<id>       #/changes/upgrades/<id>
//   #/fabric                  #/fabric/new                #/fabric/import       #/fabric/dci
//   #/admin/users              #/admin/settings            (admins)   #/admin/directory

export const SWITCH_TABS = ["ports", "trunks", "routing", "control", "hardware", "flows", "logs", "history", "config", "settings"] as const;
export type SwitchTab = (typeof SWITCH_TABS)[number];

export type Route =
  | { page: "overview" }
  | { page: "health" }
  | { page: "timeline" }
  | { page: "capacity" }
  | { page: "flows" }
  | { page: "noc"; wall: boolean }
  | { page: "network" }
  | { page: "network-routing" }
  | { page: "trace" }
  | { page: "ports" }
  | { page: "security" }
  | { page: "qos" }
  | { page: "protection" }
  | { page: "routing-target"; target: string }
  | { page: "switches" }
  | { page: "inventory" }
  | { page: "add-switches" }
  | { page: "software" }
  | { page: "switch"; name: string; tab: SwitchTab }
  | { page: "changes" }
  | { page: "history" }
  | { page: "audit" }
  | { page: "deploy"; id: number }
  | { page: "check"; id: number }
  | { page: "upgrade"; id: number }
  | { page: "fabric" }
  | { page: "new-fabric" }
  | { page: "import-fabric" }
  | { page: "dci" }
  | { page: "users" }
  | { page: "settings" }
  | { page: "directory" }
  | { page: "welcome" };

export type Section = "overview" | "network" | "switches" | "changes" | "fabric" | "admin";

export const MENU: { section: Section; label: string; href: string }[] = [
  { section: "overview", label: "Overview", href: "#/overview" },
  { section: "network", label: "Network", href: "#/network" },
  { section: "switches", label: "Switches", href: "#/switches" },
  { section: "changes", label: "Changes", href: "#/changes" },
  { section: "fabric", label: "Fabric", href: "#/fabric" },
  { section: "admin", label: "Admin", href: "#/admin/users" },   // admins only
];

const parts = (hash: string) => hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
const enc = encodeURIComponent;
const id = (text: string | undefined) => (text && /^\d+$/.test(text) ? Number(text) : null);

/** The page of a hash (anything unknown is the overview: the GUI opens there). */
export function parse(hash: string): Route {
  const [a, b, c] = parts(hash);
  if (a === "overview" && b === "health") return { page: "health" };
  if (a === "overview" && b === "timeline") return { page: "timeline" };
  if (a === "overview" && b === "capacity") return { page: "capacity" };
  if (a === "overview" && b === "flows") return { page: "flows" };
  if (a === "overview" && b === "noc") return { page: "noc", wall: c === "wall" };
  if (a === "network") {
    if (b === "routing" && c) return { page: "routing-target", target: c };
    if (b === "trace") return { page: "trace" };
    if (b === "ports") return { page: "ports" };
    if (b === "security") return { page: "security" };
    if (b === "qos") return { page: "qos" };
    if (b === "protection") return { page: "protection" };
    return b === "routing" ? { page: "network-routing" } : { page: "network" };
  }
  if (a === "switches") {
    if (b === "inventory") return { page: "inventory" };
    if (b === "add") return { page: "add-switches" };
    if (b === "software") return { page: "software" };
    if (b) return { page: "switch", name: b, tab: (SWITCH_TABS as readonly string[]).includes(c ?? "") ? (c as SwitchTab) : "ports" };
    return { page: "switches" };
  }
  if (a === "changes") {
    if (b === "history") return { page: "history" };
    if (b === "audit") return { page: "audit" };
    if (b === "deploys" && id(c) !== null) return { page: "deploy", id: id(c)! };
    if (b === "checks" && id(c) !== null) return { page: "check", id: id(c)! };
    if (b === "upgrades" && id(c) !== null) return { page: "upgrade", id: id(c)! };
    return { page: "changes" };
  }
  if (a === "admin") return b === "settings" ? { page: "settings" } : b === "directory" ? { page: "directory" } : { page: "users" };
  if (a === "welcome") return { page: "welcome" };
  if (a === "fabric") {
    if (b === "new") return { page: "new-fabric" };
    if (b === "import") return { page: "import-fabric" };
    if (b === "dci") return { page: "dci" };
    return { page: "fabric" };
  }
  return { page: "overview" };
}

/** Where an address of an older version now lives, or null if it is already current. */
export function redirect(hash: string): string | null {
  const [a, b] = parts(hash);
  switch (a) {
    case "topology": return "#/overview";
    case "segments": return "#/network";
    case "routing": return b ? `#/network/routing/${enc(b)}` : "#/network/routing";
    case "devices": return b ? `#/switches/${enc(b)}` : "#/switches";
    case "add-switches": return "#/switches/add";
    case "inventory": return "#/switches/inventory";
    case "routes": return b ? `#/switches/${enc(b)}/routing` : "#/switches";
    case "drift": return "#/changes";
    case "deploys": return id(b) !== null ? `#/changes/deploys/${b}` : "#/changes/history";
    case "diffs": return id(b) !== null ? `#/changes/checks/${b}` : "#/changes/history";
    case "audit": return "#/changes/audit";
    case "new-fabric": return "#/fabric/new";
    case "import-fabric": return "#/fabric/import";
    default: return null;
  }
}

/** The menu entry a page belongs to. */
export function section(route: Route): Section {
  switch (route.page) {
    case "network": case "network-routing": case "routing-target": case "trace": case "ports": case "security": case "qos": case "protection": return "network";
    case "switches": case "inventory": case "add-switches": case "switch": case "software": return "switches";
    case "changes": case "history": case "audit": case "deploy": case "check": case "upgrade": return "changes";
    case "fabric": case "new-fabric": case "import-fabric": case "dci": return "fabric";
    case "users": case "settings": case "directory": return "admin";
    default: return "overview";
  }
}

/** Links, so pages never spell addresses themselves. */
export const href = {
  switchPage: (name: string, tab: SwitchTab = "ports") => `#/switches/${enc(name)}${tab === "ports" ? "" : `/${tab}`}`,
  deploy: (job: number) => `#/changes/deploys/${job}`,
  check: (job: number) => `#/changes/checks/${job}`,
  upgrade: (job: number) => `#/changes/upgrades/${job}`,
  routingTarget: (target: string) => `#/network/routing/${enc(target)}`,
};
