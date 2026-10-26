// The only haptics in the app (P5-D-108): a light impact when a swipe commits, a selection tick on star, checkbox and toggle.
// expo-haptics follows the phone's own touch-feedback setting; a failure here must never break the action it decorates.
import * as Haptics from 'expo-haptics';

export const haptics = {
  /** A swipe landed. */
  impact() {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {});
  },
  /** A star, checkbox or toggle changed. */
  selection() {
    Haptics.selectionAsync().catch(() => {});
  },
};
