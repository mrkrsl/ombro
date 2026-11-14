// The wordmark, from ombro-meta/assets: one drawing for light, one for dark.

import dark from "../assets/logo-dark.svg";
import light from "../assets/logo-light.svg";
import { useStore } from "../store/store";

export function Logo({ size = 40 }: { size?: number }) {
  const theme = useStore((s) => s.theme);
  // `size` was the old drawn wordmark's font size; the drawing is 215 by 86.
  const width = Math.round(size * 3.6);
  return <img className="logo-img" src={theme === "dark" ? dark : light} width={width} alt="ombro" draggable={false} />;
}
