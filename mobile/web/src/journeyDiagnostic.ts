import type {JourneySample} from "./journeyMetrics";
const kinds=new Set(["QUERY","CONSULT","OTHER"]);
const count=(value:unknown):value is number=>typeof value==="number"&&Number.isSafeInteger(value)&&value>=0;
/** Local developer observation, never raw input, identity, money or telemetry upload. */
export function journeyDiagnostic(sample:JourneySample,coverage:unknown) {
  if(!sample||!kinds.has(sample.kind)||!count(sample.accepted_ms)||!count(sample.first_readable_ms)
    ||sample.first_readable_ms<sample.accepted_ms
    ||sample.complete_ms!==null&&(!count(sample.complete_ms)||sample.complete_ms<sample.first_readable_ms))return null;
  const input=coverage&&typeof coverage==="object"?coverage as Record<string,unknown>:{};
  const counters:Record<string,number|null>={};
  for(const key of ["submitted","accepted","first_readable","completed_visible_reply","evicted_pending",
    "accepted_not_yet_readable","submitted_not_yet_accepted","retained_timing_samples","active_pending"])
    counters[key]=count(input[key])?input[key] as number:null;
  return {schema_version:"customer-visible-journey-diagnostic-v1",scope:"LOCAL_BROWSER_VISIBLE_REPLY_NOT_SETTLEMENT",
    timing:{kind:sample.kind,accepted_ms:sample.accepted_ms,first_readable_ms:sample.first_readable_ms,complete_ms:sample.complete_ms},
    counters,source:"DEVELOPER_OBSERVATION_NOT_INDEPENDENT_USER_STUDY",network_upload:false};
}
