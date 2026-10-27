import AsyncStorage from '@react-native-async-storage/async-storage';
import { PersistQueryClientProvider } from '@tanstack/react-query-persist-client';
import { useMemo, type ReactNode } from 'react';

import { useAuth } from './auth';
import { OutboxProvider } from './outbox-runtime';
import { PushProvider } from './push-runtime';
import { CACHE_BUSTER, CACHE_MAX_AGE, createPersister, createQueryClient, queryKeys } from './query';

export { AsyncStorage };

/** Everything that belongs to the signed-in account: its own query cache (persisted), its outbox, its push registration.
 *  The tree keeps the same shape when signed out, so signing in or out does not rebuild the navigator. */
export function SessionProviders({ children }: { children: ReactNode }) {
  const { session } = useAuth();
  const userId = session?.user.id ?? null;
  const queryClient = useMemo(() => createQueryClient(), [userId]); // eslint-disable-line react-hooks/exhaustive-deps -- a new cache per account
  const persister = useMemo(() => createPersister(userId ?? 'signed-out'), [userId]);
  return (
    <PersistQueryClientProvider
      client={queryClient}
      persistOptions={{ persister, maxAge: CACHE_MAX_AGE, buster: CACHE_BUSTER, dehydrateOptions: { shouldDehydrateQuery: (q) => q.state.status === 'success' && q.queryKey[0] !== queryKeys.teamCards[0] } }}>
      <OutboxProvider userId={userId}>
        <PushProvider userId={userId}>{children}</PushProvider>
      </OutboxProvider>
    </PersistQueryClientProvider>
  );
}
