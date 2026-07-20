export const CPR_CYCLE_SECONDS: 120;

export type CprTimerSnapshot = {
  elapsedSeconds: number;
  remainingSeconds: number;
  progressPercentage: number;
  rhythmCheckDue: boolean;
};

export function cprTimerSnapshot(
  elapsedSeconds: number | null | undefined,
  cycleDurationSeconds?: number,
): CprTimerSnapshot | null;
