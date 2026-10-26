// Typography from Design.md ("Manrope"). Weight picks the font file; letter spacing is em in the design, px here.
import { StyleSheet, Text, type TextProps, type TextStyle } from 'react-native';

import { useTheme } from '@/constants/theme';

export const FONT = { 300: 'Manrope_300Light', 500: 'Manrope_500Medium', 600: 'Manrope_600SemiBold', 700: 'Manrope_700Bold' } as const;

type Spec = { size: number; weight: keyof typeof FONT; line: number; em: number; upper?: boolean };
export const VARIANTS = {
  display: { size: 48, weight: 300, line: 1.0, em: -0.04 },
  headlineLg: { size: 24, weight: 700, line: 1.2, em: -0.01 },
  headlineMd: { size: 20, weight: 700, line: 1.25, em: -0.005 },
  headlineSm: { size: 18, weight: 700, line: 1.3, em: 0.02 },
  body: { size: 14, weight: 500, line: 1.4, em: 0 },
  bodySm: { size: 13, weight: 500, line: 1.4, em: 0 },
  label: { size: 14, weight: 600, line: 1.2, em: 0 },
  labelSm: { size: 12, weight: 600, line: 1.2, em: 0 },
  caption: { size: 12, weight: 500, line: 1.3, em: 0.01 },
  micro: { size: 10, weight: 600, line: 1.2, em: 0.02, upper: true },
} as const satisfies Record<string, Spec>;

export type Variant = keyof typeof VARIANTS;

export function variantStyle(v: Variant): TextStyle {
  const s: Spec = VARIANTS[v];
  return { fontFamily: FONT[s.weight], fontSize: s.size, lineHeight: Math.round(s.size * s.line), letterSpacing: s.em * s.size, ...(s.upper ? { textTransform: 'uppercase' as const } : {}) };
}

export type AppTextProps = TextProps & { variant?: Variant; color?: string };

export const MAX_FONT_SCALE = 1.3; // text grows with the phone's setting up to 130%; layouts are built to hold that

export function AppText({ variant = 'body', color, style, ...rest }: AppTextProps) {
  const t = useTheme();
  return <Text maxFontSizeMultiplier={MAX_FONT_SCALE} {...rest} style={[styles.base, variantStyle(variant), { color: color ?? t.textPrimary }, style]} />;
}

const styles = StyleSheet.create({ base: { includeFontPadding: false } });
