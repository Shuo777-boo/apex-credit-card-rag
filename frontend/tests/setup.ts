import { vi } from "vitest";
import { configure } from '@testing-library/react';
configure({asyncUtilTimeout:5000});
Object.defineProperty(window, "matchMedia", {
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }),
});
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
const originalStyle = window.getComputedStyle;
window.getComputedStyle = ((element: Element) => {
  const style = originalStyle(element);
  if (element.tagName !== "TEXTAREA") return style;
  return new Proxy(style, {
    get(target, name) {
      if (name === "getPropertyValue")
        return (property: string) => {
          if (
            [
              "padding-top",
              "padding-bottom",
              "border-top-width",
              "border-bottom-width",
            ].includes(property)
          )
            return "4px";
          if (property === "font-size") return "14px";
          if (property === "line-height") return "22px";
          return target.getPropertyValue(property);
        };
      return Reflect.get(target, name);
    },
  });
}) as typeof window.getComputedStyle;


