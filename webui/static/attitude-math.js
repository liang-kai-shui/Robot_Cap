// Model mounting is a quarter-turn about sensor Z: C R C^-1.
// Rotate quaternion X/Y, rather than swapping Euler angles (incorrect for mixed tilt).
// Raw telemetry and the firmware's yaw controller remain unchanged.
export function modelQuaternion(sample, frame = 'header') {
  const half = Math.PI / 360;
  const sx=Math.sin(sample.roll*half), cx=Math.cos(sample.roll*half);
  const sy=Math.sin(sample.pitch*half), cy=Math.cos(sample.pitch*half);
  const sz=Math.sin(sample.yaw*half), cz=Math.cos(sample.yaw*half);
  const x=sx*cy*cz-cx*sy*sz, y=cx*sy*cz+sx*cy*sz;
  const z=cx*cy*sz-sx*sy*cz, w=cx*cy*cz+sx*sy*sz;
  if(frame === 'header') return {x:-y, y:x, z, w};
  if(frame === 'header-negative') return {x:y, y:-x, z, w};
  return {x,y,z,w};
}

export function attitudeParts(sample) {
  const calibrated = sample?.calibrated === 1;
  return {tilt:calibrated && sample.tilt_valid === 1 && Number.isFinite(sample.roll) && Number.isFinite(sample.pitch),
          yaw:calibrated && sample.yaw_valid === 1 && Number.isFinite(sample.yaw)};
}
