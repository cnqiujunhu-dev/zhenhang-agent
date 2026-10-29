import type { Conversation, ConversationTurn } from "./conversationTypes";
export const HISTORY_PAGE_LIMIT = 3;
export const HISTORY_TURN_LIMIT = 600;
export const HISTORY_WINDOW_LIMIT_NOTICE = "当前窗口最多展示600条历史消息；更早记录仍保存在服务端，请先完成当前阅读。";

export function validateAuthorizedConversationPage(page: Conversation, conversationId: string, before?: number) {
  if (!page || page.conversation_id !== conversationId || !Array.isArray(page.turns) ||
    typeof page.has_earlier !== "boolean" || page.turns.length > 200) throw new Error("历史页不完整");
  const rows = [...page.turns].sort((a,b) => a.sequence-b.sequence);
  if (rows.some(turn => !turn || turn.conversation_id !== conversationId || typeof turn.turn_id !== "string" || !turn.turn_id ||
    !Number.isSafeInteger(turn.sequence) || turn.sequence < 1 || before !== undefined && turn.sequence >= before))
    throw new Error("历史页范围不一致");
  if (new Set(rows.map(turn => turn.sequence)).size !== rows.length || new Set(rows.map(turn => turn.turn_id)).size !== rows.length)
    throw new Error("历史页记录重复");
  if (rows.some((turn,index) => index > 0 && turn.sequence !== rows[index-1].sequence+1)) throw new Error("历史页序号不连续");
  if (before !== undefined && (rows.length ? rows.at(-1)!.sequence !== before-1 : before !== 1)) throw new Error("历史页与请求边界不一致");
  if (page.has_earlier) {
    if (!rows.length || rows[0].sequence <= 1 || page.before_cursor !== rows[0].sequence) throw new Error("历史页游标与内容不一致");
  } else if (page.before_cursor != null || rows.length && rows[0].sequence !== 1) throw new Error("历史页完成标记与内容不一致");
  return rows;
}

export function pendingHistoryViews(turns: ConversationTurn[]): ConversationTurn[] {
  return turns.map(turn => ({
    turn_id: turn.turn_id, conversation_id: turn.conversation_id, sequence: turn.sequence,
    message: "历史消息正在重新核验访问范围…", status: "ANSWERED", mode: "NEED_CONTEXT",
    answer: "核验完成后会显示仍可访问的内容，暂不展示此前的解释。",
    sources: [], task_link: null, error_code: "HISTORY_ACCESS_RECHECK", created_at: turn.created_at, updated_at: turn.updated_at,
  }));
}

/** Sequential, cursor-bounded live-authorized reads; never copies an old answer. */
export async function readAuthorizedHistoryWindow(
  read: (before: number) => Promise<Conversation>,
  conversationId: string, before: number, desiredOldest: number,
  maxPages = HISTORY_PAGE_LIMIT,
) {
  if (!Number.isSafeInteger(before) || before < 1 || !Number.isSafeInteger(desiredOldest) || desiredOldest < 1 ||
    desiredOldest > before || !Number.isSafeInteger(maxPages) || maxPages < 1 || maxPages > HISTORY_PAGE_LIMIT)
    throw new Error("历史读取范围不合法");
  let cursor: number | null = before, hasEarlier = true, pages = 0;
  const records = new Map<string, ConversationTurn>();
  while (cursor && hasEarlier && pages < maxPages && records.size < HISTORY_TURN_LIMIT) {
    const requested = cursor;
    const page = await read(requested);
    const rows = validateAuthorizedConversationPage(page,conversationId,requested);
    if (rows.some(turn => records.has(turn.turn_id))) throw new Error("跨页历史记录重复");
    pages += 1;
    for (const turn of rows) records.set(turn.turn_id,turn);
    hasEarlier = page.has_earlier === true;
    cursor = hasEarlier ? page.before_cursor ?? null : null;
    if (hasEarlier && (!cursor || cursor >= requested || cursor < 1 || !Number.isSafeInteger(cursor)))
      throw new Error("历史页游标没有前进");
    if (rows.length && rows[0].sequence <= desiredOldest) break;
    if (!rows.length) break;
  }
  const turns = [...records.values()].sort((a,b) => a.sequence-b.sequence).slice(-HISTORY_TURN_LIMIT);
  const oldest = turns[0]?.sequence ?? before;
  const limitReached = (pages >= maxPages || turns.length >= HISTORY_TURN_LIMIT) && hasEarlier && oldest > desiredOldest;
  return { turns, hasEarlier, cursor, pages, limitReached, verifiedBefore: before };
}
