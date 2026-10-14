export type TaskStepStatus = 'pending' | 'running' | 'completed' | 'failed';

export type TaskStep = {
  id: string;
  title: string;
  description: string;
  status: TaskStepStatus;
};

export type AgentTask = {
  id: string;
  title: string;
  status: 'planning' | 'running' | 'completed' | 'failed';
  steps: TaskStep[];
};
