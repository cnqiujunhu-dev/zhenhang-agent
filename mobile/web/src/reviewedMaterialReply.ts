import type {TaskTrace} from "./types";

/** Read-only display from the owned runtime's freshly revalidated material.
 * It is never accepted as a receipt, financial action or permission. */
export function reviewedMaterialReply(trace:TaskTrace) {
  const value=trace.reviewed_material_reply;
  if(trace.state!=="SUCCEEDED" || trace.outcome?.verified!==true || trace.outcome.state!==trace.state
    ||trace.outcome.kind!=="QUERY_RESULT"||trace.pending_confirmation
    ||!trace.nodes.some(node=>node.action==="BILL_ANALYSIS"&&node.status==="SUCCEEDED")
    ||!value||typeof value!=="object"||Object.keys(value).sort().join(",")!=="bank_data_changed,read_only,schema_version,screenshot_proves_posting,status,text"
    ||value.schema_version!=="customer-reviewed-bill-reply-v1"||!["READY","UNAVAILABLE"].includes(value.status)
    ||value.read_only!==true||value.bank_data_changed!==false||value.screenshot_proves_posting!==false
    ||typeof value.text!=="string"||!value.text.trim()||value.text.length>6000)return null;
  return value.text;
}
