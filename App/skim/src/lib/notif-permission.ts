// What to do about notification permission (PHASE4.md 7.3). The prompt is shown after the first join; this covers everyone else: a
// student who joined before, said no, or reinstalled. Pure so the three cases can be tested.
export type PermissionState = { granted: boolean; canAskAgain: boolean };
export type PermissionAction = 'none' | 'ask' | 'settings';

/** granted -> nothing to do; can still be asked -> show the system prompt; refused for good -> only the phone's settings can change it. */
export function permissionAction(p: PermissionState | null): PermissionAction {
  if (!p || p.granted) return 'none';
  return p.canAskAgain ? 'ask' : 'settings';
}

export const BANNER_TEXT = {
  ask: { title: 'Turn on notifications', body: 'Get reminders for quizzes and exams, and a heads-up when a date changes.', button: 'Turn on' },
  settings: { title: 'Notifications are off', body: 'Allow notifications for Skim in your phone’s settings to get reminders and date-change alerts.', button: 'Open settings' },
} as const;
