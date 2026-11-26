/** Keyboard shortcuts shared by the app shell and its hints. */

/** ⌘K on macOS, Ctrl+K on Windows/Linux. Both are accepted everywhere, so external keyboards and
 * remote desktops work too. `code` covers layouts where the K key doesn't produce "k". */
export function isCommandMenuShortcut(e: Pick<KeyboardEvent, "key" | "code" | "metaKey" | "ctrlKey" | "altKey" | "shiftKey">): boolean {
  return (e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && (e.key.toLowerCase() === "k" || e.code === "KeyK");
}

export function isApplePlatform(nav: { platform?: string; userAgent?: string; userAgentData?: { platform?: string } } | undefined =
  typeof navigator === "undefined" ? undefined : (navigator as Navigator & { userAgentData?: { platform?: string } })): boolean {
  const p = nav?.userAgentData?.platform || nav?.platform || nav?.userAgent || "";
  return /mac|iphone|ipad|ipod/i.test(p);
}

/** The hint shown next to the command menu button. */
export function commandMenuShortcutLabel(apple: boolean = isApplePlatform()): string {
  return apple ? "⌘K" : "Ctrl K";
}
