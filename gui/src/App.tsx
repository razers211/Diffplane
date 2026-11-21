import { TourButton } from "./Tour";
import { useEffect, useState } from "react";
import { fetchFabrics, fetchMe, fetchSetup, logout, UNAUTHORIZED, type Me } from "./api";
import { useFetch } from "./useFetch";
import { BrandMark, ICONS } from "./icons";
import { allows, FabricsContext, RoleContext } from "./role";
import { MyPassword, Users } from "./Users";
import { FirstSetup, Welcome } from "./FirstSetup";
import { Settings } from "./Settings";
import { Directory } from "./Directory";
import { AddSwitches } from "./AddSwitches";
import { ImportFabric } from "./ImportFabric";
import { Inventory } from "./Inventory";
import { NewFabric } from "./NewFabric";
import { SwitchPage } from "./DeviceConfig";
import { DeviceList } from "./DeviceList";
import { Audit, DeployJobDetail, DeployJobs } from "./Deploys";
import { Login } from "./Login";
import { RoutingTargetPage, RoutingTargets } from "./RoutingIntent";
import { Segments } from "./Segments";
import { DiffJobDetail, DiffJobs } from "./Diffs";
import { ChangesPending, PendingBar, usePending } from "./Changes";
import { DraftBar, useDraft } from "./Draft";
import { Overview } from "./Overview";
import { Noc } from "./Noc";
import { CapacityPage } from "./Capacity";
import { FlowsView } from "./Flows";
import { HealthPage } from "./Health";
import { PathTrace } from "./PathTrace";
import { Dci } from "./Dci";
import { Qos } from "./Qos";
import { Protection } from "./Protection";
import { Security } from "./Security";
import { PortsBoard } from "./Ports";
import { Timeline } from "./Timeline";
import { Fabric } from "./Fabric";
import { Software, UpgradeJobDetail, UpgradeJobs } from "./Software";
import { MENU, parse, redirect, section, type Route, type Section } from "./nav";

// Five menu entries, each with its own pages: see nav.ts.

