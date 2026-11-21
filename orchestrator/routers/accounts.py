"""Sign-in, first setup, platform settings, images, directory, alert receiver, users."""

import os
import time

from fastapi import APIRouter, Body, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from orchestrator import db, drift
from orchestrator import alerting, images
from orchestrator.access import Access
from orchestrator.directory import Directory
from orchestrator.first_setup import FirstSetup
from orchestrator.platform_settings import PlatformSettings
from orchestrator.auth import audit
from orchestrator.auth import COOKIE, Accounts, session_ttl
from orchestrator.deps import (
    ADMIN, current_account, current_user, get_accounts, get_images, get_settings, get_setup, LOGIN,
    _netbox,
)
from orchestrator.routers.fabrics import list_fabrics

router = APIRouter()


# -- login ------------------------------------------------------------------------------

def _set_session_cookie(response: Response, token: str) -> None:
    # Secure unless COOKIE_SECURE=false (only plain-HTTP test clients need that).
    response.set_cookie(
        COOKIE, token, max_age=int(session_ttl().total_seconds()), httponly=True, samesite="strict",
        secure=os.environ.get("COOKIE_SECURE", "true").lower() != "false",
    )


@router.post("/auth/login")
async def login(
    request: Request,
    response: Response,
    username: str = Body(),
    password: str = Body(),
    accounts: Accounts = Depends(get_accounts),
) -> dict:
    throttle: Throttle | None = getattr(request.app.state, "throttle", None)
    client = request.client.host if request.client else "?"
    if throttle is not None and (wait := throttle.wait(client, time.monotonic())):
        raise HTTPException(status_code=429, detail="too many failed sign-ins: try again later",
                            headers={"Retry-After": str(int(wait) + 1)})
    token = await accounts.login(username, password)
    if token is None:
        if throttle is not None:
            throttle.failed(client, time.monotonic())
        raise HTTPException(status_code=401, detail="wrong username or password")
    _set_session_cookie(response, token)
    user = await accounts.user_for(token)
    return {"username": user.username if user else username, "role": user.role if user else None,
            "fabrics": Access.of(user).fabrics if user else None}


@router.get("/setup")
async def setup_needed(setup: FirstSetup = Depends(get_setup), accounts: Accounts = Depends(get_accounts)) -> dict:
    """Whether the platform has no account yet (no login needed: the login page asks)."""
    return {"needed": await setup.needed(accounts)}


@router.post("/setup")
async def first_admin(
    response: Response, code: str = Body(), username: str = Body(), password: str = Body(),
    setup: FirstSetup = Depends(get_setup), accounts: Accounts = Depends(get_accounts),
) -> dict:
    """The first admin, with the setup code from the orchestrator's log; signs them in."""
    try:
        await setup.claim(accounts, code, username, password)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    token = await accounts.login(username.strip(), password)
    _set_session_cookie(response, token)
    return {"username": username.strip(), "role": "admin"}


@router.post("/auth/logout")
async def logout(
    response: Response,
    session: str | None = Cookie(default=None, alias=COOKIE),
    accounts: Accounts = Depends(get_accounts),
) -> dict:
    await accounts.logout(session)
    response.delete_cookie(COOKIE)
    return {}


@router.get("/auth/me", dependencies=[LOGIN])
async def me(account: db.User = Depends(current_account)) -> dict:
    # fabrics: None reaches every fabric; else {site: may approve deploys there}.
    return {"username": account.username, "role": account.role, "fabrics": Access.of(account).fabrics,
            "source": getattr(account, "source", "local")}


@router.post("/auth/password", dependencies=[LOGIN])
async def change_my_password(current: str = Body(), new: str = Body(), user: str = Depends(current_user),
                             accounts: Accounts = Depends(get_accounts)) -> dict:
    """Any user changes their own password (the current one is asked again). Signs them out everywhere."""
    try:
        await accounts.change_own_password(user, current, new)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {}


async def _settings_applied(request: Request, key: str, env) -> str | None:
    """Settings a running component keeps: hand it the new value. What went wrong, if anything."""
    if key == "DRIFT_INTERVAL_SECONDS" and request.app.state.drift is not None:
        request.app.state.drift.state.interval = drift.interval_from_env()
    if key.startswith("ALERT_"):
        return await alerting.apply(env)
    return None


@router.get("/settings", dependencies=[LOGIN, ADMIN])
async def list_settings(settings: PlatformSettings = Depends(get_settings)) -> dict:
    """Every setting and where its value comes from; secrets never shown."""
    return settings.view()


