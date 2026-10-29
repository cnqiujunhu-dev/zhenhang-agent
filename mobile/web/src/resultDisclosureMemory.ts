/** UI preferences only. No result data, permissions or request payloads. */
export class ResultDisclosureMemory {
  private scope = "";
  private opened = new Set<string>();
  update(scope: string, blocked = false) {
    if (blocked || !scope || scope !== this.scope) this.opened.clear();
    this.scope = blocked ? "" : scope;
  }
  remember(scope: string, key: string | null, open: boolean) {
    if (!scope || scope !== this.scope || !key) return;
    this.opened.delete(key);
    if (open) this.opened.add(key);
    while (this.opened.size > 200) this.opened.delete(this.opened.values().next().value!);
  }
  wantsOpen(scope: string, key: string | null) {
    return Boolean(scope && scope === this.scope && key && this.opened.has(key));
  }
}

export function resultDisclosureKey(readingKey: unknown, index: number, summary: unknown): string | null {
  if (typeof readingKey !== "string" || !readingKey || readingKey.length > 500
    || !Number.isSafeInteger(index) || index < 0 || index > 200
    || typeof summary !== "string" || !summary.trim() || summary.length > 200) return null;
  return JSON.stringify([readingKey,index,summary.trim()]);
}
