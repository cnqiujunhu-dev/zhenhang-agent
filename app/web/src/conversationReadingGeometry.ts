import type { ReadingRegion, ReadingTurn } from "./conversationReadingAnchor";

/** Geometry only. Opaque IDs and positions stay in mounted-conversation memory. */
export function collectReadingTurns(root: HTMLElement): ReadingTurn[] {
  return Array.from(root.querySelectorAll<HTMLElement>("[data-turn-id], .task-conversation[data-task-id]"))
    .filter(element => !element.closest("[hidden],[inert]") && element.getBoundingClientRect().height > 0)
    .map(element => {
      const bounds = element.getBoundingClientRect();
      const regions: ReadingRegion[] = [];
      for (const block of element.querySelectorAll<HTMLElement>("[data-reading-key]")) {
        if (block.closest("[hidden],[inert]")) continue;
        const key = block.dataset.readingKey;
        if (!key) continue;
        // Per-query records are immutable in a public task revision. Ordinals
        // are bound to that revision's opaque key, never merchant/card text.
        const records = Array.from(block.querySelectorAll<HTMLElement>(".eqc-record-grid > .eqc-record, .insight-records > li"));
        if (records.length) {
          records.forEach((record, index) => {
            const rect = record.getBoundingClientRect();
            if (rect.height > 0 && !record.closest("[hidden],[inert]"))
              regions.push({key: `${key}:record:${index}`, top: rect.top, height: rect.height});
          });
        } else {
          const rect = block.getBoundingClientRect();
          if (rect.height > 0) regions.push({key, top: rect.top, height: rect.height});
        }
      }
      return {turnId: element.dataset.turnId || `task:${element.dataset.taskId || ""}`,
        top: bounds.top, height: bounds.height, regions};
    });
}
