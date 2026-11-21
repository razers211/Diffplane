import { createContext, useContext } from "react";
import type { FabricLimits, Role } from "./api";

// Same ladder as the orchestrator (orchestrator/auth.py): each role can do what the ones
// before it can. The API enforces it; the GUI only hides what the user cannot do.
const LADDER: Role[] = ["viewer", "operator", "approver", "admin"];

export function allows(role: Role, needed: Role): boolean {
  return LADDER.indexOf(role) >= LADDER.indexOf(needed);
}

export const RoleContext = createContext<Role>("viewer");

export function useCan(needed: Role): boolean {
  return allows(useContext(RoleContext), needed);
}

// Fabrics the account is limited to: null reaches every fabric. The API filters and refuses
// the others; the GUI only hides what the account cannot use.
export const FabricsContext = createContext<FabricLimits>(null);

export function useLimits(): FabricLimits {
  return useContext(FabricsContext);
}

/** Approvers everywhere; an operator where an admin let them approve that fabric's deploys. */
export function useMayApprove(site: string | null | undefined): boolean {
  const role = useContext(RoleContext);
  const limits = useContext(FabricsContext);
  return allows(role, "approver") || (role === "operator" && !!limits && !!site && !!limits[site]);
}
