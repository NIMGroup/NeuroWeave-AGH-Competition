/**
 * Classy Mode - a premium, restrained pointer atmosphere.
 *
 * One fixed `<canvas>` overlay (`pointer-events: none`) renders two coupled
 * layers, both tinted from the live `--accent` token so Blue/Teal/Violet and
 * Light/Dark all work without hardcoded palettes:
 *
 * - Ambient glow: a soft radial illumination that eases toward the pointer
 *   (lerped each frame, so fast motion trails gracefully) and breathes down
 *   to a faint resting halo when the pointer is still.
 * - Wave rings: up to a bounded set of thin expanding rings spawned on
 *   throttled pointer travel; each ring carries 2-3 faint overlapping echoes
 *   that expand at slightly different radii and fade quickly.
 *
 * Implementation notes (performance contract):
 * - No DOM nodes per movement, no React state per movement.
 * - Pointer events only record target coordinates; one rAF loop draws.
 * - The loop runs only while Classy Mode is on AND (rings alive OR glow
 *   above its resting floor); pointer-idle collapses to a near-static halo
 *   and the loop sleeps once settled. `visibilitychange` also pauses work.
 * - `prefers-reduced-motion: reduce` keeps the effect completely off.
 */

type Ripple = {
  x: number;
  y: number;
  born: number;
  r: number;
  g: number;
  b: number;
  dark: boolean;
};

const RIPPLE_LIFETIME = 900;
const RIPPLE_MAX_RADIUS = 44;
const MIN_TRAVEL = 28;
const MIN_INTERVAL = 70;
const MAX_RIPPLES = 14;
/* Ambient halo tuned to read as "expensive paper light", not neon. */
const GLOW_RADIUS = 210;
const GLOW_FOLLOW = 0.14;
const GLOW_PEAK_ALPHA = 0.12;
const GLOW_REST_ALPHA = 0.05;
const GLOW_DARK_BOOST = 1.6;

let canvas: HTMLCanvasElement | null = null;
let context: CanvasRenderingContext2D | null = null;
let ripples: Ripple[] = [];
let rafId = 0;
let lastX = -1;
let lastY = -1;
let lastSpawn = 0;
/* Lerped glow position: eases toward the pointer for an organic trail. */
let glowX = -1;
let glowY = -1;
let targetX = -1;
let targetY = -1;
let glowAlpha = 0;
let hidden = false;

