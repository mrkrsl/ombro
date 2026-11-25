import "./customFaces";

export { OmbroAvatar } from "./OmbroAvatar";
export type { OmbroAvatarProps, OmbroAvatarHandle, AvatarMode } from "./OmbroAvatar";
export { AvatarController } from "./controller";
export type { IdleOptions, ThinkOptions } from "./controller";
export { PixelEngine } from "./engine";
export {
  registerFace, getFace, hasFace, listFaces, facesByMood, parseFrame, frameToRows,
  blinkFrame, winkFrame, glanceFrame,
} from "./frames";
export type { FaceDef, Mood } from "./frames";
export { registerEffect, getEffect, listEffects, scrollText, spinner, fireworks, target, stars } from "./effects";
export type { EffectRun, EffectFactory } from "./effects";
export { frameToSvg, faceToSvg } from "./svg";
export type { SvgOptions } from "./svg";
export { DEFAULT_PALETTE, GEOMETRY } from "./theme";
export type { Palette } from "./theme";
