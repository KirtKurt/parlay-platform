export function isLiveBoardPayload(payload: any) {
  if (!payload || payload.ok === false) return false;
  const source = String(payload.source || payload.mode || '').toUpperCase();
  if (source.includes('SAMPLE') || payload.mode === 'sample') return false;
  const games = (payload.boards || []).flatMap((board: any) => board?.games || []);
  if (games.some((game: any) => /sample|market board/i.test(String(game?.books?.[0]?.book || '')))) return false;
  return (payload.boards || []).some((board: any) => Array.isArray(board?.games) && board.games.length > 0);
}
