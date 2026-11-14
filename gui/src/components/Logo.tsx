// The wordmark. A drawn stand-in until the logo files arrive in ombro-meta/ombro-gui/assets.

export function Logo({ size = 40 }: { size?: number }) {
  return (
    <div className="logo" style={{ fontSize: size }} role="img" aria-label="ombro">
      <span aria-hidden="true">ombro</span>
    </div>
  );
}
