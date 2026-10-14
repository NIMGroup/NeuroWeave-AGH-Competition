import type { SavedDraft } from './libraryApi';
export function mergeDraftRecovery(server:SavedDraft[],local:SavedDraft[],deletedIds:string[]) {
  const deleted=new Set(deletedIds);
  const items=new Map(server.filter(d=>!deleted.has(d.id)).map(d=>[d.id,{...d,server_revision:d.revision}]));
  for(const draft of local) {
    if(!draft?.id||!draft.editor||deleted.has(draft.id))continue;
    const existing=items.get(draft.id);
    if(!existing||draft.revision>=existing.revision)items.set(draft.id,{...draft,server_revision:existing?.server_revision??0});
  }
  return [...items.values()].sort((a,b)=>b.updated_at.localeCompare(a.updated_at)).slice(0,100);
}
