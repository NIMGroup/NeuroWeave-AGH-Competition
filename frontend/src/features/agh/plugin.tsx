import { BusinessToolbar, NewSessionWorkbench, WorkbenchController } from './Workbench';
import { installTransport } from './host';
import type { AGHHost } from './host';
import { RecordManagerButton, RecordManagerController } from './RecordManager';

export function apply(ctx: AGHHost) {
  installTransport(ctx);
  ctx.slots.register('conversation.input.left', () => <BusinessToolbar host={ctx}/>, { id: 'neuroweave-business', priority: 10 });
  ctx.slots.register('conversation.hero.agentPreset', () => <NewSessionWorkbench host={ctx}/>, { id: 'neuroweave-new-session', priority: 10 });
  ctx.slots.register('sidebar.footer.action', () => <RecordManagerButton host={ctx}/>, { id: 'neuroweave-record-management', priority: 10 });
  ctx.slots.register('shell.overlay', () => <><WorkbenchController host={ctx}/><RecordManagerController host={ctx}/></>, { id: 'neuroweave-workbench-dialog', priority: 10 });
}
