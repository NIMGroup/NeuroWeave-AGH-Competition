import { Dialog } from '../../ui/Dialog';
import { ui } from '../../lib/libraryText';
import { LibraryPanel } from './LibraryPanel';
import type { Workflow } from '../workflows/workflowTypes';

export function PipelineTaskDialog({onClose,onCreated}:{onClose:()=>void;onCreated:(workflow:Workflow)=>void}) {
  return <Dialog title={ui('从 Pipeline 创建任务')} description={ui('1. 选择 Pipeline → 2. 选择输入与输出 → 3. 配置与预检')}
    closeLabel={ui('关闭')} onClose={onClose} size="xl">
    <LibraryPanel domain="pipelines" taskPicker selectedSkillIds={[]} disabled={false} onAdd={()=>{}} onRemove={()=>{}}
      onDetails={()=>{}} onClear={()=>{}} onTaskCreated={onCreated}/>
  </Dialog>;
}
