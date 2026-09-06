import type { Schedule } from './types';

/** Default concurrency behavior when a schedule does not specify one. */
export const defaultConcurrency = 'skip' as const;

/**
 * Human-readable description of a schedule's timing: "manual" for
 * on-demand (null cron) schedules, otherwise the raw cron expression text.
 */
export function describeSchedule(schedule: Pick<Schedule, 'cron'>): string {
  return schedule.cron || 'manual';
}
