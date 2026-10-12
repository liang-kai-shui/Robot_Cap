"""Physical backend contract; ESP32SerialBackend supplies the tested transport."""
from robot.backends.base import RobotBackend


class PhysicalBackend(RobotBackend):
    """Future hardware contract: bounded I/O, cooperative cancellation, independent stop.

    Each registered action handler must accept cancel_event and deadline and
    check both during I/O. emergency_stop must work while any handler is active.
    Concrete serial hardware support lives in robot.backends.esp32_serial.
    """
