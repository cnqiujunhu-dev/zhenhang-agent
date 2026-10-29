// Account/notification surfaces own local open, dismissal and focus lifecycle.
// Read and bank-navigation authority stay in App's existing resource gate.
import {useState} from "react";
import {CustomerText,LanguageSelector,useCustomerLanguage} from "./CustomerLanguageProvider";
import {dateText,Icon} from "./components";
import {usePopoverNavigation} from "./usePopoverNavigation";
import {ResourceNotifications,notificationCountLabel} from "./ResourceReadPresentation";
import type {ResourceDatasetRead} from "./financialResourceReadTrust";
import type {AccountIdentity} from "./auth/types";
import type {DemoSession,Profile,Notification} from "./types";

export const profileLabels: Record<string, string> = {
  young_professional: "年轻职场人",
  family: "家庭账户",
  family_manager: "家庭管理者",
  retiree: "退休生活",
  freelancer: "自由职业者",
  small_business: "个体经营者",
};
const notificationLabels: Record<string, string> = {
  CARD_LOCKED: "卡片已保护",
  CARD_UPDATED: "卡片状态更新",
  TRANSFER_COMPLETED: "转账已完成",
  SUBSCRIPTION_CANCELLED: "订阅已取消",
  WEALTH_ORDER_COMPLETED: "理财交易已完成",
  TASK_SUCCEEDED: "任务已完成",
};


