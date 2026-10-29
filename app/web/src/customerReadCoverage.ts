import type { CustomerReadCoverage } from "./types";

type Data = Record<string, unknown>;
const object = (value: unknown): value is Data => value !== null && typeof value === "object" && !Array.isArray(value);
const records = (value: unknown): value is Data[] => Array.isArray(value) && value.every(object);
const unavailable = (): CustomerReadCoverage => ({ status: "UNAVAILABLE_SCOPE" });

function negativeFamilyBalance(result: Data, coverage: Data): boolean {
  const marker=coverage.balance_authorization,current=result.current_actor,actors=result.actors,permissions=result.permissions;
  if (!object(marker)||Object.keys(marker).sort().join(",")!=="owner_name,schema_version,status"
    ||marker.schema_version!=="family-balance-negative-authorization-v1"||marker.status!=="NOT_AUTHORIZED"
    ||typeof marker.owner_name!=="string"||!marker.owner_name.trim()||Object.hasOwn(result,"authorized_balances")
    ||!object(current)||current.role!=="FAMILY_HELPER"||!object(permissions)||permissions.can_read_owner_balance!==false
    ||!records(actors)||actors.length!==2||!records(result.delegations)||!records(result.proposals)
    ||JSON.stringify(coverage.selected_dimensions)!=='["FAMILY_OWNERS"]'||!object(coverage.selected_counts)
    ||Object.keys(coverage.selected_counts).join(",")!=="FAMILY_OWNERS"||coverage.selected_counts.FAMILY_OWNERS!==1) return false;
  if (actors.some(actor=>!["OWNER","FAMILY_HELPER"].includes(String(actor.role))||typeof actor.user_id!=="string"||!actor.user_id
    ||typeof actor.display_name!=="string"||!actor.display_name.trim())||new Set(actors.map(actor=>actor.user_id)).size!==2
    ||actors.filter(actor=>actor.user_id===current.user_id&&actor.role===current.role&&actor.display_name===current.display_name).length!==1) return false;
  const owners=actors.filter(actor=>actor.role==="OWNER");
  if (owners.length!==1||owners[0].display_name!==marker.owner_name) return false;
  return result.delegations.every(row=>row.owner_user_id===owners[0].user_id&&row.grantee_user_id===current.user_id
    &&typeof row.valid==="boolean"&&Array.isArray(row.scopes)&&row.scopes.every(scope=>typeof scope==="string")
    &&!(row.valid&&row.scopes.includes("READ_BALANCE")));
}

/** Rendering only. The server verifies owned task/source/epoch before publishing
 * this finite summary. It is never a confirmation or an authorization grant. */
export function customerReadCoverage(action: string, result: unknown): CustomerReadCoverage {
  if (!object(result) || Object.hasOwn(result, "read_selection")) return unavailable();
  if (!Object.hasOwn(result, "read_coverage")) return { status: "UNFILTERED" };
  const value = result.read_coverage;
  if (!object(value)) return unavailable();
  const dimensions: Record<string, string> | null = action === "CARBON_VIEW" ? { REWARDS: "rewards", ACTIVITIES: "events" }
    : action === "WEALTH_COMPARE" ? { PRODUCTS: "products" }
    : action === "FAMILY_VIEW" ? { FAMILY_OWNERS: "actors", FAMILY_GRANTEES: "actors" } : null;
  if (Object.keys(value).length === 1 && value.status === "UNAVAILABLE_SCOPE") return unavailable();
  const deniedBalance=action==="FAMILY_VIEW"&&negativeFamilyBalance(result,value);
  if (!dimensions || Object.keys(value).sort().join(",") !== (deniedBalance
    ? "balance_authorization,read_kind,selected_counts,selected_dimensions,status" : "read_kind,selected_counts,selected_dimensions,status")
    || value.status !== "FILTERED" || value.read_kind !== action || !Array.isArray(value.selected_dimensions)
    || !value.selected_dimensions.length || !value.selected_dimensions.every(item => typeof item === "string" && Object.hasOwn(dimensions, item))
    || new Set(value.selected_dimensions).size !== value.selected_dimensions.length || !object(value.selected_counts)
    || Object.keys(value.selected_counts).sort().join(",") !== [...value.selected_dimensions].sort().join(",")) return unavailable();
  const selected = value.selected_dimensions as Extract<CustomerReadCoverage,{status:"FILTERED"}>["selected_dimensions"];
  const counts = value.selected_counts;
  if (selected.some(item => !Number.isSafeInteger(counts[item]) || counts[item] !== 1
    || !records(result[dimensions[item]]) || (action !== "FAMILY_VIEW" && (result[dimensions[item]] as Data[]).length !== counts[item]))) return unavailable();
  // Family actors also include the current actor and the other side of the
  // selected relationship. Its count is deliberately not a selection count.
  if (action === "FAMILY_VIEW" && (!object(result.current_actor) || !records(result.actors)
    || !result.actors.length || !records(result.delegations) || !records(result.proposals)
    || (!deniedBalance&&!records(result.authorized_balances)))) return unavailable();
  if (action === "CARBON_VIEW" && (Object.hasOwn(result, "point_ledger_net") || !Array.isArray(result.redemptions) || result.redemptions.length !== 0
    || Object.entries(dimensions).some(([dimension, collection]) => !selected.includes(dimension as "REWARDS" | "ACTIVITIES")
      && (!Array.isArray(result[collection]) || result[collection].length !== 0)))) return unavailable();
  return { status: "FILTERED", read_kind: action as "CARBON_VIEW" | "WEALTH_COMPARE" | "FAMILY_VIEW", selected_dimensions: [...selected],
    selected_counts: Object.fromEntries(selected.map(item => [item, 1])),
    ...(deniedBalance?{balance_authorization:{schema_version:"family-balance-negative-authorization-v1" as const,
      status:"NOT_AUTHORIZED" as const,owner_name:String((value.balance_authorization as Data).owner_name)}}:{}) };
}
