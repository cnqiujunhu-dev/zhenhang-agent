import { useEffect, useState } from "react";
import { viewportGeometry } from "./viewportGeometry.ts";
export function visibleViewportHeight(
  layoutHeight: number,
  visualHeight?: number,
) {
  return viewportGeometry(layoutHeight, { height: visualHeight }).height;
}
export function useVisibleViewportGeometry() {
  const read = () =>
    viewportGeometry(window.innerHeight, window.visualViewport);
  const [geometry, setGeometry] = useState(read);
  useEffect(() => {
    let frame = 0;
    const update = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => setGeometry((current) => {
        const next = read();
        return Object.keys(next).every((key) => next[key as keyof typeof next] === current[key as keyof typeof current])
          ? current : next;
      }));
    };
    window.addEventListener("resize", update);
    window.visualViewport?.addEventListener("resize", update);
    window.visualViewport?.addEventListener("scroll", update);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", update);
      window.visualViewport?.removeEventListener("resize", update);
      window.visualViewport?.removeEventListener("scroll", update);
    };
  }, []);
  return geometry;
}
export function useVisibleViewport() {
  return useVisibleViewportGeometry().height;
}