function useRoute(): Route {
  const current = () => {
    const moved = redirect(window.location.hash);
    if (moved) window.location.replace(moved);   // an address from before
    return parse(moved ?? window.location.hash);
  };
  const [route, setRoute] = useState(current);
  useEffect(() => {
    const onChange = () => setRoute(current());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

function useSite(): [string, (site: string) => void] {
  const [site, setSite] = useState(() => {
    try {
      return localStorage.getItem("site") ?? "";
    } catch {
      return "";
    }
  });
  const update = (value: string) => {
    setSite(value);
    try {
      localStorage.setItem("site", value);
    } catch {
      // per-viewer convenience only
    }
  };
  return [site, update];
}

function useUser(): [Me | null | undefined, (user: Me | null) => void] {
  // undefined: still checking the session; null: signed out
  const [user, setUser] = useState<Me | null | undefined>(undefined);
  useEffect(() => {
    fetchMe()
      .then((me) => setUser(me))
      .catch(() => setUser(null));
    const onUnauthorized = () => setUser(null);
    window.addEventListener(UNAUTHORIZED, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED, onUnauthorized);
  }, []);
  return [user, setUser];
}

const MENU_ICONS: Record<Section, keyof typeof ICONS> = {
  overview: "overview", network: "segments", switches: "devices", changes: "deploys", fabric: "fabric", admin: "admin",
};

// The tabs under a menu entry, and which pages each one covers.
const SUBTABS: Partial<Record<Section, { label: string; href: string; pages: Route["page"][] }[]>> = {
  overview: [
    { label: "Summary", href: "#/overview", pages: ["overview"] },
    { label: "Health", href: "#/overview/health", pages: ["health"] },
    { label: "Timeline", href: "#/overview/timeline", pages: ["timeline"] },
    { label: "Capacity", href: "#/overview/capacity", pages: ["capacity"] },
    { label: "Flows", href: "#/overview/flows", pages: ["flows"] },
    { label: "NOC", href: "#/overview/noc", pages: ["noc"] },
  ],
  network: [
    { label: "Segments", href: "#/network", pages: ["network"] },
    { label: "Ports", href: "#/network/ports", pages: ["ports"] },
    { label: "Routing", href: "#/network/routing", pages: ["network-routing", "routing-target"] },
    { label: "Reachability", href: "#/network/trace", pages: ["trace"] },
    { label: "Security groups", href: "#/network/security", pages: ["security"] },
    { label: "QoS", href: "#/network/qos", pages: ["qos"] },
    { label: "Port protection", href: "#/network/protection", pages: ["protection"] },
  ],
  switches: [
    { label: "All switches", href: "#/switches", pages: ["switches", "add-switches"] },
    { label: "Inventory", href: "#/switches/inventory", pages: ["inventory"] },
    { label: "Software", href: "#/switches/software", pages: ["software"] },
  ],
  admin: [
    { label: "Accounts", href: "#/admin/users", pages: ["users"] },
    { label: "Settings", href: "#/admin/settings", pages: ["settings"] },
    { label: "Directory", href: "#/admin/directory", pages: ["directory"] },
  ],
  fabric: [
    { label: "Settings", href: "#/fabric", pages: ["fabric", "new-fabric", "import-fabric"] },
    { label: "DC links", href: "#/fabric/dci", pages: ["dci"] },
  ],
  changes: [
    { label: "Pending", href: "#/changes", pages: ["changes"] },
    { label: "History", href: "#/changes/history", pages: ["history", "deploy", "check", "upgrade"] },
    { label: "Audit", href: "#/changes/audit", pages: ["audit"] },
  ],
};

function title(route: Route): string {
  switch (route.page) {
    case "switch": return `Switches / ${route.name}`;
    case "health": return "Overview / Health";
    case "timeline": return "Overview / Timeline";
    case "capacity": return "Overview / Capacity";
    case "flows": return "Overview / Flows";
    case "noc": return "Overview / NOC";
    case "add-switches": return "Switches / Add switches";
    case "inventory": return "Switches / Inventory";
    case "software": return "Switches / Software";
    case "upgrade": return `Changes / Upgrade #${route.id}`;
    case "network-routing": return "Network / Routing";
    case "trace": return "Network / Reachability";
    case "ports": return "Network / Ports";
    case "security": return "Network / Security groups";
    case "qos": return "Network / QoS";
    case "protection": return "Network / Port protection";
    case "routing-target": return `Network / Routing / ${route.target.split("+").join(" + ")}`;
    case "history": return "Changes / History";
    case "audit": return "Changes / Audit";
    case "deploy": return `Changes / Deploy #${route.id}`;
    case "check": return `Changes / Check #${route.id}`;
    case "users": return "Admin / Accounts";
    case "welcome": return "Welcome";
    case "settings": return "Admin / Settings";
    case "directory": return "Admin / Directory";
    case "new-fabric": return "Fabric / New fabric";
    case "dci": return "Fabric / DC links";
    case "import-fabric": return "Fabric / Import fabric";
    default: return MENU.find((m) => m.section === section(route))?.label ?? "";
  }
}

/** The open section's pages, as tabs under the top bar. */
function PageTabs({ route }: { route: Route }) {
  const tabs = SUBTABS[section(route)] ?? [];
  if (!tabs.length) return null;
  return (
    <nav className="page-tabs" aria-label="Section">
      {tabs.map((t) => (
        <a key={t.href} href={t.href} className={t.pages.includes(route.page) ? "active" : ""}
           aria-current={t.pages.includes(route.page) ? "page" : undefined}>{t.label}</a>
      ))}
      {route.page === "switch" && <a className="active sub" aria-current="page" href={`#/switches/${encodeURIComponent(route.name)}`}>{route.name}</a>}
    </nav>
  );
}

function FabricPicker({ site, onChange, round, limited }: { site: string; onChange: (site: string) => void; round: number; limited: boolean }) {
  const fabrics = useFetch(fetchFabrics, [round]);
  const list = fabrics.data ?? [];
  const current = list.find((f) => f.site === site);
  // A limited account works in one of its fabrics at a time: no "All fabrics".
  useEffect(() => {
    if (limited && fabrics.data && fabrics.data.length && !current) onChange(fabrics.data[0]!.site);
  }, [limited, fabrics.data, current, onChange]);
  return (
    <div className="fabric-picker"
         title={current ? (current.architecture === "centralized" ? "Centralized · gateway pair" : "Symmetric · gateways on leaves") : `${list.length} fabric${list.length === 1 ? "" : "s"}`}>
      {ICONS.fabric}
      <label className="sr-only" htmlFor="fabric-select">Fabric</label>
      <select id="fabric-select" value={site} onChange={(e) => onChange(e.target.value)}>
        {!limited && <option value="">All fabrics</option>}
        {list.map((f) => (
          <option key={f.site} value={f.site}>
            {f.name}
          </option>
        ))}
        {site && !current && !limited && <option value={site}>{site}</option>}
      </select>
    </div>
  );
}

const initials = (name: string) =>
  name
    .split(/[\s._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]!.toUpperCase())
    .join("") || "?";

/** The login page, or the first-time setup while the platform has no account. */
function SignIn({ onLogin }: { onLogin: (me: Me) => void }) {
  const setup = useFetch(fetchSetup, []);
  if (!setup.data && !setup.error) return null;
  return setup.data?.needed ? <FirstSetup onDone={onLogin} /> : <Login onLogin={onLogin} />;
}

export function App() {
  const route = useRoute();
  const [site, setSite] = useSite();
  const [user, setUser] = useUser();
  const [fabricsRound, setFabricsRound] = useState(0);   // reload the fabric list after creating one
  const here = section(route);
  const [menuOpen, setMenuOpen] = useState(false);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const p = usePending(site, !!user, !!user && allows(user.role, "operator"));
  const draft = useDraft();   // draft mode: every page reads the draft; remounted when it changes
  const created = (newSite: string) => {
    setSite(newSite);
    setFabricsRound(fabricsRound + 1);
    window.location.hash = "#/overview";
  };

  if (user === undefined) return null;
  if (user === null) return <SignIn onLogin={setUser} />;

  const signOut = async () => {
    try {
      await logout();
    } finally {
      setUser(null);
    }
  };

  // Wall mode: the NOC alone, full screen, no navigation.
  if (route.page === "noc" && route.wall) {
    return (
      <RoleContext.Provider value={user.role}>
        <FabricsContext.Provider value={user.fabrics ?? null}>
          <Noc site={site} wall />
        </FabricsContext.Provider>
      </RoleContext.Provider>
    );
  }

  return (
    <RoleContext.Provider value={user.role}>
    <FabricsContext.Provider value={user.fabrics ?? null}>
      <div className="shell" data-zone={here}>
        <header className="topnav">
          <div className="topnav-row">
            <a className="brand" href="#/overview"><span className="mark"><BrandMark /></span><span>Diffplane</span></a>
            <nav className="sections" aria-label="Main">
              {MENU.filter((m) => m.section !== "admin" || allows(user.role, "admin")).map((m) => (
                <a key={m.section} href={m.href} data-zone={m.section} className={here === m.section ? "active" : ""} aria-current={here === m.section ? "true" : undefined}>
                  {ICONS[MENU_ICONS[m.section]]}
                  <span>{m.label}</span>
                  {m.section === "changes" && (p.state?.outOfSync.length ?? 0) > 0 && (
                    <span className="nav-badge" title="switches that differ from intent">{p.state!.outOfSync.length}</span>
                  )}
                </a>
              ))}
            </nav>
            <div className="topnav-end">
              <FabricPicker site={site} onChange={setSite} round={fabricsRound} limited={!!user.fabrics} />
              <ThemeToggle />
              <div className="user">
                <button className="avatar" onClick={() => setMenuOpen(!menuOpen)} aria-expanded={menuOpen}
                        title={`${user.username} (${user.role})`}>{initials(user.username)}</button>
                {menuOpen && (
                  <div className="user-pop">
                    <div className="who"><b>{user.username}</b><small>{user.role}</small></div>
                    {passwordOpen
                      ? <MyPassword onDone={() => { setPasswordOpen(false); setUser(null); }} />
                      : <button onClick={() => setPasswordOpen(true)}>Change password</button>}
                    <button onClick={signOut}>Sign out</button>
                  </div>
                )}
              </div>
            </div>
          </div>
          <PageTabs route={route} />
        </header>
        <div className="workspace">
          <div className="page-head">
            <span className="crumbs">
              <b>{title(route)}</b>
              <span className="fabric-tag"><small>Fabric</small>{site || "All fabrics"}</span>
            </span>
            <TourButton route={route} />
          </div>
          {draft && <DraftBar draft={draft} />}
          {route.page !== "changes" && <PendingBar p={p} />}
          <main key={draft ? `draft-${draft.id}` : "live"}>
            {route.page === "overview" && <Overview site={site} />}
            {route.page === "health" && <HealthPage site={site} />}
            {route.page === "timeline" && <Timeline site={site} />}
            {route.page === "capacity" && <CapacityPage site={site} />}
            {route.page === "flows" && <FlowsView site={site} />}
            {route.page === "noc" && <Noc site={site} wall={false} />}
            {route.page === "users" && <Users me={user.username} />}
            {route.page === "welcome" && <Welcome />}
            {route.page === "settings" && <Settings />}
            {route.page === "directory" && <Directory />}
            {route.page === "network" && <Segments site={site} />}
            {route.page === "trace" && <PathTrace site={site} />}
            {route.page === "ports" && <PortsBoard site={site} />}
            {route.page === "security" && <Security site={site} />}
            {route.page === "qos" && <Qos site={site} />}
            {route.page === "protection" && <Protection site={site} />}
            {route.page === "network-routing" && <RoutingTargets site={site} />}
            {route.page === "routing-target" && <RoutingTargetPage key={route.target} site={site} target={route.target} />}
            {route.page === "switches" && <DeviceList site={site} />}
            {route.page === "inventory" && <Inventory />}
            {route.page === "software" && <Software site={site} />}
            {route.page === "add-switches" && <AddSwitches site={site} />}
            {route.page === "switch" && <SwitchPage key={route.name} name={route.name} tab={route.tab} />}
            {route.page === "changes" && <ChangesPending site={site} p={p} />}
            {route.page === "history" && <><DeployJobs site={site} /><UpgradeJobs site={site} /><DiffJobs site={site} /></>}
            {route.page === "audit" && <Audit />}
            {route.page === "deploy" && <DeployJobDetail id={route.id} />}
            {route.page === "check" && <DiffJobDetail id={route.id} />}
            {route.page === "upgrade" && <UpgradeJobDetail id={route.id} />}
            {route.page === "fabric" && <Fabric site={site} />}
            {route.page === "dci" && <Dci site={site} />}
            {route.page === "new-fabric" && <NewFabric onCreated={created} />}
            {route.page === "import-fabric" && <ImportFabric onCreated={created} />}
          </main>
        </div>
      </div>
    </FabricsContext.Provider>
    </RoleContext.Provider>
  );
}

/** Light or dark, remembered in this browser; until chosen, the system's setting (see main.tsx). */
function ThemeToggle() {
  const [dark, setDark] = useState(() =>
    (document.documentElement.dataset.theme ?? (window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light")) === "dark");
  const flip = () => {
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("theme", next); } catch { /* storage blocked: the choice lasts this page only */ }
    setDark(!dark);
  };
  const label = dark ? "Switch to light mode" : "Switch to dark mode";
  return <button className="avatar theme-toggle" onClick={flip} aria-label={label} title={label}>{dark ? ICONS.sun : ICONS.moon}</button>;
}
