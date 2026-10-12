#pragma once
// Supervised motion keeps the forward guard OFF. Acquisition is independent:
// displaying range must not silently change the motor's stopping policy.
#ifndef ROBOT_SUPERVISED_TEST
#define ROBOT_SUPERVISED_TEST 1
#endif
#ifndef ROBOT_TOF_ENABLED
#define ROBOT_TOF_ENABLED 1
#endif
#if !ROBOT_SUPERVISED_TEST && !ROBOT_TOF_ENABLED
#error "Forward guard requires ToF acquisition"
#endif
