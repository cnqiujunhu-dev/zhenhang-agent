import {useState} from 'react';
import {WealthPanel} from './BusinessPanels';
import {useCustomerLanguage} from './CustomerLanguageProvider';

/** Explicit customer form, separate from the read-only profile homepage. */
export function RiskAssessmentEntry({token}:{token:string}) {
  const [open,setOpen]=useState(false),[revision,setRevision]=useState(0);
  const en=useCustomerLanguage().locale==='en';
  return <section className="conversation-risk-entry"><button type="button" className="button secondary compact" aria-expanded={open} onClick={()=>setOpen(!open)}>
    {open?(en?'Close risk assessment':'收起风险测评'):(en?'Complete risk assessment':'填写风险测评')}</button>
    {open&&<WealthPanel token={token} revision={revision} assessmentOnly onPrompt={()=>{}} onUpdated={()=>setRevision(value=>value+1)}/>}
  </section>;
}
