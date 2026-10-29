import { useCustomerLanguage } from "./CustomerLanguageProvider";
type Data = Record<string,unknown>;
const object = (value:unknown):value is Data => !!value && typeof value === "object" && !Array.isArray(value);
export function FamilyProfileCards({result}:{result:unknown}) {
  const {locale}=useCustomerLanguage(),en=locale==="en";
  const rows=object(result)&&Array.isArray(result.members)?result.members:[];
  const ready=object(result)&&result.schema_version==="family-profile-v1"&&result.bank_state_changed===false
    &&result.account_access_granted===false&&rows.every(row=>object(row)&&typeof row.family_member_name==="string"&&row.account_access_granted===false);
  if(!ready)return <p>{en?"Family details still need checking.":"家庭资料还需要核对。"}</p>;
  return <section aria-label={en?"Family directory details":"家庭成员资料"}>
    <h3>{result.family_profile_state_changed===true?en?"Family details saved":"家庭资料已保存":en?"Family directory":"家庭成员资料"}</h3>
    {!rows.length&&<p>{en?"No family members saved yet. Provide a display name and relationship in chat.":"目前没有登记的家人，可以在对话中提供显示名和关系。"}</p>}
    {rows.map((row,index)=><p key={index}>{String(row.family_member_name)}{typeof row.family_relationship==="string"?` · ${row.family_relationship}`:""}</p>)}
    <p>{en?"Directory details grant no login, account or banking permission. Birthday details are confirmed separately.":"资料不授予登录、账户或银行权限。生日资料另行核对确认。"}</p>
  </section>;
}
