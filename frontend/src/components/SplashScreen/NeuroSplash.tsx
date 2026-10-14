import { useEffect, useMemo, useState } from "react";
import "./NeuroSplash.css";

type Particle = {
  id: number;
  angle: number;
  distance: number;
  size: number;
  delay: number;
  duration: number;
  drift: number;
};

type NeuralNode = {
  id: number;
  x: number;
  y: number;
  size: number;
  delay: number;
};

const PARTICLE_COUNT = 34;
const NODE_COUNT = 18;

function createParticles(): Particle[] {
  return Array.from({ length: PARTICLE_COUNT }, (_, id) => {
    const angle = (id / PARTICLE_COUNT) * Math.PI * 2;
    const distance = 170 + Math.random() * 180;

    return {
      id,
      angle,
      distance,
      size: 1.5 + Math.random() * 3,
      delay: Math.random() * 0.35,
      duration: 2.2 + Math.random() * 0.9,
      drift: -30 + Math.random() * 60,
    };
  });
}

function createNodes(): NeuralNode[] {
  return Array.from({ length: NODE_COUNT }, (_, id) => {
    const angle = (id / NODE_COUNT) * Math.PI * 2;
    const radius = 75 + Math.random() * 95;

    return {
      id,
      x: Math.cos(angle) * radius,
      y: Math.sin(angle) * radius * 0.7,
      size: 2 + Math.random() * 2.5,
      delay: Math.random() * 0.5,
    };
  });
}

function BrainIcon() {
  return (
    <svg
      className="neuro-splash__brain"
      viewBox="0 0 320 260"
      fill="none"
      aria-hidden="true"
    >
      <defs>
        <linearGradient
          id="neuroBrainGradient"
          x1="40"
          y1="20"
          x2="280"
          y2="240"
          gradientUnits="userSpaceOnUse"
        >
          <stop offset="0%" stopColor="#2563eb" />
          <stop offset="50%" stopColor="#3b82f6" />
          <stop offset="100%" stopColor="#111827" />
        </linearGradient>

        <filter id="neuroBrainGlow" x="-80%" y="-80%" width="260%" height="260%">
          <feGaussianBlur stdDeviation="7" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      {/* Outer brain */}
      <path
        className="brain-outline"
        d="
          M158 31
          C132 11 96 21 87 48
          C57 42 31 61 36 91
          C11 106 18 141 43 151
          C37 180 60 205 87 203
          C98 229 131 235 155 218
          C172 239 207 236 221 216
          C251 224 280 201 275 173
          C302 157 301 122 277 107
          C288 77 267 48 238 48
          C226 20 191 15 169 33
          C165 30 161 29 158 31
          Z
        "
      />

      {/* Brain folds */}
      <path className="brain-fold" d="M91 51 C116 67 105 88 82 96 C62 104 63 125 84 136" />
      <path className="brain-fold" d="M132 34 C113 54 125 73 143 85 C161 97 153 118 133 127" />
      <path className="brain-fold" d="M173 37 C151 57 160 78 180 89 C200 100 193 121 174 133" />
      <path className="brain-fold" d="M218 52 C195 62 199 83 218 96 C235 108 230 129 211 139" />

      <path className="brain-fold" d="M55 143 C80 134 96 149 88 169 C80 188 97 202 117 198" />
      <path className="brain-fold" d="M127 130 C105 143 108 164 128 177 C146 189 141 208 123 219" />
      <path className="brain-fold" d="M171 136 C149 150 153 170 173 181 C192 191 188 210 171 222" />
      <path className="brain-fold" d="M222 143 C202 151 207 171 225 181 C242 191 239 207 222 215" />

      {/* Neural nodes inside brain */}
      <g filter="url(#neuroBrainGlow)">
        <circle className="brain-node" cx="84" cy="96" r="4" />
        <circle className="brain-node" cx="143" cy="85" r="4" />
        <circle className="brain-node" cx="180" cy="89" r="4" />
        <circle className="brain-node" cx="218" cy="96" r="4" />
        <circle className="brain-node" cx="88" cy="169" r="4" />
        <circle className="brain-node" cx="128" cy="177" r="4" />
        <circle className="brain-node" cx="173" cy="181" r="4" />
        <circle className="brain-node" cx="225" cy="181" r="4" />
      </g>

      {/* Neural connections */}
      <path className="brain-connection" d="M84 96 L143 85 L180 89 L218 96" />
      <path className="brain-connection" d="M88 169 L128 177 L173 181 L225 181" />
      <path className="brain-connection" d="M143 85 L128 177" />
      <path className="brain-connection" d="M180 89 L173 181" />
    </svg>
  );
}

