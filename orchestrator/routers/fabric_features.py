"""OSPF authentication, switch login, NetStream, syslog, SNMP and SNMP password jobs."""

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from orchestrator import deploys, segments as segs
from orchestrator import ospf_auth, snmp_access, snmp_passwords, switch_login
from orchestrator.access import Access
from orchestrator import fabric_ntp
from orchestrator.auth import Accounts
from orchestrator.netbox import NetBoxClient, NetBoxError
from orchestrator.deps import (
    current_user, _deploy_approval, get_access, get_accounts, get_deploys, get_netbox, get_password_jobs,
    INTENT_WRITE, LOGIN, OPERATE, _segment_call,
)

router = APIRouter()


class NetStreamSettings(BaseModel):
    enabled: bool
    host: str | None = None   # the collector's IPv4 address, reached over MEth in _management_vpn_
    port: int | None = None   # UDP, 2055 by default


class OspfAuthSetting(BaseModel):
    enabled: bool


class OspfAuthRoll(BaseModel):
    action: str   # add, send or retire


@router.get("/fabrics/{site}/ospf-auth", dependencies=[LOGIN])
async def get_fabric_ospf_auth(site: str, netbox: NetBoxClient = Depends(get_netbox)) -> dict:
    """On or off, the key IDs, which one is sent, and where a roll stands; never a key."""
    try:
        return await ospf_auth.get(netbox, site)
    except segs.SegmentError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.put("/fabrics/{site}/ospf-auth", dependencies=INTENT_WRITE)
async def set_fabric_ospf_auth(site: str, body: OspfAuthSetting, user: str = Depends(current_user),
                               netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts),
                               jobs: snmp_passwords.PasswordJobs = Depends(get_password_jobs)) -> dict:
    return await _segment_call(ospf_auth.set_enabled(netbox, accounts.sessions, jobs.verifier, site, body.enabled, user),
                               accounts, user, "fabric.ospf_auth", f"{site} enabled={body.enabled}")


@router.post("/fabrics/{site}/ospf-auth/roll", dependencies=INTENT_WRITE)
async def roll_fabric_ospf_key(site: str, body: OspfAuthRoll, user: str = Depends(current_user),
                               netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts),
                               jobs: snmp_passwords.PasswordJobs = Depends(get_password_jobs)) -> dict:
    return await _segment_call(ospf_auth.roll(netbox, accounts.sessions, jobs.reader, site, body.action, user),
                               accounts, user, "fabric.ospf_auth.roll", f"{site} {body.action}")


class SwitchLoginSetting(BaseModel):
    enabled: bool
    domain: str = switch_login.DEFAULT_DOMAIN
    servers: list[str] = []
    key: str | None = None   # write-only; needed the first time


@router.get("/fabrics/{site}/switch-login", dependencies=[LOGIN])
async def get_fabric_switch_login(site: str, netbox: NetBoxClient = Depends(get_netbox)) -> dict:
    """On or off, the domain, the servers and whether a key is set; never the key."""
    try:
        return await switch_login.get(netbox, site)
    except segs.SegmentError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.put("/fabrics/{site}/switch-login", dependencies=INTENT_WRITE)
async def set_fabric_switch_login(site: str, body: SwitchLoginSetting, user: str = Depends(current_user),
                                  netbox: NetBoxClient = Depends(get_netbox),
                                  accounts: Accounts = Depends(get_accounts)) -> dict:
    return await _segment_call(switch_login.set_login(netbox, accounts.sessions, site, body.enabled, body.domain,
                                                      body.servers, body.key, user),
                               accounts, user, "fabric.switch_login", f"{site} enabled={body.enabled}")


class SwitchAccount(BaseModel):
    level: int
    password: str   # the initial one: VRP makes its user change it at first login


@router.put("/fabrics/{site}/switch-accounts/{name}", dependencies=INTENT_WRITE)
async def add_switch_account(site: str, name: str, body: SwitchAccount, user: str = Depends(current_user),
                             netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts),
                             jobs: deploys.DeployJobs = Depends(get_deploys)) -> dict:
    return await _segment_call(switch_login.add_account(netbox, accounts.sessions, jobs.annet, site, name, body.level,
                                                        body.password, user),
                               accounts, user, "fabric.local_account", f"{site} {name} level={body.level}")


