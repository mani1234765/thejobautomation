import { describe, expect, it } from "vitest";
import { commandMenuShortcutLabel, isApplePlatform, isCommandMenuShortcut } from "./shortcuts";

const key = (over: Partial<KeyboardEvent>) =>
  ({ key: "k", code: "KeyK", metaKey: false, ctrlKey: false, altKey: false, shiftKey: false, ...over }) as KeyboardEvent;

describe("command menu shortcut", () => {
  it("opens with ⌘K (macOS) and Ctrl+K (Windows/Linux)", () => {
    expect(isCommandMenuShortcut(key({ metaKey: true }))).toBe(true);
    expect(isCommandMenuShortcut(key({ ctrlKey: true }))).toBe(true);
    expect(isCommandMenuShortcut(key({ ctrlKey: true, key: "K" }))).toBe(true); // caps lock
  });

  it("matches the physical K key on non-Latin layouts", () => {
    expect(isCommandMenuShortcut(key({ ctrlKey: true, key: "л" }))).toBe(true);
  });

  it("ignores plain K, other keys and other modifier combinations", () => {
    expect(isCommandMenuShortcut(key({}))).toBe(false);
    expect(isCommandMenuShortcut(key({ metaKey: true, key: "j", code: "KeyJ" }))).toBe(false);
    expect(isCommandMenuShortcut(key({ ctrlKey: true, altKey: true }))).toBe(false);
    expect(isCommandMenuShortcut(key({ metaKey: true, shiftKey: true }))).toBe(false);
  });

  it("shows the platform's convention", () => {
    expect(isApplePlatform({ platform: "MacIntel" })).toBe(true);
    expect(isApplePlatform({ userAgentData: { platform: "macOS" } })).toBe(true);
    expect(isApplePlatform({ platform: "Linux x86_64" })).toBe(false);
    expect(isApplePlatform({ userAgentData: { platform: "Windows" } })).toBe(false);
    expect(commandMenuShortcutLabel(true)).toBe("⌘K");
    expect(commandMenuShortcutLabel(false)).toBe("Ctrl K");
  });
});
