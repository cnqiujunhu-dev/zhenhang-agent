export const expiry="2099-01-01T00:00:00.000000Z";
const common=(action,confirmationExpiresAt)=>({schema_version:"customer-banking-confirmation-v1",action,scope_verified:true,currency:"CNY",confirmation_expires_at:confirmationExpiresAt});
export function schedule(confirmationExpiresAt=expiry){return {...common("TRANSFER_SCHEDULE",confirmationExpiresAt),
  source:{display_last4:"5b03",instrument_type:"DEBIT",currency:"CNY"},
  recipient:{username:"wangwei",display_name:"王伟",display_last4:"10ba",instrument_type:"DEBIT",currency:"CNY"},
  amount_minor:88888,due_at:"2099-01-31T01:30:00+00:00",timezone:"Asia/Shanghai",clock_basis:"CALENDAR_REAL",
  frequency:"MONTHLY",total_occurrences:3,last_due_at:"2099-03-31T01:30:00+00:00",end_at:"2099-03-31T01:30:00+00:00",
  total_budget_minor:266664,funds_reserved:false,future_execution_revalidation:true,failure_policy:"STOP_REMAINING",month_end_policy:"CLAMP_ORIGINAL_DAY_NO_DRIFT",memo:"三个月房租"};}
export function aa(confirmationExpiresAt=expiry){return {...common("AA_REQUEST",confirmationExpiresAt),
  collector:{username:"quyuwei",display_name:"区宇威",display_last4:"5b03",instrument_type:"DEBIT",currency:"CNY"},
  total_minor:10001,participant_count:3,participants:[{username:"wangwei",display_name:"王伟",amount_minor:3334},
    {username:"chenyue",display_name:"陈月",amount_minor:3334},{username:"zhanghao",display_name:"张浩",amount_minor:3333}],
  expires_at:"2099-01-03T10:00:00+00:00",timezone:"Asia/Shanghai",remainder_rule:"LIST_ORDER_EXTRA_CENT",invitation_only:true,automatic_debit:false};}
export function customerDto(value,{stepUp=true,confirmationExpiresAt=expiry}={}){return {schema_version:"2.0",task_id:"fixture-parent",version:3,state:"AWAITING_CONFIRMATION",
  message:"核对本次安排",reply_language:"zh-CN",nodes:[{node_id:"fixture-node",operation_id:"fixture-op",child_task_id:"fixture-child",action:value.action,
    label:value.action,status:"WAITING_CONFIRMATION",depends_on:[],result:{preview:{instrument_type:"CREDIT",amount_minor:1}}}],
  pending_confirmation:{task_id:"fixture-child",confirmation_id:"fixture-confirmation",plan_hash:"a".repeat(64),expected_task_version:1,node_key:"node",label:value.action,
    summary:"摘要中错误地写了9999元，不能以此补字段",scope:{domain_confirmation:value,source_account_id:"PRIVATE_ACCOUNT_ID",scope_hash:"PRIVATE_SCOPE_HASH"},requires_step_up:stepUp,expires_at:confirmationExpiresAt},
  allowed_actions:["confirm","revise","cancel"],receipt:null,analysis:null,
  outcome:{kind:"PROCESS",state:"AWAITING_CONFIRMATION",verified:false,completed_operation_ids:[]}};}