function NeuralNetwork() {
  const nodes = useMemo(() => createNodes(), []);

  return (
    <div className="neuro-splash__network" aria-hidden="true">
      <svg
        className="neuro-splash__network-lines"
        viewBox="-220 -160 440 320"
        preserveAspectRatio="xMidYMid meet"
      >
        <line x1="0" y1="0" x2="-115" y2="-72" />
        <line x1="0" y1="0" x2="-145" y2="-5" />
        <line x1="0" y1="0" x2="-105" y2="75" />

        <line x1="0" y1="0" x2="110" y2="-72" />
        <line x1="0" y1="0" x2="150" y2="0" />
        <line x1="0" y1="0" x2="108" y2="75" />

        <line x1="-115" y1="-72" x2="-175" y2="-110" />
        <line x1="-115" y1="-72" x2="-180" y2="-30" />
        <line x1="-105" y1="75" x2="-165" y2="110" />

        <line x1="110" y1="-72" x2="170" y2="-110" />
        <line x1="110" y1="-72" x2="180" y2="-28" />
        <line x1="108" y1="75" x2="168" y2="110" />
      </svg>

      {nodes.map((node) => (
        <span
          key={node.id}
          className="network-node"
          style={
            {
              "--node-x": `${node.x}px`,
              "--node-y": `${node.y}px`,
              "--node-size": `${node.size}px`,
              "--node-delay": `${node.delay}s`,
            } as React.CSSProperties
          }
        />
      ))}
    </div>
  );
}

function NeuroImageFragments() {
  return (
    <div className="neuro-splash__fragments" aria-hidden="true">
      <div className="neuro-fragment neuro-fragment--one">
        <span />
        <span />
        <span />
      </div>

      <div className="neuro-fragment neuro-fragment--two">
        <span />
        <span />
        <span />
      </div>

      <div className="neuro-fragment neuro-fragment--three">
        <span />
        <span />
        <span />
      </div>

      <div className="neuro-fragment neuro-fragment--four">
        <span />
        <span />
        <span />
      </div>
    </div>
  );
}

function NeuralParticles() {
  const particles = useMemo(() => createParticles(), []);

  return (
    <div className="neuro-splash__particles" aria-hidden="true">
      {particles.map((particle) => {
        const x = Math.cos(particle.angle) * particle.distance;
        const y = Math.sin(particle.angle) * particle.distance * 0.65;

        return (
          <span
            key={particle.id}
            className="neural-particle"
            style={
              {
                "--particle-x": `${x}px`,
                "--particle-y": `${y}px`,
                "--particle-size": `${particle.size}px`,
                "--particle-delay": `${particle.delay}s`,
                "--particle-duration": `${particle.duration}s`,
                "--particle-drift": `${particle.drift}px`,
              } as React.CSSProperties
            }
          />
        );
      })}
    </div>
  );
}

export default function NeuroSplash() {
  /*
   * Session-scoped: replay only once per browser session, and skip entirely
   * when reduced motion is requested - the animation is the whole point.
   */
  const shouldSkip = () => {
    try {
      if (sessionStorage.getItem("neuroweave-splash-seen")) return true;
      if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return true;
    } catch {
      /* storage unavailable - show the splash */
    }
    return false;
  };

  const [visible, setVisible] = useState(() => !shouldSkip());
  const [closing, setClosing] = useState(false);

  useEffect(() => {
    if (!visible) return;
    try {
      sessionStorage.setItem("neuroweave-splash-seen", "1");
    } catch {
      /* storage unavailable - the splash still shows for this mount only */
    }

    const closeTimer = window.setTimeout(() => {
      setClosing(true);
    }, 1400);

    const removeTimer = window.setTimeout(() => {
      setVisible(false);
    }, 2000);

    return () => {
      window.clearTimeout(closeTimer);
      window.clearTimeout(removeTimer);
    };
  }, [visible]);

  const handleSkip = () => {
    setClosing(true);

    window.setTimeout(() => {
      setVisible(false);
    }, 450);
  };

  if (!visible) {
    return null;
  }

  return (
    <div
      className={`neuro-splash ${
        closing ? "neuro-splash--closing" : ""
      }`}
      role="presentation"
    >
      {/* Ambient background */}
      <div className="neuro-splash__background">
        <div className="neuro-splash__glow neuro-splash__glow--one" />
        <div className="neuro-splash__glow neuro-splash__glow--two" />

        <div className="neuro-splash__grid" />
      </div>

      <div className="neuro-splash__content">
        {/* Main intelligence visualization */}
        <div className="neuro-splash__visual">
          <div className="neuro-splash__core-glow" />

          <NeuralNetwork />
          <NeuralParticles />
          <NeuroImageFragments />

          <div className="neuro-splash__brain-wrap">
            <BrainIcon />
          </div>

          {/* Final convergence point */}
          <div className="neuro-splash__convergence">
            <span />
            <span />
            <span />
          </div>
        </div>

        {/* Brand text only — no fake logo */}
        <div className="neuro-splash__brand">
          <div className="neuro-splash__brand-name">
            Neuro<span>Weave</span>
          </div>

          <div className="neuro-splash__tagline">
            Intelligence in every connection
          </div>

          <div className="neuro-splash__status">
            <span className="neuro-splash__status-dot" />
            Neural system initializing
          </div>
        </div>
      </div>

      <button
        type="button"
        className="neuro-splash__skip"
        onClick={handleSkip}
        aria-label="Skip intro"
      >
        Skip
      </button>
    </div>
  );
}