export interface CustomerTopbarActionsProps {
 session:DemoSession|null;account?:AccountIdentity|null;formalAccount:boolean;actorIsHelper:boolean;hasBankAccount:boolean;
 profileName:string;selectedProfile:string;profiles:Profile[];sessionBusy:boolean;largeText:boolean;
 scope:string;visible:boolean;notificationRead:ResourceDatasetRead;
 onLargeText:(value:boolean)=>void;onSimulation:()=>void;onPassword?:()=>void;onTestTools?:()=>void;onLogout?:()=>void;
 onProfileChange:(profile:string)=>void;onStartSession:()=>void;onRefreshNotifications:()=>void;
 onOpenNotification:(item:Notification)=>boolean;
}
export function CustomerTopbarActions({session,account,formalAccount,actorIsHelper,hasBankAccount,profileName,selectedProfile,profiles,sessionBusy,largeText,
 scope:popoverOwner,visible,notificationRead,onLargeText,onSimulation,onPassword,onTestTools,onLogout,onProfileChange,onStartSession,onRefreshNotifications,onOpenNotification}:CustomerTopbarActionsProps) {
 const {locale,t:customerUiText}=useCustomerLanguage();
 const [notificationOpen,setNotificationOpen]=useState(false),[profileOpen,setProfileOpen]=useState(false);
 const notificationPopover=usePopoverNavigation({scope:popoverOwner,visible,open:notificationOpen,setOpen:setNotificationOpen});
 const profilePopover=usePopoverNavigation({scope:popoverOwner,visible,open:profileOpen,setOpen:setProfileOpen});
 const displayedNotifications=(notificationRead.status==="READY"?notificationRead.data:notificationRead.previous) as Notification[]|null;
 return (
          <div className="topbar-actions">
            <LanguageSelector />
            <button
              className="accessibility-button"
              aria-pressed={largeText}
              onClick={() => {
                const next = !largeText;
                onLargeText(next);
              }}
              title={customerUiText("切换大字阅读模式")}
            ><CustomerText text="大字模式" /></button>
            {hasBankAccount && account?.role === "DEV_TEST" && (
              <button
                className="environment-label"
                onClick={() => onSimulation()}
                disabled={!session}
              >
                <i /><CustomerText text="开发测试" /><Icon name="chevron" size={10} />
              </button>
            )}
            <div className="popover-wrap">
              <button
                key={popoverOwner}
                ref={notificationPopover.triggerRef}
                className={`icon-button notification-button ${notificationPopover.open ? "selected" : ""}`}
                onClick={() => {
                  notificationPopover.toggle();
                  profilePopover.dismiss();
                }}
                aria-expanded={notificationPopover.open}
                aria-controls="customer-notification-popover"
                aria-label={notificationCountLabel(notificationRead, locale)}
              >
                <Icon name="bell" />
                {displayedNotifications && displayedNotifications.length > 0 && (
                  <span className="notification-dot" />
                )}
              </button>
              {notificationPopover.open && (
                <div id="customer-notification-popover" ref={notificationPopover.panelRef} className="notification-popover" data-customer-opened-surface="notifications">
                  <ResourceNotifications read={notificationRead} labels={notificationLabels} onRefresh={() => onRefreshNotifications()}
                    onOpen={item => {
                      if (onOpenNotification(item)) notificationPopover.dismiss();
                    }} />
                </div>
              )}
            </div>
            <div className="topbar-divider" />
            <div className="popover-wrap">
              <button
                key={popoverOwner}
                ref={profilePopover.triggerRef}
                className="profile-button"
                onClick={() => {
                  profilePopover.toggle();
                  notificationPopover.dismiss();
                }}
                aria-expanded={profilePopover.open}
                aria-controls="customer-profile-popover"
              >
                <span className="avatar">{session ? customerUiText("圳") : customerUiText("访")}</span>
                <span>
                  {session?.actor_role === "FAMILY_HELPER"
                    ? `${session.display_name || customerUiText("家庭协助者")} · 协助`
                    : session
                      ? account?.display_name ||
                        account?.username ||
                        profileName
                      : customerUiText("开启账户")}
                </span>
                <Icon name="chevron" size={14} />
              </button>
              {profilePopover.open && (
                <div id="customer-profile-popover" ref={profilePopover.panelRef} className="profile-popover" data-customer-opened-surface="profile">
                  {formalAccount && account ? (
                    <>
                      <strong>
                        {account.display_name || account.username}
                      </strong>
                      <p>
                        {actorIsHelper
                          ? customerUiText("当前协助视图仅显示授权投影。请返回本人身份管理账号。")
                          : customerUiText("账套与任务持续保存。账户切换请退出当前账号后登录。")}
                      </p>
                      <div className="account-menu-summary">
                        <span><CustomerText text="账号" /><strong>{account.username}</strong>
                        </span>
                        <span><CustomerText text="权限" />{" "}
                          <strong>
                            {actorIsHelper
                              ? customerUiText("家庭协助身份")
                              : account.role === "DEV_TEST"
                                ? customerUiText("开发数据测试")
                                : customerUiText("普通客户")}
                          </strong>
                        </span>
                        <span><CustomerText text="账套" /><strong>{profileName}</strong>
                        </span>
                      </div>
                      {!actorIsHelper && (
                        <button
                          className="button secondary"
                          onClick={() => {
                            profilePopover.dismiss(true);
                            onPassword?.();
                          }}
                        >
                          <Icon name="lock" size={15} /><CustomerText text="修改密码" /></button>
                      )}
                      {!actorIsHelper && account.role === "DEV_TEST" && (
                        <button
                          className="button secondary"
                          onClick={() => {
                            profilePopover.dismiss(true);
                            onTestTools?.();
                          }}
                        >
                          <Icon name="workflow" size={15} /><CustomerText text="专用数据工具" /></button>
                      )}
                      <button className="button subtle" onClick={onLogout}><CustomerText text="退出登录" /></button>
                    </>
                  ) : (
                    <>
                      <strong>
                        {session ? customerUiText("选择新的账户方案") : customerUiText("开启个人账户")}
                      </strong>
                      <p><CustomerText text="每次开启会创建独立账户与业务状态。" /></p>
                      <label htmlFor="profile-select"><CustomerText text="账套画像" /></label>
                      <select
                        id="profile-select"
                        value={selectedProfile}
                        onChange={(event) =>
                          onProfileChange(event.target.value)
                        }
                      >
                        {profiles.length ? (
                          profiles.map((profile) => {
                            const value =
                              profile.profile ||
                              profile.key ||
                              profile.id ||
                              "young_professional";
                            return (
                              <option key={value} value={value}>
                                {profile.label ||
                                  profile.name ||
                                  profileLabels[value] ||
                                  value}
                              </option>
                            );
                          })
                        ) : (
                          <option value="young_professional"><CustomerText text="年轻职场人" /></option>
                        )}
                      </select>
                      <button
                        className="button primary"
                        disabled={sessionBusy}
                        onClick={onStartSession}
                      >
                        {sessionBusy
                          ? customerUiText("正在准备账套…")
                          : session
                            ? customerUiText("开启新账套")
                            : customerUiText("开始体验")}
                        <Icon name="arrow" size={16} />
                      </button>
                      {session && (
                        <small><CustomerText text="当前账套" />{session.sandbox_id.slice(0, 12)}…<br /><CustomerText text="会话有效至" />{dateText(session.expires_at)}
                        </small>
                      )}
                    </>
                  )}
                </div>
              )}
            </div>
          </div>
 );
}

