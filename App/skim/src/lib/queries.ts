import { useQuery } from '@tanstack/react-query';

import { useAuth } from './auth';
import { meOptions, subscriptionsOptions } from './query-options';

export function useSubscriptions() {
  const { api } = useAuth();
  return useQuery(subscriptionsOptions(api));
}

export function useMe() {
  const { api } = useAuth();
  return useQuery(meOptions(api));
}
