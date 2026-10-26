import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useMemo } from 'react';

import type { Me } from './api';
import { useOutbox } from './outbox-runtime';
import { useMe } from './queries';
import { queryKeys } from './query';
import { applyPatch, parseSettings, type Settings, type SettingsPatch } from './settings';

/** The settings, always complete. A change shows at once (optimistic) and is queued in the outbox as ONE merged PATCH, so quick
 *  taps on several controls become a single request and work offline. */
export function useSettings(): { settings: Settings; ready: boolean; update(patch: SettingsPatch): Promise<void> } {
  const me = useMe();
  const qc = useQueryClient();
  const { enqueue } = useOutbox();
  const settings = useMemo(() => parseSettings(me.data?.settings), [me.data?.settings]);

  const update = useCallback(
    async (patch: SettingsPatch) => {
      qc.setQueryData<Me>(queryKeys.me, (m) => (m ? { ...m, settings: applyPatch(parseSettings(m.settings), patch) as unknown as Me['settings'] } : m));
      await enqueue({ key: 'settings', method: 'PATCH', path: '/me/settings', body: patch, merge: true });
    },
    [qc, enqueue],
  );
  return { settings, ready: !!me.data, update };
}