@router.delete("/fabrics/{site}/switch-accounts/{name}", dependencies=INTENT_WRITE)
async def remove_switch_account(site: str, name: str, user: str = Depends(current_user),
                                netbox: NetBoxClient = Depends(get_netbox),
                                accounts: Accounts = Depends(get_accounts)) -> dict:
    return await _segment_call(switch_login.remove_account(netbox, accounts.sessions, site, name, user),
                               accounts, user, "fabric.local_account.delete", f"{site} {name}")


@router.put("/fabrics/{site}/netstream", dependencies=INTENT_WRITE)
async def set_fabric_netstream(site: str, body: NetStreamSettings, user: str = Depends(current_user),
                               netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    """Sampled NetStream for every switch of the fabric: intent, deployed as usual."""
    return await _segment_call(fabric_ntp.set_netstream(netbox, site, body.enabled, body.host, body.port), accounts, user,
                               "fabric.netstream", f"{site} enabled={body.enabled} host={body.host} port={body.port}")


class SyslogSettings(BaseModel):
    enabled: bool
    host: str | None = None   # the receiver's IPv4 address (the southbound VIP in HA), over MEth in _management_vpn_
    port: int | None = None   # UDP, 514 by default


@router.put("/fabrics/{site}/syslog", dependencies=INTENT_WRITE)
async def set_fabric_syslog(site: str, body: SyslogSettings, user: str = Depends(current_user),
                            netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    """Syslog from every switch of the fabric to the platform: intent, deployed as usual."""
    result = await _segment_call(fabric_ntp.set_syslog(netbox, site, body.enabled, body.host, body.port), accounts, user,
                                 "fabric.syslog", f"{site} enabled={body.enabled} host={body.host} port={body.port}")
    return {k: result[k] for k in ("enabled", "host", "port")}


# -- SNMP readers: v2c and/or v3 readers, trap targets, passwords ----------

class SnmpReader(BaseModel):
    auth: str = snmp_access.AUTH[0]   # authentication algorithm (SHA2-256 by default)
    priv: str = snmp_access.PRIV[0]   # encryption algorithm (AES256 by default)


class SnmpTrap(BaseModel):
    address: str   # the monitoring system's IPv4 address, over MEth in _management_vpn_
    reader: str    # sent as this reader (v3, authentication and encryption)


class SnmpPasswords(BaseModel):
    auth: str
    priv: str


@router.get("/fabrics/{site}/snmp", dependencies=[LOGIN])
async def fabric_snmp(site: str, netbox: NetBoxClient = Depends(get_netbox),
                      accounts: Accounts = Depends(get_accounts)) -> dict:
    """Who may read the fabric's switches: v2c on or off, v3 readers (and whether their passwords are
    stored: never the passwords), trap targets."""
    try:
        current = await snmp_access.get(netbox, site)
    except segs.SegmentError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    stored = set(await snmp_passwords.known(accounts.sessions, site))
    return {**snmp_access.view(current), "passwords": sorted(stored & set(current["readers"])),
            "algorithms": {"auth": list(snmp_access.AUTH), "priv": list(snmp_access.PRIV)}}


@router.put("/fabrics/{site}/snmp/v2c", dependencies=INTENT_WRITE)
async def set_fabric_snmp_v2c(site: str, on: bool = Body(embed=True), user: str = Depends(current_user),
                              netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    """v2c (the platform's community) on or off on every switch of the fabric: intent, deployed as usual."""
    return snmp_access.view(await _segment_call(snmp_access.set_v2c(netbox, site, on), accounts, user,
                                                "fabric.snmp.v2c", f"{site} on={on}"))


@router.put("/fabrics/{site}/snmp/readers/{name}", dependencies=INTENT_WRITE)
async def set_fabric_snmp_reader(site: str, name: str, body: SnmpReader, user: str = Depends(current_user),
                                 netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    """A v3 reader (a USM user of group FABRIC-RO): intent; its passwords are typed by a password job."""
    return snmp_access.view(await _segment_call(snmp_access.set_reader(netbox, site, name, body.auth, body.priv), accounts,
                                                user, "fabric.snmp.reader", f"{site} {name} auth={body.auth} priv={body.priv}"))


@router.delete("/fabrics/{site}/snmp/readers/{name}", dependencies=INTENT_WRITE)
async def delete_fabric_snmp_reader(site: str, name: str, user: str = Depends(current_user),
                                    netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    result = await _segment_call(snmp_access.delete_reader(netbox, site, name), accounts, user,
                                 "fabric.snmp.reader.delete", f"{site} {name}")
    await snmp_passwords.forget(accounts.sessions, site, name, user)
    return snmp_access.view(result)


@router.put("/fabrics/{site}/snmp/readers/{name}/passwords", dependencies=[LOGIN, OPERATE])
async def set_fabric_snmp_passwords(site: str, name: str, body: SnmpPasswords, user: str = Depends(current_user),
                                    netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    """Store a reader's passwords (write-only; typed on the switches by the next password job)."""
    try:
        readers = (await snmp_access.get(netbox, site))["readers"]
        await snmp_passwords.set_passwords(accounts.sessions, site, readers, name, body.auth, body.priv, user)
    except (segs.SegmentError, deploys.DeployError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"reader": name, "stored": True}


@router.put("/fabrics/{site}/snmp/traps/{name}", dependencies=INTENT_WRITE)
async def set_fabric_snmp_trap(site: str, name: str, body: SnmpTrap, user: str = Depends(current_user),
                               netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    return snmp_access.view(await _segment_call(snmp_access.set_trap(netbox, site, name, body.address, body.reader), accounts,
                                                user, "fabric.snmp.trap", f"{site} {name} {body.address} as {body.reader}"))


@router.delete("/fabrics/{site}/snmp/traps/{name}", dependencies=INTENT_WRITE)
async def delete_fabric_snmp_trap(site: str, name: str, user: str = Depends(current_user),
                                  netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts)) -> dict:
    return snmp_access.view(await _segment_call(snmp_access.delete_trap(netbox, site, name), accounts, user,
                                                "fabric.snmp.trap.delete", f"{site} {name}"))


@router.post("/fabrics/{site}/snmp/copy", dependencies=INTENT_WRITE)
async def copy_fabric_snmp(site: str, source: str = Body(embed=True), user: str = Depends(current_user),
                           netbox: NetBoxClient = Depends(get_netbox), accounts: Accounts = Depends(get_accounts),
                           access: Access = Depends(get_access)) -> dict:
    """Readers, trap targets and stored passwords of another fabric, added to this one's."""
    if not access.sees(source):
        raise HTTPException(status_code=403, detail=f"no access to {source}")
    result = await _segment_call(snmp_access.copy(netbox, site, source), accounts, user,
                                 "fabric.snmp.copy", f"{site} from {source}")
    await snmp_passwords.copy(accounts.sessions, site, source, user)
    return snmp_access.view(result)


@router.get("/fabrics/{site}/snmp/state", dependencies=[LOGIN])
async def fabric_snmp_state(site: str, jobs: snmp_passwords.PasswordJobs = Depends(get_password_jobs)) -> list[dict]:
    """Each reader on each active switch, read now from the switches."""
    try:
        return await jobs.switch_state(site)
    except segs.SegmentError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/snmp-password-jobs", status_code=202, dependencies=[LOGIN, OPERATE])
async def request_snmp_passwords(site: str = Body(), readers: list[str] = Body(), user: str = Depends(current_user),
                                 jobs: snmp_passwords.PasswordJobs = Depends(get_password_jobs),
                                 access: Access = Depends(get_access)) -> dict:
    """Type readers' stored passwords on every active switch of the fabric, after approval."""
    if not access.sees(site):
        raise HTTPException(status_code=403, detail=f"no access to {site}")
    try:
        return {"id": await jobs.request(user, site, readers)}
    except (deploys.DeployError, segs.SegmentError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except NetBoxError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/snmp-password-jobs/{job_id}/approve", dependencies=[LOGIN, OPERATE, Depends(_deploy_approval)])
async def approve_snmp_passwords(job_id: int, user: str = Depends(current_user),
                                 jobs: snmp_passwords.PasswordJobs = Depends(get_password_jobs)) -> dict:
    try:
        await jobs.approve(job_id, user)
    except deploys.DeployError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"id": job_id}


@router.get("/snmp-password-jobs", dependencies=[LOGIN])
async def list_snmp_password_jobs(limit: int = 20, jobs: snmp_passwords.PasswordJobs = Depends(get_password_jobs),
                                  access: Access = Depends(get_access)) -> list[dict]:
    return [j for j in await deploys.list_deploys(jobs.sessions, min(max(limit, 1), 200), jobs.KIND)
            if access.sees(j["site"])]


@router.get("/snmp-password-jobs/{job_id}", dependencies=[LOGIN])
async def get_snmp_password_job(job_id: int, jobs: snmp_passwords.PasswordJobs = Depends(get_password_jobs),
                                access: Access = Depends(get_access)) -> dict:
    job = await deploys.get_deploy(jobs.sessions, job_id, jobs.KIND)
    if job is None or not access.sees(job["site"]):
        raise HTTPException(status_code=404, detail=f"password job {job_id} not found")
    return job