function reducedMotion(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function accentRgb(): { r: number; g: number; b: number } {
  const value = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim();
  const hex = /^#([0-9a-f]{6})$/i.exec(value);
  if (hex) {
    const n = parseInt(hex[1], 16);
    return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
  }
  const rgb = /^rgba?\((\d+),\s*(\d+),\s*(\d+)/i.exec(value);
  if (rgb) return { r: Number(rgb[1]), g: Number(rgb[2]), b: Number(rgb[3]) };
  return { r: 37, g: 99, b: 235 };
}

function isDark(): boolean {
  return document.documentElement.classList.contains('dark');
}

function sizeCanvas() {
  if (!canvas) return;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(window.innerWidth * dpr);
  canvas.height = Math.round(window.innerHeight * dpr);
  context?.setTransform(dpr, 0, 0, dpr, 0, 0);
}

/* Pointer-aware light: draw() mirrors the eased glow into --classy-px/py so
   glass panels (rail, sidebar, header) share one light source with the canvas
   halo. Written from the existing rAF loop only when the position actually
   moves - no listeners, no React state, no per-move DOM work. */
let reportedPx = -500;
let reportedPy = -500;

function mirrorPointer() {
  if (!canvas) return;
  if (Math.abs(glowX - reportedPx) < 1 && Math.abs(glowY - reportedPy) < 1) return;
  reportedPx = glowX;
  reportedPy = glowY;
  const style = document.documentElement.style;
  style.setProperty('--classy-px', `${Math.round(glowX)}px`);
  style.setProperty('--classy-py', `${Math.round(glowY)}px`);
}

function clearPointerMirror() {
  reportedPx = -500;
  reportedPy = -500;
  document.documentElement.style.removeProperty('--classy-px');
  document.documentElement.style.removeProperty('--classy-py');
}

function draw(now: number) {
  if (!context) return;
  context.clearRect(0, 0, window.innerWidth, window.innerHeight);

  /* Ease the halo toward the pointer; settle toward a faint resting alpha. */
  if (glowX < 0 || targetX < 0) {
    glowX = targetX;
    glowY = targetY;
  } else {
    glowX += (targetX - glowX) * GLOW_FOLLOW;
    glowY += (targetY - glowY) * GLOW_FOLLOW;
  }
  mirrorPointer();
  const moving = ripples.length > 0;
  glowAlpha += ((moving ? GLOW_PEAK_ALPHA : GLOW_REST_ALPHA) - glowAlpha) * 0.08;

  if (glowX >= 0 && glowAlpha > 0.004) {
    const latest = ripples[ripples.length - 1];
    const rgb: { r: number; g: number; b: number } = latest ?? accentRgb();
    const dark = latest ? latest.dark : isDark();
    const alpha = Math.min(glowAlpha * (dark ? GLOW_DARK_BOOST : 1), 0.16);
    const gradient = context.createRadialGradient(glowX, glowY, 0, glowX, glowY, GLOW_RADIUS);
    gradient.addColorStop(0, `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, ${alpha.toFixed(3)})`);
    gradient.addColorStop(0.55, `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, ${(alpha * 0.45).toFixed(3)})`);
    gradient.addColorStop(1, `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0)`);
    context.fillStyle = gradient;
    context.beginPath();
    context.arc(glowX, glowY, GLOW_RADIUS, 0, Math.PI * 2);
    context.fill();
  }

  ripples = ripples.filter((ripple) => now - ripple.born < RIPPLE_LIFETIME);
  for (const ripple of ripples) {
    const t = (now - ripple.born) / RIPPLE_LIFETIME;
    const eased = 1 - (1 - t) * (1 - t);
    /* Primary ring plus two fainter echoes at staggered radii. */
    const echoes = [
      { scale: 1, alpha: 0.26 },
      { scale: 0.72, alpha: 0.13 },
      { scale: 1.28, alpha: 0.07 }
    ];
    for (const echo of echoes) {
      context.beginPath();
      context.arc(ripple.x, ripple.y, 5 + eased * RIPPLE_MAX_RADIUS * echo.scale, 0, Math.PI * 2);
      context.strokeStyle = `rgba(${ripple.r}, ${ripple.g}, ${ripple.b}, 1)`;
      context.globalAlpha = echo.alpha * (1 - eased);
      context.lineWidth = 1.25;
      context.stroke();
    }
  }
  context.globalAlpha = 1;

  const settled =
    ripples.length === 0 &&
    Math.abs(glowAlpha - GLOW_REST_ALPHA) < 0.004 &&
    (glowX < 0 || (Math.abs(targetX - glowX) < 0.5 && Math.abs(targetY - glowY) < 0.5));
  if (!settled && !hidden) {
    rafId = requestAnimationFrame(draw);
  } else {
    rafId = 0;
    /* Paint one final resting frame so disabling motion never leaves a gap. */
    if (ripples.length === 0 && glowX >= 0 && !hidden) drawResting();
  }
}

function drawResting() {
  if (!context || glowX < 0) return;
  const rgb = accentRgb();
  const alpha = Math.min(GLOW_REST_ALPHA * (isDark() ? GLOW_DARK_BOOST : 1), 0.16);
  const gradient = context.createRadialGradient(glowX, glowY, 0, glowX, glowY, GLOW_RADIUS);
  gradient.addColorStop(0, `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, ${alpha.toFixed(3)})`);
  gradient.addColorStop(1, `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0)`);
  context.clearRect(0, 0, window.innerWidth, window.innerHeight);
  context.fillStyle = gradient;
  context.beginPath();
  context.arc(glowX, glowY, GLOW_RADIUS, 0, Math.PI * 2);
  context.fill();
}

function kick() {
  if (rafId === 0 && !hidden) rafId = requestAnimationFrame(draw);
}

function handlePointerMove(event: PointerEvent) {
  targetX = event.clientX;
  targetY = event.clientY;
  if (glowX < 0) {
    glowX = targetX;
    glowY = targetY;
  }
  kick();
  /* Rings stay throttled by distance + time so fast motion never floods. */
  const now = performance.now();
  const dx = event.clientX - lastX;
  const dy = event.clientY - lastY;
  const travelled = dx * dx + dy * dy;
  if (lastX >= 0 && (travelled < MIN_TRAVEL * MIN_TRAVEL || now - lastSpawn < MIN_INTERVAL)) return;
  lastX = event.clientX;
  lastY = event.clientY;
  lastSpawn = now;
  const rgb = accentRgb();
  ripples.push({ x: event.clientX, y: event.clientY, born: now, r: rgb.r, g: rgb.g, b: rgb.b, dark: isDark() });
  if (ripples.length > MAX_RIPPLES) ripples.shift();
  kick();
}

function handleVisibility() {
  hidden = document.hidden;
  if (!hidden) kick();
  else if (rafId) {
    cancelAnimationFrame(rafId);
    rafId = 0;
  }
}

function handleReducedMotion(event: MediaQueryListEvent) {
  /* If the user enables reduced motion mid-session, tear the layer down. */
  if (event.matches) setMouseWaveEnabled(false);
}

/** Enables or disables the effect. Safe to call repeatedly with the same value. */
export function setMouseWaveEnabled(enabled: boolean) {
  if (typeof document === 'undefined') return;
  if (enabled && !canvas && !reducedMotion()) {
    canvas = document.createElement('canvas');
    canvas.className = 'wave-layer';
    canvas.setAttribute('aria-hidden', 'true');
    context = canvas.getContext('2d');
    document.body.appendChild(canvas);
    sizeCanvas();
    hidden = document.hidden;
    window.addEventListener('pointermove', handlePointerMove, { passive: true });
    window.addEventListener('resize', sizeCanvas);
    document.addEventListener('visibilitychange', handleVisibility);
    const query = window.matchMedia('(prefers-reduced-motion: reduce)');
    query.addEventListener('change', handleReducedMotion);
  } else if (!enabled && canvas) {
    window.removeEventListener('pointermove', handlePointerMove);
    window.removeEventListener('resize', sizeCanvas);
    document.removeEventListener('visibilitychange', handleVisibility);
    const query = window.matchMedia('(prefers-reduced-motion: reduce)');
    query.removeEventListener('change', handleReducedMotion);
    if (rafId) cancelAnimationFrame(rafId);
    rafId = 0;
    ripples = [];
    clearPointerMirror();
    lastX = -1;
    lastY = -1;
    targetX = -1;
    targetY = -1;
    glowX = -1;
    glowY = -1;
    glowAlpha = 0;
    hidden = false;
    canvas.remove();
    canvas = null;
    context = null;
  }
}
