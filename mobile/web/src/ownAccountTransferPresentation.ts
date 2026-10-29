/** Public display shape only. Execution and source authority remain server-side. */
type Data=Record<string,unknown>;
export const ownTransferFieldsValid=(value:Data)=>value.currency==="CNY"&&typeof value.amount_minor==="number"&&Number.isSafeInteger(value.amount_minor)&&value.amount_minor>0
  && ["source_card_last4","destination_card_last4"].every(key=>typeof value[key]==="string"&&/^[a-f0-9]{4}$/i.test(value[key] as string))
  && value.same_owner_internal_movement===true&&value.outward_transfer_daily_quota_consumed===false;
export function recordedOwnAccountTransfer(value:unknown):boolean {
  if(value===null||typeof value!=="object"||Array.isArray(value))return false;
  const result=value as Data;
  return ownTransferFieldsValid(result)&&result.schema_version==="own-account-transfer-receipt-v1"&&result.action==="OWN_ACCOUNT_TRANSFER"
    &&result.status==="POSTED"&&result.bank_state_changed===true&&result.ledger_balanced===true
    &&result.authority==="VERIFIED_SAME_OWNER_CASH_MOVEMENT";
}
