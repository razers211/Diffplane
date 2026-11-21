import type { Route } from "./nav";

// "How does this page work?": one short walk-through per page, a step per
// part of the page. A step whose part is not on screen (empty fabric, viewer role...) is skipped.

export interface TourStep { selector: string; text: string }

export const TOURS: Partial<Record<Route["page"], TourStep[]>> = {
  overview: [
    { selector: ".strip", text: "The fabric at a glance: its health score (click it for the reasons), switches, how many match their intent and are read, alerts and approvals." },
    { selector: ".panel.topology", text: "The live fabric. Lines are links; in Traffic view dots run along them, faster when busier. Click a switch or a line for its details." },
    { selector: "[aria-label='Needs attention']", text: "What needs you now: a switch that differs, a deploy to approve, a switch not polled. Each card has its button." },
    { selector: "[aria-label='Deploy history']", text: "The last deploys. Every deploy, with its patches and log, is in Changes › History." },
  ],
  health: [
    { selector: "[data-tour='fabric-score']", text: "The fabric's score is its worst switch's or link's. Every problem takes points off: 40 for the worst, 20 or 10 for the rest; the reasons say which." },
    { selector: "[data-tour='health-switches']", text: "Each switch's score and why. Planned switches are not scored, and a partner that is planned never counts against anyone." },
    { selector: "[data-tour='health-links']", text: "Links between built switches: an end down, the wrong neighbour seen over LLDP, or errors rising." },
    { selector: "[data-tour='health-segments']", text: "Segments: their ports and their firewall's port, and whether their switches are polled." },
  ],
  timeline: [
    { selector: "[data-tour='timeline-filters']", text: "Narrow it to a switch, a kind of change or a time span." },
    { selector: "[data-tour='timeline']", text: "What changed, newest first: links, peers, hardware, alerts and health, read every 30 s, with deploys and intent changes. Red went wrong, green came back." },
  ],
  capacity: [
    { selector: "[data-tour='capacity-ports']", text: "Each port's and trunk's busiest hour as a share of its speed; the mark is 70 %. The last column says when its 4-week trend reaches 70 %." },
    { selector: "[data-tour='capacity-port']", text: "The port you clicked: its busiest hour per day with the trend line, and its traffic over weeks (hourly averages, kept 13 months)." },
  ],
  trace: [
    { selector: "[data-tour='trace-form']", text: "Pick two hosts, or a switch as rack1-leaf1/web (its gateway in that segment), and what traffic you mean. The platform reads the switches now; nothing is changed." },
    { selector: "[data-tour='reach-answer']", text: "The first place traffic stops and why, with a link to the page that fixes it." },
    { selector: ".panel.topology", text: "The path on the map, numbered in order; red where it stops." },
  ],
  dci: [
    { selector: "[data-tour='dci-links']", text: "A DC link joins this fabric's border pair to another DC's: uplinks with eBGP, then EVPN between the borders." },
    { selector: "[data-tour='dci-segments']", text: "Stretch a segment both fabrics have by name. It waits until the link is deployed and verified on its borders." },
  ],
  qos: [
    { selector: "[data-tour='qos-mode']", text: "Off or On for the whole fabric. On puts the queue plan on every port of every switch at the next deploy." },
    { selector: "[data-tour='qos-plan']", text: "The queues every port gets: network control and voice first, then storage, best effort and scavenger by weight." },
    { selector: "[data-tour='qos-segments']", text: "Drag a segment into the storage or scavenger lane: everything entering it gets that DSCP, so every switch queues it the same way." },
    { selector: "[data-tour='qos-ports']", text: "Mark everything entering one port or trunk instead, whatever segment it is in." },
  ],
  security: [
    { selector: "[data-tour='security-mode']", text: "Off, Learn or Enforce. Learn puts the groups on the switches but still allows everything; Enforce allows only what contracts say." },
    { selector: "[data-tour='security-groups']", text: "Every routed segment is a group. Add a named group for part of a segment: some of its addresses, or hosts by MAC." },
    { selector: ".group-map", text: "Drag a group onto the one it may reach, then pick its services. Each arrow is a contract; click it to change it." },
    { selector: "[data-tour='security-matrix']", text: "The same contracts as a table: rows reach columns. The reply traffic is always allowed with them." },
    { selector: "[data-tour='security-counters']", text: "Pick a switch and read its counters now: packets each contract matched and dropped there." },
  ],
  noc: [
    { selector: "[data-tour='noc-health']", text: "The fabric's health: its worst switch or link sets the score, with the reasons." },
    { selector: "[data-tour='noc-alerts']", text: "Alerts firing now, critical first." },
    { selector: "[data-tour='noc-traffic']", text: "Traffic entering the fabric at its edge ports, sampled every 5 s while the page is open." },
    { selector: "[data-tour='noc-logs']", text: "Warnings and errors the switches sent by syslog, newest first (when syslog is on for the fabric)." },
    { selector: "[data-tour='noc-wall']", text: "Wall mode: full screen, larger type, the fabrics in turn every 15 s, no click needed. Its address is #/overview/noc/wall." },
  ],
  flows: [
    { selector: "[data-tour='flows-filters']", text: "Edge ports are the totals: traffic entering the fabric. Uplinks show overlay traffic again where it crosses leaves." },
    { selector: "[data-tour='flows-talkers']", text: "The addresses sending the most, with their segment (from the gateway subnets)." },
    { selector: "[data-tour='flows-conversations']", text: "Who talks to whom on which service. Sampled 1 in 1024: small or short flows may not show." },
  ],
  network: [
    { selector: "[data-tour='segments']", text: "Segments are the networks servers live in. Click a segment to see its ports, attach more, route it or rename it." },
    { selector: "[data-tour='new-segment']", text: "New segment walks you through name, ports and routing; the numbers are proposed for you." },
    { selector: "[data-tour='ipv6']", text: "IPv6 of a routed segment: its /64 and ::1 gateway, and how hosts get addresses (SLAAC, DHCPv6 or none)." },
    { selector: "[data-tour='relay']", text: "DHCP relay of a routed segment: its servers, and where each sits (its own VPN, behind its firewall, a peered segment)." },
    { selector: "[data-tour='exits']", text: "Drag a segment onto a firewall to send its default route there, or onto another segment to let them reach each other." },
    { selector: "[data-tour='services']", text: "Services are firewalls and ISP links: a dedicated port, its own VLAN and VPN instance." },
  ],
  protection: [
    { selector: "[data-tour='protection-mode']", text: "Off writes nothing, Watch only logs and alerts, On also drops storms and shuts a looping port for a short pause." },
    { selector: "[data-tour='protection-tripped']", text: "Ports protection is acting on right now: a storm being dropped, or a port shut by a loop or a BPDU, with when it reopens." },
    { selector: "[data-tour='protection-templates']", text: "Drag a template onto a port or trunk (or tap it, then the port). Server is for one machine, Hypervisor for a host with virtual switches." },
    { selector: "[data-tour='protection-panel']", text: "Coloured ports are protected. Uplinks, the peer-link and firewall or ISP ports cannot be picked: they are never protected." },
    { selector: "[data-tour='protection-lines']", text: "What the switch gets. Nothing reaches it until you review and deploy; Expert view shows the exact lines." },
  ],
  ports: [
    { selector: "[data-tour='ports-palette']", text: "Your segments. Drag one onto a port or a trunk (or tap it, then tap the port), then say tagged or untagged." },
    { selector: "[data-tour='ports-switch']", text: "Pick a switch: an M-LAG pair shows both. A segment on an M-LAG trunk is written on both leaves." },
    { selector: "[data-tour='ports-managed']", text: "Port names and on/off. The first time, the platform reads the switches and takes over what they have, so nothing changes by itself." },
    { selector: ".switch-ports .faceplate", text: "Click free ports to bundle them into a new Eth-Trunk; click a used port to see its segments and detach one. A dashed, faded port is turned off." },
    { selector: "[data-tour='port-settings']", text: "The clicked port's name and On/Off. Turning off a port that carries segments asks first. Copper ports also pick a speed (Auto by default). Expert holds the frame size." },
    { selector: "[data-tour='ports-saved']", text: "What you saved here. Nothing reaches a switch until you review and deploy it." },
  ],
  switch: [
    { selector: ".switch-head", text: "The switch: whether it is active or planned, whether it matches its intent, its role and management address." },
    { selector: "nav[aria-label='Switch']", text: "Daily things first: ports and traffic, trunks, the live routing table, then its config and diff, and one-time settings last." },
    { selector: ".faceplate", text: "The front panel: colours say what each port carries; a green light means the link is up. Click a port to see what it is." },
    { selector: ".panel.trunks", text: "Eth-Trunks bundle ports into one link. New trunk lets you click the member ports on the front panel." },
    { selector: ".routes .instance-chips", text: "Read live from the switch: pick a routing table (an instance) to see its routes." },
    { selector: "table.rib", text: "Each route: where it goes, how it was learned (the coloured chip), the next hop, and whether it is in use." },
    { selector: "[data-tour='latest-diff']", text: "The last comparison of this switch with its intent. Run diff checks again now." },
    { selector: "[data-tour='port-optics']", text: "The optical modules of ports in use: received light on the module's own scale (red: past its alarm level). The corner mark on the front panel says the same." },
    { selector: "[data-tour='hardware']", text: "Power, fans, CPU, memory, temperatures and every optical module, read every 50 s against the switch's own thresholds. Modules on ports not in use are grey and never alarm." },
    { selector: "[data-tour='flows-talkers']", text: "Flows through this switch's own ports: who sends the most and to whom." },
    { selector: "[data-tour='logs']", text: "Messages this switch sent: warnings and worse, newest first, kept 90 days. Filter by severity or text." },
    { selector: ".timeline-page", text: "What changed on this switch: links, peers, hardware, alerts and its health score, newest first." },
    { selector: "[data-tour='control-plane']", text: "BGP and EVPN peers, OSPF neighbours, M-LAG, VXLAN tunnels and table sizes, read every 30 s. Grey: its partner is planned, so down is expected." },
    { selector: "[data-tour='config-history']", text: "The switch's running config as kept after each deploy, every day and on request. Pick two versions to see what changed between them." },
    { selector: "[data-tour='rollback-points']", text: "The intent as it was at each verified deploy. Preview what going back to one would change, for this switch or the whole fabric." },
    { selector: "[data-tour='generated']", text: "The full config the platform would give this switch, generated from intent. Nothing is read from the switch here." },
    { selector: "[data-tour='management']", text: "Make the switch active with its management address, pin its host keys, or take it out of service." },
    { selector: "[data-tour='role']", text: "A leaf pair can become border leaves (routing only) and back; both switches change together." },
    { selector: "[data-tour='hardware-read']", text: "What the switch says it is: its model and software. Its ports, their speed and copper or fibre, are read from it too, so new models need no setup." },
    { selector: "[data-tour='location']", text: "Where the switch is: room, rack and height." },
  ],
  "network-routing": [
    { selector: "[data-tour='routing-targets']", text: "Advanced routing (BGP peers, policies, static routes...) is set per switch, or per M-LAG pair. Open one to edit it." },
    { selector: "[data-tour='routing-import']", text: "Routing someone configured by hand on a switch is adopted here automatically, never removed." },
  ],
  "routing-target": [
    { selector: "[data-tour='routing-target']", text: "The routing of this switch or pair. Both leaves of a pair get the same." },
    { selector: "[data-tour='routing-view']", text: "Simple shows the routing in plain words; Expert view shows every object with its routing terms. Both edit the same settings." },
    { selector: "[data-tour='routing-conns']", text: "Each connection to another router says what we accept from it and what we send it. Change picks a plain choice; the platform writes the rules for you." },
    { selector: "[data-tour='route-flow']", text: "The route flow: each router's routes come in from the left through a filter and leave to the right through another. Click a filter to read its rules; drag a route-policy or prefix list onto one to use it." },
    { selector: "[data-tour='routing-section']", text: "Each section is one kind of object. Add a row, save, then deploy as for any change." },
  ],
  switches: [
    { selector: "[data-tour='switch-filters']", text: "Find a switch by name, address or rack, or by role." },
    { selector: "[data-tour='switch-list']", text: "Every switch of the fabric. Click one to open it: ports, trunks, routing table, config, settings." },
    { selector: "[data-tour='add-switches']", text: "Add switches on the map: place them, cable them, then add them in one go." },
  ],
  inventory: [
    { selector: "[data-tour='inventory-add']", text: "Switches you have in stock but not yet in a fabric: their serial, model and management MAC." },
    { selector: "[data-tour='inventory-list']", text: "Pick one later when adding switches: it takes its serial and MAC into the fabric." },
  ],
  software: [
    { selector: "[data-tour='software-images']", text: "Huawei's software files kept by the platform, each with the SHA-256 it computed. Admins upload and delete them." },
    { selector: "[data-tour='software-upload']", text: "Upload under Huawei's own file name. Type the SHA-256 from Huawei's site too, and a damaged download is refused." },
    { selector: "[data-tour='software-request']", text: "Choose the release and the switches. Planning only reads them; nothing changes before approval." },
    { selector: "[data-tour='upgrade-list']", text: "Every upgrade of this fabric. Click one to read its plans, approve it, or follow it." },
  ],
  upgrade: [
    { selector: "[aria-label='Rollout']", text: "The switches of this upgrade, in order. Each one reboots only after the previous one is verified." },
    { selector: ".approval", text: "Read each plan, tick the box, then approve. The switches reboot one at a time." },
    { selector: "[data-tour='upgrade-download']", text: "The file a switch is fetching now, as far as the platform has sent it. The switch itself says nothing until the end." },
    { selector: "[data-tour='upgrade-plans']", text: "What each switch will do: red files are deleted, green ones copied and set to boot, then the reboot." },
  ],
  history: [
    { selector: "[data-tour='deploy-list']", text: "Every deploy: who asked, who approved, which switches changed. Click one for its patches and log." },
    { selector: "[data-tour='check-list']", text: "Every comparison of the switches with intent, by hand or the regular one every 15 minutes." },
  ],
  audit: [
    { selector: "[data-tour='audit']", text: "Who did what: every change, deploy request, approval and sign-in, newest first. Filter by person or action." },
  ],
  deploy: [
    { selector: "[aria-label='Rollout']", text: "The switches of this deploy, in the order they are sent, each with its state." },
    { selector: "[aria-label='Fabric map']", text: "The same on the map: amber while a switch changes, green once verified, red if it failed." },
    { selector: ".approval", text: "Read every patch below, tick the box, then approve. Only approvers see this." },
    { selector: "[data-tour='patches']", text: "The exact commands each switch will receive: green lines are added, red ones removed." },
  ],
  check: [
    { selector: ".patch-card", text: "Each switch compared with its intent: what differs, line by line. An empty one matches." },
  ],
  "new-fabric": [
    { selector: "[data-tour='new-fabric-settings']", text: "The new fabric's name, ASN and architecture. The rest is proposed for you." },
    { selector: "[data-tour='new-fabric-pools']", text: "Where its addresses come from, proposed from one private block; change any of them." },
    { selector: ".build-map", text: "Its first switches: place them on the map and cable them." },
    { selector: "[data-tour='detect-switches']", text: "Switches that already answer: approve their host keys, then their model and ports are read from them, so any model is built with the ports it really has." },
  ],
  welcome: [
    { selector: "[data-tour='welcome-account']", text: "Done: you are the platform's first admin. Add your team's accounts when you are ready." },
    { selector: "[data-tour='welcome-credentials']", text: "The logins the platform uses on the switches. Without them it can design fabrics but not read or change switches." },
    { selector: "[data-tour='welcome-fabric']", text: "Start a fabric: design a new one on a map, or import a running one." },
  ],
  settings: [
    { selector: "[data-tour='settings-switch-access']", text: "The logins and keys the platform uses on the switches. Passwords are never shown again: replace them when needed." },
    { selector: "[data-tour='settings-zero-touch-provisioning']", text: "How new switches get their first config: the network the platform answers on, and the admin account it creates." },
    { selector: "[data-tour='settings-platform']", text: "How often switches are compared with intent, how long a sign-in lasts, the certificate's names." },
    { selector: "[data-tour='settings-fixed']", text: "What connects the platform to its own databases: only in deploy/.env, on purpose." },
  ],
  directory: [
    { selector: "[data-tour='directory-ad']", text: "Where Active Directory is and how the platform searches it. LDAPS only, checked against your CA certificate." },
    { selector: "[data-tour='directory-groups']", text: "Which AD groups give which role and fabrics. Someone in no mapped group cannot sign in." },
    { selector: "[data-tour='directory-radius']", text: "RADIUS, asked when AD does not know the user. Its answer carries the role and fabrics." },
    { selector: "[data-tour='directory-test']", text: "Try a user's sign-in: you see the role and fabrics they would get, and AD's groups for the mapping." },
  ],
  users: [
    { selector: "[data-tour='users-new']", text: "Create an account: a name, a first password, and a role. Each role can do what the ones before it can." },
    { selector: "[data-tour='users-list']", text: "Change someone's role here, set a new password (it signs them out), or delete an account. There is always at least one admin." },
  ],
  "import-fabric": [
    { selector: "[data-tour='import-switches']", text: "Give the management addresses of the running switches. An approver checks each one's host key before anything is read." },
    { selector: "[data-tour='import-preview']", text: "What the platform would make of them, and what it would change on each: ideally nothing." },
  ],
  changes: [
    { selector: ".change-steps", text: "Where your change is on its way to the switches: saved, patch to review, approval, deploy and verify." },
    { selector: "[aria-label='Intent changes']", text: "What was saved since the last deploy, by whom and when." },
    { selector: "[aria-label='Switches that differ']", text: "Each switch whose config would change. Open its diff to see exactly what." },
    { selector: "[data-tour='checks']", text: "Checks for problems a diff cannot show (routing loops, overlapping subnets, unreachable services). Errors block approval; warnings are for the approver to weigh." },
    { selector: ".actions-row", text: "Prepare the deploy: the platform computes each switch's patch for an approver to read." },
    { selector: "[data-tour='window-picker']", text: "Approve now, or for a change window: pick a day and the hours. The fabric's usual window is marked green and chosen first." },
    { selector: "[aria-label='Scheduled deploys']", text: "Deploys approved for a window. Open one for its countdown, or to cancel it before it starts." },
    { selector: "[data-tour='change-sets']", text: "Changes for a window: a draft collects your saves without changing anything now. Prepare its deploy, then approve it for a window." },
  ],
  "add-switches": [
    { selector: "[data-tour='detect-switches']", text: "Switches that already answer: approve their host keys, then their model and ports are read from them, so any model is built with the ports it really has." },
    { selector: ".bm-tiles", text: "Click or drag what you want to add. New switches get proposed names." },
    { selector: ".build-canvas", text: "New switches appear dashed (planned) and cabled to every spine or leaf. Drag one switch onto another, or click both, for one more cable." },
    { selector: ".bm-stock", text: "Switches in stock: drag one onto a new box and it becomes that switch, with its serial and MAC." },
    { selector: ".build-map + .panel", text: "The details of each new switch: names, model, management address if it is already reachable. Everything is checked before anything is written." },
  ],
  fabric: [
    { selector: "[aria-label='Settings']", text: "How this fabric was set up: architecture, ASN, gateway MAC. They are fixed once the fabric exists." },
    { selector: "[aria-label='Address pools']", text: "Where the fabric's addresses come from; NetBox hands them out one by one." },
    { selector: "[aria-label='NTP']", text: "The time servers every switch uses. Change them here, then deploy." },
    { selector: "[aria-label='DHCP relay']", text: "Where relay loopbacks get their addresses: one /32 per gateway leaf and relaying segment. Set it before turning relay on." },
    { selector: "[aria-label='Syslog']", text: "Switch messages for each switch's Logs tab: off by default. On, switches send warnings and worse over MEth to the receiver." },
    { selector: "[aria-label='Monitoring access']", text: "Who may read the switches over SNMP: the platform's community (v2c) and v3 readers, each a login with two passwords, plus where traps go." },
    { selector: "[aria-label='Found on import']", text: "What an imported fabric keeps as it was found. Make it standard shows the change first; a deploy applies it." },
    { selector: "[aria-label='Switch login']", text: "Lets people log in to switches with their company account, as name@domain. Local accounts stay; each switch takes the change back by itself if the platform cannot log in again." },
    { selector: "[aria-label='Link authentication']", text: "Makes switches prove themselves on fabric links with a secret key the platform generates. It goes on before links come up; afterwards keys are changed in three deploys." },
    { selector: "[data-tour='snmp-readers']", text: "Readers are created by a deploy; their passwords are then typed on the switches by a password job you approve." },
    { selector: "[aria-label='NetStream']", text: "Flow samples for the flow pages: off by default. On, switches send 1 packet in 1024 of their edge ports and uplinks over MEth." },
    { selector: "[data-tour='usual-window']", text: "The fabric's usual change window, offered first when a deploy is approved for later. Approvers and admins set it." },
    { selector: "[aria-label='Deploys']", text: "Whether approved deploys may be sent to this fabric's switches. Admins turn it on by typing the fabric's name." },
    { selector: "[aria-label='Delete this fabric']", text: "Removes the fabric from the platform once every switch is planned. Nothing is sent to the switches." },
  ],
};

/** The steps of a tour whose part is on screen now. */
export const visibleSteps = (steps: TourStep[], present: (selector: string) => boolean) => steps.filter((s) => present(s.selector));
