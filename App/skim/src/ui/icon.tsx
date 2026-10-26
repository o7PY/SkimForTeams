// Design.md names the Solar icon set; no Solar package exists for React Native, so Material Community Icons stand in (P4-DIFF-008).
import { MaterialCommunityIcons } from '@expo/vector-icons';
import type { ComponentProps } from 'react';

import { useTheme } from '@/constants/theme';

export type IconName = ComponentProps<typeof MaterialCommunityIcons>['name'];

export function Icon({ name, size = 24, color }: { name: IconName; size?: number; color?: string }) {
  const t = useTheme();
  return <MaterialCommunityIcons name={name} size={size} color={color ?? t.textPrimary} />;
}
