import { useEffect, useRef, useState } from "react";
import { FinancialResourceReadTrust, type ResourceReadScope } from "./financialResourceReadTrust";

export function useFinancialResourceReadTrust(scope: ResourceReadScope) {
  const ledgerRef = useRef<FinancialResourceReadTrust | null>(null);
  if (!ledgerRef.current) ledgerRef.current = new FinancialResourceReadTrust(() => document.visibilityState === "visible");
  const ledger = ledgerRef.current;
  ledger.bind(scope); // New actor/visibility props revoke old receipts before effects run.
  const [, render] = useState(0);
  useEffect(() => ledger.subscribe(() => render(value => value + 1)), [ledger]);
  return ledger;
}