@router.put("/settings/{key}", dependencies=[LOGIN, ADMIN])
async def set_setting(key: str, request: Request, value: str = Body(embed=True), user: str = Depends(current_user),
                      settings: PlatformSettings = Depends(get_settings)) -> dict:
    try:
        await settings.set(key, value, user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {**settings.view(), "applied": await _settings_applied(request, key, settings.env)}


@router.delete("/settings/{key}", dependencies=[LOGIN, ADMIN])
async def reset_setting(key: str, request: Request, user: str = Depends(current_user),
                        settings: PlatformSettings = Depends(get_settings)) -> dict:
    """Back to the value in deploy/.env."""
    try:
        await settings.reset(key, user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {**settings.view(), "applied": await _settings_applied(request, key, settings.env)}


@router.get("/images", dependencies=[LOGIN])
async def list_images(store: images.ImageStore = Depends(get_images)) -> list[dict]:
    return await store.list()


@router.post("/images", dependencies=[LOGIN, ADMIN], status_code=201)
async def upload_image(request: Request, name: str, sha256: str | None = None, user: str = Depends(current_user),
                       store: images.ImageStore = Depends(get_images)) -> dict:
    """The file is the raw request body (no form encoding), streamed to the store while hashed."""
    try:
        return await store.add(name, request.stream(), sha256, user)
    except images.ImageError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except OSError as exc:   # the store's disk is full, most likely
        raise HTTPException(status_code=507, detail=f"the image store could not keep it: {exc.strerror}")


@router.delete("/images/{image_id}", dependencies=[LOGIN, ADMIN], status_code=204)
async def delete_image(image_id: int, user: str = Depends(current_user),
                       store: images.ImageStore = Depends(get_images)) -> None:
    try:
        await store.delete(image_id, user)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# -- directory sign-in -----------------------------------------------------------------------

@router.get("/directory", dependencies=[LOGIN, ADMIN])
async def directory_view(settings: PlatformSettings = Depends(get_settings)) -> dict:
    """The directory settings (secrets only as set or not) and which providers are complete."""
    from orchestrator import directory as dirs

    return {"settings": settings.rows("Directory"), "providers": Directory(settings.env).providers(),
            "defaults": {"DIRECTORY_LDAP_USER_FILTER": dirs.DEFAULT_USER_FILTER,
                         "DIRECTORY_LDAP_GROUP_FILTER": dirs.DEFAULT_GROUP_FILTER,
                         "DIRECTORY_RADIUS_ATTRIBUTE": dirs.DEFAULT_RADIUS_ATTRIBUTE}}


class DirectoryTest(BaseModel):
    username: str
    password: str


@router.post("/directory/test", dependencies=[LOGIN, ADMIN])
async def directory_test(body: DirectoryTest, user: str = Depends(current_user),
                         settings: PlatformSettings = Depends(get_settings), accounts: Accounts = Depends(get_accounts)) -> dict:
    """Ask the directory as a sign-in would, without signing in or keeping an account."""
    result = await Directory(settings.env).authenticate(body.username.lower(), body.password)
    await audit(accounts.sessions, user, "directory.test", detail=f"{body.username}: {result.outcome}")
    return {"outcome": result.outcome, "source": result.source, "role": result.role, "fabrics": result.fabrics,
            "detail": result.detail, "groups": result.groups}


@router.get("/alerts/receiver", dependencies=[LOGIN, ADMIN])
async def alert_receiver(settings: PlatformSettings = Depends(get_settings)) -> dict:
    """Where alerts go now, and what is missing for the chosen receiver."""
    data, problem = alerting.config(settings.env)
    return {"receiver": data["route"]["receiver"], "chosen": settings.env.get("ALERT_RECEIVER") or "none", "problem": problem}


@router.post("/alerts/test", dependencies=[LOGIN, ADMIN])
async def test_alert(user: str = Depends(current_user), settings: PlatformSettings = Depends(get_settings)) -> dict:
    """Send a test alert through Alertmanager to the receiver."""
    try:
        await alerting.send_test(user, settings.env)
    except Exception as exc:   # Alertmanager down or unset: say so, never a 500
        raise HTTPException(status_code=503, detail=f"Alertmanager did not take the test alert: {exc or type(exc).__name__}")
    return {"sent": True, "receiver": alerting.config(settings.env)[0]["route"]["receiver"]}


# -- accounts (admins) ------------------------------------------------------------------

class NewUser(BaseModel):
    username: str
    password: str
    role: str = "viewer"


class UserChange(BaseModel):
    role: str | None = None
    password: str | None = None
    fabrics: dict[str, bool] | None = None   # given as null: every fabric again


@router.get("/users", dependencies=[LOGIN, ADMIN])
async def list_users(accounts: Accounts = Depends(get_accounts)) -> list[dict]:
    return await accounts.list_users()


@router.post("/users", status_code=201, dependencies=[LOGIN, ADMIN])
async def create_user(body: NewUser, user: str = Depends(current_user), accounts: Accounts = Depends(get_accounts)) -> dict:
    try:
        await accounts.create_user(body.username, body.password, body.role, actor=user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"username": body.username.strip(), "role": body.role}


@router.patch("/users/{name}", dependencies=[LOGIN, ADMIN])
async def change_user(name: str, body: UserChange, request: Request, user: str = Depends(current_user),
                      accounts: Accounts = Depends(get_accounts)) -> dict:
    """An admin changes a user's role, the fabrics they are limited to, or sets a new
    password (which signs that user out)."""
    try:
        if body.role is not None:
            await accounts.set_role(name, body.role, actor=user)
        if "fabrics" in body.model_fields_set:
            if body.fabrics:
                known = {f["site"] for f in await list_fabrics(await _netbox(request), Access("admin"))}
                if unknown := sorted(set(body.fabrics) - known):
                    raise ValueError(f"no fabric {', '.join(unknown)}")
            await accounts.set_fabrics(name, body.fabrics, actor=user)
        if body.password is not None:
            await accounts.set_password(name, body.password, actor=user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"username": name}


@router.delete("/users/{name}", dependencies=[LOGIN, ADMIN])
async def delete_user(name: str, user: str = Depends(current_user), accounts: Accounts = Depends(get_accounts)) -> dict:
    if name == user:
        raise HTTPException(status_code=400, detail="you cannot delete your own account")
    try:
        await accounts.delete_user(name, actor=user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {}
