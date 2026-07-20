export const CPR_CYCLE_SECONDS = 120;

export function cprTimerSnapshot(elapsedSeconds, cycleDurationSeconds = CPR_CYCLE_SECONDS) {
  if (elapsedSeconds === null || elapsedSeconds === undefined) {
    return null;
  }

  const duration = Math.max(0, cycleDurationSeconds);
  const elapsed = clamp(elapsedSeconds, 0, duration);

  // elapsed_seconds = current_time - cycle_start_time. The backend owns this value.
  // remaining_seconds = cycle_duration - elapsed_seconds.
  // progress_percentage = elapsed_seconds / cycle_duration * 100.
  return {
    elapsedSeconds: elapsed,
    remainingSeconds: Math.max(0, duration - elapsed),
    progressPercentage: duration === 0 ? 100 : (elapsed / duration) * 100,
    rhythmCheckDue: elapsed >= duration,
  };
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, Math.floor(value)));
}
