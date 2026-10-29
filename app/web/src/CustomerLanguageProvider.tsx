import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { request } from "./api";
import { isCustomerLocale, setTransportLocale, translateCustomerText, type CustomerLocale } from "./customerLanguage";
import { safeSessionGet, safeSessionSet } from "./safeStorage";
import { explicitReplyLanguage } from "./conversationReplyLanguage";

const LanguageContext = createContext({ locale: "zh-CN" as CustomerLocale,
  explicitPreference: false,
  t: (text: string) => text, choose: (_locale: CustomerLocale) => {},
  bindAccount: (_token: string, _account: string) => {}, notice: "" });

export function CustomerLanguageProvider({ children, initialLocale }: { children: ReactNode; initialLocale?: CustomerLocale }) {
  const humanChoice = useRef(explicitReplyLanguage(initialLocale, safeSessionGet("zx_reply_language_explicit")));
  const [locale, setLocale] = useState<CustomerLocale>(() => humanChoice.current || (safeSessionGet("zx_display_language") === "en" ? "en" : "zh-CN"));
  const [explicitPreference, setExplicitPreference] = useState(Boolean(humanChoice.current));
  const [notice, setNotice] = useState("");
  const binding = useRef({ token: "", account: "", epoch: 0, choice: humanChoice.current ? 1 : 0 });
  const abortRef = useRef<AbortController | null>(null);
  setTransportLocale(locale, explicitPreference);
  const bindAccount = useCallback((token: string, account: string) => {
    const current = binding.current;
    if (current.token === token && current.account === account) return;
    abortRef.current?.abort();
    const next = { token, account, epoch: current.epoch + 1, choice: current.choice };
    binding.current = next;
    setNotice("");
    if (!token || !account) return;
    const abort = new AbortController(); abortRef.current = abort;
    request<{ key: string; value: unknown }[]>("/api/v1/preferences", token, "GET", undefined, undefined, { signal: abort.signal })
      .then(rows => {
        if (abort.signal.aborted || binding.current.epoch !== next.epoch || binding.current.choice !== next.choice || next.choice > 0 || !Array.isArray(rows)) return;
        const saved = rows.find(row => row.key === "display_language")?.value;
        if (isCustomerLocale(saved)) { setLocale(saved); safeSessionSet("zx_display_language", saved); }
      }).catch(() => {});
  }, []);
  const choose = useCallback((next: CustomerLocale) => {
    if (!isCustomerLocale(next)) return;
    const current = { ...binding.current, choice: binding.current.choice + 1 };
    binding.current = current;
    setLocale(next); setExplicitPreference(true); setTransportLocale(next, true); safeSessionSet("zx_display_language", next); safeSessionSet("zx_reply_language_explicit", next); setNotice("");
    if (!current.token || !current.account) return;
    // Only a human language click writes a preference. No default writes.
    request("/api/v1/preferences", current.token, "PUT", { key: "display_language", value: next })
      .catch(() => { if (binding.current.epoch === current.epoch && binding.current.choice === current.choice) setNotice("语言偏好未保存，本页仍使用所选语言。"); });
  }, []);
  useEffect(() => () => { abortRef.current?.abort(); binding.current.epoch += 1; }, []);
  useEffect(() => { document.documentElement.lang = locale; }, [locale]);
  const value = useMemo(() => ({ locale, explicitPreference, t: (text: string) => translateCustomerText(text, locale), choose, bindAccount, notice }), [locale, explicitPreference, choose, bindAccount, notice]);
  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}
export const useCustomerLanguage = () => useContext(LanguageContext);
export function CustomerLanguageScope({ locale, children }: { locale?: CustomerLocale; children: ReactNode }) {
  const parent = useCustomerLanguage();
  const scopedLocale = parent.explicitPreference ? parent.locale : locale || parent.locale;
  const value = useMemo(() => ({ ...parent, locale: scopedLocale, t: (text: string) => translateCustomerText(text, scopedLocale) }), [parent, scopedLocale]);
  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}
export function CustomerText({ text }: { text: string }) { const { t } = useCustomerLanguage(); return t(text); }
export function LanguageSelector() {
  const { locale, choose, notice, t } = useCustomerLanguage();
  return <div className="language-choice"><div className="language-buttons" role="group" aria-label={t("显示语言")}>
    <button type="button" aria-pressed={locale === "zh-CN"} onClick={() => choose("zh-CN")}>中文</button>
    <button type="button" aria-pressed={locale === "en"} onClick={() => choose("en")}>English</button>
  </div>{notice && <small role="status">{t(notice)}</small>}</div>;
}
