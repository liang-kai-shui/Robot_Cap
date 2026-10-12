"""Focused Windows terminal controls; every fresh key requests one bounded move."""
import argparse
from datetime import datetime
import msvcrt
from pathlib import Path
import time

import serial


def main():
    parser = argparse.ArgumentParser(description="P forward30cm then left90, WASD/F/J/L move, C/E measure, T stop, Q quit")
    parser.add_argument("--port", default="COM5")
    args = parser.parse_args()
    # Set control lines BEFORE opening so the client does not deliberately reset
    # the board. Some USB bridges can still pulse them during open.
    port = serial.Serial()
    port.port, port.baudrate = args.port, 115200
    port.timeout, port.write_timeout = 0, 0.2
    port.dtr, port.rts = False, False
    log_path = Path(__file__).with_name("keyboard-drive-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".log")
    try:
        port.open()
    except serial.SerialException as error:
        print(f"Cannot open {args.port}: {error}\nClose idf.py monitor and other serial tools first.")
        return 1
    print("Focus THIS terminal. P forward30cm then left90 | WASD move | F forward30cm | J left90 | L right90 | C/E measure | T STOP | Z zero | Q quit")
    print("C/E: disconnect motor 5V, keep USB. Hand-roll straight 50cm; E after stopping. Motion keys disabled until E/T.")
    print("WASD <=0.8s. J/L angle turn <=3s, needs fixed calibrated BMI and chassis on a level floor. No BOOT unlock.")
    print("F: encoder distance target 30cm, <=6s; report DISTANCE_RESULT after coast. Cable drag and slip affect ground travel.")
    print("v1.8 SUPERVISED TEST: ToF and forward obstacle stop are OFF. Watch the car, keep clear space, T stops.")
    print("P runs F once, waits for its valid result and new READY, then J once. T/Q cancels all remaining steps. No automatic retry.")
    print(f"Log: {log_path}")
    ready = False
    measurement_active = False
    supervised_ready = False
    sequence_phase = None
    sequence_started = 0.0
    sequence_result_ok = sequence_step_done = sequence_move_started = False
    ever_ready = False
    pending_since = None
    last_motion_press = -1.0
    last_measure_press = -1.0
    receive = bytearray()
    started = time.monotonic()
    timeout_reported = False
    log = None

    def sequence_event(message):
        print(message)
        if log is not None and not log.closed:
            log.write(message + "\n")
            log.flush()

    def cancel_sequence(reason, send_stop=True):
        nonlocal sequence_phase, ready, pending_since
        if sequence_phase is None:
            return
        phase = sequence_phase
        sequence_phase = None
        ready = False
        pending_since = None
        if send_stop:
            port.write(b"t")
        sequence_event(f"SEQUENCE_CANCELLED phase={phase} cause={reason}; remaining steps discarded.")

    try:
        with log_path.open("w", encoding="utf-8") as log:
            # Open with a STOP, never a motion command. Firmware emits READY
            # after cooldown. An older firmware cannot satisfy this handshake.
            port.write(b"t")
            while True:
                incoming = port.read(min(port.in_waiting, 4096))
                if incoming:
                    receive.extend(incoming)
                    while b"\n" in receive:
                        line, _, remainder = receive.partition(b"\n")
                        receive = bytearray(remainder)
                        text = line.decode("utf-8", errors="replace").rstrip()
                        print(text)
                        log.write(text + "\n")
                        log.flush()
                        if "VEHICLE_START" in text or "VEHICLE_STOP" in text or "VEHICLE_BLOCKED" in text or "reason=" in text:
                            ready = False
                            pending_since = None
                        if "VEHICLE_DONE" in text:
                            pending_since = None
                        if "VEHICLE_READY:" in text:
                            ready = True
                            supervised_ready = "firmware=1.7" in text and "front_guard=OFF" in text
                            ever_ready = True
                            pending_since = None
                        if "DISTANCE_CAL_BEGIN" in text:
                            measurement_active = True
                            ready = False
                            pending_since = None
                        if "DISTANCE_CAL_RESULT" in text or "DISTANCE_CAL_CANCEL" in text:
                            measurement_active = False
                            ready = False
                            pending_since = None
                        if "DISTANCE_CAL_REJECTED" in text:
                            pending_since = None
                        if sequence_phase is not None:
                            fields = dict(part.split("=", 1) for part in text.split() if "=" in part)
                            expected_action = "f" if sequence_phase == "forward" else "j"
                            if "VEHICLE_START" in text:
                                if fields.get("action") == expected_action:
                                    sequence_move_started = True
                                else:
                                    cancel_sequence("UNEXPECTED_ACTION")
                            if "VEHICLE_BLOCKED" in text or "reason=" in text or "ANGLE_INVALID" in text:
                                cancel_sequence("DEVICE_BLOCK_OR_FAULT")
                            if "VEHICLE_STOP" in text:
                                expected_stop = "DISTANCE_TARGET" if sequence_phase == "forward" else "ANGLE_TARGET"
                                if fields.get("action") != expected_action or fields.get("source") != expected_stop:
                                    cancel_sequence("DEVICE_STOP")
                            if "DISTANCE_RESULT" in text and sequence_phase == "forward":
                                sequence_result_ok = fields.get("action") == "f" and fields.get("encoder_valid") == "1" and fields.get("yaw_valid") == "1"
                                if not sequence_result_ok:
                                    cancel_sequence("INVALID_DISTANCE_RESULT")
                            if "TURN_RESULT" in text and sequence_phase == "turn":
                                sequence_result_ok = fields.get("action") == "j" and fields.get("valid") == "1" and fields.get("within5deg") == "YES"
                                if not sequence_result_ok:
                                    cancel_sequence("INVALID_OR_OUT_OF_TOLERANCE_TURN")
                            if "VEHICLE_DONE" in text and sequence_phase is not None:
                                expected_type = "DISTANCE_MOVE" if sequence_phase == "forward" else "ANGLE_TURN"
                                # Require the actual START, matching result and completion;
                                # an orphan READY/DONE cannot launch the second segment.
                                if not sequence_move_started or not sequence_result_ok or fields.get("action") != expected_action or fields.get("type", "").rstrip(";") != expected_type:
                                    cancel_sequence("MISSING_OR_UNEXPECTED_COMPLETION")
                                elif sequence_phase == "forward":
                                    sequence_step_done = True
                                    ready = False
                                    sequence_event("SEQUENCE_STEP_DONE step=1/2; waiting for stopped wheels and a NEW READY before left90.")
                                else:
                                    sequence_phase = None
                                    ready = False
                                    sequence_event("SEQUENCE_DONE steps=2/2; forward30cm then left90 completed, motors off.")
                if len(receive) > 8192:
                    receive.clear()
                keys = []
                while msvcrt.kbhit():
                    key = msvcrt.getwch()
                    if key in ("\x00", "\xe0"):
                        msvcrt.getwch()  # Ignore Windows special-key scan codes.
                    else:
                        keys.append(key.lower())
                if "q" in keys or "\x1b" in keys or "\x03" in keys:
                    cancel_sequence("QUIT", send_stop=False)
                    break
                if "t" in keys:
                    port.write(b"t")
                    ready = False
                    pending_since = None
                    cancel_sequence("T", send_stop=False)
                    print("STOP sent")
                elif sequence_phase is not None:
                    # Discard all other controls, including held P repeats, while
                    # the sequence owns either motion or the pause between steps.
                    if any(key in "wasdfjlp" for key in keys):
                        last_motion_press = time.monotonic()
                elif "p" in keys:
                    controls = [key for key in keys if key in "pcewasdfjlz"]
                    now = time.monotonic()
                    fresh = now-last_motion_press >= 0.5
                    last_motion_press = now
                    if ready and not measurement_active and fresh and len(controls) == 1:
                        if not supervised_ready:
                            print("P requires flashed v1.7 and READY with front_guard=OFF; no motion sent.")
                        else:
                            port.write(b"f")
                            ready = False
                            pending_since = now
                            sequence_phase = "forward"
                            sequence_started = now
                            sequence_result_ok = sequence_step_done = sequence_move_started = False
                            sequence_event("SEQUENCE_BEGIN steps=2: forward30cm -> stopped pause -> left90 -> stop. T cancels.")
                elif any(key in "ce" for key in keys):
                    controls = [key for key in keys if key in "pcewasdfjlz"]
                    now = time.monotonic()
                    fresh = now-last_measure_press >= 0.5
                    last_measure_press = now
                    if len(controls) == 1 and fresh and pending_since is None:
                        key = controls[0]
                        if (key == "c" and ready and not measurement_active) or (key == "e" and measurement_active):
                            port.write(key.encode("ascii"))
                            ready = False
                            pending_since = now
                            print("Measurement requested; see DISTANCE_CAL_BEGIN" if key == "c" else "Result requested; see DISTANCE_CAL_RESULT / REJECTED")
                elif "z" in keys:
                    if ready:
                        port.write(b"z")
                        print("Angle zero requested; see ANGLE_ZERO_OK / ANGLE_ZERO_REJECTED")
                else:
                    movement = [key for key in keys if key in "wasdfjl"]
                    if movement:
                        now = time.monotonic()
                        fresh = now-last_motion_press >= 0.5
                        last_motion_press = now
                        if ready and not measurement_active and fresh and len(movement) == 1:
                            port.write(movement[0].encode("ascii"))
                            ready = False
                            pending_since = now
                            print(f"Sent {movement[0].upper()}; wait for VEHICLE_READY before next key")
                if sequence_phase is not None and time.monotonic()-sequence_started > 12:
                    cancel_sequence("TIMEOUT_12S")
                if sequence_phase == "forward" and sequence_step_done and ready:
                    if not supervised_ready:
                        cancel_sequence("FIRMWARE_MODE_CHANGED")
                    else:
                        # Keyboard T/Q above take priority even if READY arrives
                        # in the same loop. Firmware rechecks current BMI at J.
                        port.write(b"j")
                        ready = False
                        pending_since = time.monotonic()
                        sequence_phase = "turn"
                        sequence_result_ok = sequence_step_done = sequence_move_started = False
                        sequence_event("SEQUENCE_STEP_START step=2/2 action=J left90; T stops.")
                if pending_since is not None and time.monotonic()-pending_since > 1.2:
                    # A rejected/lost command must not leave the client waiting
                    # indefinitely. STOP restores readiness; never retry motion.
                    port.write(b"t")
                    pending_since = None
                    ready = False
                    cancel_sequence("NO_ACKNOWLEDGEMENT", send_stop=False)
                    print("No motion acknowledgement; STOP sent. Wait for VEHICLE_READY, then press a fresh key. No automatic retry.")
                if not ever_ready and not timeout_reported and time.monotonic()-started > 12:
                    timeout_reported = True
                    print("If no VEHICLE_READY appeared: check KEYBOARD_DRIVE v1.7 was flashed and COM5 is the USB-UART port.")
                time.sleep(0.005)
    except KeyboardInterrupt:
        cancel_sequence("CTRL_C", send_stop=False)
    except (serial.SerialException, OSError) as error:
        cancel_sequence("SERIAL_DISCONNECT", send_stop=False)
        print(f"Serial error: {error}; firmware keeps its deadline: WASD <=0.8s, J/L <=3s, F <=6s. No automatic retry.")
        return 1
    finally:
        try:
            port.write(b"t")
            port.flush()
        except (serial.SerialException, OSError):
            pass
        port.close()
    print("STOP sent; controller closed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
