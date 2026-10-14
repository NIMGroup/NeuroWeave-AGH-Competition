import { useQuery } from '@tanstack/react-query';
import { useLanguageStore } from '../../stores/languageStore';
import { ui } from '../../lib/libraryText';
import { Dialog } from '../../ui/Dialog';
import { DefinitionDetails } from './DefinitionDetails';
import type { Entry } from './libraryApi';
import { methodQuery } from './libraryQueries';
import { button } from './pipelineEditorStyles';

type Props = {
  entry: Entry;
  canAdd: boolean;
  onAdd: (id: string) => void;
  onClose: () => void;
};
/** Details are fetched only when opened. Browsing never creates a canvas node. */
export function MethodDetailsCard({ entry, canAdd, onAdd, onClose }: Props) {
  const language = useLanguageStore((s) => s.language);
  const query = useQuery(methodQuery(entry.id, language));
  return (
    <Dialog
      title={query.data?.label ?? entry.label}
      description={ui('方法详情')}
      closeLabel={ui('关闭')}
      size="lg"
      className="max-h-[85dvh]"
      onClose={onClose}
      footer={
        <button
          type="button"
          className={button}
          disabled={!canAdd || !query.data}
          onClick={() => {
            onAdd(entry.id);
            onClose();
          }}
        >
          {ui('添加节点')}
        </button>
      }
    >
      {query.isPending && <p role="status">{ui('正在读取方法详情…')}</p>}
      {query.isError && (
        <div role="alert" className="space-y-2 break-words text-sm text-danger">
          <p>{ui('无法加载方法定义：{0}', String(query.error))}</p>
          <button
            type="button"
            className={button}
            onClick={() => void query.refetch()}
          >
            {ui('重试加载')}
          </button>
        </div>
      )}
      {query.data && <DefinitionDetails definition={query.data} />}
    </Dialog>
  );
}
