// Wave field from TX and its wall images, evaluated per pixel in room metres.
export const MAX_SOURCES = 25;
export const SCAT_SOURCES = 13; // the body and its wall images up to two bounces

export const FIELD = `#version 300 es
precision highp float;
uniform vec4 uMap;          // gl_FragCoord -> room metres: (ax, bx, ay, by)
uniform vec4 uClip;         // visible region, m: x0, y0, x1, y1 (screen-wide in free space, then the room)
uniform vec3 uSrc[${MAX_SOURCES}]; // x, y, gain
uniform vec3 uScat[${SCAT_SOURCES}]; // the body re-radiating what reaches it from tx, and its wall images: x, y, amplitude
uniform float uScatLag;     // k·|tx−body|: the wave reaches the body before it re-radiates
uniform vec3 uBall;         // x, y, radius of the ball: the direct wave is shadowed behind it
uniform float uShadow;      // share of the direct wave left in that shadow (1: no ball)
uniform int uN;
uniform float uK;           // 2π/λ
uniform float uPhase;       // ωt
uniform float uNorm;
uniform float uGain;
uniform float uStatic;      // 1: time-free intensity |Σ| instead of the travelling real part
uniform vec3 uWash;
uniform vec3 uBloom;
out vec4 o;

void main() {
  vec2 p = vec2(uMap.x * gl_FragCoord.x + uMap.y, uMap.z * gl_FragCoord.y + uMap.w);
  // image sources only describe the field inside the walls, so the clip closes onto the room before they're added
  float inside = step(uClip.x, p.x) * step(p.x, uClip.z) * step(uClip.y, p.y) * step(p.y, uClip.w);
  float re = 0.0;
  float im = 0.0;
  for (int i = 0; i < ${MAX_SOURCES}; i++) {
    if (i >= uN) break;
    float d = max(distance(p, uSrc[i].xy), 0.05);
    float a = uSrc[i].z * inversesqrt(d);
    if (i == 0) {
      // physics.ts clearance(): does the straight line tx→p run through the ball?
      vec2 s = uSrc[0].xy;
      vec2 dp = p - s;
      float t = clamp(dot(uBall.xy - s, dp) / max(dot(dp, dp), 1e-6), 0.0, 1.0);
      float m = distance(s + t * dp, uBall.xy);
      a *= mix(uShadow, 1.0, smoothstep(uBall.z - 0.03, uBall.z + 0.03, m));
    }
    float ph = uK * d - uPhase;
    re += a * cos(ph);
    im += a * sin(ph);
  }
  for (int i = 0; i < ${SCAT_SOURCES}; i++) {
    // clamped at the body's radius so the source point doesn't blow out (the ball is drawn over it)
    float d = max(distance(p, uScat[i].xy), 0.175);
    float a = uScat[i].z * inversesqrt(d);
    float ph = uK * d + uScatLag - uPhase;
    re += a * cos(ph);
    im += a * sin(ph);
  }
  float v = mix(max(re, 0.0), 0.6 * length(vec2(re, im)), uStatic) * uNorm;
  // squaring thins crests into lines, so the pattern reads as light exposing the floor
  v = pow(clamp(v, 0.0, 1.0), 2.0) * inside * uGain;
  vec3 c = v < 0.5 ? uWash * v * 2.0 : mix(uWash, uBloom, v * 2.0 - 1.0);
  o = vec4(c, 1.0);
}`;
