// Changing the model's in-plane reference by 180 degrees is a proper rotation,
// not a mirror. Conjugating R by Rz(pi) reverses roll/pitch, retains yaw.
// Raw sensor readings and the firmware's yaw controller remain unchanged.
export function modelAngles(sample, frame = 'header') {
  const sign = frame === 'header' ? -1 : 1;
  return {roll:sign * sample.roll, pitch:sign * sample.pitch, yaw:sample.yaw};
}

export function attitudeParts(sample) {
  const calibrated = sample?.calibrated === 1;
  return {tilt:calibrated && sample.tilt_valid === 1 && Number.isFinite(sample.roll) && Number.isFinite(sample.pitch),
          yaw:calibrated && sample.yaw_valid === 1 && Number.isFinite(sample.yaw)};
}
