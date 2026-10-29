import { useEffect, useState } from "react";
import webPackage from "../package.json" with { type: "json" };
export const webRelease = webPackage.version;
export function isNewerRelease(server: string, client = webRelease): boolean {
  if (!/^\d+\.\d+\.\d+$/.test(server) || !/^\d+\.\d+\.\d+$/.test(client)) return false;
  const actual = server.split(".").map(Number), current = client.split(".").map(Number);
  for (let i=0; i<3; i++) if (actual[i] !== current[i]) return actual[i] > current[i];
  return false;
}
export function useReleaseUpdate() {
  const [newVersion, setNewVersion] = useState("");
  useEffect(() => {
    let stopped = false;
    const check = async () => {
      if (document.visibilityState === "hidden") return;
      try {
        const result = await fetch("/api/v1/health/live", {cache: "no-store"});
        const health = result.ok ? await result.json() : null;
        if (!stopped && typeof health?.version === "string" && isNewerRelease(health.version)) setNewVersion(health.version);
      } catch { /* A version check never obstructs a financial workflow. */ }
    };
    void check();
    const timer = window.setInterval(check, 60000);
    window.addEventListener("focus", check);
    return () => {stopped=true; window.clearInterval(timer); window.removeEventListener("focus",check);};
  }, []);
  return newVersion;
}
