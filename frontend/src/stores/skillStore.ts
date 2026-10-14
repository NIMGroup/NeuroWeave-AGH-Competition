import { create } from 'zustand';

type SkillSelectionStore = {
  selectedSkillIds: string[];
  toggleSkill: (skillId: string) => void;
  setSelectedSkills: (skillIds: string[]) => void;
  clearSelectedSkills: () => void;
};

export const useSkillStore = create<SkillSelectionStore>((set) => ({
  selectedSkillIds: [],

  toggleSkill: (skillId) =>
    set((state) => ({
      selectedSkillIds: state.selectedSkillIds.includes(skillId)
        ? state.selectedSkillIds.filter((id) => id !== skillId)
        : [...state.selectedSkillIds, skillId]
    })),

  clearSelectedSkills: () =>
    set({
      selectedSkillIds: []
    }),

  setSelectedSkills: (skillIds) =>
    set({
      selectedSkillIds: skillIds
    })
}));
