import { BankCardNumber, Brand, Icon, money } from './components';
import { useCustomerLanguage } from './CustomerLanguageProvider';
import { validSimulationCardNumber } from './cardIdentity';
import { creditFacilityDisplay } from './creditFacilityDisplay';
import './profileCreditCards.css';

type Data = Record<string, unknown>;
const object = (v:unknown): v is Data => v !== null && typeof v === 'object' && !Array.isArray(v);
const text = (v:unknown) => typeof v === 'string' ? v : '';
const rows = (v:unknown):Data[] => Array.isArray(v) ? v.filter(object) : [];

/** Read-only profile presentation. Credit limits are not debit balances or transfer limits. */
export function ProfileCreditCards({data}:{data:unknown}) {
  const en=useCustomerLanguage().locale==='en',unknown=en?'Not provided':'暂未提供';
  if(!object(data)||!Array.isArray(data.cards)||!data.cards.every(object))
    return <p className="data-home-empty">{en?'Credit-card data is incomplete.':'信用卡资料暂未读取完整。'}</p>;
  const cards=data.cards as Data[],facilities=creditFacilityDisplay(cards);
  const amount=(v:unknown,currency:string)=>typeof v==='number'&&Number.isSafeInteger(v)&&v>=0
    && ['CNY','USD'].includes(currency)?money(v,{currency}):unknown;
  const statusLabels:Record<string,string>=en
    ? {LOCKED:'Locked',LOST:'Lost',ISSUED:'Awaiting activation',REPLACED:'Replaced',PENDING_REVIEW:'Pending review',CLOSED:'Closed'}
    : {LOCKED:'已锁定',LOST:'已挂失',ISSUED:'待激活',REPLACED:'已换卡',PENDING_REVIEW:'待审核',CLOSED:'已关闭'};
  const applications=rows(data.applications);
  return <div className="profile-credit-view">
    {!cards.length&&<p className="data-home-empty">{en?'No credit cards.':'目前没有信用卡。'}</p>}
    <div className="data-home-card-grid profile-credit-grid">{cards.map((card,index)=>{
      const supplied=validSimulationCardNumber(card.card_number)?card.card_number:null;
      const declared=text(card.display_last4);
      const conflicting=Boolean(supplied&&declared&&supplied.slice(-4).toLowerCase()!==declared.toLowerCase());
      const number=conflicting?null:supplied;
      const tail=conflicting?unknown:number?.slice(-4)||(/^[0-9a-f]{4}$/i.test(declared)?declared:unknown);
      const balances=rows(card.balances);
      const currencies=['CNY','USD'].filter(c=>Array.isArray(card.supported_currencies)
        ? card.supported_currencies.includes(c) : balances.some(row=>row.currency===c));
      const balance=(currency:string)=>{const matches=balances.filter(row=>row.currency===currency);return matches.length===1?matches[0]:{};};
      const groupIndex=facilities?.groupByCard.get(text(card.card_id));
      const group=groupIndex===undefined?null:facilities?.groups[groupIndex];
      const sharedTail=group&&group.cardIds[0]!==card.card_id?group.tails[0]:null;
      const cny=card.base_currency==='CNY';
      return <div className="profile-credit-item" key={text(card.card_id)||index}>
        <div className="bank-card bank-card-credit" aria-label={en?`Credit card ending ${tail}`:`信用卡尾号${tail}`} data-card-tail={tail}>
          <div className="bank-card-top"><Brand compact/><span>{en?'圳行 · Credit card':'圳行 · 信用卡'}</span><Icon name="card" size={20}/></div>
          <div className="card-currency-badges">{currencies.map(c=><span key={c}>{c==='CNY'?(en?'CNY':'CNY · 人民币'):(en?'USD':'USD · 美元')}</span>)}
            {card.shared_credit_limit===true&&currencies.length===2&&<span>{en?'Shared limit':'双币共享额度'}</span>}</div>
          <BankCardNumber identity={{number,displayTail:tail}}/>
          <div className="bank-card-bottom">
            {card.status!=='ACTIVE'&&<span className="card-restriction"><span className={`state-badge ${['LOST','CLOSED'].includes(text(card.status))?'danger':'warning'}`}>
              {statusLabels[text(card.status)]||(en?'Status unverified':'状态待核对')}</span></span>}
            <span className="bank-card-limit"><small>{sharedTail?(en?'Shared credit limit':'共享信用额度'):(en?'Credit limit (CNY)':'信用额度（人民币）')}</small>
              <strong className={sharedTail?'credit-shared-reference':undefined}>{sharedTail
                ?(en?`With card ${sharedTail}`:`同尾号 ${sharedTail}`):cny?amount(card.base_credit_limit_minor,'CNY'):unknown}</strong></span>
          </div>
        </div>
        <div className="profile-credit-data">
          {sharedTail?<p className="profile-credit-sharing">{en?`Shares the limit and debt with card ending ${sharedTail}.`:`与尾号${sharedTail}共享额度及欠款。`}</p>
            :<><dl className="data-home-metrics">
              <div><dt>{en?'Available credit (CNY)':'可用额度（人民币）'}</dt><dd>{cny?amount(card.available_base_credit_minor,'CNY'):unknown}</dd></div>
              {currencies.map(c=><div key={c}><dt>{en?`Outstanding (${c})`:c==='CNY'?'人民币未还金额':'美元未还金额'}</dt><dd>{amount(balance(c).outstanding_minor,c)}</dd></div>)}
            </dl>
            <details className="profile-credit-details"><summary>{en?'Limit and repayment details':'额度与还款详情'}</summary><dl className="data-home-metrics">
              <div><dt>{en?'Used credit (CNY equivalent)':'已用额度（折合人民币）'}</dt><dd>{cny?amount(card.used_base_exposure_minor,'CNY'):unknown}</dd></div>
              <div><dt>{en?'Authorization hold (CNY equivalent)':'预授权占用（折合人民币）'}</dt><dd>{cny?amount(card.authorization_base_exposure_minor,'CNY'):unknown}</dd></div>
              {currencies.map(c=><div key={c}><dt>{en?`Credit balance (${c})`:c==='CNY'?'人民币溢缴款':'美元溢缴款'}</dt><dd>{amount(balance(c).credit_balance_minor,c)}</dd></div>)}
            </dl></details></>}
        </div>
      </div>;
    })}</div>
    {applications.length>0&&<details className="profile-credit-applications"><summary>{en?`Application records (${applications.length})`:`申请记录（${applications.length}）`}</summary>
      {applications.map((row,i)=><p key={text(row.application_id)||i}>{row.kind==='LIMIT'?(en?'Limit adjustment':'额度调整'):(en?'Card application':'信用卡申请')} · {
        ({PENDING_REVIEW:en?'Under review':'审核中',APPROVED:en?'Approved':'已批准',REJECTED:en?'Rejected':'已拒绝'} as Record<string,string>)[text(row.status)]||unknown}</p>)}
    </details>}
  </div>;
}
