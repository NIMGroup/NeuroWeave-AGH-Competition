import { create } from 'zustand';
import type { Dataset } from '../features/datasets/datasetsApi';

type DatasetSelectionStore = {
  selectedDatasetId: string | null;
  selectDataset: (dataset: Dataset | null) => void;
  clearDataset: () => void;
  setDatasetId: (id: string | null) => void;
};

export const useDatasetStore = create<DatasetSelectionStore>((set) => ({
  selectedDatasetId: null,
  setDatasetId: (selectedDatasetId) => set({ selectedDatasetId }),

  selectDataset: (dataset) =>
    set({
      selectedDatasetId: dataset?.dataset_id ?? null
    }),

  clearDataset: () =>
    set({
      selectedDatasetId: null
    })
}));

export function useSelectedDatasetId(): string | null {
  return useDatasetStore((state) => state.selectedDatasetId);
}
