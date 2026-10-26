// A bottom sheet and the on-screen keyboard. On Android the sheet's window often does NOT shrink when the keyboard opens, so the
// keyboard sits on top of it. The fix measures how far the keyboard actually overlaps the sheet's window and lifts the sheet by that
// much; if the window did shrink, the overlap is 0 and nothing is added.
export type SheetLayout = { paddingBottom: number; maxHeight: number };

/** `windowBottom`: bottom edge of the sheet's window; `keyboardTop`: top edge of the keyboard (both in screen pixels, null = no keyboard). */
export function keyboardOverlap(windowBottom: number, keyboardTop: number | null): number {
  if (keyboardTop === null || !Number.isFinite(keyboardTop) || !Number.isFinite(windowBottom)) return 0;
  return Math.max(0, Math.round(windowBottom - keyboardTop));
}

/** The panel is lifted by the overlap, and never taller than the space that is left above the keyboard. */
export function sheetLayout(windowHeight: number, overlap: number): SheetLayout {
  const lift = Math.max(0, Math.min(overlap, windowHeight * 0.8));
  const room = windowHeight - lift;
  return { paddingBottom: lift, maxHeight: Math.max(220, Math.floor(room * 0.92)) };
}
