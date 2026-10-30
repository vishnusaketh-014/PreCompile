import React, { Suspense } from 'react'
import { ShaderGradient, ShaderGradientCanvas } from '@shadergradient/react'

// Per-view gradient configs
const GRADIENTS = {
  compare: {
    type: 'waterPlane',
    color1: '#0f0721',
    color2: '#1a0a3d',
    color3: '#050810',
    uStrength: 2.5,
    uFrequency: 3.5,
    uSpeed: 0.3,
    cDistance: 3.5,
    cPolarAngle: 115,
    lightType: '3d',
    brightness: 1.1,
  },
  overview: {
    type: 'waterPlane',
    color1: '#060b1a',
    color2: '#0a1628',
    color3: '#050810',
    uStrength: 1.8,
    uFrequency: 2.5,
    uSpeed: 0.2,
    cDistance: 3.8,
    cPolarAngle: 120,
    lightType: '3d',
    brightness: 0.9,
  },
  safety: {
    type: 'waterPlane',
    color1: '#1a0505',
    color2: '#0d0808',
    color3: '#050810',
    uStrength: 2.0,
    uFrequency: 3.0,
    uSpeed: 0.4,
    cDistance: 3.2,
    cPolarAngle: 125,
    lightType: '3d',
    brightness: 0.8,
  },
  default: {
    type: 'waterPlane',
    color1: '#050a18',
    color2: '#080d20',
    color3: '#050810',
    uStrength: 1.5,
    uFrequency: 2.0,
    uSpeed: 0.15,
    cDistance: 4.0,
    cPolarAngle: 120,
    lightType: '3d',
    brightness: 0.85,
  },
}

function GradientScene({ config }) {
  return (
    <ShaderGradient
      type={config.type}
      color1={config.color1}
      color2={config.color2}
      color3={config.color3}
      uStrength={config.uStrength}
      uFrequency={config.uFrequency}
      uSpeed={config.uSpeed}
      cDistance={config.cDistance}
      cPolarAngle={config.cPolarAngle}
      lightType={config.lightType}
      brightness={config.brightness}
      animate="on"
      grain="off"
      zoomOut={false}
    />
  )
}

export default function ShaderBackground({ view }) {
  const config = GRADIENTS[view] ?? GRADIENTS.default

  return (
    <div className="gradient-bg">
      <Suspense fallback={null}>
        <ShaderGradientCanvas
          style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}
          pixelDensity={1.2}
          fov={45}
        >
          <GradientScene config={config} />
        </ShaderGradientCanvas>
      </Suspense>
    </div>
  )
}
